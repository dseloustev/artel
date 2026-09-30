"""task-grammar: the task blocks of a tasklist, their validation, waves and route floors.

Pure functions over a tasklist body (the document header already stripped); the CLI is
tasklist_tasks.py, which imports this module. Nothing here prints or exits, and the only
file this module reads is the sensitive-paths policy (load_sensitive_rules).

Contract: docs/task-grammar.md
"""
import fnmatch
import json
import re
from pathlib import Path

# A task listing more files than this is routed `full` (docs/task-grammar.md §7).
ROUTE_FULL_FILES = 5
# The reason the approval fold-back writes on a route the person changed at the pause
# (`Route: light — set at approval`): such a route is final over the static floors.
APPROVAL_REASON = 'set at approval'

ITERATION_RE = re.compile(r'^##\s+(?:Iteration|Phase)\s+(\d+)\s*:\s*(.+?)\s*$')
HEADING_2_RE = re.compile(r'^##\s+')
HEADING_3_RE = re.compile(r'^###\s+')
TASK_RE = re.compile(r'^###\s+Task\s+(\d+)\.(\d+)\s*:\s*(.+?)\s*$')
FIELD_RE = re.compile(r'^-\s+\*\*([A-Za-z][A-Za-z ]*?):\*\*\s*(.*?)\s*$')
STEP_RE = re.compile(r'^-\s+\[([ xX])\]\s+(.+?)\s*$')
ANY_CHECKBOX_RE = re.compile(r'^\s*-\s+\[([ xX])\]\s+(.+?)\s*$')
GOAL_RE = re.compile(r'^\*\*Goal:\*\*\s*(.+?)\s*$')
TEST_FOOTER_RE = re.compile(r'^\*\*Test:\*\*\s*(.+?)\s*$')
HITL_RE = re.compile(r'\[HITL:\s*([^\]]+)\]')
PATH_RE = re.compile(r'`([^`]+)`(\s*\(new\))?')
# The separator between a value and its reason: an em dash, an en dash, `--` or a
# spaced hyphen. Spaces are required, so `money-movement` is never split.
DASH_RE = re.compile(r'\s+(?:—|–|--|-)\s+')
TASK_NUMBER_RE = re.compile(r'^(?:Task\s+)?(\d+)\.(\d+)$')
REQ_ID_RE = re.compile(r'^R\d+$')
BACKTICK_SPAN_RE = re.compile(r'`[^`]*`')

REQUIREMENTS_RE = re.compile(r'^##\s+Requirements\s*$')
REQUIREMENT_ENTRY_RE = re.compile(r'^-\s+\*\*(R\d+)\*\*(.*)$')
WITHDRAWN_RE = re.compile(r'\(withdrawn\b', re.IGNORECASE)
ALREADY_MET_RE = re.compile(r'\(already met\b', re.IGNORECASE)

KNOWN_FIELDS = ('Files', 'Depends on', 'Route', 'Test', 'Produces', 'Implements')
REQUIRED_FIELDS = ('Files', 'Depends on', 'Route', 'Test')

# The rule names of docs/task-grammar.md §6; tests/test_task_grammar_docs.py holds the
# document's tables to these, so a rule cannot be added on one side only.
GRAMMAR_RULES = (
    'missing-field', 'empty-field', 'duplicate-field', 'bad-files', 'bad-dependency',
    'unknown-dependency', 'cross-iteration-dependency', 'self-dependency', 'cycle',
    'bad-route', 'route-reason', 'test-reason', 'bad-test', 'bad-implements', 'numbering',
    'no-steps', 'bare-checkbox', 'no-tasks', 'hitl-on-step',
)
CHECK_RULES = {
    'uncovered-requirement': 'Critical',
    'unknown-requirement': 'Important',
    'missing-implements': 'Important',
    'missing-file': 'Important',
    'missing-test': 'Important',
    'placeholder': 'Important',
}

# What a writer leaves behind when it copies a template instead of planning.
PLACEHOLDERS = (
    (re.compile(r'\bTBD\b'), 'TBD'),
    (re.compile(r'\bTODO\b'), 'TODO'),
    (re.compile(r'\?\?\?'), '???'),
    (re.compile(r'<[^<>]+>'), 'a template slot'),
    (re.compile(r'\bsame as task\b', re.IGNORECASE), '"same as Task"'),
    (re.compile(r'\betc\b', re.IGNORECASE), '"etc."'),
)


