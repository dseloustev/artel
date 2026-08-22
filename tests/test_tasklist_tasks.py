import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import tasklist_tasks  # noqa: E402


TASKLIST = '''# Development Tasklist: Wallet adapter (AW-1234)

Based on [vision.md](./vision.md).

---

## Progress Report

| # | Iteration | Status | Notes |
|---|-----------|--------|-------|
| 1 | Scaffold the adapter | ⬜ Pending |  |
| 2 | Wire it in | ⬜ Pending |  |

**Legend:** ⬜ Pending | 🔄 In Progress | ✅ Done | ❌ Blocked

---

## Iteration 1: Scaffold the adapter

**Goal:** Add the adapter file without wiring it in.

### `lib/wallet/adapter.dart` (new file)
- [ ] Create the adapter class
- [x] Add the license header

### After changes
- [ ] Run `verify.fast` (config.md) — must pass clean

**Test:** The module compiles.

---

## Iteration 2: Wire it in

**Goal:** Call the adapter from the wallet screen.

### `lib/wallet/screen.dart`
- [ ] [HITL: touches a sensitive surface] Swap the provider

### After changes
- [ ] Run `verify.fast` (config.md) — must pass clean

**Test:** The balance renders from the adapter.

---

## Final Verification

- [ ] Run every command in `verify.commands` (config.md), in order
'''


class TestParseTasklist(unittest.TestCase):
    def setUp(self):
        self.iterations, self.warnings = tasklist_tasks.parse_tasklist(TASKLIST)

    def test_finds_both_iterations_in_document_order(self):
        self.assertEqual([(i['number'], i['name']) for i in self.iterations],
                         [(1, 'Scaffold the adapter'), (2, 'Wire it in')])

    def test_goal_and_test_captured_per_iteration(self):
        self.assertEqual(self.iterations[0]['goal'],
                         'Add the adapter file without wiring it in.')
        self.assertEqual(self.iterations[0]['test'], 'The module compiles.')

    def test_section_strips_backticks_and_new_file_marker(self):
        child = self.iterations[0]['children'][0]
        self.assertEqual(child['section'], 'lib/wallet/adapter.dart')
        self.assertTrue(child['new_file'])

    def test_checked_box_is_done_unchecked_is_not(self):
        done = [c['done'] for c in self.iterations[0]['children']]
        self.assertEqual(done, [False, True, False])

    def test_hitl_reason_extracted_and_text_kept_whole(self):
        child = self.iterations[1]['children'][0]
        self.assertEqual(child['hitl'], 'touches a sensitive surface')
        self.assertEqual(child['text'],
                         '[HITL: touches a sensitive surface] Swap the provider')

    def test_progress_report_table_produces_no_children(self):
        # The table rows are not checkboxes, and `## Progress Report` closes any
        # open iteration. Pinned because a looser checkbox regex would eat them.
        self.assertEqual(len(self.iterations), 2)

    def test_final_verification_checkboxes_are_not_iteration_children(self):
        # `## Final Verification` is a `##` heading, so it closes iteration 2.
        # Its checkbox is the end-of-feature gate, not claimable work.
        texts = [c['text'] for c in self.iterations[1]['children']]
        self.assertNotIn('Run every command in `verify.commands` (config.md), in order',
                         texts)

    def test_after_changes_is_a_section_like_any_other(self):
        self.assertEqual(self.iterations[0]['children'][2]['section'], 'After changes')

    def test_checkbox_before_any_section_is_skipped_with_a_warning(self):
        iterations, warnings = tasklist_tasks.parse_tasklist(
            '## Iteration 1: Loose\n- [ ] Ungrouped task\n')
        self.assertEqual(iterations[0]['children'], [])
        self.assertEqual(warnings,
                         ['iteration 1: checkbox outside any `###` section skipped:'
                          ' Ungrouped task'])


class TestPhaseDialect(unittest.TestCase):
    """`## Phase N:` and `## Iteration N:` are the same heading.

    sync-phases already reads both (`## Phase N: Title` or `## Iteration N: Title`)
    and task-planner mandates no template, so a tasklist written with the other
    keyword parsed as zero iterations and exited 2 -- mirroring nothing while the
    run carried on believing the queue held its work list.
    """

    def _rows(self, keyword):
        text = TASKLIST.replace('## Iteration ', '## {} '.format(keyword))
        iterations, warnings = tasklist_tasks.parse_tasklist(text)
        rows, row_warnings = tasklist_tasks.build_rows(iterations)
        return rows, warnings + row_warnings

    def test_phase_headings_parse_identically_to_iteration_headings(self):
        self.assertEqual(self._rows('Phase'), self._rows('Iteration'))

    def test_the_title_prefix_stays_i_n_whatever_the_input_dialect(self):
        # The prefix is the idempotency key. Following the input keyword would
        # mirror one tasklist as two disjoint row sets after a reworded heading.
        rows, _ = self._rows('Phase')
        self.assertEqual(rows[0]['title'], 'I1: Scaffold the adapter')
        self.assertEqual(rows[0]['children'][0]['title'],
                         'I1 · lib/wallet/adapter.dart · Create the adapter class')


