import json
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

# The `iterations` array 0.14.0 printed for TASKLIST, captured before fix sections
# existed. Existing consumers read it, so no fix-section change may alter a byte.
GOLDEN_ITERATIONS_0_14_0 = (
    r'[{"title": "I1: Scaffold the adapter", "status": "backlog", "description": "Goal: Add '
    r'the adapter file without wiring it in.\n\nTest: The module compiles.", "children": [{"'
    r'title": "I1 \u00b7 lib/wallet/adapter.dart \u00b7 Create the adapter class", "status":'
    r' "ready", "description": "Section: lib/wallet/adapter.dart (new file)", "hitl": null},'
    r' {"title": "I1 \u00b7 lib/wallet/adapter.dart \u00b7 Add the license header", "status"'
    r': "done", "description": "Section: lib/wallet/adapter.dart (new file)", "hitl": null},'
    r' {"title": "I1 \u00b7 After changes \u00b7 Run `verify.fast` (config.md) \u2014 must p'
    r'ass clean", "status": "ready", "description": "Section: After changes", "hitl": null}]'
    r'}, {"title": "I2: Wire it in", "status": "backlog", "description": "Goal: Call the ada'
    r'pter from the wallet screen.\n\nTest: The balance renders from the adapter.", "childre'
    r'n": [{"title": "I2 \u00b7 lib/wallet/screen.dart \u00b7 [HITL: touches a sensitive sur'
    r'face] Swap the provider", "status": "backlog", "description": "Section: lib/wallet/scr'
    r'een.dart\nHITL: touches a sensitive surface", "hitl": "touches a sensitive surface"}, '
    r'{"title": "I2 \u00b7 After changes \u00b7 Run `verify.fast` (config.md) \u2014 must pa'
    r'ss clean", "status": "backlog", "description": "Section: After changes", "hitl": null}'
    r']}]'
)



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
        # Counting iterations duplicated the test above and never looked at a
        # single child, which is where a leaked table row would actually land.
        texts = [c['text'] for it in self.iterations for c in it['children']]
        self.assertEqual(5, len(texts))
        for text in texts:
            self.assertNotIn('|', text, 'table row leaked into a child: ' + text)
            for cell in ('Scaffold the adapter', 'Wire it in', '⬜ Pending', 'Legend'):
                self.assertNotIn(cell, text,
                                 'table cell leaked into a child: ' + text)

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

    def test_truncation_is_a_plain_prefix_cut(self):
        # Was: build the same input twice and compare, which no pure function
        # can fail. What has to hold is WHICH characters survive -- the title is
        # the idempotency key, so a hash suffix or a mid-string ellipsis would
        # re-key every long row and mirror it a second time.
        iterations, _ = tasklist_tasks.parse_tasklist(
            self._long_tasklist('A' * 600, 'B'))
        rows, _ = tasklist_tasks.build_rows(iterations)
        untruncated = 'I1 · lib/a.dart · ' + 'A' * 600
        self.assertEqual(untruncated[:tasklist_tasks.MAX_TITLE_CHARS],
                         rows[0]['children'][0]['title'])

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

    def test_titles_differing_only_inside_a_whitespace_run_collide(self):
        # kartoteka normalises whitespace before its UNIQUE check, so this pair
        # passed the guard here and merged in the store -- the exact silent
        # merge the guard exists to prevent.
        iterations, _ = tasklist_tasks.parse_tasklist(
            self._long_tasklist('Wire  the adapter', 'Wire the adapter'))
        rows, _ = tasklist_tasks.build_rows(iterations)
        self.assertEqual(tasklist_tasks.find_collisions(rows),
                         ['I1 · lib/a.dart · Wire the adapter'])

    def test_a_tab_and_a_space_are_the_same_separator(self):
        iterations, _ = tasklist_tasks.parse_tasklist(
            '## Iteration 1: Tabs\n\n### `lib/a.dart`\n'
            '- [ ] Wire\tthe adapter\n- [ ] Wire the adapter\n')
        rows, _ = tasklist_tasks.build_rows(iterations)
        self.assertEqual(len(tasklist_tasks.find_collisions(rows)), 1)