def is_task_format(text):
    """True when the body holds at least one `### Task N.M:` heading (§4)."""
    return any(TASK_RE.match(line) for line in text.splitlines())


def _problem(line, task, rule, message):
    return {'line': line, 'task': task, 'rule': rule, 'message': message}


def _split_reason(value):
    """(head, reason) around the first spaced dash; reason is None without one."""
    parts = DASH_RE.split(value, maxsplit=1)
    head = parts[0].strip()
    reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return head, reason


def parse(text, line_offset=0):
    """(iterations, problems, warnings) for a task-format tasklist body.

    `line_offset` is added to every reported line number, so a caller that stripped
    a header reports lines of the whole document. Problems are the grammar
    violations of docs/task-grammar.md §6; an empty list means the file mirrors.
    """
    iterations = []
    problems = []
    warnings = []
    current = None
    task = None
    last_field = None
    for index, line in enumerate(text.splitlines(), 1):
        lineno = index + line_offset
        match = ITERATION_RE.match(line)
        if match:
            current = {'number': int(match.group(1)), 'name': match.group(2),
                       'goal': None, 'test': None, 'line': lineno, 'tasks': []}
            iterations.append(current)
            task, last_field = None, None
            continue
        if HEADING_2_RE.match(line):
            current, task, last_field = None, None, None  # any other `## …` closes it
            continue
        if current is None:
            continue
        match = TASK_RE.match(line)
        if match:
            raw_title = match.group(3)
            hitl = HITL_RE.search(raw_title)
            title = re.sub(r'\s+', ' ', HITL_RE.sub(' ', raw_title)).strip()
            task = {'iteration': int(match.group(1)), 'index': int(match.group(2)),
                    'number': '{}.{}'.format(match.group(1), match.group(2)),
                    'title': title, 'hitl': hitl.group(1).strip() if hitl else None,
                    'line': lineno, 'fields': {}, 'steps': []}
            current['tasks'].append(task)
            last_field = None
            continue
        if HEADING_3_RE.match(line):
            task, last_field = None, None
            warnings.append('line {}: `{}` is not a task heading; nothing under it is a'
                            ' task'.format(lineno, line.strip()))
            continue
        match = GOAL_RE.match(line)
        if match:
            current['goal'], task, last_field = match.group(1), None, None
            continue
        match = TEST_FOOTER_RE.match(line)
        if match:
            current['test'], task, last_field = match.group(1), None, None
            continue
        if task is None:
            match = ANY_CHECKBOX_RE.match(line)
            if match:
                problems.append(_problem(
                    lineno, None, 'bare-checkbox',
                    'a checkbox outside any `### Task N.M:` block: {}'.format(match.group(2))))
            continue
        match = FIELD_RE.match(line)
        if match:
            name, value = match.group(1), match.group(2)
            if task['steps']:
                warnings.append('line {}: task {}: a field after the steps is ignored:'
                                ' {}'.format(lineno, task['number'], name))
                last_field = None
            elif name not in KNOWN_FIELDS:
                warnings.append('line {}: task {}: unknown field `{}` ignored'.format(
                    lineno, task['number'], name))
                last_field = None
            elif name in task['fields']:
                problems.append(_problem(lineno, task['number'], 'duplicate-field',
                                         'field `{}` appears twice'.format(name)))
                last_field = None
            else:
                task['fields'][name] = {'value': value, 'line': lineno}
                last_field = name
            continue
        match = STEP_RE.match(line)
        if match:
            if HITL_RE.search(match.group(2)):
                # The pre-0.23.0 place for the tag; the floor and the pause read the heading.
                problems.append(_problem(
                    lineno, task['number'], 'hitl-on-step',
                    'a `[HITL: …]` tag on a step is never read; move it to the `### Task {}:`'
                    ' heading'.format(task['number'])))
            task['steps'].append({'text': match.group(2),
                                  'done': match.group(1).lower() == 'x', 'line': lineno})
            last_field = None
            continue
        if line[:1] in (' ', '\t') and ANY_CHECKBOX_RE.match(line):
            # Not a step, and a task whose steps are all ticked would read done over it.
            problems.append(_problem(
                lineno, task['number'], 'bare-checkbox',
                'an indented checkbox is not a step of task {}: unindent it to make it a step,'
                ' or make it plain text'.format(task['number'])))
            continue
        if line[:1] in (' ', '\t') and line.strip():
            if last_field is not None:
                # A wrapped field value: the continuation joins the value.
                field = task['fields'][last_field]
                field['value'] = (field['value'] + ' ' + line.strip()).strip()
            continue  # an indented line under a step is the step's own detail
        if line.strip():
            last_field = None
    problems.extend(_validate(iterations))
    problems.sort(key=lambda problem: problem['line'])
    return iterations, problems, warnings


