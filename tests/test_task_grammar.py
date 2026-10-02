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

    def test_a_tasklist_without_task_headings_is_not_task_format(self):
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

    def test_an_indented_checkbox_under_a_task_is_a_problem(self):
        # Silently dropping it would read the task as done while the document shows an open
        # box (whole-branch review, 0.23.0).
        body = one_iteration(block('1.1', steps='- [x] Step one\n  - [ ] nested sub-step\n'))
        problems = self.problems(body)
        self.assertEqual(rules_of(problems), ['bare-checkbox'])
        self.assertIn('indented checkbox', problems[0]['message'])

    def test_an_indented_plain_line_is_still_step_detail(self):
        body = one_iteration(block('1.1', steps='- [ ] Step one\n  more detail for step one\n'))
        self.assertEqual(self.problems(body), [])

    def test_a_hitl_tag_on_a_step_is_a_problem(self):
        # The pre-0.23.0 place for the tag: the floor and the pause read only the heading.
        body = one_iteration(block('1.1', steps='- [ ] [HITL: release owner decides] Rotate it\n'))
        problems = self.problems(body)
        self.assertEqual(rules_of(problems), ['hitl-on-step'])
        self.assertIn('move it to the `### Task 1.1:` heading', problems[0]['message'])

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


class TestWavesAndReadiness(unittest.TestCase):
    def test_waves_are_dependency_levels_per_iteration(self):
        iterations = task_grammar.parse(TASKS)[0]
        self.assertEqual(task_grammar.waves(iterations),
                         {'1': [['1.1', '1.2']], '2': [['2.1'], ['2.2', '2.3']]})

    def test_a_later_numbered_dependency_still_orders_the_wave(self):
        body = one_iteration(block('1.1', depends='1.2'), block('1.2'))
        self.assertEqual(task_grammar.waves(task_grammar.parse(body)[0]),
                         {'1': [['1.2'], ['1.1']]})

    def test_ready_now_is_the_first_unfinished_iteration(self):
        iterations = task_grammar.parse(TASKS)[0]
        self.assertEqual(task_grammar.ready_now(iterations), ['1.1', '1.2'])

    def test_finishing_an_iteration_opens_the_next_ones_first_wave(self):
        text = tick(tick(TASKS, 'Add the widget test'), 'Add `PurchaseSucceeded`')
        self.assertEqual(task_grammar.ready_now(task_grammar.parse(text)[0]), ['2.1'])

    def test_finishing_a_dependency_promotes_its_dependents(self):
        text = tick(tick(TASKS, 'Add the widget test'), 'Add `PurchaseSucceeded`')
        text = tick(text, 'Emit `PurchaseSucceeded` on a settled order')
        self.assertEqual(task_grammar.ready_now(task_grammar.parse(text)[0]), ['2.2', '2.3'])

    def test_a_partly_ticked_task_is_not_done(self):
        task = task_grammar.parse(TASKS)[0][0]['tasks'][0]
        self.assertFalse(task_grammar.is_done(task))

    def test_a_phase_extract_has_its_iteration_and_its_ready_tasks(self):
        # sync-phases writes `# Phase N: title` above the iteration's section, copied whole.
        section = '## Iteration 2' + TASKS.split('## Iteration 2', 1)[1]
        body = '# Phase 2: Wire the dialog\n\n' + section.split('## Code Review Fixes')[0]
        iterations, problems, _ = task_grammar.parse(body)
        self.assertEqual(problems, [])
        self.assertEqual([it['number'] for it in iterations], [2])
        self.assertEqual(task_grammar.ready_now(iterations), ['2.1'])

    def test_everything_done_means_nothing_ready(self):
        text = TASKS.replace('- [ ] ', '- [x] ')
        self.assertEqual(task_grammar.ready_now(task_grammar.parse(text)[0]), [])


