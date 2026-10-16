"""scripts/parallel_scheduler.py: bounded dependency and resource admission.

Contract: docs/opencode-parallel.md §7 (the `Concurrency:` metadata) and §13 (the run
manifest and its task states), docs/task-grammar.md §7 (`ready_now`/`waves` stay dependency
facts) and the approved admission clauses of the parallel plan.

The iterations are real `task_grammar.parse` output and the manifests are built through
`parallel_state`'s own transitions and persistence, so the selector is exercised against
the documents a run actually stores -- no test-only keys and no hand-written states.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_scheduler  # noqa: E402
import parallel_state  # noqa: E402
import task_grammar  # noqa: E402

RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
TICKET = 'PROJ-123'
DIGEST = 'a' * 64
RESULT_KEYS = ('schema', 'start', 'resume', 'claim_capacity', 'drain', 'reasons')
SIDE_STATES = ('blocked', 'failed', 'cancelled')

# One walk from admission to each state, over the real transition table.
WALKS = {
    'reserved': (),
    'preparing': ('preparing',),
    'running': ('preparing', 'running'),
    'verifying/reviewing': ('preparing', 'running', 'verifying/reviewing'),
    'prepared': ('preparing', 'running', 'verifying/reviewing', 'prepared'),
    'integrating': ('preparing', 'running', 'verifying/reviewing', 'prepared', 'integrating'),
    'published': ('preparing', 'running', 'verifying/reviewing', 'prepared', 'integrating',
                  'published'),
    'blocked': ('blocked',),
    'failed': ('preparing', 'failed'),
    'cancelled': ('cancelled',),
}


def block(number, files=('src/x.py',), deps='none', concurrency=None, hitl=None,
          done=False, route='light', test='none — nothing to test'):
    """One `### Task N.M:` block in the tasklist grammar."""
    head = '### Task {}: work on {}'.format(number, number)
    if hitl:
        head += ' [HITL: {}]'.format(hitl)
    lines = [head,
             '- **Files:** {}'.format(', '.join('`{}`'.format(path) for path in files)),
             '- **Depends on:** {}'.format(deps),
             '- **Route:** {}'.format(route)]
    if concurrency:
        lines.append('- **Concurrency:** {}'.format(concurrency))
    lines.append('- **Test:** {}'.format(test))
    lines.append('- [{}] do the work'.format('x' if done else ' '))
    return '\n'.join(lines)


def iteration(number, *blocks):
    return '## Iteration {}: iteration {}\n\n{}'.format(number, number, '\n\n'.join(blocks))


def parse(text):
    """The iterations of a tasklist body the parser accepts without a problem."""
    iterations, problems, warnings = task_grammar.parse(text)
    assert not problems, problems
    assert not warnings, warnings
    return iterations


def walk(value, task, state):
    """One task walked from admission to `state` through the real transitions."""
    value = parallel_state.transition_task(value, task, None, 'reserved', {})
    current = 'reserved'
    for target in WALKS[state]:
        decision = (target in SIDE_STATES or current in SIDE_STATES
                    or (current == 'preparing' and target == 'reserved'))
        fields = {'decision': {'reason': 'test walk'}} if decision else {}
        value = parallel_state.transition_task(value, task, current, target, fields)
        current = target
    return value


def manifest(states=None, seats=2, phase=2, stop=None):
    """A run manifest with `states` = {task: state} walked through the real transitions."""
    value = parallel_state.new_run(TICKET, phase, RUN_ID, seats, DIGEST)
    for task, state in (states or {}).items():
        value = walk(value, task, state)
    if stop is not None:
        value = dict(value, stop=stop)
    return value


def select(iterations, value=None, seats=2, case_sensitive=True):
    return parallel_scheduler.select_admissions(
        iterations, value if value is not None else manifest(), seats, case_sensitive)


def rejects(test, kind, call):
    """Assert `call` raises SchedulerError of `kind`; return the exception."""
    with test.assertRaises(parallel_scheduler.SchedulerError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind)
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


TWO = parse(iteration(2,
                      block('2.1', files=('src/a.py',)),
                      block('2.2', files=('src/b.py',))))
THREE = parse(iteration(3,
                        block('3.1', files=('src/a.py',)),
                        block('3.2', files=('src/b.py',)),
                        block('3.3', files=('src/c.py',))))
FOUR = parse(iteration(2,
                       block('2.1', files=('src/a.py',)),
                       block('2.2', files=('src/b.py',)),
                       block('2.3', files=('src/c.py',)),
                       block('2.4', files=('src/d.py',))))
CHAIN = parse(iteration(2,
                        block('2.1', files=('src/a.py',)),
                        block('2.2', files=('src/b.py',)),
                        block('2.3', files=('src/c.py',), deps='2.1, 2.2')))
SAME = parse(iteration(2,
                       block('2.1', files=('src/a.py',)),
                       block('2.2', files=('src/a.py',)),
                       block('2.3', files=('src/c.py',))))
MULTI = parse(iteration(1, block('1.1', files=('src/a.py',)))
              + '\n\n' + iteration(2, block('2.1', files=('src/b.py',))))
EXCLUSIVE_FIRST = parse(iteration(2,
                                  block('2.1', files=('src/a.py',),
                                        concurrency='exclusive — the fixture database is shared'),
                                  block('2.2', files=('src/b.py',))))
EXCLUSIVE_LAST = parse(iteration(2,
                                 block('2.1', files=('src/a.py',)),
                                 block('2.2', files=('src/b.py',),
                                       concurrency='exclusive — shared external effects')))
HITL_FIRST = parse(iteration(2,
                             block('2.1', files=('src/a.py',), hitl='copy needs product sign-off'),
                             block('2.2', files=('src/b.py',))))
AMBIGUOUS = parse(iteration(2,
                            block('2.1', files=('src/*.py',)),
                            block('2.2', files=('src/b.py',))))


class TestAdmissionShape(unittest.TestCase):
    def test_the_admission_is_the_documented_shape(self):
        result = select(TWO)
        self.assertEqual(sorted(result), sorted(RESULT_KEYS))
        self.assertEqual(result['schema'], 1)
        self.assertEqual(result['start'], ['2.1', '2.2'])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 2)
        self.assertIs(result['drain'], False)
        self.assertEqual(result['reasons'], [])

    def test_the_selector_is_pure_and_json_safe(self):
        value = manifest({'2.1': 'running'})
        iterations = json.loads(json.dumps(TWO))
        first = parallel_scheduler.select_admissions(iterations, value, 2, True)
        second = parallel_scheduler.select_admissions(iterations, value, 2, True)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertEqual(iterations, json.loads(json.dumps(TWO)))
        self.assertEqual(value, manifest({'2.1': 'running'}))

    def test_the_manifest_survives_persistence_validation(self):
        value = manifest({'2.1': 'running'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'manifest.json'
            written = parallel_state.write_manifest(path, value, 0)
            stored = parallel_state.read_manifest(path)
        self.assertEqual(stored, written)
        result = parallel_scheduler.select_admissions(TWO, stored, 2, True)
        self.assertEqual(result['start'], ['2.2'])

    def test_the_inputs_are_validated(self):
        for seats in (0, -1, True, '2', 2.0, None):
            rejects(self, 'invalid_seats',
                    lambda seats=seats: parallel_scheduler.select_admissions(
                        TWO, manifest(), seats, True))
        rejects(self, 'invalid_seats',
                lambda: parallel_scheduler.select_admissions(TWO, manifest(seats=3), 2, True))
        for case in (None, 'yes', 1, 0):
            rejects(self, 'invalid_case_sensitive',
                    lambda case=case: parallel_scheduler.select_admissions(
                        TWO, manifest(), 2, case))
        for iterations in (None, {}, 'tasklist', [None],
                           [{'number': 0, 'tasks': []}],
                           [{'number': 2}],
                           [{'number': 2, 'tasks': [{'number': '2', 'fields': {}, 'hitl': None,
                                                     'steps': []}]}]):
            rejects(self, 'invalid_iterations',
                    lambda iterations=iterations: parallel_scheduler.select_admissions(
                        iterations, manifest(), 2, True))
        for value in (None, [], {'seats': 2},
                      dict(manifest(), tasks={'2.1': {'state': 'ok'}}),
                      dict(manifest(), tasks={'2': {'state': 'running'}}),
                      dict(manifest(), stop='wrong-phase')):
            rejects(self, 'invalid_manifest',
                    lambda value=value: parallel_scheduler.select_admissions(TWO, value, 2, True))


class TestSeats(unittest.TestCase):
    def test_one_seat_admits_one_task_and_keeps_the_serial_procedure(self):
        result = select(THREE, value=manifest(seats=1), seats=1)
        self.assertEqual(result['start'], ['3.1'])
        self.assertEqual(result['claim_capacity'], 1)
        running = manifest({'3.1': 'running'}, seats=1)
        result = select(THREE, value=running, seats=1)
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('seat' in reason for reason in result['reasons']))

    def test_two_seats_admit_two_compatible_tasks(self):
        result = select(THREE)
        self.assertEqual(result['start'], ['3.1', '3.2'])
        self.assertEqual(result['claim_capacity'], 2)

    def test_three_seats_admit_three_compatible_tasks(self):
        result = select(THREE, value=manifest(seats=3), seats=3)
        self.assertEqual(result['start'], ['3.1', '3.2', '3.3'])
        self.assertEqual(result['claim_capacity'], 3)


class TestNoDuplicateStart(unittest.TestCase):
    def test_a_task_with_a_manifest_record_is_never_started_again(self):
        for state in ('reserved', 'preparing', 'running', 'verifying/reviewing', 'prepared',
                      'integrating', 'published', 'blocked', 'failed', 'cancelled'):
            result = select(TWO, value=manifest({'2.1': state}))
            self.assertNotIn('2.1', result['start'], state)

    def test_a_reserved_task_is_resumed_for_launch_not_claimed_again(self):
        result = select(TWO, value=manifest({'2.1': 'reserved'}))
        self.assertEqual(result['resume'], ['2.1'])
        self.assertNotIn('2.1', result['start'])
        self.assertEqual(result['start'], ['2.2'])
        self.assertEqual(result['claim_capacity'], 1)

    def test_repeated_calls_cannot_oversubscribe(self):
        self.assertEqual(select(TWO)['start'], ['2.1', '2.2'])
        # The coordinator reserves exactly what the first call admitted; the next call
        # promotes both reservations instead of admitting anything new.
        reserved = manifest({'2.1': 'reserved', '2.2': 'reserved'})
        result = select(TWO, value=reserved)
        self.assertEqual(result['resume'], ['2.1', '2.2'])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(result['reasons'])


class TestDependencies(unittest.TestCase):
    def test_independent_tasks_start_together(self):
        self.assertEqual(select(TWO)['start'], ['2.1', '2.2'])

    def test_a_dependent_does_not_start_with_its_dependencies(self):
        self.assertEqual(select(CHAIN)['start'], ['2.1', '2.2'])

    def test_a_dependent_waits_for_the_last_dependency_to_publish(self):
        result = select(CHAIN, value=manifest({'2.1': 'published'}))
        self.assertEqual(result['start'], ['2.2'])
        result = select(CHAIN, value=manifest({'2.1': 'published', '2.2': 'running'}))
        self.assertEqual(result['start'], [])

    def test_publication_unlocks_a_dependent_before_the_checkbox_is_ticked(self):
        value = manifest({'2.1': 'published', '2.2': 'published'}, seats=3)
        result = select(CHAIN, value=value, seats=3)
        self.assertEqual(result['start'], ['2.3'])

    def test_a_prepared_dependency_is_not_published_yet(self):
        value = manifest({'2.1': 'prepared', '2.2': 'published'}, seats=3)
        result = select(CHAIN, value=value, seats=3)
        self.assertEqual(result['start'], [])

    def test_a_checked_dependency_unlocks_without_a_manifest_record(self):
        text = iteration(2, block('2.1', files=('src/a.py',), done=True),
                         block('2.2', files=('src/b.py',)),
                         block('2.3', files=('src/c.py',), deps='2.1'))
        self.assertEqual(select(parse(text))['start'], ['2.2', '2.3'])

    def test_ready_now_stays_the_checkbox_base_of_admission(self):
        # With nothing published, admission is exactly the parser's ready set; the
        # publication overlay only adds durable completion on top of it.
        for iterations in (CHAIN, MULTI, TWO):
            self.assertEqual(select(iterations)['start'], task_grammar.ready_now(iterations))


class TestFirstUnfinishedIteration(unittest.TestCase):
    def test_only_the_current_unfinished_iteration_admits(self):
        self.assertEqual(select(MULTI)['start'], ['1.1'])

    def test_a_later_iteration_waits_until_the_current_one_is_complete(self):
        result = select(MULTI, value=manifest({'1.1': 'running'}))
        self.assertEqual(result['start'], [])
        self.assertTrue(any('iteration 1' in reason for reason in result['reasons']))

    def test_publication_advances_the_barrier_before_the_checkboxes(self):
        result = select(MULTI, value=manifest({'1.1': 'published'}))
        self.assertEqual(result['start'], ['2.1'])

    def test_the_barrier_uses_done_and_published_together(self):
        text = (iteration(1, block('1.1', files=('src/a.py',), done=True),
                          block('1.2', files=('src/b.py',)))
                + '\n\n' + iteration(2, block('2.1', files=('src/c.py',))))
        result = select(parse(text), value=manifest({'1.2': 'published'}))
        self.assertEqual(result['start'], ['2.1'])

    def test_a_blocked_task_keeps_its_iteration_current(self):
        result = select(MULTI, value=manifest({'1.1': 'blocked'}))
        self.assertEqual(result['start'], [])

    def test_a_complete_tasklist_admits_nothing(self):
        text = iteration(2, block('2.1', files=('src/a.py',), done=True))
        result = select(parse(text))
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 2)  # the cap figure, not a task count
        self.assertTrue(any('unfinished' in reason for reason in result['reasons']))


class TestOwnedTaskCompletion(unittest.TestCase):
    """I5: for a task this run represents, only `published` completes it -- never a tick."""

    TICKED = iteration(2, block('2.1', files=('src/a.py',), done=True),
                       block('2.2', files=('src/b.py',), deps='2.1'))
    TICKED_ITERATIONS = iteration(2, block('2.1', files=('src/a.py',), done=True),
                                  block('2.2', files=('src/b.py',))) + '\n\n' + iteration(
        3, block('3.1', files=('src/c.py',)))

    def test_a_ticked_task_with_a_side_state_holds_its_dependents(self):
        for state in ('cancelled', 'failed', 'blocked'):
            with self.subTest(state=state):
                result = select(parse(self.TICKED), value=manifest({'2.1': state}))
                self.assertEqual(result['start'], [])
                self.assertEqual(result['resume'], [])
                self.assertFalse(result['drain'])

    def test_a_ticked_integrating_task_keeps_the_drain(self):
        result = select(parse(self.TICKED), value=manifest({'2.1': 'integrating'}))
        self.assertTrue(result['drain'])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])

    def test_a_ticked_reserved_task_does_not_advance_the_iteration(self):
        result = select(parse(self.TICKED_ITERATIONS), value=manifest({'2.1': 'reserved'}))
        self.assertNotIn('3.1', result['start'])
        self.assertIn('2.1', result['resume'])

    def test_a_ticked_unrepresented_task_still_completes_by_checkbox(self):
        self.assertEqual(select(parse(self.TICKED))['start'], ['2.2'])


class TestFileConflicts(unittest.TestCase):
    def test_a_ready_task_sharing_a_file_with_a_running_task_waits(self):
        result = select(SAME, value=manifest({'2.1': 'running'}))
        self.assertEqual(result['start'], ['2.3'])
        self.assertEqual(result['claim_capacity'], 1)
        self.assertTrue(any('src/a.py' in reason for reason in result['reasons']))

    def test_a_shared_path_never_starts_two_tasks_together(self):
        result = select(SAME)
        self.assertEqual(result['start'], ['2.1', '2.3'])
        self.assertTrue(any('2.1' in reason and 'src/a.py' in reason
                            for reason in result['reasons']))

    def test_the_waiting_task_starts_once_the_holder_is_published(self):
        result = select(SAME, value=manifest({'2.1': 'published'}))
        self.assertEqual(result['start'], ['2.2', '2.3'])

    def test_a_side_state_releases_the_declared_paths(self):
        result = select(SAME, value=manifest({'2.1': 'failed'}))
        self.assertEqual(result['start'], ['2.2', '2.3'])

    def test_an_unintegrated_task_still_holds_its_declared_path(self):
        result = select(SAME, value=manifest({'2.1': 'prepared'}))
        self.assertEqual(result['start'], ['2.3'])
        self.assertTrue(any('src/a.py' in reason for reason in result['reasons']))

    def test_case_insensitive_platforms_serialize_case_variants(self):
        text = iteration(2, block('2.1', files=('src/A.py',)),
                         block('2.2', files=('src/a.py',)))
        iterations = parse(text)
        value = manifest({'2.1': 'running'})
        insensitive = select(iterations, value=value, case_sensitive=False)
        self.assertEqual(insensitive['start'], [])
        self.assertTrue(any('src/a.py' in reason for reason in insensitive['reasons']))
        sensitive = select(iterations, value=value, case_sensitive=True)
        self.assertEqual(sensitive['start'], ['2.2'])

    def test_paths_are_normalized_before_the_exact_intersection(self):
        text = iteration(2, block('2.1', files=('src/a.py',)),
                         block('2.2', files=('./src//a.py',)))
        result = select(parse(text), value=manifest({'2.1': 'running'}))
        self.assertEqual(result['start'], [])

    def test_canonical_unicode_aliases_conflict_conservatively(self):
        nfc = 'caf\u00e9.txt'
        nfd = 'cafe\u0301.txt'
        self.assertNotEqual(nfc, nfd)
        text = iteration(2, block('2.1', files=(nfc,)), block('2.2', files=(nfd,)))
        iterations = parse(text)
        value = manifest({'2.1': 'running'})
        insensitive = select(iterations, value=value, case_sensitive=False)
        self.assertEqual(insensitive['start'], [])
        self.assertTrue(any('shares' in reason for reason in insensitive['reasons']))
        sensitive = select(iterations, value=value, case_sensitive=True)
        self.assertEqual(sensitive['start'], ['2.2'])

    def test_an_exact_path_does_not_conflict_with_its_suffix(self):
        text = iteration(2, block('2.1', files=('src/a.py',)),
                         block('2.2', files=('src/a.pyc',)))
        result = select(parse(text), value=manifest({'2.1': 'running'}))
        self.assertEqual(result['start'], ['2.2'])


class TestPrefixAmbiguity(unittest.TestCase):
    """A directory/prefix declaration is ambiguous ownership: its task is exclusive."""

    def test_a_prefix_declaration_across_tasks_is_exclusive(self):
        text = iteration(2, block('2.1', files=('src',)),
                         block('2.2', files=('src/a.py',)))
        result = select(parse(text))
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 1)
        self.assertTrue(any('2.1' in reason and 'prefix' in reason
                            for reason in result['reasons']))

    def test_a_prefix_declared_within_one_task_is_exclusive(self):
        text = iteration(2, block('2.1', files=('src', 'src/a.py')),
                         block('2.2', files=('src/b.py',)))
        result = select(parse(text))
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['claim_capacity'], 1)

    def test_the_prefix_owner_waits_for_a_quiet_run(self):
        text = iteration(2, block('2.1', files=('src',)),
                         block('2.2', files=('src/a.py',)))
        result = select(parse(text), value=manifest({'2.2': 'running'}))
        self.assertEqual(result['start'], [])
        self.assertTrue(any('2.1' in reason for reason in result['reasons']))

    def test_the_covered_declaration_stays_an_ordinary_path(self):
        text = iteration(2, block('2.1', files=('src',)),
                         block('2.2', files=('src/a.py',)),
                         block('2.3', files=('src/b.py',)))
        published = select(parse(text), value=manifest({'2.1': 'published'}))
        self.assertEqual(published['start'], ['2.2', '2.3'])

    def test_case_sensitivity_decides_a_prefix_segment(self):
        text = iteration(2, block('2.1', files=('src/A',)),
                         block('2.2', files=('src/a/b.py',)))
        iterations = parse(text)
        sensitive = select(iterations, case_sensitive=True)
        self.assertEqual(sensitive['start'], ['2.1', '2.2'])
        insensitive = select(iterations, case_sensitive=False)
        self.assertEqual(insensitive['start'], ['2.1'])
        self.assertEqual(insensitive['claim_capacity'], 1)

    def test_a_string_suffix_is_not_a_segment_prefix(self):
        text = iteration(2, block('2.1', files=('src/a',)),
                         block('2.2', files=('src/ab',)))
        result = select(parse(text))
        self.assertEqual(result['start'], ['2.1', '2.2'])


class TestExclusive(unittest.TestCase):
    def test_a_concurrency_exclusive_task_is_admitted_alone(self):
        result = select(EXCLUSIVE_FIRST)
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['claim_capacity'], 1)
        self.assertIs(result['drain'], False)
        self.assertTrue(any('exclusive' in reason for reason in result['reasons']))

    def test_an_exclusive_task_waits_for_a_quiet_run(self):
        result = select(EXCLUSIVE_FIRST, value=manifest({'2.2': 'running'}))
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('2.1' in reason and 'exclusive' in reason
                            for reason in result['reasons']))

    def test_a_ready_exclusive_task_stops_the_admissions_behind_it(self):
        result = select(EXCLUSIVE_LAST)
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['claim_capacity'], 1)
        self.assertTrue(any('2.2' in reason for reason in result['reasons']))

    def test_hitl_tasks_are_exclusive(self):
        result = select(HITL_FIRST)
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['claim_capacity'], 1)
        self.assertTrue(any('HITL' in reason for reason in result['reasons']))
        waiting = select(HITL_FIRST, value=manifest({'2.2': 'running'}))
        self.assertEqual(waiting['start'], [])

    def test_uncertain_write_ownership_is_exclusive(self):
        for files in (('src/',), ('src/*.py',), ('/etc/hosts',), ('src/../lib/a.py',),
                      ('src\\a.py',)):
            text = iteration(2, block('2.1', files=files), block('2.2', files=('src/b.py',)))
            result = select(parse(text), value=manifest({'2.2': 'running'}))
            self.assertEqual(result['start'], [], files)
            self.assertTrue(any('2.1' in reason for reason in result['reasons']), files)

    def test_a_task_without_declared_files_is_exclusive(self):
        text = iteration(2, block('2.1', files=()), block('2.2', files=('src/b.py',)))
        iterations, problems, _ = task_grammar.parse(text)
        self.assertTrue(any(problem['rule'] == 'empty-field' for problem in problems))
        result = select(iterations, value=manifest({'2.2': 'running'}))
        self.assertEqual(result['start'], [])
        self.assertTrue(any('write ownership' in reason for reason in result['reasons']))

    def test_an_ambiguous_task_runs_alone(self):
        result = select(AMBIGUOUS)
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['claim_capacity'], 1)

    def test_an_exclusive_task_holds_the_run_from_admission_to_publication(self):
        for state in ('preparing', 'running', 'verifying/reviewing'):
            result = select(EXCLUSIVE_FIRST, value=manifest({'2.1': state}))
            self.assertEqual(result['start'], [], state)
            self.assertEqual(result['resume'], [], state)
            self.assertEqual(result['claim_capacity'], 0, state)
            self.assertIs(result['drain'], False, state)
        # A pending exclusive reservation is the coordinator's to launch, alone.
        reserved = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'reserved'}))
        self.assertEqual(reserved['start'], [])
        self.assertEqual(reserved['resume'], ['2.1'])
        self.assertEqual(reserved['claim_capacity'], 0)

    def test_pending_exclusive_integration_stops_every_new_admission(self):
        result = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'prepared'}))
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertIs(result['drain'], True)
        self.assertTrue(any('drain' in reason for reason in result['reasons']))
        result = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'integrating'}))
        self.assertIs(result['drain'], True)
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])
        # The drain completes with publication: admission reopens.
        result = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'published'}))
        self.assertIs(result['drain'], False)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], ['2.2'])

    def test_an_ordinary_prepared_result_does_not_drain_but_an_integration_does(self):
        prepared = select(TWO, value=manifest({'2.1': 'prepared'}))
        self.assertIs(prepared['drain'], False)
        self.assertEqual(prepared['resume'], [])
        self.assertEqual(prepared['start'], ['2.2'])
        integrating = select(TWO, value=manifest({'2.1': 'integrating'}))
        self.assertIs(integrating['drain'], True)
        self.assertEqual(integrating['start'], [])
        self.assertEqual(integrating['resume'], [])
        self.assertEqual(integrating['claim_capacity'], 0)

    def test_a_side_state_releases_the_run(self):
        for state in SIDE_STATES:
            result = select(EXCLUSIVE_FIRST, value=manifest({'2.1': state}))
            self.assertEqual(result['start'], ['2.2'], state)
            self.assertEqual(result['resume'], [], state)
            self.assertIs(result['drain'], False, state)


class TestIntegrationDrain(unittest.TestCase):
    """The reviewed correction: ANY manifest task `integrating` is a durable admission
    latch -- it stops new starts and reservation resumes whatever the task's kind."""

    def test_an_ordinary_integrating_task_stops_every_new_admission(self):
        result = select(TWO, value=manifest({'2.1': 'integrating'}))
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertIs(result['drain'], True)
        self.assertTrue(any('2.1' in reason and 'integrat' in reason
                            for reason in result['reasons']), result['reasons'])

    def test_a_reservation_is_not_resumed_while_a_sibling_integrates(self):
        result = select(TWO, value=manifest({'2.1': 'integrating', '2.2': 'reserved'}))
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertIs(result['drain'], True)

    def test_an_unknown_integrating_record_drains_the_run(self):
        # a restarted coordinator's manifest may hold a record whose block is not in the
        # current parse (a phase boundary, a removed task): the latch still holds
        result = select(TWO, value=manifest({'9.9': 'integrating'}))
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertIs(result['drain'], True)
        self.assertTrue(any('9.9' in reason for reason in result['reasons']),
                        result['reasons'])

    def test_a_stopped_run_with_an_integrating_record_still_drains(self):
        stop = {'reason': 'wrong-phase row', 'intent_id': None, 'row': None}
        result = select(TWO, value=manifest({'2.1': 'integrating'}, stop=stop))
        self.assertIs(result['drain'], True)
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('integrat' in reason for reason in result['reasons']),
                        result['reasons'])

    def test_the_latch_releases_with_publication(self):
        held = select(CHAIN, value=manifest({'2.1': 'integrating', '2.2': 'published'}))
        self.assertIs(held['drain'], True)
        self.assertEqual(held['start'], [])
        released = select(CHAIN, value=manifest({'2.1': 'published', '2.2': 'published'}))
        self.assertIs(released['drain'], False)
        self.assertEqual(released['start'], ['2.3'])

    def test_an_exclusive_reservation_keeps_its_reviewed_turn(self):
        alone = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'reserved'}))
        self.assertEqual(alone['resume'], ['2.1'])
        self.assertEqual(alone['start'], [])
        busy = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'reserved', '2.2': 'running'}))
        self.assertEqual(busy['resume'], [])
        self.assertEqual(busy['start'], [])
        self.assertEqual(busy['claim_capacity'], 0)


