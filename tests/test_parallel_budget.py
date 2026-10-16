"""Task 10 fix rounds 1-2: the durable correction budget (`scripts/parallel_budget.py`).

Contract: docs/autonomous-run.md §2 (`run-state.json`, `counters`) and §5 (`MAX_TOTAL_
CORRECTION_ROUNDS = 8` authoritative in `counters.correction_rounds`, preserved across
resume), docs/opencode-parallel.md §13.12, and the reviewed Task 10 findings.

The budget is the EXISTING run-state counter, never a second ledger: these tests drive the
real CLI as a subprocess against a temporary host checkout and re-read the persisted bytes
after every step, including restarts, a crash before the write, a lost reply, concurrent
reservations and a linked coordinator root. Nothing here mocks the counter.

Ownership boundary (fix round 2, review B1): `budget reserve` is a ticket-owned mutation —
the CLI matches the repository-common ticket lock before the helper runs, while
`parallel_budget.reserve` itself stays the trusted-caller half. Positive CLI reservations here
acquire the real lock; `TestTrustedHelperBoundary` pins the split.
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
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_budget  # noqa: E402

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
    """A temporary Git host checkout with a config, an armed run-state and a real ticket lock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(os.path.realpath(self._tmp.name)) / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'test@example.com')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'commit.gpgsign', 'false')
        (self.repo / 'app.txt').write_text('v1\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'init')
        self.write_config()

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, config=None):
        path = self.repo / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config or {'ticket': {'projectKey': 'PROJ'}}),
                        encoding='utf-8')
        return path

    def lock_path(self):
        """The repository-common ticket lock, resolved the way the CLI resolves it."""
        common = git(self.repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        return Path(common) / 'artel' / 'parallel' / (TICKET + '.json')

    def head(self):
        return git(self.repo, 'rev-parse', 'HEAD')

    def acquire(self, run_id=RUN_ID, phase=PHASE):
        """Take the real ticket lock through the CLI (the production ownership path)."""
        return self.ok('owner', 'acquire', request={
            'repo': str(self.repo), 'ticket': TICKET,
            'owner': {'schema': 1, 'ticket': TICKET, 'run_id': run_id, 'phase': phase}})

    def manifest_path(self, run_id=RUN_ID):
        return (self.repo / '.artel' / 'run' / TICKET / 'parallel' / run_id /
                'manifest.json')

    def write_manifest(self, run_id=RUN_ID):
        """A recognizable manifest blob, so a refusal can be proven not to touch it."""
        path = self.manifest_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema': 1, 'ticket': TICKET, 'run_id': run_id,
                                    'fixture': True}, indent=2) + '\n', encoding='utf-8')
        return path

    def snapshot(self):
        """Every durable byte an owner refusal must leave exactly as it was."""
        state = self.state_path()
        lock = self.lock_path()
        manifest = self.manifest_path()
        return {
            'state': state.read_bytes() if state.is_file() else None,
            'lock': lock.read_bytes() if lock.is_file() else None,
            'manifest': manifest.read_bytes() if manifest.is_file() else None,
            'head': self.head(),
        }

    def assert_snapshot(self, before):
        state = self.state_path()
        lock = self.lock_path()
        manifest = self.manifest_path()
        self.assertEqual(state.read_bytes() if state.is_file() else None, before['state'])
        self.assertEqual(lock.read_bytes() if lock.is_file() else None, before['lock'])
        self.assertEqual(manifest.read_bytes() if manifest.is_file() else None,
                         before['manifest'])
        self.assertEqual(self.head(), before['head'])

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
        self.acquire()  # the reservation is owner-gated first; keep that from masking the point
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
    def setUp(self):
        super().setUp()
        # every reservation is a ticket-owned mutation: hold the real lock (B1)
        self.acquire()

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
        # Fixture ownership transfer, not a permission grant: a ticket is owned by exactly one
        # run at a time, so the second run can only reach the budget after the first released
        # the lock. The fixture drops the lock record to simulate that release and then takes
        # it through the real `owner acquire` path for the second run; the counter must not be
        # reset for the new identity.
        self.lock_path().unlink()
        self.acquire(run_id=OTHER_RUN)
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
        # no worker-launch effect: nothing outside the run directory (or Git's own metadata,
        # which holds the ticket lock) was created
        outside = sorted(p.relative_to(self.repo).as_posix()
                         for p in self.repo.rglob('*')
                         if p.is_file() and not p.relative_to(self.repo).as_posix()
                         .startswith(('.artel/run/', '.git/')))
        self.assertEqual(outside, ['.artel/config.json', 'app.txt'])

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
        space = Path(os.path.realpath(self._tmp.name)) / 'space'
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
        # the linked coordinator takes the repository-common lock itself; the reservation
        # without it is refused (the ownership path is the real one, not a fixture shortcut)
        self.assertEqual(self.reserve_error(1)['kind'], 'not_owned')
        self.acquire()
        lock = self.lock_path()
        self.assertTrue(str(lock).startswith(str(main / '.git')),
                        'the ticket lock is not in the repository-common Git directory')
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