class TestRouteFloors(unittest.TestCase):
    def floor(self, body, rules=DEFAULT_RULES):
        task = task_grammar.parse(body)[0][0]['tasks'][0]
        return task_grammar.route_floor(task, rules)

    def test_no_floor(self):
        self.assertEqual(self.floor(one_iteration(block('1.1'))), (None, []))

    def test_a_sensitive_path_floors_to_full(self):
        self.assertEqual(self.floor(one_iteration(block('1.1', files='`.github/workflows/ci.yml`'))),
                         ('full', ['sensitive path (ci-cd): .github/workflows/ci.yml']))

    def test_a_hitl_tag_floors_to_full(self):
        body = one_iteration(block('1.1', title='Rotate the key [HITL: release owner decides]'))
        self.assertEqual(self.floor(body), ('full', ['HITL tag']))

    def test_more_than_five_files_floors_to_full(self):
        files = ', '.join('`f{}.py`'.format(n) for n in range(6))
        self.assertEqual(self.floor(one_iteration(block('1.1', files=files))),
                         ('full', ['more than 5 files (6)']))
        five = ', '.join('`f{}.py`'.format(n) for n in range(5))
        self.assertEqual(self.floor(one_iteration(block('1.1', files=five))), (None, []))

    TRAIL = 'specs/.current/AW-9/'

    def hitl_task(self, files):
        body = one_iteration(block('1.1', title='Record the device runs [HITL: needs a phone]',
                                   files=files, test='none — the record is the evidence'))
        return task_grammar.parse(body)[0][0]['tasks'][0]

    def test_a_hitl_task_whose_files_are_all_in_the_trail_keeps_its_route(self):
        task = self.hitl_task('`specs/.current/AW-9/runtime/device-runs.md` (new), '
                              '`./specs/.current/AW-9/runtime/notes.md`')
        self.assertEqual(task_grammar.route_floor(task, DEFAULT_RULES, trail=self.TRAIL),
                         (None, []))
        data = task_grammar.structured(task, DEFAULT_RULES, trail=self.TRAIL)
        self.assertEqual((data['route_floor'], data['route_reasons'], data['route_effective']),
                         (None, [], 'light'))

    def test_one_file_outside_the_trail_keeps_the_hitl_floor(self):
        task = self.hitl_task('`specs/.current/AW-9/runtime/agent-run.md` (new), `.gitignore`')
        self.assertEqual(task_grammar.route_floor(task, DEFAULT_RULES, trail=self.TRAIL),
                         ('full', ['HITL tag']))

    def test_another_ticket_s_trail_is_outside(self):
        task = self.hitl_task('`specs/.current/AW-90/runtime/device-runs.md`')
        self.assertEqual(task_grammar.route_floor(task, DEFAULT_RULES, trail=self.TRAIL),
                         ('full', ['HITL tag']))

    def test_without_a_trail_every_hitl_task_is_floored(self):
        task = self.hitl_task('`specs/.current/AW-9/runtime/device-runs.md`')
        self.assertEqual(task_grammar.route_floor(task, DEFAULT_RULES), ('full', ['HITL tag']))
        self.assertEqual(task_grammar.structured(task, DEFAULT_RULES)['route_effective'], 'full')

    def test_a_hitl_task_with_no_files_keeps_the_floor(self):
        body = one_iteration(block('1.1', title='Decide the copy [HITL: product decides]',
                                   files=None))
        task = task_grammar.parse(body)[0][0]['tasks'][0]
        self.assertEqual(task_grammar.route_floor(task, DEFAULT_RULES, trail=self.TRAIL),
                         ('full', ['HITL tag']))

    def test_the_other_floors_ignore_the_trail(self):
        files = ', '.join('`specs/.current/AW-9/runtime/run-{}.md`'.format(n) for n in range(6))
        self.assertEqual(task_grammar.route_floor(self.hitl_task(files), DEFAULT_RULES,
                                                  trail=self.TRAIL),
                         ('full', ['more than 5 files (6)']))

    def test_the_effective_route_is_the_higher_of_declared_and_floor(self):
        body = one_iteration(block('1.1', files='`.env.local`'))
        task = task_grammar.parse(body)[0][0]['tasks'][0]
        data = task_grammar.structured(task, DEFAULT_RULES)
        self.assertEqual((data['route'], data['route_floor'], data['route_effective']),
                         ('light', 'full', 'full'))

    def test_a_route_set_at_approval_is_final_over_the_floors(self):
        body = one_iteration(block('1.1', files='`.env.local`', route='light — set at approval'))
        task = task_grammar.parse(body)[0][0]['tasks'][0]
        data = task_grammar.structured(task, DEFAULT_RULES)
        self.assertEqual((data['route'], data['route_reason'], data['route_floor'],
                          data['route_effective']),
                         ('light', 'set at approval', 'full', 'light'))
        self.assertEqual(data['route_reasons'], ['sensitive path (secrets): .env.local'])

    def test_any_other_reason_on_light_does_not_beat_a_floor(self):
        body = one_iteration(block('1.1', files='`.env.local`', route='light — trivial'))
        task = task_grammar.parse(body)[0][0]['tasks'][0]
        self.assertEqual(task_grammar.structured(task, DEFAULT_RULES)['route_effective'], 'full')

    def test_a_host_policy_replaces_the_default_wholesale(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / '.artel' / 'sensitive-paths.json'
            host.parent.mkdir()
            host.write_text(json.dumps({'categories': [
                {'name': 'payments', 'floor': 'plan-gate', 'globs': ['lib/ramps/*']}]}))
            rules = task_grammar.load_sensitive_rules(tmp)
        self.assertEqual([c['name'] for c in rules['categories']], ['payments'])

    def test_without_a_host_policy_the_plugin_default_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = task_grammar.load_sensitive_rules(tmp)
        self.assertIn('secrets', [c['name'] for c in rules['categories']])


class TestStructuredAndDescription(unittest.TestCase):
    def setUp(self):
        self.iterations = task_grammar.parse(TASKS)[0]

    def test_structured_keys(self):
        data = task_grammar.structured(self.iterations[0]['tasks'][0], DEFAULT_RULES)
        self.assertEqual(data, {
            'task': '1.1', 'deps': [],
            'files': [{'path': 'lib/ramps/success_dialog.dart', 'new': True},
                      {'path': 'test/ramps/success_dialog_test.dart', 'new': True}],
            'route': 'light', 'route_reason': None, 'route_floor': None, 'route_reasons': [],
            'route_effective': 'light', 'test': ['test/ramps/success_dialog_test.dart'],
            'test_none_reason': None,
            'produces': '`SuccessDialog.show(context, amount)` — static method',
            'implements': ['R1'],
            'steps': [{'text': 'Create the widget', 'done': True},
                      {'text': 'Add the widget test', 'done': False}]})

    def test_test_none_carries_its_reason(self):
        data = task_grammar.structured(self.iterations[0]['tasks'][1], DEFAULT_RULES)
        self.assertEqual((data['test'], data['test_none_reason']),
                         ([], 'an event class with no behaviour'))

    def test_description_lists_fields_hitl_and_untickable_steps(self):
        self.assertEqual(task_grammar.description(self.iterations[1]['tasks'][1]),
                         'Files: `lib/ramps/ramps_screen.dart`\n'
                         'Depends on: 2.1\n'
                         'Route: light\n'
                         'Test: `test/ramps/success_dialog_test.dart`\n'
                         'Implements: R1\n'
                         'HITL: copy needs product sign-off\n'
                         '\n'
                         'Steps:\n'
                         '- Listen for `PurchaseSucceeded`\n'
                         '- Call `SuccessDialog.show`')


class TestNextRoute(unittest.TestCase):
    def route(self, text, deviation_files=(), per_task=False):
        iterations, problems, _ = task_grammar.parse(text)
        self.assertEqual(problems, [])
        return task_grammar.next_route(iterations, DEFAULT_RULES,
                                       deviation_files=deviation_files, per_task=per_task)

    def test_the_constant(self):
        self.assertEqual(task_grammar.MODEL_BY_ROUTE, {'light': 'sonnet', 'full': 'opus'})

    def test_a_light_task_maps_to_sonnet(self):
        out = self.route(one_iteration(block('1.1')))
        self.assertEqual(out['task'], '1.1')
        self.assertEqual(out['title'], 'Task 1.1: Do it')
        self.assertEqual(out['route_declared'], 'light')
        self.assertEqual(out['route_effective'], 'light')
        self.assertEqual(out['route_reasons'], [])
        self.assertEqual(out['model'], 'sonnet')

    def test_a_full_task_maps_to_opus(self):
        out = self.route(one_iteration(block('1.1', route='full — money path')))
        self.assertEqual(out['route_effective'], 'full')
        self.assertEqual(out['model'], 'opus')

    def test_a_static_floor_raises_the_route(self):
        out = self.route(one_iteration(block('1.1', files='`.env`')))
        self.assertEqual(out['route_effective'], 'full')
        self.assertEqual(out['route_reasons'], ['sensitive path (secrets): .env'])
        self.assertEqual(out['model'], 'opus')

    def test_an_earlier_deviation_raises_a_light_task(self):
        out = self.route(one_iteration(block('1.1', files='`a.py`')),
                         deviation_files=['a.py'])
        self.assertEqual(out['route_effective'], 'full')
        self.assertIn('earlier deviation: a.py', out['route_reasons'])
        self.assertEqual(out['model'], 'opus')

    def test_an_earlier_deviation_overrides_a_route_set_at_approval(self):
        out = self.route(one_iteration(block('1.1', route='light — set at approval',
                                             files='`a.py`')),
                         deviation_files=['a.py'])
        self.assertEqual(out['route_effective'], 'full')
        self.assertEqual(out['model'], 'opus')

    def test_review_per_task_raises_every_task(self):
        out = self.route(one_iteration(block('1.1')), per_task=True)
        self.assertEqual(out['route_effective'], 'full')
        self.assertIn('review.perTask', out['route_reasons'])
        self.assertEqual(out['model'], 'opus')

    def test_no_ready_task_returns_none(self):
        text = tick(one_iteration(block('1.1')), 'Step one')
        self.assertIsNone(self.route(text))

    def test_the_title_drops_the_hitl_tag(self):
        out = self.route(one_iteration(block('1.1', title='Ship it [HITL: sign-off]')))
        self.assertEqual(out['title'], 'Task 1.1: Ship it')


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)

    def check(self, text, ids=('R1', 'R2')):
        iterations, problems, _ = task_grammar.parse(text)
        return task_grammar.check(iterations, problems, self.repo.name,
                                  None if ids is None else list(ids))

    def test_the_fixture_passes(self):
        self.assertEqual(self.check(TASKS), ([], {'uncovered': [], 'unknown': []}))

    def test_a_missing_file_is_important(self):
        findings, _ = self.check(TASKS.replace('`lib/ramps/ramps_event.dart`',
                                               '`lib/ramps/ramps_events.dart`'))
        self.assertEqual([(f['severity'], f['rule'], f['task']) for f in findings],
                         [('Important', 'missing-file', '1.2')])

    def test_a_file_an_earlier_task_creates_is_not_missing(self):
        # Editing a file an earlier task creates is ordinary; the AW-3342 replay found a
        # planner merging tasks only to dodge a false missing-file finding.
        text = TASKS.replace('- **Files:** `lib/ramps/ramps_screen.dart`\n- **Depends on:** 2.1\n'
                             '- **Route:** light\n- **Test:** `test/ramps/success_dialog_test.dart`',
                             '- **Files:** `lib/ramps/ramps_screen.dart`, `lib/ramps/success_dialog.dart`\n'
                             '- **Depends on:** 2.1\n- **Route:** light\n'
                             '- **Test:** `test/ramps/success_dialog_test.dart`')
        self.assertIn('`lib/ramps/success_dialog.dart`\n- **Depends on:** 2.1', text)
        self.assertEqual(self.check(text), ([], {'uncovered': [], 'unknown': []}))

    def test_a_file_only_a_later_iteration_creates_is_still_missing(self):
        text = TASKS.replace('- **Files:** `lib/ramps/ramps_event.dart`',
                             '- **Files:** `lib/ramps/ramps_event.dart`, `lib/ramps/later.dart`')
        text = text.replace('- **Files:** `lib/ramps/ramps_bloc.dart`',
                            '- **Files:** `lib/ramps/ramps_bloc.dart`, `lib/ramps/later.dart` (new)')
        findings, _ = self.check(text)
        self.assertEqual([(f['rule'], f['task']) for f in findings], [('missing-file', '1.2')])

    def test_a_test_file_created_by_an_earlier_iteration_is_found(self):
        findings, _ = self.check(TASKS)
        self.assertNotIn('missing-test', [f['rule'] for f in findings])

    def test_a_test_file_nobody_creates_is_important(self):
        findings, _ = self.check(TASKS.replace('`test/ramps/ramps_bloc_test.dart`',
                                               '`test/ramps/bloc_test.dart`'))
        self.assertEqual([f['rule'] for f in findings], ['missing-test'])

    def test_an_uncovered_requirement_is_critical(self):
        findings, coverage = self.check(TASKS, ids=('R1', 'R2', 'R3'))
        self.assertEqual([(f['severity'], f['rule']) for f in findings],
                         [('Critical', 'uncovered-requirement')])
        self.assertEqual(coverage['uncovered'], ['R3'])

    def test_an_unknown_requirement_is_important_per_citing_task(self):
        findings, coverage = self.check(TASKS, ids=('R1',))
        self.assertEqual([(f['rule'], f['task']) for f in findings],
                         [('unknown-requirement', '2.1'), ('unknown-requirement', '2.3')])
        self.assertEqual(coverage['unknown'], ['R2'])

    def test_implements_on_some_tasks_only(self):
        text = TASKS.replace('- **Implements:** R1\n- [ ] Add `PurchaseSucceeded`',
                             '- [ ] Add `PurchaseSucceeded`')
        findings, _ = self.check(text)
        self.assertEqual([(f['rule'], f['task']) for f in findings],
                         [('missing-implements', '1.2')])

    def test_absent_requirements_make_every_cited_id_unknown(self):
        findings, coverage = self.check(TASKS, ids=None)
        self.assertEqual(set(f['rule'] for f in findings), {'unknown-requirement'})
        self.assertIn('has no `## Requirements`', findings[0]['message'])
        self.assertEqual(coverage['uncovered'], [])

    def test_absent_requirements_without_implements_pass(self):
        text = '\n'.join(line for line in TASKS.splitlines()
                         if not line.startswith('- **Implements:**')) + '\n'
        self.assertEqual(self.check(text, ids=None), ([], {'uncovered': [], 'unknown': []}))

    def test_placeholders(self):
        text = TASKS.replace('Assert the error dialog still shows on a declined card',
                             'Handle the other cases, etc.')
        text = text.replace('Add the purchase-succeeded event', 'TBD')
        findings, _ = self.check(text)
        self.assertEqual([(f['rule'], f['task']) for f in findings],
                         [('placeholder', '1.2'), ('placeholder', '2.3')])

    def test_a_template_slot_outside_backticks_is_a_placeholder_and_inside_is_code(self):
        findings, _ = self.check(TASKS.replace('Call `SuccessDialog.show`', 'Call <method>'))
        self.assertEqual([f['rule'] for f in findings], ['placeholder'])
        findings, _ = self.check(TASKS.replace('Call `SuccessDialog.show`',
                                               'Call `show<void>()`'))
        self.assertEqual(findings, [])

    def test_grammar_problems_become_critical_findings(self):
        findings, _ = self.check(TASKS.replace('- **Route:** light\n- **Test:** none',
                                               '- **Route:** fast\n- **Test:** none'))
        self.assertEqual([(f['severity'], f['rule']) for f in findings],
                         [('Critical', 'bad-route')])