class TestExclusiveReservation(unittest.TestCase):
    def test_a_pending_exclusive_reservation_waits_for_the_workers(self):
        busy = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'reserved', '2.2': 'running'}))
        self.assertEqual(busy['resume'], [])
        self.assertEqual(busy['start'], [])
        self.assertEqual(busy['claim_capacity'], 0)
        self.assertIs(busy['drain'], False)
        quiet = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'reserved'}))
        self.assertEqual(quiet['resume'], ['2.1'])
        self.assertEqual(quiet['start'], [])
        self.assertEqual(quiet['claim_capacity'], 0)

    def test_an_exclusive_reservation_waits_for_every_unresolved_state(self):
        for state in ('preparing', 'running', 'verifying/reviewing', 'prepared'):
            result = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'reserved', '2.2': state}))
            self.assertEqual(result['resume'], [], state)
            self.assertEqual(result['start'], [], state)
            self.assertTrue(any('2.1' in reason for reason in result['reasons']), state)
        # An integrating sibling is the durable admission latch: the reservation still never
        # resumes, and the diagnostic names the integration that must complete first.
        result = select(EXCLUSIVE_FIRST,
                        value=manifest({'2.1': 'reserved', '2.2': 'integrating'}))
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertIs(result['drain'], True)
        self.assertTrue(any('2.2' in reason and 'integrat' in reason
                            for reason in result['reasons']), result['reasons'])
        # With the other work published, the exclusive reservation launches alone.
        result = select(EXCLUSIVE_FIRST,
                        value=manifest({'2.1': 'reserved', '2.2': 'published'}))
        self.assertEqual(result['resume'], ['2.1'])
        self.assertEqual(result['start'], [])

    def test_a_pending_exclusive_reservation_does_not_freeze_another_reservation(self):
        # 2.1 is exclusive and reserved; 2.2 runs; 2.3 waits and is compatible.
        text = iteration(2,
                         block('2.1', files=('src/a.py',),
                               concurrency='exclusive — the fixture database is shared'),
                         block('2.2', files=('src/b.py',)),
                         block('2.3', files=('src/c.py',)))
        iterations = parse(text)
        busy = select(iterations, value=manifest({'2.1': 'reserved', '2.2': 'running',
                                                  '2.3': 'reserved'}))
        self.assertEqual(busy['resume'], ['2.3'])
        self.assertEqual(busy['start'], [])
        after = select(iterations, value=manifest({'2.1': 'reserved', '2.2': 'published',
                                                   '2.3': 'published'}))
        self.assertEqual(after['resume'], ['2.1'])
        self.assertEqual(after['start'], [])

    def test_an_exclusive_reservation_takes_its_turn_before_a_later_reservation(self):
        value = manifest({'2.1': 'reserved', '2.2': 'reserved'})
        result = select(EXCLUSIVE_FIRST, value=value)
        self.assertEqual(result['resume'], ['2.1'])
        self.assertEqual(result['start'], [])
        after = select(EXCLUSIVE_FIRST, value=manifest({'2.1': 'published', '2.2': 'reserved'}))
        self.assertEqual(after['resume'], ['2.2'])

    def test_a_reservation_before_an_exclusive_reservation_takes_its_turn_first(self):
        value = manifest({'2.1': 'reserved', '2.2': 'reserved'})
        result = select(EXCLUSIVE_LAST, value=value)
        self.assertEqual(result['resume'], ['2.1'])
        self.assertEqual(result['start'], [])
        held = select(EXCLUSIVE_LAST, value=manifest({'2.1': 'preparing', '2.2': 'reserved'}))
        self.assertEqual(held['resume'], [])
        after = select(EXCLUSIVE_LAST, value=manifest({'2.1': 'published', '2.2': 'reserved'}))
        self.assertEqual(after['resume'], ['2.2'])

    def test_two_exclusive_reservations_take_turns_instead_of_deadlocking(self):
        text = iteration(2,
                         block('2.1', files=('src/a.py',),
                               concurrency='exclusive — shared external effects'),
                         block('2.2', files=('src/b.py',),
                               concurrency='exclusive — shared external effects'))
        iterations = parse(text)
        value = manifest({'2.1': 'reserved', '2.2': 'reserved'})
        result = select(iterations, value=value)
        self.assertEqual(result['resume'], ['2.1'])
        self.assertEqual(result['start'], [])
        after = select(iterations, value=manifest({'2.1': 'published', '2.2': 'reserved'}))
        self.assertEqual(after['resume'], ['2.2'])