class TestReserveOwnership(BudgetCase):
    """Review B1: `budget reserve` is a ticket-owned mutation like every other CLI mutation.

    A refusal must leave the run state, the reservation metadata (none is ever written), the
    manifest, the lock and HEAD byte-identical; the helper's own refusals are reached only
    when the exact run owns the ticket. The fixture states below are explicit foreign/ambiguous
    holders, never a permission grant to this run.
    """

    def armed(self):
        self.write_state()
        self.write_manifest()

    def test_a_missing_lock_refuses_not_owned(self):
        self.armed()
        before = self.snapshot()
        error = self.reserve_error(1)
        self.assertEqual(error['kind'], 'not_owned')
        self.assert_snapshot(before)

    def test_a_lock_held_by_another_run_refuses_foreign(self):
        self.armed()
        # the fixture installs an explicitly foreign holder record: another run owns the
        # ticket; nothing here grants this run permission
        self.lock_path().parent.mkdir(parents=True, exist_ok=True)
        self.lock_path().write_text(json.dumps({
            'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN, 'phase': PHASE,
            'token': 'f' * 32, 'acquired_at': '2026-01-01T00:00:00Z'}), encoding='utf-8')
        before = self.snapshot()
        error = self.reserve_error(1)
        self.assertEqual(error['kind'], 'foreign_owner')
        self.assert_snapshot(before)

    def test_a_request_phase_that_is_not_the_lock_phase_refuses_foreign(self):
        self.armed()
        self.acquire()  # the lock is this run's, at phase 2
        before = self.snapshot()
        error = self.reserve_error(1, phase=PHASE + 1)
        self.assertEqual(error['kind'], 'foreign_owner')
        self.assert_snapshot(before)

    def test_an_unreadable_lock_refuses_ambiguous(self):
        self.armed()
        self.lock_path().parent.mkdir(parents=True, exist_ok=True)
        self.lock_path().write_text('{not json', encoding='utf-8')
        before = self.snapshot()
        error = self.reserve_error(1)
        self.assertEqual(error['kind'], 'ambiguous_owner')
        self.assert_snapshot(before)

    def test_invalid_request_shape_precedes_ownership(self):
        # no lock at all: a malformed request is invalid input, never `not_owned`
        self.armed()
        for bad in ({'attempt': '1'}, {'ordinal': None}, {'expected_revision': 'x'},
                    {'run_id': 7}):
            with self.subTest(bad=bad):
                self.assertEqual(self.reserve_error(2, **bad)['kind'], 'invalid_request')
        self.assertEqual(self.refused(2, 'budget', 'reserve', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
            'kind': 'review', 'scope': '2.1', 'attempt': 1, 'ordinal': 1})['kind'],
            'invalid_request')

    def test_a_matching_owned_run_charges_once_and_replays_once(self):
        self.armed()
        self.acquire()
        first = self.reserve()
        self.assertFalse(first['replayed'])
        self.assertEqual(first['correction_rounds'], 1)
        replay = self.reserve(expected_revision=self.inspect()['revision'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['correction_rounds'], 1)
        self.assertEqual(len(self.read_state()['counters'][
            'correction_reservations']['rounds']), 1)


class TestTrustedHelperBoundary(BudgetCase):
    """The helper half is a trusted-caller API: `parallel_budget.reserve` itself reads no
    ticket lock -- the CLI's `budget reserve` is the ownership boundary (review B1). This pins
    the split so a future refactor cannot move the check out of the CLI without the contract
    and the docs moving with it."""

    def test_the_helper_reserves_for_a_trusted_caller_while_the_cli_refuses_without_a_lock(self):
        self.write_state()
        data = parallel_budget.reserve(str(self.repo), TICKET, RUN_ID, PHASE, 'review', '2.1',
                                       1, 1, 0)
        self.assertFalse(data['replayed'])
        self.assertEqual(data['correction_rounds'], 1)
        # the same identity through the CLI without the ticket lock is refused before the
        # helper (a fresh round identity, so this is not the idempotent replay)
        error = self.reserve_error(1, scope='2.2')
        self.assertEqual(error['kind'], 'not_owned')
        self.assertEqual(self.counter(), 1)


if __name__ == '__main__':
    unittest.main()
