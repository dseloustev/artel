import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import task_grammar  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'tasklist_tasks.py'

TASKS = '''# Development Tasklist: Ramps success dialog (AW-9)

## Progress Report

| # | Iteration | Status | Notes |
|---|-----------|--------|-------|
| 1 | Scaffold the dialog | ⬜ Pending |  |
| 2 | Wire the dialog | ⬜ Pending |  |

---

## Iteration 1: Scaffold the dialog

**Goal:** the dialog exists, unwired

### Task 1.1: Add the success dialog widget
- **Files:** `lib/ramps/success_dialog.dart` (new), `test/ramps/success_dialog_test.dart` (new)
- **Depends on:** none
- **Route:** light
- **Test:** `test/ramps/success_dialog_test.dart`
- **Produces:** `SuccessDialog.show(context, amount)` — static method
- **Implements:** R1
- [x] Create the widget
- [ ] Add the widget test

### Task 1.2: Add the purchase-succeeded event
- **Files:** `lib/ramps/ramps_event.dart`
- **Depends on:** none
- **Route:** light
- **Test:** none — an event class with no behaviour
- **Implements:** R1
- [ ] Add `PurchaseSucceeded`

**Test:** the module compiles

---

## Iteration 2: Wire the dialog

**Goal:** the dialog shows after a purchase

### Task 2.1: Emit the event from the bloc
- **Files:** `lib/ramps/ramps_bloc.dart`
- **Depends on:** none
- **Route:** full — money-movement path
- **Test:** `test/ramps/ramps_bloc_test.dart`
- **Implements:** R1, R2
- [ ] Emit `PurchaseSucceeded` on a settled order

### Task 2.2: Show the dialog on the event [HITL: copy needs product sign-off]
- **Files:** `lib/ramps/ramps_screen.dart`
- **Depends on:** 2.1
- **Route:** light
- **Test:** `test/ramps/success_dialog_test.dart`
- **Implements:** R1
- [ ] Listen for `PurchaseSucceeded`
- [ ] Call `SuccessDialog.show`

### Task 2.3: Keep the declined path unchanged
- **Files:** `lib/ramps/ramps_screen.dart`
- **Depends on:** 2.1
- **Route:** light
- **Test:** `test/ramps/ramps_screen_test.dart`
- **Implements:** R2
- [ ] Assert the error dialog still shows on a declined card

**Test:** buy with the test card; the dialog shows the amount

## Code Review Fixes

### review-r1
- [ ] **Task 1 (Important): Name the amount formatter**
'''

REPO_FILES = ('lib/ramps/ramps_event.dart', 'lib/ramps/ramps_bloc.dart',
              'lib/ramps/ramps_screen.dart', 'test/ramps/ramps_bloc_test.dart',
              'test/ramps/ramps_screen_test.dart')

DEFAULT_RULES = {'categories': [
    {'name': 'secrets', 'floor': 'full-gates', 'globs': ['.env*', '*secret*']},
    {'name': 'ci-cd', 'floor': 'plan-gate', 'globs': ['.github/workflows/*']},
]}


def one_iteration(*task_blocks, number=1):
    """A task-format body with one iteration holding the given task blocks."""
    return '## Iteration {}: Only\n\n**Goal:** g\n\n{}\n**Test:** t\n'.format(
        number, '\n'.join(task_blocks))


def block(number, title='Do it', files='`a.py`', depends='none', route='light',
          test='`test_a.py`', extra='', steps='- [ ] Step one\n'):
    lines = ['### Task {}: {}'.format(number, title)]
    if files is not None:
        lines.append('- **Files:** {}'.format(files))
    if depends is not None:
        lines.append('- **Depends on:** {}'.format(depends))
    if route is not None:
        lines.append('- **Route:** {}'.format(route))
    if test is not None:
        lines.append('- **Test:** {}'.format(test))
    return '\n'.join(lines) + '\n' + extra + steps


def rules_of(problems):
    return [problem['rule'] for problem in problems]


def tick(text, step):
    return text.replace('- [ ] ' + step, '- [x] ' + step)


def make_repo(files=REPO_FILES):
    tmp = tempfile.TemporaryDirectory()
    for rel in files:
        path = Path(tmp.name) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x\n', encoding='utf-8')
    return tmp