def _validate(iterations):
    problems = []
    for iteration in iterations:
        number = iteration['number']
        tasks = iteration['tasks']
        if not tasks:
            problems.append(_problem(iteration['line'], None, 'no-tasks',
                                     'iteration {} has no `### Task {}.1:` block'.format(
                                         number, number)))
            continue
        numbers = {task['number'] for task in tasks}
        for position, task in enumerate(tasks, 1):
            expected = '{}.{}'.format(number, position)
            if task['number'] != expected:
                problems.append(_problem(
                    task['line'], task['number'], 'numbering',
                    'task {} is the {} task of iteration {}; number it {}'.format(
                        task['number'], _ordinal(position), number, expected)))
            problems.extend(_validate_fields(task, number, numbers))
        problems.extend(_cycles(tasks, number))
    return problems


def _ordinal(n):
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return '{}{}'.format(n, suffix)


def _validate_fields(task, iteration_number, numbers):
    problems = []
    fields = task['fields']
    tid = task['number']
    for name in REQUIRED_FIELDS:
        if name not in fields:
            problems.append(_problem(task['line'], tid, 'missing-field',
                                     'required field `{}` is missing'.format(name)))
    for name, field in fields.items():
        if not field['value']:
            problems.append(_problem(field['line'], tid, 'empty-field',
                                     'field `{}` is empty'.format(name)))
    if not task['steps']:
        problems.append(_problem(task['line'], tid, 'no-steps',
                                 'the task has no `- [ ]` step'))

    files = fields.get('Files')
    if files and files['value'] and not PATH_RE.search(files['value']):
        problems.append(_problem(files['line'], tid, 'bad-files',
                                 '`Files:` names no backticked path'))

    depends = fields.get('Depends on')
    if depends and depends['value'] and depends['value'].lower() != 'none':
        for token in (t.strip() for t in depends['value'].split(',')):
            match = TASK_NUMBER_RE.match(token)
            if not match:
                problems.append(_problem(depends['line'], tid, 'bad-dependency',
                                         '`{}` is not a task number like `2.1`'.format(token)))
                continue
            dep = '{}.{}'.format(match.group(1), match.group(2))
            if int(match.group(1)) != iteration_number:
                problems.append(_problem(
                    depends['line'], tid, 'cross-iteration-dependency',
                    'depends on {}, outside iteration {}; an earlier iteration is already'
                    ' done and a later one cannot be waited for'.format(dep, iteration_number)))
            elif dep == tid:
                problems.append(_problem(depends['line'], tid, 'self-dependency',
                                         'the task depends on itself'))
            elif dep not in numbers:
                problems.append(_problem(depends['line'], tid, 'unknown-dependency',
                                         'depends on {}, which is not a task of iteration'
                                         ' {}'.format(dep, iteration_number)))

    route = fields.get('Route')
    if route and route['value']:
        head, reason = _split_reason(route['value'])
        if head.lower() not in ('light', 'full'):
            problems.append(_problem(route['line'], tid, 'bad-route',
                                     '`Route:` is `light` or `full — <reason>`, not'
                                     ' `{}`'.format(route['value'])))
        elif head.lower() == 'full' and reason is None:
            problems.append(_problem(route['line'], tid, 'route-reason',
                                     '`Route: full` needs a reason: `full — <reason>`'))

    test = fields.get('Test')
    if test and test['value']:
        head, reason = _split_reason(test['value'])
        if head.lower() == 'none':
            if reason is None:
                problems.append(_problem(test['line'], tid, 'test-reason',
                                         '`Test: none` needs a reason: `none — <reason>`'))
        elif not PATH_RE.search(test['value']):
            problems.append(_problem(test['line'], tid, 'bad-test',
                                     '`Test:` names no backticked test file and is not'
                                     ' `none — <reason>`'))

    implements = fields.get('Implements')
    if implements and implements['value']:
        for token in (t.strip() for t in implements['value'].split(',')):
            if not REQ_ID_RE.match(token):
                problems.append(_problem(implements['line'], tid, 'bad-implements',
                                         '`{}` is not a requirement ID like `R1`'.format(token)))
    return problems