class TestCapacity(unittest.TestCase):
    def test_reserved_running_and_prepared_assignments_each_count(self):
        for active in ('preparing', 'running'):
            value = manifest({'2.1': active, '2.2': 'reserved', '2.3': 'prepared'})
            result = select(FOUR, value=value)
            # The one free seat goes to the reservation first, filling both seats.
            self.assertEqual(result['resume'], ['2.2'], active)
            self.assertEqual(result['start'], [], active)
            self.assertEqual(result['claim_capacity'], 0, active)

    def test_prepared_work_counts_toward_the_unresolved_bound_only(self):
        value = manifest({'2.1': 'running', '2.2': 'prepared'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], ['2.3'])
        self.assertEqual(result['claim_capacity'], 1)

    def test_the_unresolved_bound_stops_the_lookahead(self):
        value = manifest({'2.1': 'running', '2.2': 'reserved', '2.3': 'prepared',
                          '2.4': 'reserved'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], ['2.2'])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(result['reasons'])

    def test_the_lookahead_admits_a_compatible_third_task_behind_a_reservation(self):
        # seats=2: 2.1 runs, 2.2 waits on the same declared file, 2.3 is compatible.
        # 2.1 + 2.2 + 2.3 = 3 unresolved assignments, within the 2 * seats = 4 bound.
        value = manifest({'2.1': 'running', '2.2': 'reserved'})
        result = select(SAME, value=value)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], ['2.3'])
        self.assertEqual(result['claim_capacity'], 1)
        self.assertNotIn('2.2', result['start'])
        self.assertIs(result['drain'], False)

    def test_the_reservation_bound_stops_the_lookahead(self):
        text = iteration(2, block('2.1', files=('src/a.py',)),
                         block('2.2', files=('src/a.py',)),
                         block('2.3', files=('src/a.py',)))
        value = manifest({'2.1': 'running', '2.2': 'reserved', '2.3': 'reserved'})
        result = select(parse(text), value=value)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('reservation' in reason for reason in result['reasons']))

    def test_saturated_seats_produce_reasons(self):
        value = manifest({'2.1': 'running', '2.2': 'running'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('seat' in reason for reason in result['reasons']))

    def test_a_side_state_does_not_consume_capacity(self):
        value = manifest({'2.1': 'failed', '2.2': 'cancelled'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], ['2.3', '2.4'])
        self.assertEqual(result['claim_capacity'], 2)

    def test_an_unknown_manifest_task_holds_the_run_conservatively(self):
        result = select(TWO, value=manifest({'9.9': 'running'}))
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('9.9' in reason for reason in result['reasons']))

    def test_an_unknown_reservation_is_never_resumed(self):
        result = select(TWO, value=manifest({'9.9': 'reserved'}))
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('9.9' in reason for reason in result['reasons']))

    def test_a_ready_exclusive_task_caps_the_claim_capacity(self):
        # The quiet run would allow seats=3 claims, but the exclusive task suspends
        # admission behind it after the one compatible task before it.
        result = select(EXCLUSIVE_LAST, value=manifest(seats=3), seats=3)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(result['claim_capacity'], 1)


