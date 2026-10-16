"""scripts/parallel_state.py: the durable run manifest and the repository-wide ticket lock.

Contract: docs/opencode-parallel.md §6 (task states and transitions) and §13 (run state,
ticket ownership and claim recovery). The manifest tests run against a real filesystem and a
real injected write fault (a failing `os.replace`); the ownership tests build a real Git
repository with a linked worktree, so the common-directory resolution is git's own and a
competing checkout is a real one.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_contract  # noqa: E402
import parallel_state  # noqa: E402

RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'
TICKET = 'PROJ-123'
DIGEST = 'a' * 64
TASK = '3.1'
COORDINATOR = 'ses_coordinator-primary'
OTHER_COORDINATOR = 'ses_coordinator-secondary'

MANIFEST_KEYS = ('schema', 'ticket', 'phase', 'run_id', 'seats', 'graph_digest', 'revision',
                 'tasks', 'claims', 'stop')

# The transition table docs/opencode-parallel.md §6 fixes, spelled independently here so a
# widened implementation fails rather than being tested against itself.
LEGAL = {
    'reserved': ('preparing', 'blocked', 'cancelled'),
    'preparing': ('running', 'reserved', 'blocked', 'failed', 'cancelled'),
    'running': ('verifying/reviewing', 'blocked', 'failed', 'cancelled'),
    'verifying/reviewing': ('prepared', 'blocked', 'failed', 'cancelled'),
    'prepared': ('integrating', 'blocked', 'cancelled'),
    'integrating': ('published', 'blocked', 'failed', 'cancelled'),
    'published': (),
    'blocked': ('reserved',),
    'failed': ('reserved',),
    'cancelled': ('reserved',),
}

# One walk from the admission state to each state, used to build a manifest "at" a state.
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

ALL_STATES = parallel_contract.TASK_STATES + parallel_contract.SIDE_STATES


def new_manifest(**overrides):
    """A fresh schema-1 run manifest with `overrides` applied."""
    value = parallel_state.new_run(TICKET, 2, RUN_ID, 2, DIGEST)
    value.update(overrides)
    return value


def owner(**overrides):
    """An acquire request (the run's identity and coordinator), with `overrides` applied."""
    value = {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': 2,
             'coordinator_id': COORDINATOR}
    value.update(overrides)
    return value


def release_request(stored, completed=True, quiescent=True):
    """The release request for `stored` (the record acquire returned), with the run's
    completion and quiescence asserted."""
    return {'schema': 1, 'ticket': stored.get('ticket', TICKET),
            'run_id': stored.get('run_id', RUN_ID), 'phase': stored.get('phase', 2),
            'coordinator_id': stored.get('coordinator_id', COORDINATOR),
            'token': stored.get('token', '0' * 32),
            'completed': completed, 'quiescent': quiescent}


def recover_request(stored, coordinator=OTHER_COORDINATOR, reason='handoff for test'):
    """The explicit recovery request for `stored`, handed to `coordinator`."""
    return {'schema': 1, 'ticket': stored.get('ticket', TICKET),
            'run_id': stored.get('run_id', RUN_ID), 'phase': stored.get('phase', 2),
            'coordinator_id': coordinator, 'reason': reason}


def rejects(test, kind, call):
    """Assert `call` raises StateError of `kind`; return the exception."""
    with test.assertRaises(parallel_state.StateError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind)
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


def git(cwd, *args):
    return subprocess.run(['git'] + list(args), cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def advance(manifest, expected, target):
    """One transition of TASK; a side entry, a retry and the preparation-failure return
    carry their decision."""
    side = parallel_contract.SIDE_STATES
    recovery = expected == 'preparing' and target == 'reserved'
    needs = target in side or expected in side or recovery
    fields = {'decision': {'reason': 'test'}} if needs else {}
    return parallel_state.transition_task(manifest, TASK, expected, target, fields)


class TestNewRun(unittest.TestCase):
    def test_the_manifest_is_the_documented_shape(self):
        value = new_manifest()
        self.assertEqual(sorted(value), sorted(MANIFEST_KEYS))
        self.assertEqual(value['schema'], 1)
        self.assertEqual(value['ticket'], TICKET)
        self.assertEqual(value['phase'], 2)
        self.assertEqual(value['run_id'], RUN_ID)
        self.assertEqual(value['seats'], 2)
        self.assertEqual(value['graph_digest'], DIGEST)
        self.assertEqual(value['revision'], 0)
        self.assertEqual(value['tasks'], {})
        self.assertEqual(value['claims'], {'seq': 0, 'intents': []})
        self.assertIsNone(value['stop'])

    def test_a_ticket_wide_run_carries_a_null_phase(self):
        self.assertIsNone(new_manifest(phase=None)['phase'])

    def test_the_manifest_is_json_safe(self):
        self.assertEqual(json.loads(json.dumps(new_manifest())), new_manifest())

    def test_a_malformed_ticket_is_rejected(self):
        for ticket in ('proj-1', 'PROJ', 'PROJ_1', 'PROJ-1-2', '', 7, None, ' PROJ-1'):
            rejects(self, 'invalid_run',
                    lambda ticket=ticket: parallel_state.new_run(ticket, 2, RUN_ID, 2, DIGEST))

    def test_a_malformed_phase_is_rejected(self):
        for phase in (0, -1, True, '2', 2.0, [], {}):
            rejects(self, 'invalid_run',
                    lambda phase=phase: parallel_state.new_run(TICKET, phase, RUN_ID, 2, DIGEST))

    def test_a_malformed_run_id_is_rejected(self):
        for run_id in (RUN_ID.upper(), 'not-a-uuid', RUN_ID.replace('-', ''),
                       '{' + RUN_ID + '}', '', 7, None):
            rejects(self, 'invalid_run',
                    lambda run_id=run_id: parallel_state.new_run(TICKET, 2, run_id, 2, DIGEST))

    def test_malformed_seats_are_rejected(self):
        for seats in (0, -1, True, False, '2', 2.0, None):
            rejects(self, 'invalid_run',
                    lambda seats=seats: parallel_state.new_run(TICKET, 2, RUN_ID, seats, DIGEST))

    def test_a_malformed_graph_digest_is_rejected(self):
        for digest in ('', '  ', 'a\nb', 7, None, ['a']):
            rejects(self, 'invalid_run',
                    lambda digest=digest: parallel_state.new_run(TICKET, 2, RUN_ID, 2, digest))


class TestReadWriteManifest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(os.path.realpath(self._tmp.name))
        self.path = self.dir / 'manifest.json'

    def test_a_written_manifest_reads_back_and_bumps_the_revision(self):
        written = parallel_state.write_manifest(self.path, new_manifest(), 0)
        self.assertEqual(written['revision'], 1)
        self.assertEqual(parallel_state.read_manifest(self.path), written)

    def test_every_write_bumps_the_revision(self):
        first = parallel_state.write_manifest(self.path, new_manifest(), 0)
        second = parallel_state.write_manifest(self.path, first, first['revision'])
        self.assertEqual(second['revision'], 2)
        self.assertEqual(parallel_state.read_manifest(self.path), second)

    def test_a_missing_manifest_is_refused(self):
        rejects(self, 'missing_manifest', lambda: parallel_state.read_manifest(self.path))

    def test_a_corrupt_file_is_refused_and_never_clobbered(self):
        self.path.write_text('{"schema": 1,', encoding='utf-8')
        rejects(self, 'corrupt_manifest', lambda: parallel_state.read_manifest(self.path))
        rejects(self, 'corrupt_manifest',
                lambda: parallel_state.write_manifest(self.path, new_manifest(), 0))
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{"schema": 1,')

    def test_a_non_object_file_is_refused(self):
        self.path.write_text('[]', encoding='utf-8')
        rejects(self, 'invalid_manifest', lambda: parallel_state.read_manifest(self.path))

    def test_a_truncated_manifest_is_refused(self):
        good = json.dumps(new_manifest(), indent=2, sort_keys=True) + '\n'
        self.path.write_text(good[:len(good) // 2], encoding='utf-8')
        rejects(self, 'corrupt_manifest', lambda: parallel_state.read_manifest(self.path))

    def test_an_unknown_key_wrong_schema_or_wrong_section_is_refused(self):
        for overrides in ({'extra': 1}, {'schema': 2}, {'schema': '1'}, {'revision': -1},
                          {'revision': True}, {'tasks': []}, {'claims': {'seq': 0}},
                          {'claims': []}, {'stop': {'reason': ''}}, {'stop': 'wrong-phase'},
                          {'ticket': 'proj-1'}, {'run_id': 'not-a-uuid'}):
            value = new_manifest(**overrides)
            rejects(self, 'invalid_manifest',
                    lambda value=value: parallel_state.write_manifest(self.path, value, 0))

    def test_a_malformed_task_or_intent_record_is_refused(self):
        for tasks in ({'3': {'state': 'reserved', 'attempt': 1, 'decision': None}},
                      {'3.1': {'state': 'ok', 'attempt': 1, 'decision': None}},
                      {'3.1': {'state': 'reserved', 'attempt': 0, 'decision': None}},
                      {'3.1': {'state': 'reserved', 'attempt': 1}},
                      {'3.1': {'state': 'reserved', 'attempt': 1, 'decision': None, 'x': 1}}):
            value = new_manifest(tasks=tasks)
            rejects(self, 'invalid_manifest',
                    lambda value=value: parallel_state.write_manifest(self.path, value, 0))
        intent = {'intent_id': 'i', 'seq': 1, 'attempt': 1, 'actor': 'artel@run#1:1',
                  'state': 'ok', 'row': None}
        value = new_manifest(claims={'seq': 1, 'intents': [intent]})
        rejects(self, 'invalid_manifest',
                lambda: parallel_state.write_manifest(self.path, value, 0))

    def test_a_stale_revision_is_refused(self):
        written = parallel_state.write_manifest(self.path, new_manifest(), 0)
        rejects(self, 'stale_revision',
                lambda: parallel_state.write_manifest(self.path, new_manifest(), 0))
        self.assertEqual(parallel_state.read_manifest(self.path), written)
        # A value carried over from an older read is stale even when the file is current.
        rejects(self, 'stale_revision',
                lambda: parallel_state.write_manifest(self.path, new_manifest(),
                                                      written['revision']))

    def test_a_malformed_expected_revision_is_refused(self):
        for expected in (-1, True, '0', 0.0, None):
            rejects(self, 'invalid_revision',
                    lambda expected=expected: parallel_state.write_manifest(
                        self.path, new_manifest(), expected))

    def test_a_failed_atomic_write_keeps_the_previous_manifest(self):
        written = parallel_state.write_manifest(self.path, new_manifest(), 0)
        with mock.patch.object(parallel_state.os, 'replace',
                               side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                parallel_state.write_manifest(self.path, written, written['revision'])
        self.assertEqual(parallel_state.read_manifest(self.path), written)
        leftovers = [p.name for p in self.dir.iterdir()
                     if p.name not in ('manifest.json', 'manifest.json.lock')]
        self.assertEqual(leftovers, [], 'the temporary file was not cleaned up')
        retried = parallel_state.write_manifest(self.path, written, written['revision'])
        self.assertEqual(retried['revision'], 2)

    def test_the_write_is_a_sibling_rename(self):
        calls = []
        real = parallel_state.os.replace

        def record(source, target):
            calls.append((Path(source), Path(target)))
            return real(source, target)

        with mock.patch.object(parallel_state.os, 'replace', side_effect=record):
            parallel_state.write_manifest(self.path, new_manifest(), 0)
        self.assertEqual(len(calls), 1)
        source, target = calls[0]
        self.assertEqual(source.parent, self.dir)
        self.assertEqual(target, self.path)
        self.assertTrue(source.name.startswith('.manifest.json.'))


class TestTransitionTask(unittest.TestCase):
    def at(self, state):
        """A manifest with TASK in `state`, walked from admission."""
        manifest = parallel_state.transition_task(new_manifest(), TASK, None, 'reserved', {})
        current = 'reserved'
        for target in WALKS[state]:
            manifest = advance(manifest, current, target)
            current = target
        self.assertEqual(manifest['tasks'][TASK]['state'], state)
        return manifest

    def test_the_transition_table_is_frozen(self):
        self.assertEqual(parallel_state.TRANSITIONS, LEGAL)

    def test_the_main_sequence_advances_and_keeps_the_attempt(self):
        manifest = new_manifest()
        manifest = parallel_state.transition_task(manifest, TASK, None, 'reserved', {})
        self.assertEqual(manifest['tasks'][TASK],
                         {'state': 'reserved', 'attempt': 1, 'decision': None})
        current = 'reserved'
        for target in ('preparing', 'running', 'verifying/reviewing', 'prepared',
                       'integrating', 'published'):
            manifest = parallel_state.transition_task(manifest, TASK, current, target, {})
            self.assertEqual(manifest['tasks'][TASK]['state'], target)
            self.assertEqual(manifest['tasks'][TASK]['attempt'], 1)
            current = target

    def test_every_legal_pair_advances_and_every_other_pair_is_refused(self):
        for source in ALL_STATES:
            for target in ALL_STATES:
                manifest = self.at(source)
                if target in LEGAL[source]:
                    result = advance(manifest, source, target)
                    self.assertEqual(result['tasks'][TASK]['state'], target)
                else:
                    rejects(self, 'illegal_transition',
                            lambda manifest=manifest, source=source, target=target:
                            advance(manifest, source, target))

    def test_a_preparation_failure_returns_to_reserved_without_a_new_attempt(self):
        manifest = self.at('preparing')
        decision = {'reason': 'the worker session could not be created'}
        result = parallel_state.transition_task(manifest, TASK, 'preparing', 'reserved',
                                                {'decision': decision})
        self.assertEqual(result['tasks'][TASK], {'state': 'reserved', 'attempt': 1,
                                                 'decision': decision})

    def test_a_preparation_failure_without_a_decision_is_refused(self):
        manifest = self.at('preparing')
        rejects(self, 'invalid_fields',
                lambda: parallel_state.transition_task(manifest, TASK, 'preparing', 'reserved',
                                                       {}))

    def test_a_side_entry_records_the_decision_that_produced_it(self):
        manifest = self.at('running')
        decision = {'reason': 'the native session failed', 'detail': 'interrupted'}
        result = parallel_state.transition_task(manifest, TASK, 'running', 'failed',
                                                {'decision': decision})
        self.assertEqual(result['tasks'][TASK],
                         {'state': 'failed', 'attempt': 1, 'decision': decision})

    def test_a_side_entry_without_a_decision_is_refused(self):
        for source in ('reserved', 'preparing', 'running', 'verifying/reviewing', 'prepared',
                       'integrating'):
            manifest = self.at(source)
            for target in ('blocked', 'failed', 'cancelled'):
                if target not in LEGAL[source]:
                    continue
                rejects(self, 'invalid_fields',
                        lambda manifest=manifest, source=source, target=target:
                        parallel_state.transition_task(manifest, TASK, source, target, {}))

    def test_a_retry_increments_the_attempt_and_keeps_the_identity(self):
        for source in ('blocked', 'failed', 'cancelled'):
            manifest = self.at(source)
            decision = {'reason': 'retry after {}'.format(source)}
            result = parallel_state.transition_task(manifest, TASK, source, 'reserved',
                                                    {'decision': decision})
            self.assertEqual(result['tasks'][TASK],
                             {'state': 'reserved', 'attempt': 2, 'decision': decision})
            self.assertEqual(sorted(result['tasks']), sorted(manifest['tasks']))

    def test_a_retry_without_a_decision_is_refused(self):
        for source in ('blocked', 'failed', 'cancelled'):
            manifest = self.at(source)
            rejects(self, 'invalid_fields',
                    lambda manifest=manifest, source=source:
                    parallel_state.transition_task(manifest, TASK, source, 'reserved', {}))

    def test_a_task_is_admitted_as_reserved_and_only_as_reserved(self):
        manifest = new_manifest()
        rejects(self, 'illegal_transition',
                lambda: parallel_state.transition_task(manifest, TASK, None, 'preparing', {}))
        rejects(self, 'unknown_state',
                lambda: parallel_state.transition_task(manifest, TASK, None, 'ok', {}))
        admitted = parallel_state.transition_task(manifest, TASK, None, 'reserved', {})
        rejects(self, 'state_mismatch',
                lambda: parallel_state.transition_task(admitted, TASK, None, 'reserved', {}))

    def test_an_unknown_or_mismatched_state_is_refused(self):
        manifest = self.at('running')
        rejects(self, 'unknown_state',
                lambda: parallel_state.transition_task(manifest, TASK, 'ok', 'preparing', {}))
        rejects(self, 'unknown_state',
                lambda: parallel_state.transition_task(manifest, TASK, 'running', 'ok', {}))
        rejects(self, 'state_mismatch',
                lambda: parallel_state.transition_task(manifest, TASK, 'preparing', 'running', {}))
        rejects(self, 'state_mismatch',
                lambda: parallel_state.transition_task(new_manifest(), TASK, 'running',
                                                       'verifying/reviewing', {}))

    def test_a_malformed_task_id_is_refused(self):
        manifest = new_manifest()
        for task in ('3', 'Task 3.1', '3.1.2', 'a.b', '', 7, None, ' 3.1'):
            rejects(self, 'invalid_task',
                    lambda task=task: parallel_state.transition_task(manifest, task, None,
                                                                     'reserved', {}))

    def test_unknown_fields_or_a_malformed_decision_are_refused(self):
        manifest = self.at('running')
        rejects(self, 'invalid_fields',
                lambda: parallel_state.transition_task(manifest, TASK, 'running', 'failed',
                                                       {'note': 'x'}))
        rejects(self, 'invalid_fields',
                lambda: parallel_state.transition_task(manifest, TASK, 'running', 'failed',
                                                       {'decision': {}}))
        rejects(self, 'invalid_fields',
                lambda: parallel_state.transition_task(manifest, TASK, 'running', 'failed',
                                                       {'decision': 'failed'}))
        rejects(self, 'invalid_fields',
                lambda: parallel_state.transition_task(manifest, TASK, 'running', 'failed', []))

    def test_a_decision_on_a_normal_transition_is_recorded(self):
        manifest = self.at('running')
        decision = {'reason': 'the gate passed'}
        result = parallel_state.transition_task(manifest, TASK, 'running', 'verifying/reviewing',
                                                {'decision': decision})
        self.assertEqual(result['tasks'][TASK]['decision'], decision)

    def test_the_reducer_returns_a_new_manifest_and_never_mutates_its_input(self):
        manifest = self.at('running')
        before = json.loads(json.dumps(manifest))
        result = parallel_state.transition_task(manifest, TASK, 'running', 'failed',
                                                {'decision': {'reason': 'x'}})
        self.assertEqual(manifest, before)
        self.assertIsNot(result, manifest)
        self.assertIsNot(result['tasks'], manifest['tasks'])
        self.assertEqual(result['revision'], manifest['revision'])


class RepoCase(unittest.TestCase):
    """A real repository plus one linked worktree, both of one common Git directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(os.path.realpath(self._tmp.name))
        self.repo = base / 'host'
        git(base, 'init', '-q', str(self.repo))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.repo, 'config', key, value)
        (self.repo / 'app.txt').write_text('v1\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'init')
        self.linked = base / 'linked'
        git(self.repo, 'worktree', 'add', '-q', str(self.linked), '-b', 'linked')

    def lock_path(self, repo=None):
        common = git(repo or self.repo, 'rev-parse', '--path-format=absolute',
                     '--git-common-dir')
        return Path(common) / 'artel' / 'parallel' / (TICKET + '.json')

    def lock_record(self, repo=None):
        return json.loads(self.lock_path(repo).read_text(encoding='utf-8'))


class TestAcquireTicket(RepoCase):
    def test_acquire_writes_the_lock_under_the_git_common_directory(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        self.assertEqual(sorted(stored), sorted(['schema', 'ticket', 'run_id', 'phase',
                                                 'coordinator_id', 'token', 'acquired_at',
                                                 'recovered_at', 'prior_coordinator_id',
                                                 'recovery_reason']))
        self.assertEqual(stored['ticket'], TICKET)
        self.assertEqual(stored['run_id'], RUN_ID)
        self.assertEqual(stored['phase'], 2)
        self.assertEqual(stored['coordinator_id'], COORDINATOR)
        self.assertIsNone(stored['recovered_at'])
        self.assertIsNone(stored['prior_coordinator_id'])
        self.assertIsNone(stored['recovery_reason'])
        self.assertEqual(len(stored['token']), 32)
        self.assertTrue(stored['token'].isalnum())
        self.assertTrue(stored['acquired_at'].endswith('Z'))
        self.assertEqual(self.lock_record(), stored)

    def test_a_competing_linked_worktree_is_refused_and_changes_nothing(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        rejects(self, 'ticket_owned',
                lambda: parallel_state.acquire_ticket(str(self.linked), TICKET,
                                                      owner(run_id=OTHER_RUN)))
        self.assertEqual(self.lock_record(), stored)

    def test_a_foreign_run_or_phase_is_refused(self):
        parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        rejects(self, 'ticket_owned',
                lambda: parallel_state.acquire_ticket(str(self.repo), TICKET,
                                                      owner(run_id=OTHER_RUN)))
        rejects(self, 'ticket_owned',
                lambda: parallel_state.acquire_ticket(str(self.repo), TICKET,
                                                      owner(phase=None)))

    def test_a_different_coordinator_of_the_same_run_is_refused(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        rejects(self, 'coordinator_conflict',
                lambda: parallel_state.acquire_ticket(
                    str(self.linked), TICKET, owner(coordinator_id=OTHER_COORDINATOR)))
        self.assertEqual(self.lock_record(), stored)

    def test_the_owning_run_reacquires_its_own_record_from_the_other_checkout(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        again = parallel_state.acquire_ticket(str(self.linked), TICKET, owner())
        self.assertEqual(again, stored)
        self.assertEqual(again['token'], stored['token'])

    def test_a_stale_lock_is_never_taken_over(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        stale = dict(stored, acquired_at='2020-01-01T00:00:00Z')
        self.lock_path().write_text(json.dumps(stale), encoding='utf-8')
        os.utime(str(self.lock_path()), (0, 0))
        rejects(self, 'ticket_owned',
                lambda: parallel_state.acquire_ticket(str(self.linked), TICKET,
                                                      owner(run_id=OTHER_RUN)))
        self.assertEqual(self.lock_record(), stale)

    def test_an_unreadable_lock_is_ambiguous_and_never_overwritten(self):
        path = self.lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema": 1,', encoding='utf-8')
        rejects(self, 'ambiguous_owner',
                lambda: parallel_state.acquire_ticket(str(self.repo), TICKET, owner()))
        self.assertEqual(path.read_text(encoding='utf-8'), '{"schema": 1,')
        path.write_text(json.dumps(dict(owner(), token='')), encoding='utf-8')
        rejects(self, 'ambiguous_owner',
                lambda: parallel_state.acquire_ticket(str(self.repo), TICKET, owner()))

    def test_acquire_outside_a_repository_is_refused(self):
        outside = Path(os.path.realpath(self._tmp.name)) / 'outside'
        outside.mkdir()
        rejects(self, 'not_a_repository',
                lambda: parallel_state.acquire_ticket(str(outside), TICKET, owner()))

    def test_the_ticket_and_owner_request_are_validated(self):
        for ticket in ('proj-1', 'PROJ', '', 7):
            rejects(self, 'invalid_ticket',
                    lambda ticket=ticket: parallel_state.acquire_ticket(str(self.repo), ticket,
                                                                        owner()))
        for overrides in ({'schema': 2}, {'run_id': 'not-a-uuid'}, {'phase': 0},
                          {'phase': True}, {'ticket': 'PROJ-9'}, {'coordinator_id': ''},
                          {'coordinator_id': 7}):
            rejects(self, 'invalid_owner',
                    lambda overrides=overrides: parallel_state.acquire_ticket(
                        str(self.repo), TICKET, owner(**overrides)))
        for request in ({}, {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': 2,
                             'extra': 1},
                        {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': 2,
                         'token': 'x'}):
            rejects(self, 'invalid_owner',
                    lambda request=request: parallel_state.acquire_ticket(
                        str(self.repo), TICKET, request))


class TestLegacyLock(RepoCase):
    """A lock written before coordinator identity existed is never silently adopted."""

    LEGACY = {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': 2,
              'token': 'f' * 32, 'acquired_at': '2026-01-01T00:00:00Z'}

    def write_legacy(self):
        path = self.lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.LEGACY, indent=2) + '\n', encoding='utf-8')
        return path

    def test_acquire_validate_and_release_refuse_a_legacy_record_unchanged(self):
        path = self.write_legacy()
        before = path.read_text(encoding='utf-8')
        rejects(self, 'legacy_owner',
                lambda: parallel_state.acquire_ticket(str(self.repo), TICKET, owner()))
        rejects(self, 'legacy_owner',
                lambda: parallel_state.validate_owner(str(self.repo), TICKET, RUN_ID, 2,
                                                      COORDINATOR))
        rejects(self, 'legacy_owner',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request(self.LEGACY)))
        self.assertEqual(path.read_text(encoding='utf-8'), before)

    def test_explicit_recovery_adopts_a_legacy_record_with_its_prior_identity_recorded(self):
        path = self.write_legacy()
        record = parallel_state.recover_ticket(str(self.repo), TICKET,
                                               recover_request(self.LEGACY,
                                                               reason='legacy adoption'))
        self.assertEqual(record['coordinator_id'], OTHER_COORDINATOR)
        self.assertIsNone(record['prior_coordinator_id'])
        self.assertEqual(record['recovery_reason'], 'legacy adoption')
        self.assertTrue(record['recovered_at'].endswith('Z'))
        self.assertNotEqual(record['token'], self.LEGACY['token'])
        self.assertEqual(record['acquired_at'], self.LEGACY['acquired_at'])
        self.assertEqual(self.lock_record(), record)
        # The explicit adoption is a real handoff: the adopted coordinator now owns it.
        again = parallel_state.acquire_ticket(
            str(self.repo), TICKET, owner(coordinator_id=OTHER_COORDINATOR))
        self.assertEqual(again, record)


class TestRecoverTicket(RepoCase):
    def test_recovery_rotates_the_token_and_fences_the_old_coordinator(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        record = parallel_state.recover_ticket(str(self.repo), TICKET,
                                               recover_request(stored, reason='handoff'))
        self.assertEqual(record['run_id'], RUN_ID)
        self.assertEqual(record['phase'], 2)
        self.assertEqual(record['coordinator_id'], OTHER_COORDINATOR)
        self.assertEqual(record['prior_coordinator_id'], COORDINATOR)
        self.assertEqual(record['recovery_reason'], 'handoff')
        self.assertNotEqual(record['token'], stored['token'])
        self.assertEqual(record['acquired_at'], stored['acquired_at'])
        # the replaced coordinator can no longer acquire, validate, mutate or release
        rejects(self, 'coordinator_conflict',
                lambda: parallel_state.acquire_ticket(str(self.repo), TICKET, owner()))
        rejects(self, 'coordinator_conflict',
                lambda: parallel_state.validate_owner(str(self.repo), TICKET, RUN_ID, 2,
                                                      COORDINATOR))
        # the rotated token refuses the old release as a foreign owner, never as a release
        rejects(self, 'foreign_owner',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request(stored)))
        self.assertEqual(self.lock_record(), record)
        # the new coordinator is the current owner and can release with its own token
        parallel_state.validate_owner(str(self.repo), TICKET, RUN_ID, 2, OTHER_COORDINATOR)
        receipt = parallel_state.release_ticket(str(self.repo), TICKET,
                                                release_request(record))
        self.assertTrue(receipt['released'])
        self.assertFalse(self.lock_path().exists())

    def test_recovery_never_crosses_runs_phases_or_the_same_coordinator(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        rejects(self, 'foreign_owner',
                lambda: parallel_state.recover_ticket(
                    str(self.repo), TICKET,
                    dict(recover_request(stored), run_id=OTHER_RUN)))
        rejects(self, 'foreign_owner',
                lambda: parallel_state.recover_ticket(
                    str(self.repo), TICKET, dict(recover_request(stored), phase=None)))
        rejects(self, 'coordinator_current',
                lambda: parallel_state.recover_ticket(
                    str(self.repo), TICKET, recover_request(stored, coordinator=COORDINATOR)))
        for request in (dict(recover_request(stored), reason=''),
                        dict(recover_request(stored), extra=1),
                        dict(recover_request(stored), coordinator_id='')):
            rejects(self, 'invalid_owner',
                    lambda request=request: parallel_state.recover_ticket(
                        str(self.repo), TICKET, request))
        self.assertEqual(self.lock_record(), stored)

    def test_recovery_without_a_lock_is_refused(self):
        rejects(self, 'not_owned',
                lambda: parallel_state.recover_ticket(str(self.repo), TICKET,
                                                      recover_request(owner())))


class TestCoordinatorGuard(RepoCase):
    def test_the_guard_excludes_a_second_holder_and_recovers_on_release(self):
        with mock.patch.object(parallel_state, 'GUARD_TIMEOUT', 0.2):
            with parallel_state.ticket_guard(str(self.repo), TICKET):
                with self.assertRaises(parallel_state.StateError) as caught:
                    with parallel_state.ticket_guard(str(self.linked), TICKET):
                        pass
                self.assertEqual(caught.exception.kind, 'owner_busy')
        with parallel_state.ticket_guard(str(self.repo), TICKET):
            pass  # released: a later holder acquires normally

    def test_concurrent_manifest_writes_serialize_the_compare_and_replace(self):
        path = self.repo / 'manifest.json'
        first = parallel_state.write_manifest(path, new_manifest(), 0)
        self.assertEqual(first['revision'], 1)
        left = new_manifest(tasks={'3.1': {'state': 'reserved', 'attempt': 1,
                                           'decision': None}}, revision=1)
        right = new_manifest(tasks={'3.2': {'state': 'reserved', 'attempt': 1,
                                            'decision': None}}, revision=1)
        arrivals = []
        arrival_lock = threading.Lock()
        both = threading.Event()
        real_atomic = parallel_state._atomic_write

        def barrier_atomic(path, text):
            # Only reachable after the revision check: pre-fix both writers arrive here and
            # both replace, losing one update. Post-fix the manifest guard admits one.
            with arrival_lock:
                arrivals.append(1)
                if len(arrivals) >= 2:
                    both.set()
            both.wait(1.0)
            return real_atomic(path, text)

        results = []
        results_lock = threading.Lock()

        def writer(value):
            try:
                written = parallel_state.write_manifest(path, value, 1)
                outcome = ('ok', written)
            except parallel_state.StateError as exc:
                outcome = ('error', exc.kind)
            with results_lock:
                results.append(outcome)

        with mock.patch.object(parallel_state, '_atomic_write', barrier_atomic):
            threads = [threading.Thread(target=writer, args=(value,))
                       for value in (left, right)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        successes = [entry for entry in results if entry[0] == 'ok']
        errors = [entry[1] for entry in results if entry[0] == 'error']
        self.assertEqual(len(results), 2)
        self.assertEqual(len(successes), 1)
        self.assertEqual(errors, ['stale_revision'])
        stored = parallel_state.read_manifest(path)
        self.assertEqual(stored['revision'], 2)
        self.assertEqual(stored, successes[0][1])


class TestReleaseTicket(RepoCase):
    def test_release_removes_only_the_matching_completed_quiescent_owner(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        rejects(self, 'foreign_owner',
                lambda: parallel_state.release_ticket(str(self.linked), TICKET,
                                                      dict(release_request(stored),
                                                           run_id=OTHER_RUN)))
        rejects(self, 'foreign_owner',
                lambda: parallel_state.release_ticket(
                    str(self.repo), TICKET, dict(release_request(stored), token='0' * 32)))
        rejects(self, 'premature_release',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request(stored, completed=False)))
        rejects(self, 'premature_release',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request(stored, quiescent=False)))
        self.assertEqual(self.lock_record(), stored)
        receipt = parallel_state.release_ticket(str(self.repo), TICKET, release_request(stored))
        self.assertEqual(receipt, {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': 2,
                                   'released': True})
        self.assertFalse(self.lock_path().exists())

    def test_a_pause_keeps_ownership(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        rejects(self, 'premature_release',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request(stored, completed=False,
                                                                      quiescent=False)))
        self.assertTrue(self.lock_path().exists())

    def test_release_without_a_lock_is_refused(self):
        rejects(self, 'not_owned',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request({})))

    def test_release_never_removes_a_corrupt_lock(self):
        path = self.lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('not json', encoding='utf-8')
        rejects(self, 'ambiguous_owner',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      release_request({})))
        self.assertEqual(path.read_text(encoding='utf-8'), 'not json')

    def test_release_from_the_other_checkout_frees_the_ticket_for_a_new_run(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        parallel_state.release_ticket(str(self.linked), TICKET, release_request(stored))
        other = parallel_state.acquire_ticket(str(self.linked), TICKET, owner(run_id=OTHER_RUN))
        self.assertEqual(other['run_id'], OTHER_RUN)
        self.assertEqual(self.lock_record(), other)

    def test_the_release_request_is_validated(self):
        stored = parallel_state.acquire_ticket(str(self.repo), TICKET, owner())
        for overrides in ({'completed': 1}, {'quiescent': 'yes'}, {'completed': None},
                          {'token': ''}, {'ticket': 'PROJ-9'}, {'schema': 2},
                          {'run_id': 'not-a-uuid'}, {'phase': 0}):
            request = dict(release_request(stored), **overrides)
            rejects(self, 'invalid_owner',
                    lambda request=request: parallel_state.release_ticket(
                        str(self.repo), TICKET, request))
        rejects(self, 'invalid_owner',
                lambda: parallel_state.release_ticket(str(self.repo), TICKET,
                                                      dict(release_request(stored), extra=1)))
        self.assertEqual(self.lock_record(), stored)


if __name__ == '__main__':
    unittest.main()
