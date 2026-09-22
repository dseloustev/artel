#!/usr/bin/env python3
"""tasklist-tasks: parse a tasklist.md into the kartoteka task rows that mirror it.

Emits JSON and contacts nothing. The orchestrator that runs this script makes the
`task_create` MCP calls from its output, because artel reaches kartoteka through
MCP tools and a hook subprocess has no access to the session's MCP clients.

Titles are the idempotency key: kartoteka's `tasks` table is
UNIQUE(ticket_key, title) and `create_task` returns the existing row rather than
inserting a second one. Composing titles here, deterministically, rather than
letting an agent re-derive them from the markdown is what makes a re-run match
instead of duplicating the work list.

Exit codes: 0 parsed (JSON on stdout), 2 error (error envelope, nothing to mirror).
Contract: docs/superpowers/specs/2026-08-22-artel-task-queue-design.md

Usage: tasklist-tasks --tasklist <path|-> --ticket-key <KEY>
"""
import json
import re
import sys
import time
from pathlib import Path

# kartoteka's tasks.MAX_TITLE_CHARS. A second copy of a constant is acceptable
# here for the same reason knowledge_mirror.py's MAX_BYTES is: this is a guard,
# never the authority. kartoteka enforces the real limit, and the two disagreeing
# costs at worst a rejected row the server would have accepted.
MAX_TITLE_CHARS = 500

# Either keyword: `generate-tasklist` writes `## Iteration N:`, `sync-phases` and
# `task-planner` both treat `## Phase N:` as the same heading, and no agent prompt
# mandates one over the other. The emitted title prefix stays `I<N>` for both --
# it is the idempotency key, so letting it follow the input dialect would mirror
# one tasklist as two disjoint sets of rows the day someone reworded a heading.
ITERATION_RE = re.compile(r'^##\s+(?:Iteration|Phase)\s+(\d+)\s*:\s*(.+?)\s*$')
# What an iteration heading looks like before ITERATION_RE decides whether it
# parses. A line matching this and not that is an iteration the file meant to
# have and the parser cannot see (`## Iteration 1 - Scaffold`, no colon).
ITERATION_LIKE_RE = re.compile(r'^##\s+(?:Iteration|Phase)\b')
# The H1 sync-phases gives a phase extract (`# Phase N: Title`). Such a file
# never holds an iteration, so a fix section there is no sign of lost ones.
PHASE_FILE_RE = re.compile(r'^#\s+Phase\s+\d+\s*:')
HEADING_2_RE = re.compile(r'^##\s+')
SECTION_RE = re.compile(r'^###\s+(.+?)\s*$')
CHECKBOX_RE = re.compile(r'^\s*-\s+\[([ xX])\]\s+(.+?)\s*$')
GOAL_RE = re.compile(r'^\*\*Goal:\*\*\s*(.+?)\s*$')
TEST_RE = re.compile(r'^\*\*Test:\*\*\s*(.+?)\s*$')
HITL_RE = re.compile(r'\[HITL:\s*([^\]]+)\]')
NEW_FILE_RE = re.compile(r'\s*\(new file\)\s*$')

# The four sections the queue records but never offers (docs/task-queue.md §6):
# gate remediation and the end-of-feature gate, appended after the iterations
# were mirrored. The code is their title prefix, as `I<N>` is an iteration's.
FIX_SECTIONS = {
    'Code Review Fixes': 'CRF',
    'Runtime Fixes': 'RTF',
    'Verify Fixes': 'VF',
    'Final Verification': 'FV',
}
FIX_HEADING_RE = re.compile(
    r'^##\s+(Code Review Fixes|Runtime Fixes|Verify Fixes|Final Verification)\s*$')
# A fix task starts at column 0. An indented checkbox under it is one of its
# sub-steps, which belongs in its description rather than in a row of its own.
TASK_RE = re.compile(r'^-\s+\[([ xX])\]\s+(.+?)\s*$')
# The source of a fix task with no `### <source>` heading above it in its
# section: Final Verification as tasklist-writer writes it, and every fix task
# written before writers opened their batches with one.
DEFAULT_SOURCE = 'tasklist'


