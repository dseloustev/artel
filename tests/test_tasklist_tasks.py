import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import task_grammar  # noqa: E402
import tasklist_tasks  # noqa: E402


TASKLIST = '''# Development Tasklist: Wallet adapter (AW-1234)

## Progress Report

| # | Iteration | Status | Notes |
|---|-----------|--------|-------|
| 1 | Scaffold the adapter | ⬜ Pending |  |
| 2 | Wire it in | ⬜ Pending |  |

---

## Iteration 1: Scaffold the adapter

**Goal:** Add the adapter file without wiring it in.

### Task 1.1: Create the adapter class
- **Files:** `lib/wallet/adapter.dart` (new)
- **Depends on:** none
- **Route:** light
- **Test:** none — the module compiles
- **Implements:** R1
- [ ] Create the adapter class
- [x] Add the license header

### Task 1.2: Run the fast verify
- **Files:** `lib/wallet/adapter.dart`
- **Depends on:** 1.1
- **Route:** light
- **Test:** none — no code change
- **Implements:** R1
- [ ] Run `verify.fast` (config.md) — must pass clean

**Test:** The module compiles.

---

## Iteration 2: Wire it in

**Goal:** Call the adapter from the wallet screen.

### Task 2.1: Swap the provider [HITL: touches a sensitive surface]
- **Files:** `lib/wallet/screen.dart`
- **Depends on:** none
- **Route:** light
- **Test:** none — the balance renders from the adapter
- **Implements:** R2
- [ ] Swap the provider

**Test:** The balance renders from the adapter.

---

## Final Verification

- [ ] Run every command in `verify.commands` (config.md), in order
'''


# Two review rounds after generation. Round 2 re-uses round 1's checkbox text on
# purpose: the source heading is what keeps the two rows apart.
FIX_TASKLIST = TASKLIST + '''
## Code Review Fixes

### review-r1
- [x] **Task 1: Guard the null wallet**
  - Return early when the wallet is absent.
  - Acceptance criteria:
    - A null wallet renders the empty state.
- [ ] **Task 2: [HITL: needs a product call] Rename the balance label**
  - Acceptance criteria:
    - The label reads "Available".

### review-r2
- [ ] **Task 1: Guard the null wallet**
  - [ ] Cover the refresh path too
'''


def _sections(text):
    return tasklist_tasks.build_sections(tasklist_tasks.parse_sections(text))