def run_cli(*args, stdin=None):
    proc = subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                          capture_output=True, text=True, input=stdin)
    return proc.returncode, json.loads(proc.stdout)


class TestFormatDetection(unittest.TestCase):
    def test_a_task_heading_makes_the_task_format(self):
        self.assertTrue(task_grammar.is_task_format(TASKS))

    def test_a_tasklist_without_task_headings_is_the_old_format(self):
        old = '## Iteration 1: A\n\n### `lib/a.dart`\n- [ ] Do it\n'
        self.assertFalse(task_grammar.is_task_format(old))

    def test_a_task_heading_outside_the_grammar_does_not_count(self):
        self.assertFalse(task_grammar.is_task_format('### Task 4: Update the dialog\n'))


class TestParse(unittest.TestCase):
    def setUp(self):
        self.iterations, self.problems, self.warnings = task_grammar.parse(TASKS)

    def test_the_fixture_is_clean(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.warnings, [])

    def test_iterations_and_tasks_in_document_order(self):
        self.assertEqual([it['number'] for it in self.iterations], [1, 2])
        self.assertEqual([t['number'] for t in self.iterations[1]['tasks']],
                         ['2.1', '2.2', '2.3'])

    def test_goal_and_footer_test_stay_on_the_iteration(self):
        self.assertEqual(self.iterations[0]['goal'], 'the dialog exists, unwired')
        self.assertEqual(self.iterations[0]['test'], 'the module compiles')

    def test_hitl_leaves_the_title_and_is_kept_apart(self):
        task = self.iterations[1]['tasks'][1]
        self.assertEqual(task['title'], 'Show the dialog on the event')
        self.assertEqual(task['hitl'], 'copy needs product sign-off')

    def test_steps_carry_their_tick(self):
        steps = self.iterations[0]['tasks'][0]['steps']
        self.assertEqual([(s['text'], s['done']) for s in steps],
                         [('Create the widget', True), ('Add the widget test', False)])

    def test_the_fix_section_is_not_a_task(self):
        self.assertEqual(sum(len(it['tasks']) for it in self.iterations), 5)

    def test_phase_headings_are_iterations_too(self):
        body = one_iteration(block('3.1'), number=3).replace('## Iteration 3', '## Phase 3')
        iterations, problems, _ = task_grammar.parse(body)
        self.assertEqual(problems, [])
        self.assertEqual(iterations[0]['tasks'][0]['number'], '3.1')

    def test_a_wrapped_field_value_joins_its_continuation(self):
        body = one_iteration(block('1.1', files='`a.py`,\n  `b.py` (new)'))
        iterations, problems, _ = task_grammar.parse(body)
        self.assertEqual(problems, [])
        self.assertEqual(iterations[0]['tasks'][0]['fields']['Files']['value'],
                         '`a.py`, `b.py` (new)')

    def test_an_unknown_field_is_a_warning(self):
        body = one_iteration(block('1.1', extra='- **Owner:** payments team\n'))
        _, problems, warnings = task_grammar.parse(body)
        self.assertEqual(problems, [])
        self.assertIn('unknown field `Owner` ignored', warnings[0])

    def test_a_field_after_the_steps_is_a_warning(self):
        body = one_iteration(block('1.1', steps='- [ ] Step one\n- **Produces:** `X`\n'))
        _, problems, warnings = task_grammar.parse(body)
        self.assertEqual(problems, [])
        self.assertIn('a field after the steps is ignored', warnings[0])

    def test_an_other_level_three_heading_warns_and_its_boxes_are_bare(self):
        body = one_iteration(block('1.1'), '### After changes\n- [ ] Run codegen\n')
        _, problems, warnings = task_grammar.parse(body)
        self.assertIn('is not a task heading', warnings[0])
        self.assertEqual(rules_of(problems), ['bare-checkbox'])

    def test_line_offset_is_added_to_every_problem(self):
        body = one_iteration(block('1.1', route='fast'))
        _, plain, _ = task_grammar.parse(body)
        _, shifted, _ = task_grammar.parse(body, line_offset=9)
        self.assertEqual(shifted[0]['line'], plain[0]['line'] + 9)