def _section(raw):
    """(name, is_new_file) for a `### …` heading.

    The `(new file)` marker moves to the description rather than staying in the
    name, because the name becomes half of the title and the title is a key: a
    file that stops being new must not silently retitle every task under it.
    """
    new_file = bool(NEW_FILE_RE.search(raw))
    name = NEW_FILE_RE.sub('', raw).strip().strip('`').strip()
    return name, new_file


def parse_tasklist(text):
    """(iterations, warnings) for a tasklist.md, in document order.

    Only checkboxes inside an `## Iteration N:` block (or `## Phase N:`, the same
    heading under the other keyword) and under a `### ` section
    are collected. That exclusion is load-bearing twice: the Progress Report
    table sits under its own `##` heading, and `## Final Verification`'s
    checkboxes are the end-of-feature gate rather than claimable work.
    """
    iterations = []
    warnings = []
    current = None
    section = None
    section_new_file = False
    for line in text.splitlines():
        match = ITERATION_RE.match(line)
        if match:
            current = {'number': int(match.group(1)), 'name': match.group(2),
                       'goal': None, 'test': None, 'children': []}
            iterations.append(current)
            section, section_new_file = None, False
            continue
        if HEADING_2_RE.match(line):
            current, section = None, None  # any other `## …` closes the iteration
            continue
        if current is None:
            continue
        match = SECTION_RE.match(line)
        if match:
            section, section_new_file = _section(match.group(1))
            continue
        match = GOAL_RE.match(line)
        if match:
            current['goal'] = match.group(1)
            continue
        match = TEST_RE.match(line)
        if match:
            current['test'] = match.group(1)
            continue
        match = CHECKBOX_RE.match(line)
        if match:
            if section is None:
                # Skipped rather than filed under a placeholder section: the
                # section is half the title, so inventing one invents a key.
                warnings.append(
                    'iteration {}: checkbox outside any `###` section skipped: {}'.format(
                        current['number'], match.group(2)))
                continue
            hitl = HITL_RE.search(match.group(2))
            current['children'].append({
                'section': section,
                'new_file': section_new_file,
                'text': match.group(2),
                'done': match.group(1).lower() == 'x',
                'hitl': hitl.group(1).strip() if hitl else None,
            })
    return iterations, warnings


def parse_sections(text):
    """The four fix sections, in order of first appearance, with their tasks.

    A pass of its own rather than a branch of parse_tasklist, so the iteration
    parse -- and the `iterations` array every existing consumer reads -- stays
    exactly what it was. A heading that appears twice is one section: its tasks
    share one parent row either way.
    """
    sections = []
    by_code = {}
    current = None
    source = DEFAULT_SOURCE
    task = None
    for line in text.splitlines():
        match = FIX_HEADING_RE.match(line)
        if match:
            code = FIX_SECTIONS[match.group(1)]
            if code not in by_code:
                by_code[code] = {'code': code, 'heading': match.group(1), 'tasks': []}
                sections.append(by_code[code])
            current, source, task = by_code[code], DEFAULT_SOURCE, None
            continue
        if HEADING_2_RE.match(line):
            current, task = None, None  # any other `## …` closes the section
            continue
        if current is None:
            continue
        match = SECTION_RE.match(line)
        if match:
            source, task = match.group(1).strip('`').strip(), None
            continue
        match = TASK_RE.match(line)
        if match:
            hitl = HITL_RE.search(match.group(2))
            task = {'source': source, 'text': match.group(2),
                    'done': match.group(1).lower() == 'x',
                    'hitl': hitl.group(1).strip() if hitl else None,
                    'detail': []}
            current['tasks'].append(task)
            continue
        if task is not None and line[:1] in (' ', '\t'):
            task['detail'].append(line)
        elif line.strip():
            task = None  # a paragraph or a `**Gate:**` line is not the task's body
    return sections


def _detail(lines):
    """A fix task's nested lines, dedented to their shallowest indent."""
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.strip())
    return '\n'.join(line[indent:].rstrip() for line in lines).strip('\n')


def _capped(title):
    """(title, was_truncated). Truncation is a plain prefix cut so it is stable
    across runs -- an unstable one would change the key and duplicate the row."""
    if len(title) <= MAX_TITLE_CHARS:
        return title, False
    return title[:MAX_TITLE_CHARS], True


