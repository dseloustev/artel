"""scripts/parallel_queue.py: the coordinator's claim intents, returned-row records and
restart reconciliation.

Contract: docs/opencode-parallel.md §13 (claim recovery) and docs/task-queue.md §7 (the
parallel claim order). The reducers are pure -- they never call the queue; the tests drive
them through fake external boundaries (a fake claim call and a fake worker launcher) so the
durability order the coordinator must keep is observable rather than asserted in prose.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_queue  # noqa: E402
import parallel_state  # noqa: E402

RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
TICKET = 'PROJ-123'
DIGEST = 'a' * 64

INTENT_KEYS = ('intent_id', 'seq', 'attempt', 'actor', 'state', 'row', 'task')
ROW_KEYS = ('task_id', 'title', 'status', 'actor', 'phase', 'task')


def manifest(phase=2, run_id=RUN_ID):
    return parallel_state.new_run(TICKET, phase, run_id, 2, DIGEST)


def row(**overrides):
    """A normalized queue row: the observation the coordinator records."""
    value = {'task_id': 41, 'title': 'I2 · 3.1 · Guard the null wallet',
             'status': 'in_progress', 'actor': None, 'phase': 2, 'task': '3.1'}
    value.update(overrides)
    return value


def begin(value=None, task=None):
    """A manifest with one pending claim intent, plus the intent itself."""
    return parallel_queue.begin_claim(value if value is not None else manifest(), task)


def claimed(value=None, **row_overrides):
    """A manifest with one recorded (mapped) claim, the intent and the action."""
    prepared = begin(value)
    returned = row(actor=prepared['intent']['actor'], **row_overrides)
    recorded = parallel_queue.record_claim(prepared['manifest'],
                                           prepared['intent']['intent_id'], returned)
    return prepared, recorded, returned


def rejects(test, kind, call):
    """Assert `call` raises QueueError of `kind`; return the exception."""
    with test.assertRaises(parallel_queue.QueueError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind)
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


class TestBeginClaim(unittest.TestCase):
    def test_the_intent_is_the_documented_shape(self):
        result = begin()
        intent = result['intent']
        self.assertEqual(sorted(intent), sorted(INTENT_KEYS))
        self.assertEqual(intent['intent_id'], RUN_ID + '#1:1')
        self.assertEqual(intent['seq'], 1)
        self.assertEqual(intent['attempt'], 1)
        self.assertEqual(intent['actor'], 'artel@' + RUN_ID + '#1:1')
        self.assertEqual(intent['state'], 'pending')
        self.assertIsNone(intent['row'])
        self.assertIsNone(intent['task'])
        self.assertEqual(result['manifest']['claims'], {'seq': 1, 'intents': [intent]})

    def test_the_reducer_returns_a_new_manifest_and_never_mutates_its_input(self):
        value = manifest()
        result = begin(value)
        self.assertEqual(value['claims'], {'seq': 0, 'intents': []})
        self.assertIsNot(result['manifest'], value)
        self.assertIsNot(result['manifest']['claims'], value['claims'])
        self.assertEqual(result['manifest']['revision'], value['revision'])

    def test_the_actor_carries_the_run_the_sequence_and_the_attempt(self):
        other_run = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'
        other = begin(manifest(run_id=other_run))
        self.assertEqual(other['intent']['actor'], 'artel@' + other_run + '#1:1')
        self.assertTrue(other['intent']['actor'].startswith('artel@' + other_run + '#'))

    def test_a_second_claim_opens_the_next_sequence(self):
        _, recorded, _ = claimed()
        result = parallel_queue.begin_claim(recorded['manifest'])
        self.assertEqual(result['intent']['seq'], 2)
        self.assertEqual(result['intent']['attempt'], 1)
        self.assertEqual(result['intent']['actor'], 'artel@' + RUN_ID + '#2:1')
        self.assertEqual(result['manifest']['claims']['seq'], 2)

    def test_a_pending_intent_blocks_another_claim(self):
        prepared = begin()
        rejects(self, 'unresolved_claim',
                lambda: parallel_queue.begin_claim(prepared['manifest']))

    def test_a_reconciled_lost_claim_retries_its_own_sequence(self):
        prepared = begin()
        reconciled = parallel_queue.reconcile_claims(prepared['manifest'], [])
        self.assertEqual(reconciled['actions'], [])
        self.assertEqual(reconciled['manifest']['claims']['intents'][0]['state'], 'lost')
        retried = parallel_queue.begin_claim(reconciled['manifest'])
        self.assertEqual(retried['intent']['intent_id'], RUN_ID + '#1:2')
        self.assertEqual(retried['intent']['actor'], 'artel@' + RUN_ID + '#1:2')
        again = parallel_queue.reconcile_claims(retried['manifest'], [])
        third = parallel_queue.begin_claim(again['manifest'])
        self.assertEqual(third['intent']['intent_id'], RUN_ID + '#1:3')

    def test_a_released_orphan_opens_the_next_sequence(self):
        prepared = begin()
        reconciled = parallel_queue.reconcile_claims(
            prepared['manifest'], [row(actor=prepared['intent']['actor'])])
        self.assertEqual(reconciled['manifest']['claims']['intents'][0]['state'], 'orphaned')
        # An unconfirmed release blocks a new claim...
        rejects(self, 'unresolved_claim',
                lambda: parallel_queue.begin_claim(reconciled['manifest']))
        # ...and a fresh observation proving the exact row is no longer held confirms it.
        released = parallel_queue.reconcile_claims(
            reconciled['manifest'], [row(actor=prepared['intent']['actor'], status='done')])
        self.assertEqual(released['manifest']['claims']['intents'][0]['state'], 'released')
        self.assertEqual(released['actions'], [])
        result = parallel_queue.begin_claim(released['manifest'])
        self.assertEqual(result['intent']['seq'], 2)
        self.assertEqual(result['intent']['attempt'], 1)

    def test_a_latched_stop_refuses_admission(self):
        prepared = begin()
        stopped = parallel_queue.record_claim(prepared['manifest'],
                                              prepared['intent']['intent_id'],
                                              row(actor=prepared['intent']['actor'], phase=3))
        rejects(self, 'admission_stopped',
                lambda: parallel_queue.begin_claim(stopped['manifest']))


class TestRecordClaim(unittest.TestCase):
    def test_the_returned_row_is_recorded_and_the_run_action_names_it(self):
        prepared = begin()
        returned = row(actor=prepared['intent']['actor'])
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'], returned)
        intent = result['manifest']['claims']['intents'][0]
        self.assertEqual(intent['state'], 'mapped')
        self.assertEqual(intent['row'], returned)
        self.assertEqual(result['action'], {'kind': 'run', 'task_id': 41, 'task': '3.1',
                                            'claim': prepared['intent']['actor'],
                                            'row': returned})
        self.assertEqual(result['manifest']['revision'], prepared['manifest']['revision'])
        self.assertIsNone(result['manifest']['stop'])

    def test_the_reducer_never_mutates_its_input(self):
        prepared = begin()
        before = prepared['manifest']['claims']['intents'][0]
        parallel_queue.record_claim(prepared['manifest'], prepared['intent']['intent_id'],
                                    row(actor=prepared['intent']['actor']))
        self.assertEqual(prepared['manifest']['claims']['intents'][0], before)
        self.assertEqual(before['state'], 'pending')

    def test_a_foreign_holder_stops_and_is_never_adopted_or_cleared(self):
        prepared = begin()
        returned = row(actor='artel@other-host')
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'], returned)
        intent = result['manifest']['claims']['intents'][0]
        self.assertEqual(intent['state'], 'foreign')
        self.assertEqual(intent['row'], returned)
        self.assertEqual(result['action']['kind'], 'stop')
        self.assertEqual(result['action']['reason'], 'unexpected-holder')
        self.assertEqual(result['manifest']['stop']['reason'], 'unexpected-holder')
        self.assertEqual(result['manifest']['stop']['row'], returned)
        rejects(self, 'admission_stopped',
                lambda: parallel_queue.begin_claim(result['manifest']))

    def test_a_row_without_a_holder_is_not_adopted(self):
        prepared = begin()
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'], row(actor=None))
        self.assertEqual(result['action']['kind'], 'stop')
        self.assertEqual(result['manifest']['claims']['intents'][0]['state'], 'foreign')

    def test_the_claim_pins_the_admitted_task(self):
        # The coordinator admits a task from the grammar, then claims it: the intent carries
        # the task, so a row for another one cannot be worked as if it were admitted.
        prepared = begin(task='3.1')
        self.assertEqual(prepared['intent']['task'], '3.1')
        returned = row(actor=prepared['intent']['actor'], task='3.1')
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'], returned)
        self.assertEqual(result['action']['kind'], 'run')
        self.assertEqual(result['action']['task'], '3.1')

    def test_a_wrong_task_row_is_released_once_then_admission_stops(self):
        prepared = begin(task='3.1')
        returned = row(actor=prepared['intent']['actor'], task='2.1')
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'], returned)
        intent = result['manifest']['claims']['intents'][0]
        self.assertEqual(intent['state'], 'wrong-task')
        self.assertEqual(intent['row'], returned)
        self.assertEqual(result['action']['reason'], 'wrong-task')
        self.assertEqual(result['action']['task_id'], 41)
        self.assertEqual(result['manifest']['stop']['reason'], 'wrong-task')
        rejects(self, 'admission_stopped',
                lambda: parallel_queue.begin_claim(result['manifest']))

    def test_a_task_that_is_not_a_task_number_is_refused_before_any_call(self):
        rejects(self, 'invalid_task', lambda: parallel_queue.begin_claim(manifest(), 'nope'))

    def test_a_wrong_phase_row_is_released_once_then_admission_stops(self):
        prepared = begin(manifest(phase=2))
        returned = row(actor=prepared['intent']['actor'], phase=3)
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'], returned)
        intent = result['manifest']['claims']['intents'][0]
        self.assertEqual(intent['state'], 'wrong-phase')
        self.assertEqual(intent['row'], returned)
        self.assertEqual(result['action'], {'kind': 'release', 'reason': 'wrong-phase',
                                            'task_id': 41, 'row': returned,
                                            'intent_id': prepared['intent']['intent_id']})
        self.assertEqual(result['manifest']['stop']['reason'], 'wrong-phase')
        rejects(self, 'admission_stopped',
                lambda: parallel_queue.begin_claim(result['manifest']))

    def test_a_phase_scoped_run_refuses_a_row_without_a_phase(self):
        prepared = begin(manifest(phase=2))
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'],
                                             row(actor=prepared['intent']['actor'], phase=None))
        self.assertEqual(result['action']['kind'], 'release')
        self.assertEqual(result['manifest']['claims']['intents'][0]['state'], 'wrong-phase')

    def test_a_ticket_wide_run_accepts_any_phase(self):
        prepared = begin(manifest(phase=None))
        result = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'],
                                             row(actor=prepared['intent']['actor'], phase=7))
        self.assertEqual(result['action']['kind'], 'run')

    def test_an_unknown_or_resolved_intent_is_refused(self):
        prepared = begin()
        rejects(self, 'unknown_intent',
                lambda: parallel_queue.record_claim(prepared['manifest'], 'nope',
                                                    row(actor=prepared['intent']['actor'])))
        mapped = parallel_queue.record_claim(prepared['manifest'],
                                             prepared['intent']['intent_id'],
                                             row(actor=prepared['intent']['actor']))
        rejects(self, 'intent_resolved',
                lambda: parallel_queue.record_claim(mapped['manifest'],
                                                    prepared['intent']['intent_id'],
                                                    row(actor=prepared['intent']['actor'])))

    def test_the_row_shape_is_strict(self):
        prepared = begin()
        for overrides in ({'task_id': 0}, {'task_id': True}, {'task_id': '41'},
                          {'title': ''}, {'title': 'a\nb'}, {'status': ''}, {'status': 7},
                          {'actor': ''}, {'actor': 7}, {'phase': 0}, {'phase': True},
                          {'task': '3'}, {'task': 'x.y'}, {'task': 7}):
            value = row(actor=prepared['intent']['actor'])
            value.update(overrides)
            rejects(self, 'invalid_row',
                    lambda value=value: parallel_queue.record_claim(
                        prepared['manifest'], prepared['intent']['intent_id'], value))
        for value in (None, [], 'row', 7):
            rejects(self, 'invalid_row',
                    lambda value=value: parallel_queue.record_claim(
                        prepared['manifest'], prepared['intent']['intent_id'], value))
        missing = row(actor=prepared['intent']['actor'])
        del missing['task']
        rejects(self, 'invalid_row',
                lambda: parallel_queue.record_claim(prepared['manifest'],
                                                    prepared['intent']['intent_id'], missing))
        extra = row(actor=prepared['intent']['actor'], note='x')
        rejects(self, 'invalid_row',
                lambda: parallel_queue.record_claim(prepared['manifest'],
                                                    prepared['intent']['intent_id'], extra))


class TestReconcileClaims(unittest.TestCase):
    def test_a_pending_intent_with_its_row_is_orphaned_and_released(self):
        prepared = begin()
        held = row(actor=prepared['intent']['actor'])
        result = parallel_queue.reconcile_claims(prepared['manifest'], [held])
        intent = result['manifest']['claims']['intents'][0]
        self.assertEqual(intent['state'], 'orphaned')
        self.assertEqual(intent['row'], held)
        self.assertEqual(result['actions'],
                         [{'kind': 'release', 'reason': 'orphaned-claim', 'task_id': 41,
                           'row': held, 'intent_id': prepared['intent']['intent_id']}])

    def test_a_pending_intent_without_a_row_is_lost(self):
        prepared = begin()
        result = parallel_queue.reconcile_claims(prepared['manifest'], [])
        self.assertEqual(result['manifest']['claims']['intents'][0]['state'], 'lost')
        self.assertEqual(result['actions'], [])

    def test_a_foreign_holder_is_never_reclaimed(self):
        prepared = begin()
        foreign = row(task_id=42, actor='artel@other-host')
        result = parallel_queue.reconcile_claims(prepared['manifest'], [foreign])
        self.assertEqual(result['manifest']['claims']['intents'][0]['state'], 'lost')
        self.assertEqual(result['actions'], [])
        self.assertIsNone(result['manifest']['stop'])
        again = parallel_queue.reconcile_claims(
            claimed()[1]['manifest'], [foreign, row(actor='artel@' + RUN_ID + '#9:9')])
        self.assertEqual([action['task_id'] for action in again['actions']], [41])

    def test_a_row_held_by_this_run_with_an_unknown_actor_is_released(self):
        prepared = begin()
        unknown = row(actor='artel@' + RUN_ID + '#9:1')
        result = parallel_queue.reconcile_claims(prepared['manifest'], [unknown])
        self.assertEqual(result['actions'],
                         [{'kind': 'release', 'reason': 'orphaned-claim', 'task_id': 41,
                           'row': unknown, 'intent_id': None}])

    def test_a_live_mapped_claim_is_never_released(self):
        prepared, recorded, returned = claimed()
        result = parallel_queue.reconcile_claims(recorded['manifest'], [returned])
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['manifest']['claims']['intents'][0]['state'], 'mapped')

    def test_a_wrong_phase_orphan_releases_once_and_latches_the_stop(self):
        prepared = begin(manifest(phase=2))
        held = row(actor=prepared['intent']['actor'], phase=3)
        result = parallel_queue.reconcile_claims(prepared['manifest'], [held])
        self.assertEqual(result['actions'][0]['kind'], 'release')
        self.assertEqual(result['manifest']['stop']['reason'], 'wrong-phase')
        rejects(self, 'admission_stopped',
                lambda: parallel_queue.begin_claim(result['manifest']))

    def test_an_unexecuted_wrong_phase_release_is_reissued_from_fresh_evidence(self):
        # record_claim persisted the decision and the stop, and the process died before the
        # external release. The fresh observation still shows the exact same row held by the
        # exact same run/sequence/attempt actor, so the outstanding release is planned again
        # -- the decision is recovered, never re-decided, and admission stays stopped.
        prepared = begin(manifest(phase=2))
        held = row(actor=prepared['intent']['actor'], phase=3)
        decided = parallel_queue.record_claim(prepared['manifest'],
                                              prepared['intent']['intent_id'], held)
        self.assertEqual(decided['action']['kind'], 'release')
        recovered = parallel_queue.reconcile_claims(decided['manifest'], [held])
        self.assertEqual(recovered['actions'],
                         [{'kind': 'release', 'reason': 'wrong-phase', 'task_id': 41,
                           'row': held, 'intent_id': prepared['intent']['intent_id']}])
        self.assertEqual(recovered['manifest']['claims']['intents'][0]['state'], 'wrong-phase')
        self.assertEqual(recovered['manifest']['stop'], decided['manifest']['stop'])
        rejects(self, 'admission_stopped',
                lambda: parallel_queue.begin_claim(recovered['manifest']))

    def test_a_wrong_phase_row_is_released_again_only_while_still_observed_held(self):
        # The decision is durable; whether the release executed is read from the queue. The
        # same row still `in_progress` under the same actor proves it did not and the
        # outstanding release is re-issued; a row observed `ready`/unheld proves it did and
        # is never released again.
        prepared = begin(manifest(phase=2))
        held = row(actor=prepared['intent']['actor'], phase=3)
        decided = parallel_queue.record_claim(prepared['manifest'],
                                              prepared['intent']['intent_id'], held)
        again = parallel_queue.reconcile_claims(decided['manifest'], [held])
        self.assertEqual([action['reason'] for action in again['actions']], ['wrong-phase'])
        released = dict(held, status='ready', actor=None)
        after = parallel_queue.reconcile_claims(decided['manifest'], [released])
        self.assertEqual(after['actions'], [])
        self.assertEqual(after['manifest']['stop']['reason'], 'wrong-phase')

    def test_a_wrong_phase_release_never_touches_a_foreign_or_reacquired_holder(self):
        # Only the exact actor holding the exact recorded row is evidence about the
        # outstanding release. A foreign holder, a reacquired holder under a new actor and
        # this run's own actor on a different row are left exactly as observed: no release,
        # no adoption, no holder transfer.
        prepared = begin(manifest(phase=2))
        held = row(actor=prepared['intent']['actor'], phase=3)
        decided = parallel_queue.record_claim(prepared['manifest'],
                                              prepared['intent']['intent_id'], held)
        for observed in (dict(held, actor='artel@other-host'),
                         dict(held, actor='artel@' + RUN_ID + '#2:1'),
                         dict(held, task_id=42)):
            result = parallel_queue.reconcile_claims(decided['manifest'], [observed])
            self.assertEqual(result['actions'], [], observed)
            self.assertEqual(result['manifest']['claims']['intents'][0]['row'], held)
            self.assertEqual(result['manifest']['stop'], decided['manifest']['stop'])

    def test_an_orphaned_claim_is_re_released_while_still_observed_in_progress(self):
        # The sibling discipline the wrong-phase recovery mirrors: an orphaned intent's row
        # is re-released on every fresh observation until the release has executed.
        prepared = begin()
        held = row(actor=prepared['intent']['actor'])
        first = parallel_queue.reconcile_claims(prepared['manifest'], [held])
        second = parallel_queue.reconcile_claims(first['manifest'], [held])
        self.assertEqual([action['reason'] for action in first['actions']], ['orphaned-claim'])
        self.assertEqual(second['actions'], first['actions'])

    def test_a_row_that_is_not_held_is_not_released(self):
        prepared = begin()
        held = row(actor=prepared['intent']['actor'])
        for status in ('ready', 'done', 'blocked', 'backlog'):
            result = parallel_queue.reconcile_claims(prepared['manifest'],
                                                     [dict(held, status=status)])
            self.assertEqual(result['actions'], [], status)

    def test_the_reducer_never_mutates_its_input(self):
        prepared = begin()
        before = prepared['manifest']['claims']['intents'][0]
        parallel_queue.reconcile_claims(prepared['manifest'],
                                        [row(actor=prepared['intent']['actor'])])
        self.assertEqual(prepared['manifest']['claims']['intents'][0], before)

    def test_the_rows_are_validated(self):
        prepared = begin()
        rejects(self, 'invalid_rows',
                lambda: parallel_queue.reconcile_claims(prepared['manifest'], 'rows'))
        rejects(self, 'invalid_row',
                lambda: parallel_queue.reconcile_claims(prepared['manifest'],
                                                        [row(task='nope')]))
        rejects(self, 'invalid_row',
                lambda: parallel_queue.reconcile_claims(prepared['manifest'], [7]))

    def test_a_malformed_manifest_is_refused(self):
        for value in (None, [], {}, {'claims': {}}, dict(manifest(), phase='2')):
            rejects(self, 'invalid_manifest',
                    lambda value=value: parallel_queue.begin_claim(value))


class FakeQueue:
    """The one claim call. It answers a row held by the actor it was given and re-reads
    the manifest, so the test sees what was durable at call time."""

    def __init__(self, path, **row_overrides):
        self.path = path
        self.template = row(**row_overrides)
        self.calls = []
        self.failure = None

    def ready(self, actor):
        self.calls.append({'actor': actor,
                           'manifest': parallel_state.read_manifest(self.path)})
        if self.failure is not None:
            raise self.failure
        return dict(self.template, actor=actor)


class FakeWorkers:
    """The launch boundary. It refuses to launch unless the returned mapping is durable."""

    def __init__(self, path):
        self.path = path
        self.launched = []
        self.refused = []

    def launch(self, action):
        on_disk = parallel_state.read_manifest(self.path)
        mapped = [intent for intent in on_disk['claims']['intents']
                  if intent['state'] == 'mapped']
        if not mapped or mapped[-1]['row'] != action['row']:
            self.refused.append(action)
            return
        self.launched.append({'action': action, 'manifest': on_disk})


class FakeReleaser:
    """The release boundary: it applies a release action to its rows and re-reads the
    manifest, so the test sees what was durable at call time."""

    def __init__(self, path, rows):
        self.path = path
        self.rows = rows
        self.calls = []

    def release(self, action):
        self.calls.append({'action': action,
                           'manifest': parallel_state.read_manifest(self.path)})
        for row in self.rows:
            if row['task_id'] == action['task_id']:
                row['status'] = 'ready'
                row['actor'] = None


class TestClaimDurabilityOrder(unittest.TestCase):
    """The coordinator's write order, driven through fake external boundaries."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(os.path.realpath(tmp.name)) / 'manifest.json'

    def flow(self, written, queue, workers, write_mapping=True):
        """The documented order: persist the intent, claim, persist the mapping, launch."""
        prepared = parallel_queue.begin_claim(written)
        written = parallel_state.write_manifest(self.path, prepared['manifest'],
                                                written['revision'])
        returned = queue.ready(prepared['intent']['actor'])
        recorded = parallel_queue.record_claim(written, prepared['intent']['intent_id'],
                                               returned)
        if write_mapping:
            written = parallel_state.write_manifest(self.path, recorded['manifest'],
                                                    written['revision'])
        workers.launch(recorded['action'])
        return prepared, recorded

    def test_the_intent_is_durable_before_the_claim_and_the_mapping_before_the_launch(self):
        written = parallel_state.write_manifest(self.path, manifest(), 0)
        queue, workers = FakeQueue(self.path), FakeWorkers(self.path)
        prepared, recorded = self.flow(written, queue, workers)
        seen = queue.calls[0]
        self.assertEqual(seen['actor'], prepared['intent']['actor'])
        self.assertEqual(seen['manifest']['claims']['intents'], [prepared['intent']])
        self.assertEqual(seen['manifest']['claims']['intents'][0]['state'], 'pending')
        self.assertEqual(recorded['action']['kind'], 'run')
        self.assertEqual(workers.refused, [])
        self.assertEqual(len(workers.launched), 1)
        launched = workers.launched[0]
        self.assertEqual(launched['action'], recorded['action'])
        self.assertEqual(launched['manifest']['claims']['intents'][0]['state'], 'mapped')
        self.assertEqual(launched['manifest']['claims']['intents'][0]['row'],
                         recorded['action']['row'])

    def test_no_worker_launches_before_the_mapping_is_durable(self):
        written = parallel_state.write_manifest(self.path, manifest(), 0)
        queue, workers = FakeQueue(self.path), FakeWorkers(self.path)
        _, recorded = self.flow(written, queue, workers, write_mapping=False)
        self.assertEqual(workers.launched, [])
        self.assertEqual(workers.refused, [recorded['action']])
        on_disk = parallel_state.read_manifest(self.path)
        self.assertEqual(on_disk['claims']['intents'][0]['state'], 'pending')

    def test_an_unknown_outcome_is_not_retried_before_reconciliation(self):
        written = parallel_state.write_manifest(self.path, manifest(), 0)
        queue = FakeQueue(self.path)
        queue.failure = TimeoutError('the claim call timed out')
        workers = FakeWorkers(self.path)
        prepared = parallel_queue.begin_claim(written)
        written = parallel_state.write_manifest(self.path, prepared['manifest'],
                                                written['revision'])
        with self.assertRaises(TimeoutError):
            queue.ready(prepared['intent']['actor'])
        self.assertEqual(workers.launched, [])
        on_disk = parallel_state.read_manifest(self.path)
        self.assertEqual(on_disk['claims']['intents'][0]['state'], 'pending')
        rejects(self, 'unresolved_claim',
                lambda: parallel_queue.begin_claim(on_disk))
        reconciled = parallel_queue.reconcile_claims(on_disk, [])
        written = parallel_state.write_manifest(self.path, reconciled['manifest'],
                                                on_disk['revision'])
        retried = parallel_queue.begin_claim(written)
        self.assertEqual(retried['intent']['seq'], 1)
        self.assertEqual(retried['intent']['attempt'], 2)

    def test_a_restart_releases_its_own_orphan_and_never_a_foreign_holder(self):
        written = parallel_state.write_manifest(self.path, manifest(), 0)
        prepared = parallel_queue.begin_claim(written)
        written = parallel_state.write_manifest(self.path, prepared['manifest'],
                                                written['revision'])
        # The process died here: the row is held by this run's actor, nothing recorded.
        orphan = row(actor=prepared['intent']['actor'])
        reconciled = parallel_queue.reconcile_claims(written, [orphan])
        self.assertEqual([action['kind'] for action in reconciled['actions']], ['release'])
        self.assertEqual(reconciled['actions'][0]['reason'], 'orphaned-claim')
        written = parallel_state.write_manifest(self.path, reconciled['manifest'],
                                                written['revision'])
        # A foreign holder is never touched, and a second restart finds no own claim: the
        # exact recorded row is no longer held by this run's actor, so the release decision
        # is confirmed as `released` -- and the foreign row is left exactly as observed.
        foreign = parallel_queue.reconcile_claims(written, [row(actor='artel@other-host')])
        self.assertEqual(foreign['actions'], [])
        self.assertEqual(foreign['manifest']['claims']['intents'][0]['state'], 'released')
        resumed = parallel_queue.begin_claim(foreign['manifest'])
        self.assertEqual(resumed['intent']['seq'], 2)

    def test_a_crash_before_the_release_recovers_it_and_a_finished_release_is_not_repeated(self):
        written = parallel_state.write_manifest(self.path, manifest(), 0)
        prepared = parallel_queue.begin_claim(written)
        written = parallel_state.write_manifest(self.path, prepared['manifest'],
                                                written['revision'])
        returned = row(actor=prepared['intent']['actor'], phase=3)
        recorded = parallel_queue.record_claim(written, prepared['intent']['intent_id'],
                                               returned)
        self.assertEqual(recorded['action']['kind'], 'release')
        written = parallel_state.write_manifest(self.path, recorded['manifest'],
                                                written['revision'])
        # The process died here: the wrong-phase decision and the stop are durable, the
        # external release never happened. The fresh observation still shows the exact same
        # row held by the exact same actor, so the outstanding release is planned again.
        releaser = FakeReleaser(self.path, [returned])
        recovered = parallel_queue.reconcile_claims(written, [returned])
        self.assertEqual([action['reason'] for action in recovered['actions']], ['wrong-phase'])
        self.assertEqual(recovered['actions'][0]['intent_id'], prepared['intent']['intent_id'])
        written = parallel_state.write_manifest(self.path, recovered['manifest'],
                                                written['revision'])
        releaser.release(recovered['actions'][0])
        self.assertEqual(releaser.calls[0]['manifest']['stop']['reason'], 'wrong-phase')
        # The release executed: the fresh observation is ready/unheld and nothing repeats.
        after = parallel_queue.reconcile_claims(written, releaser.rows)
        self.assertEqual(after['actions'], [])
        self.assertEqual(after['manifest']['stop']['reason'], 'wrong-phase')
        rejects(self, 'admission_stopped', lambda: parallel_queue.begin_claim(written))


if __name__ == '__main__':
    unittest.main()