class TestRequirements(unittest.TestCase):
    PRD = '''# PRD

## Goal

Buy things.

## Requirements

- **R1** — A completed purchase shows a success dialog.
  *Accepts when:* the test card shows it.
- **R2** — A declined card shows the existing error. (withdrawn — out of scope now)
- **R3** — The amount is formatted. (already met — `AmountFormatter` does it)
- **R4** — The dialog closes on tap.

## Out of Scope

- **R9** — not a requirement, a stray bold in another section
'''

    def test_ids_markers_and_section_scope(self):
        self.assertEqual(task_grammar.parse_requirements(self.PRD), {
            'present': True, 'ids': ['R1', 'R4'], 'withdrawn': ['R2'],
            'already_met': ['R3']})

    def test_a_prd_without_the_section(self):
        self.assertEqual(task_grammar.parse_requirements('# PRD\n\n## Goal\n'), {
            'present': False, 'ids': [], 'withdrawn': [], 'already_met': []})


class TestCli(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)

    def write(self, text, name='tasklist.md'):
        path = Path(self.repo.name) / name
        path.write_text(text, encoding='utf-8')
        return str(path)

    def test_mirror_run_emits_task_rows_waves_and_ready_now(self):
        code, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name)
        self.assertEqual(code, 0)
        data = out['data']
        self.assertEqual(data['format'], 'tasks')
        self.assertEqual([row['title'] for row in data['iterations']],
                         ['I1: Scaffold the dialog', 'I2: Wire the dialog'])
        children = data['iterations'][1]['children']
        self.assertEqual([c['title'] for c in children],
                         ['I2 · 2.1 · Emit the event from the bloc',
                          'I2 · 2.2 · Show the dialog on the event',
                          'I2 · 2.3 · Keep the declined path unchanged'])
        self.assertEqual([c['status'] for c in data['iterations'][0]['children']],
                         ['ready', 'ready'])
        self.assertEqual([c['status'] for c in children], ['backlog'] * 3)
        self.assertEqual(children[1]['route_effective'], 'full')
        self.assertEqual(children[1]['route_reasons'], ['HITL tag'])
        self.assertEqual(data['waves'], {'1': [['1.1', '1.2']], '2': [['2.1'], ['2.2', '2.3']]})
        self.assertEqual(data['ready_now'], [
            {'task': '1.1', 'title': 'I1 · 1.1 · Add the success dialog widget'},
            {'task': '1.2', 'title': 'I1 · 1.2 · Add the purchase-succeeded event'}])
        self.assertEqual(data['sections'][0]['title'], 'CRF: Code Review Fixes')

    def test_a_done_task_mirrors_done(self):
        text = tick(TASKS, 'Add the widget test')
        _, out = run_cli('--tasklist', self.write(text), '--ticket-key', 'AW-9',
                         '--repo', self.repo.name)
        self.assertEqual([c['status'] for c in out['data']['iterations'][0]['children']],
                         ['done', 'ready'])

    def test_a_long_title_is_capped_and_ready_now_uses_the_capped_title(self):
        text = TASKS.replace('Add the success dialog widget', 'W' * 600)
        _, out = run_cli('--tasklist', self.write(text), '--ticket-key', 'AW-9',
                         '--repo', self.repo.name)
        title = out['data']['iterations'][0]['children'][0]['title']
        self.assertEqual(len(title), 500)
        self.assertEqual(out['data']['ready_now'][0]['title'], title)

    def test_grammar_problems_exit_2_with_every_problem(self):
        text = TASKS.replace('- **Route:** full — money-movement path', '- **Route:** full')
        text = text.replace('- **Depends on:** 2.1\n- **Route:** light\n- **Test:** `test/ramps/ramps_screen_test.dart`',
                            '- **Depends on:** 2.9\n- **Route:** light\n- **Test:** `test/ramps/ramps_screen_test.dart`')
        code, out = run_cli('--tasklist', self.write(text), '--ticket-key', 'AW-9')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')
        self.assertEqual([p['rule'] for p in out['error']['data']['problems']],
                         ['route-reason', 'unknown-dependency'])
        self.assertIn('2 problem(s) in the task grammar; first: line', out['error']['message'])

    def test_lines_count_from_the_top_of_a_headed_document(self):
        header = '---\ntype: tasklist\nticket: AW-9\nversion: 3\n---\n'
        text = header + TASKS.replace('- **Route:** full — money-movement path',
                                      '- **Route:** full')
        _, out = run_cli('--tasklist', self.write(text), '--ticket-key', 'AW-9')
        line = out['error']['data']['problems'][0]['line']
        self.assertEqual(text.splitlines()[line - 1], '- **Route:** full')

    def test_crlf_stdin_parses_like_the_file(self):
        _, from_file = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                               '--repo', self.repo.name)
        _, from_stdin = run_cli('--tasklist', '-', '--ticket-key', 'AW-9',
                                '--repo', self.repo.name, stdin=TASKS.replace('\n', '\r\n'))
        self.assertEqual(from_stdin['data'], from_file['data'])

    def test_sensitive_paths_override(self):
        policy = self.write(json.dumps({'categories': [
            {'name': 'payments', 'floor': 'plan-gate', 'globs': ['lib/ramps/ramps_bloc.dart']}]}),
            name='policy.json')
        _, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                         '--repo', self.repo.name, '--sensitive-paths', policy)
        task = out['data']['iterations'][1]['children'][0]
        self.assertEqual(task['route_reasons'],
                         ['sensitive path (payments): lib/ramps/ramps_bloc.dart'])

    # Task 2.2 of the fixture, rewritten as an evidence record under the ticket's trail.
    RECORD = TASKS.replace(
        '- **Files:** `lib/ramps/ramps_screen.dart`\n- **Depends on:** 2.1\n- **Route:** light\n'
        '- **Test:** `test/ramps/success_dialog_test.dart`',
        '- **Files:** `specs/.current/AW-9/runtime/sign-off.md` (new)\n- **Depends on:** 2.1\n'
        '- **Route:** light\n- **Test:** none — the record is the evidence')

    def hitl_row(self, config=None):
        if config is not None:
            path = Path(self.repo.name) / '.artel' / 'config.json'
            path.parent.mkdir(exist_ok=True)
            path.write_text(config, encoding='utf-8')
        code, out = run_cli('--tasklist', self.write(self.RECORD), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name)
        self.assertEqual(code, 0)
        row = out['data']['iterations'][1]['children'][1]
        self.assertEqual(row['hitl'], 'copy needs product sign-off')
        return row['route_effective'], row['route_reasons']

    def test_a_hitl_record_in_the_trail_mirrors_light(self):
        self.assertNotEqual(self.RECORD, TASKS)
        self.assertEqual(self.hitl_row(), ('light', []))

    def test_the_trail_follows_the_host_s_specs_dir(self):
        self.assertEqual(self.hitl_row('{"specs": {"dir": "docs/specs"}}'),
                         ('full', ['HITL tag']))

    def test_a_garbled_config_is_the_default_trail(self):
        self.assertEqual(self.hitl_row('not json'), ('light', []))

    def test_check_passes_on_the_fixture(self):
        code, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--check', '--requirements', 'R1,R2', '--repo', self.repo.name)
        self.assertEqual(code, 0)
        self.assertEqual(out['data'], {'ticket_key': 'AW-9', 'format': 'tasks',
                                       'findings': [],
                                       'coverage': {'uncovered': [], 'unknown': []},
                                       'warnings': []})

    def test_check_exits_1_on_an_important_finding(self):
        text = TASKS.replace('`lib/ramps/ramps_bloc.dart`', '`lib/ramps/bloc.dart`')
        code, out = run_cli('--tasklist', self.write(text), '--ticket-key', 'AW-9',
                            '--check', '--requirements', 'R1,R2', '--repo', self.repo.name)
        self.assertEqual(code, 1)
        self.assertEqual([f['rule'] for f in out['data']['findings']], ['missing-file'])

    def test_check_reports_grammar_problems_as_findings_not_exit_2(self):
        text = TASKS.replace('- **Route:** full — money-movement path', '- **Route:** full')
        code, out = run_cli('--tasklist', self.write(text), '--ticket-key', 'AW-9',
                            '--check', '--requirements', 'R1,R2', '--repo', self.repo.name)
        self.assertEqual(code, 1)
        self.assertEqual(out['data']['findings'][0]['severity'], 'Critical')

    def test_check_requirements_none_and_absent(self):
        code, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--check', '--requirements', 'none', '--repo', self.repo.name)
        self.assertEqual(code, 1)
        self.assertEqual(out['data']['coverage']['unknown'], ['R1', 'R2'])
        code, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--check', '--requirements', 'absent', '--repo', self.repo.name)
        self.assertEqual(code, 1)
        self.assertIn('has no `## Requirements`', out['data']['findings'][0]['message'])

    def test_check_on_an_old_format_tasklist_is_refused(self):
        old = '## Iteration 1: A\n\n### `lib/a.dart`\n- [ ] Do it\n'
        code, out = run_cli('--tasklist', self.write(old), '--ticket-key', 'AW-9',
                            '--check', '--requirements', 'absent')
        self.assertEqual((code, out['error']['kind']), (2, 'tasklist_malformed'))
        self.assertIn('old format', out['error']['message'])

    def test_check_needs_requirements_and_requirements_needs_check(self):
        code, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9', '--check')
        self.assertEqual((code, out['error']['kind']), (2, 'invalid_argument'))
        code, out = run_cli('--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--requirements', 'R1')
        self.assertEqual((code, out['error']['kind']), (2, 'invalid_argument'))

    def test_requirements_verb_from_a_file_and_from_stdin(self):
        prd = TestRequirements.PRD
        code, out = run_cli('requirements', '--prd', self.write(prd, name='prd.md'))
        self.assertEqual(code, 0)
        self.assertEqual(out['verb'], 'tasklist-requirements')
        self.assertEqual(out['data']['ids'], ['R1', 'R4'])
        _, piped = run_cli('requirements', '--prd', '-', stdin=prd)
        self.assertEqual(piped['data'], out['data'])

    def test_requirements_verb_skips_the_header(self):
        prd = '---\ntype: prd\nticket: AW-9\nversion: 2\n---\n' + TestRequirements.PRD
        _, out = run_cli('requirements', '--prd', self.write(prd, name='prd.md'))
        self.assertEqual(out['data']['ids'], ['R1', 'R4'])

    def test_requirements_verb_errors(self):
        code, out = run_cli('requirements', '--prd', str(Path(self.repo.name) / 'nope.md'))
        self.assertEqual((code, out['error']['kind']), (2, 'prd_not_found'))
        code, out = run_cli('requirements', '--prd', '-', stdin='')
        self.assertEqual((code, out['error']['kind']), (2, 'empty_input'))
        code, out = run_cli('requirements')
        self.assertEqual((code, out['error']['kind']), (2, 'invalid_argument'))

    def test_route_next_emits_the_model(self):
        code, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next')
        self.assertEqual(code, 0)
        self.assertEqual(out['verb'], 'tasklist-route')
        data = out['data']
        self.assertEqual(data['ticket_key'], 'AW-9')
        self.assertEqual(data['format'], 'tasks')
        self.assertEqual(data['task'], '1.1')
        self.assertEqual(data['title'], 'Task 1.1: Add the success dialog widget')
        self.assertEqual(data['route_declared'], 'light')
        self.assertEqual(data['route_effective'], 'light')
        self.assertEqual(data['model'], 'sonnet')

    def test_route_next_reads_review_per_task_from_config(self):
        artel = Path(self.repo.name) / '.artel'
        artel.mkdir()
        (artel / 'config.json').write_text('{"review": {"perTask": true}}', encoding='utf-8')
        _, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                         '--repo', self.repo.name, '--next')
        self.assertEqual(out['data']['route_effective'], 'full')
        self.assertEqual(out['data']['model'], 'opus')
        self.assertIn('review.perTask', out['data']['route_reasons'])

    def test_route_next_reads_deviation_files_from_run_state(self):
        run = Path(self.repo.name) / '.artel' / 'run' / 'AW-9'
        run.mkdir(parents=True)
        (run / 'run-state.json').write_text(
            json.dumps({'deviation_files': ['lib/ramps/ramps_event.dart']}), encoding='utf-8')
        _, out = run_cli('route', '--tasklist', self.write(tick(TASKS, 'Add the widget test')),
                         '--ticket-key', 'AW-9', '--repo', self.repo.name, '--next')
        self.assertEqual(out['data']['task'], '1.2')
        self.assertEqual(out['data']['model'], 'opus')
        self.assertIn('earlier deviation: lib/ramps/ramps_event.dart',
                      out['data']['route_reasons'])

    def test_route_next_reads_a_phase_scoped_tasklist(self):
        phase = Path(self.repo.name) / 'specs' / 'AW-9' / 'phase-2'
        phase.mkdir(parents=True)
        (phase / 'tasks.md').write_text(one_iteration(block('1.1', route='full — money path')),
                                        encoding='utf-8')
        _, out = run_cli('route', '--tasklist', str(phase / 'tasks.md'), '--ticket-key', 'AW-9',
                         '--repo', self.repo.name, '--next')
        self.assertEqual(out['data']['model'], 'opus')

    def test_route_next_reads_stdin(self):
        code, out = run_cli('route', '--tasklist', '-', '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next', stdin=TASKS)
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['model'], 'sonnet')

    def test_route_next_with_no_ready_task_is_null(self):
        text = tick(one_iteration(block('1.1')), 'Step one')
        code, out = run_cli('route', '--tasklist', self.write(text), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next')
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['ticket_key'], 'AW-9')
        self.assertEqual(out['data']['format'], 'tasks')
        self.assertIsNone(out['data']['task'])
        self.assertIsNone(out['data']['model'])
        self.assertEqual(out['data']['route_reasons'], [])

    def test_route_needs_the_next_flag(self):
        code, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_route_a_malformed_tasklist_exits_2(self):
        code, out = run_cli('route', '--tasklist', self.write('## Iteration 1: A\n\n- [ ] do\n'),
                            '--ticket-key', 'AW-9', '--next')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'tasklist_malformed')

    def test_route_an_unreadable_config_exits_2(self):
        artel = Path(self.repo.name) / '.artel'
        artel.mkdir()
        (artel / 'config.json').write_text('{oops', encoding='utf-8')
        code, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_route_an_unreadable_run_state_exits_2(self):
        run = Path(self.repo.name) / '.artel' / 'run' / 'AW-9'
        run.mkdir(parents=True)
        (run / 'run-state.json').write_text('{oops', encoding='utf-8')
        code, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_run_state')

    def test_route_a_non_boolean_review_per_task_exits_2(self):
        artel = Path(self.repo.name) / '.artel'
        artel.mkdir()
        (artel / 'config.json').write_text('{"review": {"perTask": "yes"}}', encoding='utf-8')
        code, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_route_a_non_object_review_exits_2(self):
        artel = Path(self.repo.name) / '.artel'
        artel.mkdir()
        (artel / 'config.json').write_text('{"review": true}', encoding='utf-8')
        code, out = run_cli('route', '--tasklist', self.write(TASKS), '--ticket-key', 'AW-9',
                            '--repo', self.repo.name, '--next')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')