def build_rows(iterations):
    """(rows, warnings) — the mirror payload, in document order.

    Document order is load-bearing downstream: kartoteka's task_ready claims
    `ORDER BY task_id LIMIT 1` and the table has no priority column, so
    insertion order IS queue order. Mirroring in document order is what gives
    the queue the tasklist's dependency order for free.
    """
    rows = []
    warnings = []
    for index, iteration in enumerate(iterations):
        number = iteration['number']
        parent_title, truncated = _capped('I{}: {}'.format(number, iteration['name']))
        if truncated:
            warnings.append('iteration {} title truncated to {} chars'.format(
                number, MAX_TITLE_CHARS))
        parts = []
        if iteration['goal']:
            parts.append('Goal: ' + iteration['goal'])
        if iteration['test']:
            parts.append('Test: ' + iteration['test'])
        children = []
        for child in iteration['children']:
            title, truncated = _capped('I{} · {} · {}'.format(
                number, child['section'], child['text']))
            if truncated:
                warnings.append('task title truncated to {} chars: {}'.format(
                    MAX_TITLE_CHARS, title))
            section = child['section'] + (' (new file)' if child['new_file'] else '')
            description = ['Section: ' + section]
            if child['hitl']:
                description.append('HITL: ' + child['hitl'])
            if child['done']:
                status = 'done'
            elif index == 0:
                # The FIRST iteration in document order, which is `Iteration 1`
                # under the template's contiguous-from-1 rule. Positional rather
                # than `number == 1` so a hand-trimmed tasklist still mirrors
                # something claimable instead of an all-backlog queue.
                status = 'ready'
            else:
                status = 'backlog'
            children.append({'title': title, 'status': status,
                             'description': '\n'.join(description),
                             'hitl': child['hitl']})
        rows.append({'title': parent_title, 'status': 'backlog',
                     'description': '\n\n'.join(parts), 'children': children})
    return rows, warnings


def _normalized(title):
    """Match the store's own normalisation before comparing.

    kartoteka collapses whitespace runs and strips the ends before enforcing
    UNIQUE(ticket_key, title), so comparing raw titles here would pass a pair
    the store then silently merges.
    """
    return re.sub(r'\s+', ' ', title).strip()


def find_collisions(rows):
    """Titles appearing more than once, first-seen order.

    Fatal rather than a warning: kartoteka's create_task would return the first
    row for the second title and write no event, so a collision is a silent
    merge of distinct work -- exactly what putting the iteration in the title
    exists to prevent. Catches post-truncation collisions, duplicated checkbox
    text, and pairs that differ only inside a whitespace run.
    """
    counts = {}
    repeated = []
    for row in rows:
        for title in [row['title']] + [child['title'] for child in row['children']]:
            key = _normalized(title)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] == 2:
                repeated.append(title)
    return repeated


def build_sections(sections):
    """(rows, warnings) -- one parent row per fix section, its tasks as children.

    Never `ready`, parents included: kartoteka's task_ready claims the oldest
    ready row for the ticket with no notion of section, so a ready fix row
    would be handed to any queue-path implementer. A checked box is `done`;
    every other task is `backlog`, even in a tasklist with no iterations, and
    moves only by task_update (docs/task-queue.md §3).
    """
    rows = []
    warnings = []
    seen = set()
    for section in sections:
        code = section['code']
        children = []
        for task in section['tasks']:
            title, truncated = _capped('{} · {} · {}'.format(
                code, task['source'], task['text']))
            if truncated:
                warnings.append('task title truncated to {} chars: {}'.format(
                    MAX_TITLE_CHARS, title))
            key = _normalized(title)
            if key in seen:
                # Titles are identity: the store answers a repeat with the first
                # row -- `done`, if an earlier round finished it -- and the open
                # box vanishes from the queue again. A warning, not a failure:
                # one repeated fix must not stop the iteration rows mirroring.
                warnings.append(
                    'fix task repeats a title already in this file and gets no row of'
                    ' its own -- give its batch a new `### <source>` heading: {}'.format(
                        title))
                continue
            seen.add(key)
            description = ['Source: ' + task['source']]
            if task['hitl']:
                description.append('HITL: ' + task['hitl'])
            if any(line.strip() for line in task['detail']):
                description.extend(['', _detail(task['detail'])])
            children.append({'title': title,
                             'status': 'done' if task['done'] else 'backlog',
                             'description': '\n'.join(description),
                             'hitl': task['hitl']})
        if children:
            rows.append({'title': '{}: {}'.format(code, section['heading']),
                         'status': 'backlog',
                         'description': 'Tasks under `## {}`, worked from the tasklist'
                                        ' file. Recorded here; never offered by'
                                        ' task_ready.'.format(section['heading']),
                         'children': children})
    return rows, warnings