class TestBuildRows(unittest.TestCase):
    def setUp(self):
        iterations, _ = tasklist_tasks.parse_tasklist(TASKLIST)
        self.rows, self.warnings = tasklist_tasks.build_rows(iterations)

    def test_parent_title_carries_the_iteration_number(self):
        self.assertEqual(self.rows[0]['title'], 'I1: Scaffold the adapter')

    def test_parent_rows_are_never_ready(self):
        # claim_ready_task filters on status alone and would hand an iteration
        # row to an agent as if it were work. Nothing in kartoteka enforces this.
        self.assertEqual([r['status'] for r in self.rows], ['backlog', 'backlog'])

    def test_parent_description_carries_goal_and_test(self):
        self.assertEqual(self.rows[0]['description'],
                         'Goal: Add the adapter file without wiring it in.\n\n'
                         'Test: The module compiles.')

    def test_child_title_is_iteration_section_text(self):
        self.assertEqual(self.rows[0]['children'][0]['title'],
                         'I1 · lib/wallet/adapter.dart · Create the adapter class')

    def test_after_changes_block_does_not_collide_across_iterations(self):
        # The template repeats this line verbatim in every iteration. Mirrored
        # flat it would resolve to iteration 1's row and create_task would
        # return it unchanged -- a silent merge of distinct work.
        first = self.rows[0]['children'][2]['title']
        second = self.rows[1]['children'][1]['title']
        self.assertNotEqual(first, second)
        self.assertEqual(first,
                         'I1 · After changes · Run `verify.fast` (config.md) — must pass clean')
        self.assertEqual(second,
                         'I2 · After changes · Run `verify.fast` (config.md) — must pass clean')
        self.assertEqual(tasklist_tasks.find_collisions(self.rows), [])

    def test_new_file_marker_lands_in_description_not_title(self):
        child = self.rows[0]['children'][0]
        self.assertNotIn('(new file)', child['title'])
        self.assertEqual(child['description'],
                         'Section: lib/wallet/adapter.dart (new file)')

    def test_first_iteration_children_are_ready_later_are_backlog(self):
        self.assertEqual([c['status'] for c in self.rows[0]['children']],
                         ['ready', 'done', 'ready'])
        self.assertEqual(self.rows[1]['children'][1]['status'], 'backlog')

    def test_hitl_in_a_later_iteration_is_backlog_not_ready(self):
        # A HITL tag never changes the iteration gate. It is never mirrored
        # `blocked` either -- claiming one is what triggers the pause.
        child = self.rows[1]['children'][0]
        self.assertEqual(child['status'], 'backlog')
        self.assertEqual(child['hitl'], 'touches a sensitive surface')
        self.assertIn('[HITL: touches a sensitive surface]', child['title'])
        self.assertIn('HITL: touches a sensitive surface', child['description'])


class TestTitleCap(unittest.TestCase):
    def _long_tasklist(self, first, second):
        return ('## Iteration 1: Long\n\n### `lib/a.dart`\n'
                '- [ ] {}\n- [ ] {}\n'.format(first, second))

    def test_title_is_capped_and_reported(self):
        iterations, _ = tasklist_tasks.parse_tasklist(
            self._long_tasklist('A' * 600, 'B'))
        rows, warnings = tasklist_tasks.build_rows(iterations)
        title = rows[0]['children'][0]['title']
        self.assertEqual(len(title), tasklist_tasks.MAX_TITLE_CHARS)
        self.assertEqual(len(warnings), 1)
        self.assertIn('truncated', warnings[0])

    def test_truncation_is_deterministic_across_runs(self):
        text = self._long_tasklist('A' * 600, 'B')
        first, _ = tasklist_tasks.build_rows(tasklist_tasks.parse_tasklist(text)[0])
        second, _ = tasklist_tasks.build_rows(tasklist_tasks.parse_tasklist(text)[0])
        self.assertEqual(first[0]['children'][0]['title'],
                         second[0]['children'][0]['title'])

    def test_two_titles_colliding_after_truncation_are_found(self):
        iterations, _ = tasklist_tasks.parse_tasklist(
            self._long_tasklist('A' * 600 + ' one', 'A' * 600 + ' two'))
        rows, _ = tasklist_tasks.build_rows(iterations)
        collisions = tasklist_tasks.find_collisions(rows)
        self.assertEqual(len(collisions), 1)
        self.assertTrue(collisions[0].startswith('I1 · lib/a.dart · AAA'))

    def test_duplicate_checkbox_text_in_one_section_is_a_collision(self):
        iterations, _ = tasklist_tasks.parse_tasklist(
            self._long_tasklist('Same task', 'Same task'))
        rows, _ = tasklist_tasks.build_rows(iterations)
        self.assertEqual(tasklist_tasks.find_collisions(rows),
                         ['I1 · lib/a.dart · Same task'])


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
        self.assertEqual(len(out['data']['iterations']), 2)
        self.assertEqual(out['data']['warnings'], [])

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

    def test_collision_exits_2_and_names_the_titles(self):
        text = ('## Iteration 1: Dupes\n\n### `lib/a.dart`\n'
                '- [ ] Same task\n- [ ] Same task\n')
        code, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'title_collision')
        self.assertIn('I1 · lib/a.dart · Same task', out['error']['message'])

    def test_missing_ticket_key_exits_2_invalid_argument(self):
        code, out = run_cli('--tasklist', self._write(TASKLIST))
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_unknown_flag_exits_2_invalid_argument(self):
        code, out = run_cli('--tasklist', self._write(TASKLIST),
                            '--ticket-key', 'AW-1234', '--strict')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')


if __name__ == '__main__':
    unittest.main()