class TestFixSections(unittest.TestCase):
    def setUp(self):
        self.rows, self.warnings = _sections(FIX_TASKLIST)
        self.fv, self.crf = self.rows

    def test_one_parent_per_section_in_document_order(self):
        self.assertEqual([r['title'] for r in self.rows],
                         ['FV: Final Verification', 'CRF: Code Review Fixes'])

    def test_parents_are_backlog_labels(self):
        self.assertEqual([r['status'] for r in self.rows], ['backlog', 'backlog'])

    def test_source_heading_is_the_middle_segment_and_the_text_is_kept_verbatim(self):
        # `/artel:tasks done` flips the box whose text is everything after the
        # title's second ` · ` -- bold markers included.
        self.assertEqual([c['title'] for c in self.crf['children']], [
            'CRF · review-r1 · **Task 1: Guard the null wallet**',
            'CRF · review-r1 · **Task 2: [HITL: needs a product call] Rename the balance label**',
            'CRF · review-r2 · **Task 1: Guard the null wallet**',
        ])

    def test_a_box_directly_under_the_heading_takes_the_default_source(self):
        self.assertEqual(
            self.fv['children'][0]['title'],
            'FV · tasklist · Run every command in `verify.commands` (config.md), in order')

    def test_nested_lines_go_to_the_description_never_the_title(self):
        self.assertEqual(self.crf['children'][0]['description'],
                         'Source: review-r1\n\n'
                         '- Return early when the wallet is absent.\n'
                         '- Acceptance criteria:\n'
                         '  - A null wallet renders the empty state.')

    def test_a_nested_checkbox_is_part_of_its_task_not_a_task(self):
        self.assertEqual(len(self.crf['children']), 3)
        self.assertEqual(self.crf['children'][2]['description'],
                         'Source: review-r2\n\n- [ ] Cover the refresh path too')

    def test_checked_is_done_and_everything_else_is_backlog_never_ready(self):
        self.assertEqual([c['status'] for c in self.crf['children']],
                         ['done', 'backlog', 'backlog'])
        self.assertEqual(self.fv['children'][0]['status'], 'backlog')

    def test_hitl_is_extracted_and_the_row_stays_backlog(self):
        child = self.crf['children'][1]
        self.assertEqual(child['hitl'], 'needs a product call')
        self.assertEqual(child['status'], 'backlog')
        self.assertEqual(child['description'].splitlines()[:2],
                         ['Source: review-r1', 'HITL: needs a product call'])

    def test_two_rounds_with_the_same_text_are_two_rows(self):
        # The regression this change exists for: without the source, round 2's
        # open box shared round 1's `done` title and came back `done`.
        first, second = self.crf['children'][0], self.crf['children'][2]
        self.assertNotEqual(first['title'], second['title'])
        self.assertEqual((first['status'], second['status']), ('done', 'backlog'))
        self.assertEqual(self.warnings, [])

    def test_a_paragraph_ends_a_task_s_body(self):
        rows, _ = _sections('## Final Verification\n\nRun after everything.\n\n'
                            '- [ ] Run the gate\n\n**Gate:** do not merge red.\n')
        self.assertEqual(rows[0]['children'][0]['description'], 'Source: tasklist')


class TestFixSectionEdges(unittest.TestCase):
    def test_no_iterations_still_means_backlog_never_ready(self):
        # deep-review creates a tasklist holding nothing but its fixes. The
        # iteration rule "first iteration is ready" must not leak into them.
        rows, _ = _sections('# Tasklist — AW-1234\n\n## Code Review Fixes\n\n'
                            '### deep-review-2026-09-18\n- [ ] **Task 1: Split the adapter**\n')
        self.assertEqual(rows[0]['children'][0]['status'], 'backlog')

    def test_a_repeated_title_warns_and_the_later_box_gets_no_row(self):
        rows, warnings = _sections('## Code Review Fixes\n\n- [x] **Task 1: Same**\n'
                                   '- [ ] **Task 1: Same**\n')
        self.assertEqual([c['status'] for c in rows[0]['children']], ['done'])
        self.assertEqual(len(warnings), 1)
        self.assertIn('CRF · tasklist · **Task 1: Same**', warnings[0])
        self.assertIn('new `### <source>` heading', warnings[0])

    def test_a_heading_that_appears_twice_is_one_section(self):
        rows, _ = _sections('## Code Review Fixes\n\n### review-r1\n- [ ] A\n\n'
                            '## Runtime Fixes\n\n### runtime-r1\n- [ ] B\n\n'
                            '## Code Review Fixes\n\n### review-r2\n- [ ] C\n')
        self.assertEqual([r['title'] for r in rows],
                         ['CRF: Code Review Fixes', 'RTF: Runtime Fixes'])
        self.assertEqual([c['title'] for c in rows[0]['children']],
                         ['CRF · review-r1 · A', 'CRF · review-r2 · C'])

    def test_every_section_has_its_code(self):
        text = ''.join('## {}\n\n- [ ] t\n\n'.format(h) for h in (
            'Code Review Fixes', 'Runtime Fixes', 'Verify Fixes', 'Final Verification'))
        rows, _ = _sections(text)
        self.assertEqual([r['title'] for r in rows], [
            'CRF: Code Review Fixes', 'RTF: Runtime Fixes',
            'VF: Verify Fixes', 'FV: Final Verification'])

    def test_a_level_four_heading_is_not_a_source(self):
        # Where a reviewer puts its priority groupings (docs/task-queue.md §6):
        # `####` must leave the batch's `###` source in place.
        rows, _ = _sections('## Code Review Fixes\n\n### review-r1\n#### Blocking\n'
                            '- [ ] **Task 1: A**\n\n#### Important\n- [ ] **Task 2: B**\n')
        self.assertEqual([c['title'] for c in rows[0]['children']],
                         ['CRF · review-r1 · **Task 1: A**', 'CRF · review-r1 · **Task 2: B**'])

    def test_a_section_with_no_task_emits_no_parent(self):
        rows, _ = _sections('## Code Review Fixes\n\nNothing yet.\n')
        self.assertEqual(rows, [])

    def test_a_phase_file_yields_its_fix_sections_and_no_iteration(self):
        # sync-phases writes `# Phase N:` and `## Tasks`, so the extract holds
        # no iteration; its fix sections live nowhere else.
        text = ('# Phase 2: Wire it in\n\n## Tasks\n\n- [ ] 2.1 Swap the provider\n\n'
                '## Code Review Fixes\n\n### review-p2-r1\n- [ ] **Task 1: X**\n')
        rows, _ = _sections(text)
        self.assertEqual([c['title'] for c in rows[0]['children']],
                         ['CRF · review-p2-r1 · **Task 1: X**'])


