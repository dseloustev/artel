"""Task 10 fix round 1: the durable correction budget (`scripts/parallel_budget.py`).

Contract: docs/autonomous-run.md §2 (`run-state.json`, `counters`) and §5 (`MAX_TOTAL_
CORRECTION_ROUNDS = 8` authoritative in `counters.correction_rounds`, preserved across
resume), docs/opencode-parallel.md §13.12, and the reviewed Task 10 findings.

The budget is the EXISTING run-state counter, never a second ledger: these tests drive the
real CLI as a subprocess against a temporary host checkout and re-read the persisted bytes
after every step, including restarts, a crash before the write, a lost reply, concurrent
reservations and a linked coordinator root. Nothing here mocks the counter.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'scripts' / 'parallel.py'
TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d'
PHASE = 2
CAP = 8


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


class BudgetCase(unittest.TestCase):
    """A temporary host checkout with a config and an armed run-state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(os.path.realpath(self._tmp.name)) / 'repo'
        self.repo.mkdir()
        self.write_config()

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, config=None):
        path = self.repo / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config or {'ticket': {'projectKey': 'PROJ'}}),
                        encoding='utf-8')
        return path

    def state_path(self, ticket=TICKET):
        return self.repo / '.artel' / 'run' / ticket / 'run-state.json'
    def state(self, **overrides):
        value = {
            'ticket': '{}-{}'.format(TICKET, PHASE) if overrides.pop('phased', True) else TICKET,
            'run_active': True,
            'completed': False,
            'pause_reason': None,
            'started_at': '2026-10-16T00:00:00Z',
            'counters': {'verify': 0, 'review_rounds': 0, 'escalations': 0,
                         'correction_rounds': 0},
            'effective_mode': 'yolo',
            'gates_confirmed': ['TASKLIST_READY'],
            'deviation_files': [],
            'mode_reasons': ['test fixture'],
        }
        value.update(overrides)
        return value

    def write_state(self, value=None):
        """Write the run-state at the ticket-top-level path; its `ticket` field carries the
        phase suffix (docs/autonomous-run.md §2), the directory never does."""
        value = self.state() if value is None else value
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        return path

    def read_state(self):
        return json.loads(self.state_path().read_text(encoding='utf-8'))

    # --- the CLI ------------------------------------------------------------------------

    def cli(self, *argv, request=None, stdin=False):
        command = [sys.executable, str(CLI)] + [str(a) for a in argv]
        payload = None
        if request is not None:
            if stdin:
                command += ['--request', '-']
                payload = json.dumps(request)
            else:
                handle, name = tempfile.mkstemp(dir=str(self.repo.parent), suffix='.json')
                with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                    stream.write(json.dumps(request))
                command += ['--request', name]
        return subprocess.run(command, cwd=str(self.repo), input=payload,
                              capture_output=True, text=True, timeout=120)

    def envelope(self, *argv, **kwargs):
        proc = self.cli(*argv, **kwargs)
        envelope = None
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                try:
                    envelope = json.loads(line)
                except ValueError:
                    envelope = None
                break
        self.assertIsInstance(envelope, dict, proc.stderr or proc.stdout)
        return proc.returncode, envelope

    def ok(self, *argv, **kwargs):
        code, envelope = self.envelope(*argv, **kwargs)
        self.assertEqual(code, 0, envelope)
        self.assertTrue(envelope['ok'], envelope)
        return envelope['data']

    def refused(self, code, *argv, **kwargs):
        got, envelope = self.envelope(*argv, **kwargs)
        self.assertEqual(got, code, envelope)
        self.assertFalse(envelope['ok'], envelope)
        return envelope['error']

    # --- the budget commands ------------------------------------------------------------

    def inspect(self, phase=PHASE, ticket=TICKET, **kwargs):
        return self.ok('budget', 'inspect', request={'repo': str(self.repo), 'ticket': ticket,
                                                     'phase': phase}, **kwargs)

    def reserve(self, kind='review', scope='2.1', attempt=1, ordinal=1, expected_revision=0,
                run_id=RUN_ID, phase=PHASE, ticket=TICKET, **kwargs):
        request = {'repo': str(self.repo), 'ticket': ticket, 'run_id': run_id, 'phase': phase,
                   'kind': kind, 'scope': scope, 'attempt': attempt, 'ordinal': ordinal,
                   'expected_revision': expected_revision}
        return self.ok('budget', 'reserve', request=request, **kwargs)

    def reserve_error(self, code=1, **kwargs):
        request = {'repo': str(self.repo), 'ticket': kwargs.pop('ticket', TICKET),
                   'run_id': kwargs.pop('run_id', RUN_ID), 'phase': kwargs.pop('phase', PHASE),
                   'kind': kwargs.pop('kind', 'review'), 'scope': kwargs.pop('scope', '2.1'),
                   'attempt': kwargs.pop('attempt', 1), 'ordinal': kwargs.pop('ordinal', 1),
                   'expected_revision': kwargs.pop('expected_revision', 0)}
        request.update(kwargs.pop('request', {}))
        return self.refused(code, 'budget', 'reserve', request=request, **kwargs)

    def revision(self):
        return self.read_state()['counters']['correction_reservations']['revision']

    def counter(self):
        return self.read_state()['counters']['correction_rounds']