class TestGateRemediationSectionsAreNotMirrored(unittest.TestCase):
    """The queue holds iteration work and nothing else.

    `implementer` is dispatched for these three sections too, and works them
    from the file -- `agents/implementer.md` Step 1 and docs/task-queue.md §6.
    A parser that emitted rows for them would file gate remediation behind a
    promotion that never comes, because nothing is their parent iteration.
    There was a test for `## Final Verification` and none for these three, and
    that gap is what let the too-broad "queue before file" rule through review.
    """

    SECTIONS = ('## Code Review Fixes', '## Runtime Fixes', '## Verify Fixes')

    def _children(self, extra):
        iterations, _ = tasklist_tasks.parse_tasklist(TASKLIST + extra)
        self.assertEqual(2, len(iterations), 'the two real iterations, and no more')
        return [c['text'] for it in iterations for c in it['children']]

    def test_bare_checkboxes_under_a_fix_heading_are_not_children(self):
        for heading in self.SECTIONS:
            with self.subTest(heading):
                extra = '\n{}\n\n- [ ] **Task 1: fix what the gate found**\n'.format(heading)
                self.assertNotIn('**Task 1: fix what the gate found**',
                                 self._children(extra))

    def test_a_fix_heading_with_a_section_is_not_mirrored_either(self):
        # The `### ` gate is what skips a bare checkbox, so a fix list that
        # happened to group its items by file would otherwise sail through it.
        for heading in self.SECTIONS:
            with self.subTest(heading):
                extra = ('\n{}\n\n### `lib/a.dart`\n'
                         '- [ ] fix what the gate found\n'.format(heading))
                self.assertNotIn('fix what the gate found', self._children(extra))


SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'tasklist_tasks.py'


def run_cli(*args):
    import json
    import subprocess
    proc = subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


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

    def test_a_section_with_no_task_emits_no_parent(self):
        rows, _ = _sections('## Code Review Fixes\n\nNothing yet.\n')
        self.assertEqual(rows, [])

    def test_a_phase_file_yields_its_fix_sections_and_no_iteration(self):
        # sync-phases writes `# Phase N:` and `## Tasks`, so the extract holds
        # no iteration; its fix sections live nowhere else.
        text = ('# Phase 2: Wire it in\n\n## Tasks\n\n- [ ] 2.1 Swap the provider\n\n'
                '## Code Review Fixes\n\n### review-p2-r1\n- [ ] **Task 1: X**\n')
        iterations, _ = tasklist_tasks.parse_tasklist(text)
        rows, _ = _sections(text)
        self.assertEqual(iterations, [])
        self.assertEqual([c['title'] for c in rows[0]['children']],
                         ['CRF · review-p2-r1 · **Task 1: X**'])


class TestFixSectionsAreNotIterationChildren(unittest.TestCase):
    """A fix section's tasks never land in an iteration.

    `## Code Review Fixes`, `## Runtime Fixes` and `## Verify Fixes` are `##`
    headings, so they close the iteration above them, and their tasks are emitted
    in `data.sections` instead (TestFixSections below). Filed as children of the
    last iteration they would be `ready` or `backlog` behind a promotion, and
    task_ready would offer them -- docs/task-queue.md §6.
    """

    SECTIONS = ('## Code Review Fixes', '## Runtime Fixes', '## Verify Fixes')

    def _children(self, extra):
        iterations, _ = tasklist_tasks.parse_tasklist(TASKLIST + extra)
        self.assertEqual(2, len(iterations), 'the two real iterations, and no more')
        return [c['text'] for it in iterations for c in it['children']]

    def test_bare_checkboxes_under_a_fix_heading_are_not_children(self):
        for heading in self.SECTIONS:
            with self.subTest(heading):
                extra = '\n{}\n\n- [ ] **Task 1: fix what the gate found**\n'.format(heading)
                self.assertNotIn('**Task 1: fix what the gate found**',
                                 self._children(extra))

    def test_a_fix_heading_with_a_section_is_not_mirrored_either(self):
        # The `### ` under a fix heading is its source, not an iteration section.
        for heading in self.SECTIONS:
            with self.subTest(heading):
                extra = ('\n{}\n\n### `lib/a.dart`\n'
                         '- [ ] fix what the gate found\n'.format(heading))
                self.assertNotIn('fix what the gate found', self._children(extra))

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


    def test_a_tasklist_without_a_fix_section_prints_exactly_what_0_14_0_did(self):
        text = TASKLIST.split('\n---\n\n## Final Verification')[0] + '\n'
        _, out = run_cli('--tasklist', self._write(text), '--ticket-key', 'AW-1234')
        self.assertEqual(json.dumps(out['data']),
                         '{"ticket_key": "AW-1234", "warnings": [], "iterations": '
                         + GOLDEN_ITERATIONS_0_14_0 + '}')

    def test_fix_sections_leave_the_iterations_array_untouched(self):
        code, out = run_cli('--tasklist', self._write(FIX_TASKLIST), '--ticket-key', 'AW-1234')
        self.assertEqual(code, 0)
        self.assertEqual(json.dumps(out['data']['iterations']), GOLDEN_ITERATIONS_0_14_0)
        self.assertEqual(list(out['data']), ['ticket_key', 'warnings', 'iterations', 'sections'])
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