class TestGrammarProblems(unittest.TestCase):
    def problems(self, body):
        return task_grammar.parse(body)[1]

    def test_missing_required_fields(self):
        body = one_iteration(block('1.1', files=None, depends=None, route=None, test=None))
        self.assertEqual(rules_of(self.problems(body)), ['missing-field'] * 4)

    def test_empty_field(self):
        self.assertIn('empty-field', rules_of(self.problems(one_iteration(block('1.1', files='')))))

    def test_duplicate_field(self):
        body = one_iteration(block('1.1', extra='- **Route:** full — twice\n'))
        self.assertEqual(rules_of(self.problems(body)), ['duplicate-field'])

    def test_bad_route_and_full_without_a_reason(self):
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', route='fast')))),
                         ['bad-route'])
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', route='full')))),
                         ['route-reason'])

    def test_every_reason_separator_is_read(self):
        for route in ('full — r', 'full – r', 'full -- r', 'full - r'):
            with self.subTest(route):
                self.assertEqual(self.problems(one_iteration(block('1.1', route=route))), [])

    def test_a_hyphenated_word_is_not_a_reason_separator(self):
        self.assertEqual(rules_of(self.problems(one_iteration(
            block('1.1', route='full-money')))), ['bad-route'])

    def test_test_none_needs_a_reason_and_a_bare_word_is_not_a_test(self):
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', test='none')))),
                         ['test-reason'])
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', test='the unit tests')))),
                         ['bad-test'])

    def test_files_needs_a_backticked_path(self):
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', files='a.py')))),
                         ['bad-files'])

    def test_dependency_tokens(self):
        body = one_iteration(block('1.1'), block('1.2', depends='Task 1.1'))
        self.assertEqual(self.problems(body), [])
        body = one_iteration(block('1.1'), block('1.2', depends='1.1 (the bloc)'))
        self.assertEqual(rules_of(self.problems(body)), ['bad-dependency'])

    def test_unknown_self_and_cross_iteration_dependencies(self):
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', depends='1.4')))),
                         ['unknown-dependency'])
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', depends='1.1')))),
                         ['self-dependency'])
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', depends='2.1')))),
                         ['cross-iteration-dependency'])

    def test_a_cycle_is_reported_once(self):
        body = one_iteration(block('1.1', depends='1.3'), block('1.2', depends='1.1'),
                             block('1.3', depends='1.2'))
        problems = self.problems(body)
        self.assertEqual(rules_of(problems), ['cycle'])
        self.assertIn('1.1 -> 1.3 -> 1.2 -> 1.1', problems[0]['message'])

    def test_bad_implements(self):
        self.assertEqual(rules_of(self.problems(one_iteration(
            block('1.1', extra='- **Implements:** R1, FR-2\n')))), ['bad-implements'])

    def test_numbering_gaps_duplicates_and_wrong_iteration(self):
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1'), block('1.3')))),
                         ['numbering'])
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1'), block('1.1', title='B')))),
                         ['numbering'])
        self.assertEqual(rules_of(self.problems(one_iteration(block('2.1')))), ['numbering'])

    def test_no_steps(self):
        self.assertEqual(rules_of(self.problems(one_iteration(block('1.1', steps='')))),
                         ['no-steps'])

    def test_a_bare_checkbox_in_a_task_iteration(self):
        body = one_iteration(block('1.1')).replace('**Goal:** g\n', '**Goal:** g\n- [ ] Loose\n')
        self.assertEqual(rules_of(self.problems(body)), ['bare-checkbox'])

    def test_an_iteration_with_no_task(self):
        body = one_iteration(block('1.1')) + '\n## Iteration 2: Empty\n\n**Goal:** g\n'
        self.assertEqual(rules_of(self.problems(body)), ['no-tasks'])

    def test_every_problem_is_reported_at_once_in_line_order(self):
        body = one_iteration(block('1.1', route='fast'), block('1.2', test='none', steps=''))
        problems = self.problems(body)
        self.assertEqual(rules_of(problems), ['bad-route', 'no-steps', 'test-reason'])
        self.assertEqual([p['line'] for p in problems], sorted(p['line'] for p in problems))