# ============================================================================================
# inspect and the persisted counter
# ============================================================================================

class TestInspect(BudgetCase):
    def test_inspect_reports_the_existing_counter_and_the_scope_caps(self):
        state = self.state()
        state['counters']['correction_rounds'] = 3
        self.write_state(state)
        before = self.state_path().read_bytes()
        data = self.inspect()
        self.assertEqual(data['cap'], CAP)
        self.assertEqual(data['correction_rounds'], 3)
        self.assertEqual(data['remaining'], 5)
        self.assertEqual(data['revision'], 0)
        self.assertEqual(data['reservations'], [])
        self.assertEqual(data['scope_caps']['review'],
                         {'task': 1, 'ticket': 3, 'phase': 3})
        self.assertEqual(data['scope_caps']['runtime'], {'ticket': 1, 'phase': 1})
        self.assertEqual(data['scope_caps']['checkpoint'], {'ticket': 2, 'phase': 2})
        self.assertEqual(self.state_path().read_bytes(), before, 'inspect wrote bytes')

    def test_inspect_accepts_stdin_and_a_file_request(self):
        self.write_state()
        from_stdin = self.inspect(stdin=True)
        from_file = self.inspect()
        self.assertEqual(from_stdin, from_file)

    def test_a_missing_or_corrupt_or_foreign_run_state_fails_closed(self):
        self.assertEqual(self.refused(1, 'budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE})['kind'],
            'missing_run_state')
        error = self.reserve_error(1)
        self.assertEqual(error['kind'], 'missing_run_state')
        self.assertFalse((self.repo / '.artel' / 'run').exists(),
                         'a refused reservation created run state')
        self.write_state()
        self.state_path().write_text('{not json', encoding='utf-8')
        self.assertEqual(self.refused(2, 'budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE})['kind'],
            'corrupt_run_state')
        self.write_state(self.state(ticket='OTHER-9'))
        self.assertEqual(self.refused(1, 'budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE})['kind'],
            'run_state_mismatch')
        state = self.state()
        state['run_active'] = False
        self.write_state(state)
        self.assertEqual(self.refused(1, 'budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE})['kind'],
            'run_not_active')


# ============================================================================================
# reserving: charges, replays and the caps
# ============================================================================================

class TestReserve(BudgetCase):
    def test_a_new_round_charges_the_existing_counter_and_preserves_everything_else(self):
        state = self.state()
        state['counters']['correction_rounds'] = 3
        state['counters']['verify'] = 2
        state['custom'] = {'kept': True}
        self.write_state(state)
        data = self.reserve()
        self.assertFalse(data['replayed'])
        self.assertEqual(data['correction_rounds'], 4)
        self.assertEqual(data['remaining'], 4)
        self.assertEqual(data['revision'], 1)
        stored = self.read_state()
        self.assertEqual(stored['counters']['correction_rounds'], 4)
        self.assertEqual(stored['counters']['verify'], 2)
        self.assertEqual(stored['custom'], {'kept': True})
        self.assertEqual(stored['mode_reasons'], ['test fixture'])
        block = stored['counters']['correction_reservations']
        self.assertEqual(block['schema'], 1)
        self.assertEqual(block['revision'], 1)
        self.assertEqual(len(block['rounds']), 1)
        record = block['rounds'][0]
        self.assertEqual(record['key'], 'review:2.1:a1:r1')
        self.assertEqual(record['ticket'], TICKET)
        self.assertEqual(record['run_id'], RUN_ID)
        self.assertEqual(record['phase'], PHASE)
        self.assertTrue(record['reserved_at'])

    def test_the_same_stable_round_replays_across_a_restart_for_free(self):
        self.write_state()
        first = self.reserve()
        before = self.state_path().read_bytes()
        # a restarted coordinator inspects fresh, then replays the same identity and round
        revision = self.inspect()['revision']
        replay = self.reserve(expected_revision=revision)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['correction_rounds'], 1)
        self.assertEqual(replay['reservation'], first['reservation'])
        self.assertEqual(self.state_path().read_bytes(), before)

    def test_a_lost_reply_costs_one_round_and_the_replay_costs_none(self):
        self.write_state()
        self.reserve()  # the reservation landed; the reply was lost
        self.assertEqual(self.counter(), 1)
        self.reserve(expected_revision=self.revision())  # the exact replay
        self.assertEqual(self.counter(), 1)
        self.assertEqual(len(self.read_state()['counters'][
            'correction_reservations']['rounds']), 1)

    def test_a_crash_before_the_write_costs_nothing_then_charges_once(self):
        self.write_state()
        self.reserve()
        # the crash window: a caller that never saw the write replays its stale revision
        error = self.reserve_error(1, expected_revision=0)
        self.assertEqual(error['kind'], 'stale_revision')
        self.assertEqual(self.counter(), 1)
        # the fresh resume charges the next round identity exactly once
        second = self.reserve(kind='checkpoint', scope='phase-2', ordinal=1,
                              expected_revision=self.revision())
        self.assertEqual(second['correction_rounds'], 2)

    def test_several_rounds_of_one_scope_count_separately(self):
        self.write_state()
        self.reserve(kind='checkpoint', scope='phase-2', ordinal=1)
        self.reserve(kind='checkpoint', scope='phase-2', ordinal=2,
                     expected_revision=self.revision())
        self.assertEqual(self.counter(), 2)
        self.assertEqual(len(self.read_state()['counters'][
            'correction_reservations']['rounds']), 2)

    def test_the_same_round_under_another_run_identity_charges_again(self):
        self.write_state()
        self.reserve()
        again = self.reserve(run_id=OTHER_RUN, expected_revision=self.revision())
        self.assertFalse(again['replayed'])
        self.assertEqual(again['correction_rounds'], 2)

    def test_every_scope_shares_the_run_wide_cap(self):
        self.write_state()
        rounds = [('review', '2.1', 1, 1), ('review', '2.2', 1, 1),
                  ('review', 'ticket', 1, 1), ('review', 'ticket', 1, 2),
                  ('runtime', 'phase-2', 1, 1), ('checkpoint', 'phase-2', 1, 1),
                  ('checkpoint', 'phase-2', 1, 2), ('review', 'phase-2', 1, 1)]
        for kind, scope, attempt, ordinal in rounds:
            self.reserve(kind=kind, scope=scope, attempt=attempt, ordinal=ordinal,
                         expected_revision=self.inspect()['revision'])
        self.assertEqual(self.counter(), CAP)
        before = self.state_path().read_bytes()
        error = self.reserve_error(1, kind='runtime', scope='ticket', ordinal=1,
                                   expected_revision=self.revision())
        self.assertEqual(error['kind'], 'budget_exhausted')
        self.assertEqual(self.state_path().read_bytes(), before)
        # no worker-launch effect: nothing outside the run directory was created
        outside = sorted(p.relative_to(self.repo).as_posix()
                         for p in self.repo.rglob('*')
                         if p.is_file() and not p.relative_to(self.repo).as_posix()
                         .startswith('.artel/run/'))
        self.assertEqual(outside, ['.artel/config.json'])

    def test_a_fabricated_extra_round_is_refused_by_its_scope_cap(self):
        self.write_state()
        self.reserve(kind='review', scope='2.1', ordinal=1)
        error = self.reserve_error(1, kind='review', scope='2.1', ordinal=2,
                                   expected_revision=self.revision())
        self.assertEqual(error['kind'], 'scope_cap')
        error = self.reserve_error(1, kind='review', scope='ticket', ordinal=4,
                                   expected_revision=self.revision())
        self.assertEqual(error['kind'], 'scope_cap')
        allowed = self.reserve(kind='review', scope='ticket', ordinal=2,
                               expected_revision=self.revision())
        self.assertFalse(allowed['replayed'])
        self.assertEqual(allowed['correction_rounds'], 2)

    def test_a_stale_revision_and_invalid_input_fail_before_the_write(self):
        self.write_state()
        self.reserve()
        before = self.state_path().read_bytes()
        error = self.reserve_error(1, expected_revision=0)
        self.assertEqual(error['kind'], 'stale_revision')
        self.assertEqual(self.state_path().read_bytes(), before)
        for bad in ({'kind': 'bogus'}, {'scope': 'nowhere'}, {'attempt': 0}, {'ordinal': 0},
                    {'expected_revision': -1}):
            with self.subTest(bad=bad):
                kwargs = dict(bad)
                expected_revision = kwargs.pop('expected_revision', self.revision())
                error = self.reserve_error(2, expected_revision=expected_revision, **kwargs)
                self.assertEqual(error['kind'], 'invalid_request', error)
                self.assertEqual(self.state_path().read_bytes(), before)

    def test_the_run_state_and_lock_live_under_the_linked_coordinator_root(self):
        space = Path(self._tmp.name) / 'space'
        space.mkdir()
        main = space / 'main'
        git(space, 'init', '-q', str(main))
        git(main, 'config', 'user.email', 'test@example.com')
        git(main, 'config', 'user.name', 'Test')
        (main / 'app.txt').write_text('v1\n', encoding='utf-8')
        git(main, 'add', '.')
        git(main, 'commit', '-q', '-m', 'init')
        linked = space / 'linked'
        git(main, 'worktree', 'add', '-q', '-b', 'feature/PROJ-123', str(linked))
        self.repo = linked
        self.write_config()
        self.write_state()
        data = self.reserve()
        self.assertFalse(data['replayed'])
        self.assertTrue((linked / '.artel' / 'run' / TICKET / 'run-state.json').is_file())
        self.assertTrue((linked / '.artel' / 'run' / TICKET / 'run-state.json.lock').is_file())
        self.assertFalse((main / '.artel').exists(),
                         'the repository main checkout was written')

    def test_simultaneous_reservations_serialize_without_lost_updates(self):
        self.write_state()
        rounds = [('review', '2.1', 1), ('review', '2.2', 1), ('review', 'ticket', 1),
                  ('runtime', 'phase-2', 1), ('checkpoint', 'phase-2', 1),
                  ('checkpoint', 'phase-2', 2)]
        results = {}
        errors = []

        def reserve(index, kind, scope, ordinal):
            for _ in range(40):
                revision = self.inspect()['revision']
                request = {'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
                           'phase': PHASE, 'kind': kind, 'scope': scope, 'attempt': 1,
                           'ordinal': ordinal, 'expected_revision': revision}
                code, envelope = self.envelope('budget', 'reserve', request=request)
                if code == 0:
                    results[index] = envelope['data']
                    return
                if envelope['error']['kind'] != 'stale_revision':
                    errors.append(envelope['error'])
                    return
            errors.append({'kind': 'retries_exhausted'})

        threads = [threading.Thread(target=reserve, args=(index,) + entry)
                   for index, entry in enumerate(rounds)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), len(rounds))
        self.assertEqual(self.counter(), len(rounds))
        self.assertLessEqual(self.counter(), CAP)
        keys = sorted(record['key'] for record in self.read_state()['counters'][
            'correction_reservations']['rounds'])
        self.assertEqual(len(keys), len(set(keys)))
        revisions = [data['revision'] for data in results.values()]
        self.assertEqual(sorted(revisions), list(range(1, len(rounds) + 1)))


if __name__ == '__main__':
    unittest.main()
