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
"""
import re

# kartoteka's tasks.MAX_TITLE_CHARS. A second copy of a constant is acceptable
# here for the same reason knowledge_mirror.py's MAX_BYTES is: this is a guard,
# never the authority. kartoteka enforces the real limit, and the two disagreeing
# costs at worst a rejected row the server would have accepted.
MAX_TITLE_CHARS = 500

ITERATION_RE = re.compile(r'^##\s+Iteration\s+(\d+)\s*:\s*(.+?)\s*$')
HEADING_2_RE = re.compile(r'^##\s+')
SECTION_RE = re.compile(r'^###\s+(.+?)\s*$')
CHECKBOX_RE = re.compile(r'^\s*-\s+\[([ xX])\]\s+(.+?)\s*$')
GOAL_RE = re.compile(r'^\*\*Goal:\*\*\s*(.+?)\s*$')
TEST_RE = re.compile(r'^\*\*Test:\*\*\s*(.+?)\s*$')
HITL_RE = re.compile(r'\[HITL:\s*([^\]]+)\]')
NEW_FILE_RE = re.compile(r'\s*\(new file\)\s*$')


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

    Only checkboxes inside an `## Iteration N:` block and under a `### ` section
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
