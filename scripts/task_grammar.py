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
    'no-steps', 'bare-checkbox', 'no-tasks',
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
            task['steps'].append({'text': match.group(2),
                                  'done': match.group(1).lower() == 'x', 'line': lineno})
            last_field = None
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