class TestResume(unittest.TestCase):
    def test_an_incompatible_reservation_waits_then_resumes_after_publication(self):
        running = select(SAME, value=manifest({'2.1': 'running', '2.2': 'reserved'}))
        self.assertEqual(running['resume'], [])
        self.assertEqual(running['start'], ['2.3'])
        self.assertNotIn('2.2', running['start'])
        published = select(SAME, value=manifest({'2.1': 'published', '2.2': 'reserved'}))
        self.assertEqual(published['resume'], ['2.2'])
        self.assertNotIn('2.2', published['start'])
        self.assertEqual(published['start'], ['2.3'])

    def test_a_resumed_task_is_never_re_claimed(self):
        first = select(TWO, value=manifest({'2.1': 'reserved'}))
        self.assertEqual(first['resume'], ['2.1'])
        self.assertNotIn('2.1', first['start'])
        # After the coordinator persists the promotion, it is not a reservation any more.
        second = select(TWO, value=manifest({'2.1': 'preparing'}))
        self.assertEqual(second['resume'], [])
        self.assertNotIn('2.1', second['start'])

    def test_the_free_launch_seat_goes_to_the_reservation_before_a_new_start(self):
        value = manifest({'2.1': 'running', '2.2': 'reserved'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], ['2.2'])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)

    def test_resumed_and_new_actions_never_exceed_the_active_capacity(self):
        result = select(FOUR, value=manifest({'2.2': 'reserved'}))
        self.assertEqual(result['resume'], ['2.2'])
        self.assertEqual(result['start'], ['2.1'])
        self.assertEqual(len(result['resume']) + len(result['start']), 2)

    def test_conflicting_reservations_promote_one_then_the_next_after_publication(self):
        value = manifest({'2.1': 'reserved', '2.2': 'reserved'})
        first = select(SAME, value=value)
        self.assertEqual(first['resume'], ['2.1'])
        self.assertNotIn('2.2', first['resume'])
        self.assertTrue(any('2.2' in reason for reason in first['reasons']))
        # The first reservation runs; the second is re-checked once it is published.
        running = select(SAME, value=manifest({'2.1': 'running', '2.2': 'reserved'}))
        self.assertEqual(running['resume'], [])
        published = select(SAME, value=manifest({'2.1': 'published', '2.2': 'reserved'}))
        self.assertEqual(published['resume'], ['2.2'])

    def test_a_retried_reservation_waits_for_unresolved_work(self):
        value = manifest({'2.1': 'prepared', '2.2': 'failed'})
        retried = parallel_state.transition_task(value, '2.2', 'failed', 'reserved',
                                                 {'decision': {'reason': 'retry'}})
        result = select(SAME, value=retried)
        self.assertEqual(result['resume'], [])
        self.assertTrue(any('2.2' in reason for reason in result['reasons']))
        integrated = parallel_state.transition_task(retried, '2.1', 'prepared', 'integrating', {})
        published = parallel_state.transition_task(integrated, '2.1', 'integrating', 'published',
                                                   {})
        self.assertEqual(select(SAME, value=published)['resume'], ['2.2'])

    def test_a_preparation_failure_return_waits_for_unresolved_work(self):
        value = manifest({'2.1': 'running', '2.2': 'preparing'})
        returned = parallel_state.transition_task(value, '2.2', 'preparing', 'reserved',
                                                  {'decision': {'reason': 'the session failed'}})
        result = select(SAME, value=returned)
        self.assertEqual(result['resume'], [])
        self.assertTrue(any('2.2' in reason for reason in result['reasons']))

    def test_a_reservation_outside_the_current_iteration_waits(self):
        value = manifest({'2.1': 'reserved'})
        result = select(MULTI, value=value)
        self.assertEqual(result['resume'], [])
        self.assertTrue(any('2.1' in reason for reason in result['reasons']))
        value = manifest({'1.1': 'published', '2.1': 'reserved'})
        self.assertEqual(select(MULTI, value=value)['resume'], ['2.1'])

    def test_a_reservation_with_incomplete_dependencies_waits(self):
        value = manifest({'2.1': 'running', '2.2': 'published', '2.3': 'reserved'}, seats=3)
        result = select(CHAIN, value=value, seats=3)
        self.assertEqual(result['resume'], [])
        self.assertTrue(any('2.3' in reason for reason in result['reasons']))
        value = manifest({'2.1': 'published', '2.2': 'published', '2.3': 'reserved'}, seats=3)
        self.assertEqual(select(CHAIN, value=value, seats=3)['resume'], ['2.3'])

    def test_a_latched_stop_withholds_resume_too(self):
        row = {'task_id': 41, 'title': 'I2 · 2.1 · work on 2.1', 'status': 'in_progress',
               'actor': 'artel@' + RUN_ID + '#1:1', 'phase': 2, 'task': '2.1'}
        stop = {'reason': 'wrong-phase', 'intent_id': RUN_ID + '#1:1', 'row': row}
        result = select(TWO, value=manifest({'2.1': 'reserved'}, stop=stop))
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)

    def test_the_reservation_bound_bounds_the_promotions_too(self):
        # seats=2: one running, two reservations -> one promotion, no new start.
        value = manifest({'2.1': 'running', '2.2': 'reserved', '2.3': 'reserved'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], ['2.2'])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('2.3' in reason for reason in result['reasons']))

    def test_the_unresolved_bound_bounds_promotions_and_starts(self):
        value = manifest({'2.1': 'prepared', '2.2': 'prepared', '2.3': 'prepared',
                          '2.4': 'prepared'})
        result = select(FOUR, value=value)
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['start'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertTrue(any('unresolved' in reason for reason in result['reasons']))


class TestStop(unittest.TestCase):
    def test_a_latched_stop_halts_admission(self):
        row = {'task_id': 41, 'title': 'I2 · 2.1 · work on 2.1', 'status': 'in_progress',
               'actor': 'artel@' + RUN_ID + '#1:1', 'phase': 2, 'task': '2.1'}
        stop = {'reason': 'wrong-phase', 'intent_id': RUN_ID + '#1:1', 'row': row}
        result = select(TWO, value=manifest({'2.1': 'running'}, stop=stop))
        self.assertEqual(result['start'], [])
        self.assertEqual(result['resume'], [])
        self.assertEqual(result['claim_capacity'], 0)
        self.assertIs(result['drain'], False)
        self.assertTrue(any('reconciliation' in reason for reason in result['reasons']))


if __name__ == '__main__':
    unittest.main()