def _titled(*titles):
    """A task-format body with one iteration holding one task per title."""
    blocks = []
    for index, title in enumerate(titles, 1):
        blocks.append('### Task 1.{}: {}\n'
                      '- **Files:** `lib/a.dart`\n'
                      '- **Depends on:** none\n'
                      '- **Route:** light\n'
                      '- **Test:** none — no code change\n'
                      '- [ ] Do it\n'.format(index, title))
    return '## Iteration 1: Long\n\n**Goal:** g\n\n' + '\n'.join(blocks) + '\n**Test:** t\n'


class TestTitleCap(unittest.TestCase):
    def _rows(self, *titles):
        iterations, problems, _ = task_grammar.parse(_titled(*titles))
        self.assertEqual([], problems)
        rows, _, warnings = tasklist_tasks.build_task_rows(iterations, {'categories': []})
        return rows, warnings

    def test_title_is_capped_and_reported(self):
        rows, warnings = self._rows('A' * 600)
        title = rows[0]['children'][0]['title']
        self.assertEqual(len(title), tasklist_tasks.MAX_TITLE_CHARS)
        self.assertEqual(len(warnings), 1)
        self.assertIn('truncated', warnings[0])

    def test_truncation_is_a_plain_prefix_cut(self):
        # Was: build the same input twice and compare, which no pure function
        # can fail. What has to hold is WHICH characters survive -- the title is
        # the idempotency key, so a hash suffix or a mid-string ellipsis would
        # re-key every long row and mirror it a second time.
        rows, _ = self._rows('A' * 600)
        untruncated = 'I1 · 1.1 · ' + 'A' * 600
        self.assertEqual(untruncated[:tasklist_tasks.MAX_TITLE_CHARS],
                         rows[0]['children'][0]['title'])


class TestFindCollisions(unittest.TestCase):
    """Titles are the store's idempotency key: a repeat silently merges two rows."""

    def test_a_duplicated_title_is_found(self):
        rows = [{'title': 'I1: A', 'children': [{'title': 'I1 · 1.1 · Same'},
                                                {'title': 'I1 · 1.1 · Same'}]}]
        self.assertEqual(tasklist_tasks.find_collisions(rows), ['I1 · 1.1 · Same'])

    def test_titles_differing_only_inside_a_whitespace_run_collide(self):
        # kartoteka normalises whitespace before its UNIQUE check, so this pair
        # passed the guard here and merged in the store -- the exact silent
        # merge the guard exists to prevent.
        rows = [{'title': 'I1: A', 'children': [{'title': 'I1 · 1.1 · Wire  the adapter'},
                                                {'title': 'I1 · 1.1 · Wire the adapter'}]}]
        self.assertEqual(tasklist_tasks.find_collisions(rows),
                         ['I1 · 1.1 · Wire the adapter'])

    def test_a_tab_and_a_space_are_the_same_separator(self):
        rows = [{'title': 'I1: A', 'children': [{'title': 'I1 · 1.1 · Wire\tthe adapter'},
                                                {'title': 'I1 · 1.1 · Wire the adapter'}]}]
        self.assertEqual(len(tasklist_tasks.find_collisions(rows)), 1)

    def test_no_repeat_is_no_collision(self):
        rows = [{'title': 'I1: A', 'children': [{'title': 'I1 · 1.1 · One'}]},
                {'title': 'I2: B', 'children': [{'title': 'I2 · 2.1 · One'}]}]
        self.assertEqual(tasklist_tasks.find_collisions(rows), [])


SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'tasklist_tasks.py'


def run_cli(*args):
    import json
    import subprocess
    proc = subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


class TestCli(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, text):
        path = Path(self.tmp.name) / 'tasklist.md'
        path.write_text(text, encoding='utf-8')
        return str(path)

    def test_ok_envelope_carries_ticket_key_and_rows(self):
        code, out = run_cli('--tasklist', self._write(TASKLIST),
                            '--ticket-key', 'AW-1234')
        self.assertEqual(code, 0)
        self.assertTrue(out['ok'])
        self.assertEqual(out['verb'], 'tasklist-tasks')
        self.assertEqual(out['data']['ticket_key'], 'AW-1234')
        self.assertEqual(out['data']['format'], 'tasks')
        self.assertEqual(len(out['data']['iterations']), 2)
        self.assertEqual(out['data']['warnings'], [])

    def test_fix_sections_leave_the_iterations_array_untouched(self):
        _, plain = run_cli('--tasklist', self._write(TASKLIST), '--ticket-key', 'AW-1234')
        code, out = run_cli('--tasklist', self._write(FIX_TASKLIST), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['iterations'], plain['data']['iterations'])
        self.assertEqual([s['title'] for s in out['data']['sections']],
                         ['FV: Final Verification', 'CRF: Code Review Fixes'])

    def test_a_tasklist_of_fixes_alone_exits_0_with_no_iterations(self):
        text = ('# Tasklist — AW-1234\n\n## Code Review Fixes\n\n'
                '### deep-review-2026-09-18\n- [ ] **Task 1: Split the adapter**\n')
        code, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['iterations'], [])
        self.assertEqual(len(out['data']['sections']), 1)

    def test_a_fix_heading_with_no_task_and_no_iteration_is_still_malformed(self):
        code, out = run_cli('--tasklist', self._write('## Code Review Fixes\n\nNone.\n'),
                            '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')

    def test_final_verification_with_no_iteration_is_malformed(self):
        # Every generated tasklist ends with `## Final Verification`, so a file
        # carrying it and no task block lost its tasks: mirroring its FV row
        # alone would let `/artel:tasks list` read "drained" on an unstarted ticket.
        text = ('# Development Tasklist\n\n## Final Verification\n\n'
                '- [ ] Run every command in `verify.commands` (config.md), in order\n')
        code, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')
        self.assertIn('## Final Verification', out['error']['message'])

    def test_an_old_format_tasklist_is_refused(self):
        # The old format -- an iteration heading and checkbox tasks -- is no
        # longer read: its work would mirror as nothing.
        old = '## Iteration 1: A\n\n### `lib/a.dart`\n- [ ] Do it\n'
        code, out = run_cli('--tasklist', self._write(old), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')
        self.assertIn('## Iteration 1: A', out['error']['message'])
        self.assertIn('old format', out['error']['message'])

    def test_an_iteration_heading_alone_is_refused(self):
        code, out = run_cli('--tasklist',
                            self._write('## Phase 2: Wire it in\n\nNo tasks yet.\n'),
                            '--ticket-key', 'AW-1234')
        self.assertEqual((code, out['error']['kind']), (2, 'tasklist_malformed'))
        self.assertIn('## Phase 2: Wire it in', out['error']['message'])

    def test_an_iteration_heading_that_does_not_parse_is_malformed(self):
        # No colon, so the heading is not a `## Iteration N:` one; the fix task
        # beside it must not turn the file into a fixes-only tasklist that
        # mirrors cleanly.
        text = ('## Iteration 1 - Scaffold\n\n### `lib/a.dart`\n- [ ] Create the adapter\n\n'
                '## Code Review Fixes\n\n### review-r1\n- [ ] **Task 1: Guard it**\n')
        code, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')
        self.assertIn('## Iteration 1 - Scaffold', out['error']['message'])

    def test_a_phase_file_exits_0_with_its_fix_sections(self):
        text = ('# Phase 2: Wire it in\n\n## Tasks\n\n- [ ] 2.1 Swap the provider\n\n'
                '## Code Review Fixes\n\n### review-p2-r1\n- [ ] **Task 1: X**\n')
        code, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['iterations'], [])
        self.assertEqual([s['title'] for s in out['data']['sections']],
                         ['CRF: Code Review Fixes'])

    def test_a_phase_file_given_a_final_verification_fix_still_exits_0(self):
        # `/artel:tasks add <KEY>-<N> … --fix FV` appends `## Final Verification`
        # to the phase file. A `# Phase N:` extract never holds an iteration, so
        # the section is no sign of lost iterations there.
        text = ('# Phase 2: Wire it in\n\n## Tasks\n\n- [ ] 2.1 Swap the provider\n\n'
                '## Final Verification\n\n### manual-p2-2026-09-19\n- [ ] Run the smoke test\n')
        code, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 0)
        self.assertEqual([s['title'] for s in out['data']['sections']],
                         ['FV: Final Verification'])

    def test_check_refuses_an_old_format_tasklist(self):
        # The plan review reads the task grammar; a file without it has nothing
        # to review and is no longer silently skipped.
        old = '## Iteration 1: A\n\n### `lib/a.dart`\n- [ ] Do it\n'
        code, out = run_cli('--tasklist', self._write(old), '--ticket-key', 'AW-1234',
                            '--check', '--requirements', 'absent')
        self.assertEqual((code, out['error']['kind']), (2, 'tasklist_malformed'))
        self.assertIn('old format', out['error']['message'])

    def test_missing_file_exits_2_tasklist_not_found(self):
        code, out = run_cli('--tasklist', '/nonexistent/tasklist.md',
                            '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'tasklist_not_found')

    def test_no_iterations_exits_2_rather_than_mirroring_nothing(self):
        # A tasklist either yields a complete row set or yields nothing. Half a
        # work list read as a whole one is indistinguishable from a short ticket.
        code, out = run_cli('--tasklist', self._write('# Empty\n\nNo iterations.\n'),
                            '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')

    def test_missing_ticket_key_exits_2_invalid_argument(self):
        code, out = run_cli('--tasklist', self._write(TASKLIST))
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_unknown_flag_exits_2_invalid_argument(self):
        code, out = run_cli('--tasklist', self._write(TASKLIST),
                            '--ticket-key', 'AW-1234', '--strict')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_stdin_gives_the_same_envelope_as_the_file(self):
        import subprocess
        from_file = subprocess.run(
            [sys.executable, str(SCRIPT), '--tasklist', self._write(TASKLIST),
             '--ticket-key', 'AW-1234'], capture_output=True, text=True)
        from_stdin = subprocess.run(
            [sys.executable, str(SCRIPT), '--tasklist', '-', '--ticket-key', 'AW-1234'],
            input=TASKLIST, capture_output=True, text=True)
        self.assertEqual(from_stdin.returncode, 0)
        a, b = json.loads(from_file.stdout), json.loads(from_stdin.stdout)
        a.pop('elapsed_ms'), b.pop('elapsed_ms')
        self.assertEqual(a, b)

    def test_stdin_errors_name_stdin(self):
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), '--tasklist', '-', '--ticket-key', 'AW-1234'],
            input='## Iteration 1 - no colon\n- [ ] x\n', capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('<stdin>', json.loads(proc.stdout)['error']['message'])

    EMPTY_INPUT = ('no document on stdin; if it was piped from spec_store.py get, that command '
                   'failed — its exit status and stderr say why (run the pipe with set -o '
                   'pipefail)')

    def _stdin(self, raw):
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), '--tasklist', '-', '--ticket-key', 'AW-1234'],
            input=raw, capture_output=True)
        return proc.returncode, json.loads(proc.stdout.decode('utf-8'))

    def test_empty_stdin_is_refused_not_read_as_a_malformed_tasklist(self):
        # A failed `spec_store.py get` upstream prints nothing; the error must
        # point at the pipe, not at a tasklist nobody wrote.
        for raw in (b'', b' \n\t\r\n'):
            code, out = self._stdin(raw)
            self.assertEqual((code, out['ok']), (2, False), raw)
            self.assertEqual(out['error'], {'kind': 'empty_input', 'message': self.EMPTY_INPUT})

    def test_crlf_stdin_gives_the_same_envelope_as_the_lf_file(self):
        code_file, from_file = run_cli('--tasklist', self._write(FIX_TASKLIST),
                                       '--ticket-key', 'AW-1234')
        code, from_stdin = self._stdin(FIX_TASKLIST.replace('\n', '\r\n').encode('utf-8'))
        self.assertEqual(code, code_file)
        from_file.pop('elapsed_ms'), from_stdin.pop('elapsed_ms')
        self.assertEqual(from_stdin, from_file)

    def test_an_empty_tasklist_file_keeps_todays_behaviour(self):
        code, out = run_cli('--tasklist', self._write(''), '--ticket-key', 'AW-1234')
        self.assertEqual((code, out['error']['kind']), (2, 'tasklist_malformed'))