def dependencies(task):
    """The task numbers a task's `Depends on:` names, well-formed tokens only."""
    field = task['fields'].get('Depends on')
    if not field or not field['value'] or field['value'].lower() == 'none':
        return []
    deps = []
    for token in (t.strip() for t in field['value'].split(',')):
        match = TASK_NUMBER_RE.match(token)
        if match:
            deps.append('{}.{}'.format(match.group(1), match.group(2)))
    return deps


def _cycles(tasks, iteration_number):
    """One `cycle` problem per dependency cycle among an iteration's tasks."""
    by_number = {task['number']: task for task in tasks}
    graph = {task['number']: [d for d in dependencies(task)
                              if d in by_number and d != task['number']]
             for task in tasks}
    state = {}
    problems = []
    reported = set()

    def visit(node, path):
        state[node] = 'open'
        path.append(node)
        for dep in graph[node]:
            if state.get(dep) == 'open':
                cycle = path[path.index(dep):]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    first = by_number[cycle[0]]
                    problems.append(_problem(
                        first['line'], first['number'], 'cycle',
                        'dependency cycle: {}'.format(' -> '.join(cycle + [dep]))))
            elif dep not in state:
                visit(dep, path)
        path.pop()
        state[node] = 'closed'

    for task in tasks:
        if task['number'] not in state:
            visit(task['number'], [])
    return problems


def is_done(task):
    return bool(task['steps']) and all(step['done'] for step in task['steps'])


def waves(iterations):
    """{iteration number (str): [[task numbers], …]} in dependency levels (§7)."""
    out = {}
    for iteration in iterations:
        remaining = [task['number'] for task in iteration['tasks']]
        deps = {task['number']: set(dependencies(task)) & set(remaining)
                for task in iteration['tasks']}
        levels = []
        placed = set()
        while remaining:
            level = [n for n in remaining if deps[n] <= placed]
            if not level:
                break  # a cycle; the grammar check has already reported it
            levels.append(level)
            placed.update(level)
            remaining = [n for n in remaining if n not in placed]
        out[str(iteration['number'])] = levels
    return out


def ready_now(iterations):
    """Task numbers claimable now: in the first iteration with a task not done, the
    tasks not done whose dependencies are all done (§7)."""
    for iteration in iterations:
        pending = [task for task in iteration['tasks'] if not is_done(task)]
        if not pending:
            continue
        done = {task['number'] for task in iteration['tasks'] if is_done(task)}
        return [task['number'] for task in pending
                if set(dependencies(task)) <= done]
    return []


def load_sensitive_rules(repo_root='.', override=None):
    """The sensitive-paths policy, resolved the way hooks/sensitive_guard.py does: a
    host `.artel/sensitive-paths.json` replaces the plugin default wholesale."""
    if override:
        path = Path(override)
    else:
        host = Path(repo_root) / '.artel' / 'sensitive-paths.json'
        path = host if host.is_file() else (
            Path(__file__).resolve().parent.parent / 'hooks' / 'sensitive-paths.json')
    return json.loads(path.read_text(encoding='utf-8'))


def files_of(task):
    """[{'path', 'new'}] from the task's `Files:` field."""
    field = task['fields'].get('Files')
    if not field:
        return []
    return [{'path': path.strip(), 'new': bool(new)}
            for path, new in PATH_RE.findall(field['value'])]