def malformed_reason(text, iterations, sections):
    """Why a tasklist with no parsed iteration is malformed, or None when it is not.

    A file of fixes alone -- deep-review's, or a phase extract -- legitimately
    has no iteration. Three shapes do not, and each would otherwise mirror a few
    fix rows and read as a whole work list: `/artel:tasks list` would call an
    unstarted ticket drained.
    """
    if iterations:
        return None
    lines = text.splitlines()
    unparsed = [line.strip() for line in lines
                if ITERATION_LIKE_RE.match(line) and not ITERATION_RE.match(line)]
    if unparsed:
        return ('`{}` looks like an iteration heading but does not parse; write it'
                ' `## Iteration N: <name>` (or `## Phase N: <name>`)'.format(unparsed[0]))
    phase_file = any(PHASE_FILE_RE.match(line) for line in lines)
    if not phase_file and any(section['code'] == 'FV' for section in sections):
        return ('a `## Final Verification` section but no `## Iteration N:` or'
                ' `## Phase N:` section: every generated tasklist ends with Final'
                ' Verification, so its iterations are missing or unparseable')
    if not any(section['tasks'] for section in sections):
        return 'no `## Iteration N:` or `## Phase N:` sections and no fix-section tasks'
    return None


def envelope(ok, elapsed_ms, data=None, error=None):
    out = {'ok': ok, 'verb': 'tasklist-tasks', 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def main(argv):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    def fail(kind, message):
        print(envelope(False, elapsed(), error={'kind': kind, 'message': message}))
        return 2

    tasklist_path = None
    ticket_key = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--tasklist':
            i += 1
            tasklist_path = argv[i] if i < len(argv) else None
        elif arg == '--ticket-key':
            i += 1
            ticket_key = argv[i] if i < len(argv) else None
        else:
            return fail('invalid_argument', 'unknown flag: {}'.format(arg))
        i += 1
    if not tasklist_path:
        return fail('invalid_argument', 'missing required --tasklist <path|->')
    if not ticket_key:
        return fail('invalid_argument', 'missing required --ticket-key <KEY>')

    if tasklist_path == '-':
        # A stored tasklist arrives by pipe from `spec_store.py get` (docs/spec-storage.md
        # §4.2): the document goes script to script, never through a model's context.
        text = sys.stdin.buffer.read().decode('utf-8')
        tasklist_path = '<stdin>'
    else:
        tasklist_file = Path(tasklist_path)
        if not tasklist_file.is_file():
            return fail('tasklist_not_found', 'tasklist not found: {}'.format(tasklist_path))
        text = tasklist_file.read_text(encoding='utf-8')
    iterations, warnings = parse_tasklist(text)
    sections = parse_sections(text)
    reason = malformed_reason(text, iterations, sections)
    if reason:
        return fail('tasklist_malformed', '{}: {}'.format(tasklist_path, reason))
    rows, row_warnings = build_rows(iterations)
    collisions = find_collisions(rows)
    if collisions:
        return fail('title_collision',
                    'titles are the idempotency key and these repeat: {}'.format(
                        '; '.join(collisions)))
    section_rows, section_warnings = build_sections(sections)
    data = {
        'ticket_key': ticket_key,
        'warnings': warnings + row_warnings + section_warnings,
        'iterations': rows,
    }
    if section_rows:
        # Only when there is one, so a tasklist without a fix section prints
        # exactly what 0.14.0 printed.
        data['sections'] = section_rows
    print(envelope(True, elapsed(), data=data))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(json.dumps({'ok': False, 'verb': 'tasklist-tasks', 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