class TestHeaderIsSkipped(unittest.TestCase):
    def test_a_header_changes_nothing_the_parser_reports(self):
        import subprocess
        headed = ('---\ntype: tasklist\nticket: AW-1234\nversion: 3\n'
                  'status: TASKLIST_READY\n---\n') + TASKLIST
        runs = []
        for text in (TASKLIST, headed):
            proc = subprocess.run([sys.executable, str(SCRIPT), '--tasklist', '-',
                                   '--ticket-key', 'AW-1234'],
                                  input=text, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            runs.append(json.loads(proc.stdout)['data'])
        self.assertEqual(runs[0], runs[1])


class TestTrailPrefix(unittest.TestCase):
    """`<specs.dir>/<ticket_key>/` — what route floor 2 treats as the ticket's spec trail."""

    def repo(self, config=None):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if config is not None:
            path = Path(tmp.name) / '.artel' / 'config.json'
            path.parent.mkdir()
            path.write_text(config, encoding='utf-8')
        return tmp.name

    def test_the_default_when_there_is_no_config(self):
        self.assertEqual(tasklist_tasks.trail_prefix(self.repo(), 'AW-12'),
                         'specs/.current/AW-12/')

    def test_specs_dir_from_the_config(self):
        for value in ('docs/specs', 'docs/specs/', './docs/specs'):
            repo = self.repo(json.dumps({'specs': {'dir': value}}))
            self.assertEqual(tasklist_tasks.trail_prefix(repo, 'AW-12'), 'docs/specs/AW-12/',
                             value)

    def test_an_unreadable_or_incomplete_config_falls_back_to_the_default(self):
        for config in ('not json', '[]', '{}', '{"specs": null}', '{"specs": {"dir": 7}}',
                       '{"specs": {"dir": "  "}}'):
            self.assertEqual(tasklist_tasks.trail_prefix(self.repo(config), 'AW-12'),
                             'specs/.current/AW-12/', config)


if __name__ == '__main__':
    unittest.main()