def tests_of(task):
    """(paths, none_reason) from the task's `Test:` field."""
    field = task['fields'].get('Test')
    if not field or not field['value']:
        return [], None
    head, reason = _split_reason(field['value'])
    if head.lower() == 'none':
        return [], reason
    return [path.strip() for path, _ in PATH_RE.findall(field['value'])], None


def implements_of(task):
    field = task['fields'].get('Implements')
    if not field or not field['value']:
        return []
    return [t.strip() for t in field['value'].split(',') if REQ_ID_RE.match(t.strip())]


def route_of(task):
    """(route, reason) from the task's `Route:` field; route is `light` or `full`."""
    field = task['fields'].get('Route')
    head, reason = _split_reason(field['value'])
    return head.lower(), reason


def _in_trail(path, trail):
    """True when a `Files:` path lies under the ticket's spec-trail prefix."""
    if path.startswith('./'):
        path = path[2:]
    return path.startswith(trail)


def route_floor(task, rules, threshold=ROUTE_FULL_FILES, trail=None):
    """(floor, reasons): floor is `full` when a static floor applies, else None.

    Floors 1-3 of docs/task-grammar.md §7. Floor 4 (an earlier deviation on the same
    files) is known only at runtime and is applied by the orchestrator.

    `trail` is the ticket's spec trail as a repo-relative prefix ending in `/`. A HITL
    task whose files all lie under it is an evidence record: floor 2 leaves it alone.
    Without a trail, or without files, every HITL task is floored.
    """
    reasons = []
    files = files_of(task)
    for entry in files:
        for category in rules.get('categories') or []:
            if any(fnmatch.fnmatch(entry['path'], glob) for glob in category.get('globs') or []):
                reasons.append('sensitive path ({}): {}'.format(category.get('name'),
                                                                entry['path']))
                break
    record_only = bool(trail) and bool(files) and all(
        _in_trail(entry['path'], trail) for entry in files)
    if task['hitl'] and not record_only:
        reasons.append('HITL tag')
    if len(files) > threshold:
        reasons.append('more than {} files ({})'.format(threshold, len(files)))
    return ('full' if reasons else None), reasons


def structured(task, rules, trail=None):
    """The contract's structured keys for one task (docs/task-grammar.md §7).

    `route_effective` is the higher of the declared route and the floor, except that a
    route set at approval is final: the floor is still reported, for the journal.
    `trail` is passed to route_floor.
    """
    route, reason = route_of(task)
    floor, reasons = route_floor(task, rules, trail=trail)
    tests, none_reason = tests_of(task)
    produces = task['fields'].get('Produces')
    if reason == APPROVAL_REASON:
        effective = route  # the person's choice at the pause stands over floors 1-3
    else:
        effective = 'full' if route == 'full' or floor == 'full' else 'light'
    return {
        'task': task['number'],
        'deps': dependencies(task),
        'files': files_of(task),
        'route': route,
        'route_reason': reason,
        'route_floor': floor,
        'route_reasons': reasons,
        'route_effective': effective,
        'test': tests,
        'test_none_reason': none_reason,
        'produces': produces['value'] if produces else None,
        'implements': implements_of(task),
        'steps': [{'text': step['text'], 'done': step['done']} for step in task['steps']],
    }


def description(task):
    """The row description: the fields as written, HITL, then the steps (no ticks)."""
    lines = ['{}: {}'.format(name, task['fields'][name]['value'])
             for name in KNOWN_FIELDS if name in task['fields']]
    if task['hitl']:
        lines.append('HITL: ' + task['hitl'])
    lines.append('')
    lines.append('Steps:')
    lines.extend('- ' + step['text'] for step in task['steps'])
    return '\n'.join(lines)


def _finding(severity, task, line, rule, message):
    return {'severity': severity, 'task': task, 'line': line, 'rule': rule, 'message': message}


def _placeholder_hits(text):
    bare = BACKTICK_SPAN_RE.sub('', text)
    return [label for pattern, label in PLACEHOLDERS if pattern.search(bare)]


