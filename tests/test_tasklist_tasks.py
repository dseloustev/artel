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


if __name__ == '__main__':
    unittest.main()