def check(iterations, problems, repo_root, requirement_ids):
    """(findings, coverage): the mechanical plan review (docs/task-grammar.md §6).

    `requirement_ids` is the PRD's active IDs, or None when the PRD has no
    `## Requirements` section (or there is no PRD).
    """
    root = Path(repo_root)
    findings = [_finding('Critical', p['task'], p['line'], p['rule'], p['message'])
                for p in problems]
    new_files = set()
    for iteration in iterations:
        # `(new)` files of this iteration count for its own tests, earlier ones too.
        for task in iteration['tasks']:
            new_files.update(entry['path'] for entry in files_of(task) if entry['new'])
        for task in iteration['tasks']:
            tid = task['number']
            files_field = task['fields'].get('Files')
            for entry in files_of(task):
                # A file a task of this or an earlier iteration creates is there by the
                # time this task runs, exactly as for `Test:` paths below.
                if (not entry['new'] and not (root / entry['path']).exists()
                        and entry['path'] not in new_files):
                    findings.append(_finding(
                        'Important', tid, files_field['line'], 'missing-file',
                        '`{}` does not exist, is not marked `(new)`, and no task of this or an'
                        ' earlier iteration creates it'.format(entry['path'])))
            test_field = task['fields'].get('Test')
            for path in tests_of(task)[0]:
                if not (root / path).exists() and path not in new_files:
                    findings.append(_finding(
                        'Important', tid, test_field['line'], 'missing-test',
                        '`{}` does not exist and no task of this or an earlier iteration'
                        ' creates it'.format(path)))
            places = [('title', task['line'], task['title'])]
            places.extend((name, field['line'], field['value'])
                          for name, field in task['fields'].items())
            places.extend(('step', step['line'], step['text']) for step in task['steps'])
            for where, line, text in places:
                for label in _placeholder_hits(text):
                    findings.append(_finding('Important', tid, line, 'placeholder',
                                             '{} holds a placeholder ({})'.format(where, label)))
    all_tasks = [task for iteration in iterations for task in iteration['tasks']]
    cited = []
    for task in all_tasks:
        for rid in implements_of(task):
            if rid not in cited:
                cited.append(rid)
    known = set(requirement_ids or [])
    unknown = [rid for rid in cited if rid not in known]
    for task in all_tasks:
        for rid in implements_of(task):
            if rid not in known:
                findings.append(_finding(
                    'Important', task['number'], task['fields']['Implements']['line'],
                    'unknown-requirement',
                    '{} is not a requirement of the PRD'.format(rid) if requirement_ids is not None
                    else '{} is cited but the PRD has no `## Requirements`'.format(rid)))
    uncovered = []
    if requirement_ids:
        uncovered = [rid for rid in requirement_ids if rid not in cited]
        for rid in uncovered:
            findings.append(_finding('Critical', None, None, 'uncovered-requirement',
                                     '{} has no task that implements it'.format(rid)))
        with_field = [task for task in all_tasks if 'Implements' in task['fields']]
        if with_field:
            for task in all_tasks:
                if 'Implements' not in task['fields']:
                    findings.append(_finding('Important', task['number'], task['line'],
                                             'missing-implements',
                                             'the PRD has requirements; the task names none'
                                             ' in `Implements:`'))
    return findings, {'uncovered': uncovered, 'unknown': unknown}


def parse_requirements(text):
    """{present, ids, withdrawn, already_met} for a PRD body's `## Requirements`."""
    present = False
    inside = False
    ids, withdrawn, already_met = [], [], []
    for line in text.splitlines():
        if REQUIREMENTS_RE.match(line):
            present, inside = True, True
            continue
        if HEADING_2_RE.match(line):
            inside = False
            continue
        if not inside:
            continue
        match = REQUIREMENT_ENTRY_RE.match(line)
        if not match:
            continue
        rid, rest = match.group(1), match.group(2)
        if rid in ids or rid in withdrawn or rid in already_met:
            continue
        if WITHDRAWN_RE.search(rest):
            withdrawn.append(rid)
        elif ALREADY_MET_RE.search(rest):
            already_met.append(rid)
        else:
            ids.append(rid)
    return {'present': present, 'ids': ids, 'withdrawn': withdrawn, 'already_met': already_met}
