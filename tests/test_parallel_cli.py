"""Task 10: the coordinator CLI (`scripts/parallel.py`) and its operator contract.

Contract: docs/opencode-parallel.md §13.10-§13.11, the approved CLI row of the interface map,
and the reviewed Task 9 handoff. Every command is exercised as a real subprocess against a
temporary Git repository: requests arrive on stdin and from a file, refusals are typed and
leave the durable bytes unchanged, and every mutation is re-opened from disk instead of being
trusted from its own envelope.

The CLI is a dispatcher, not a second implementation: these tests assert the *contract* the
coordinator procedure consumes -- the envelope, the exit codes, the ownership/revision guards
and the persisted artifacts -- never the helpers' internal reducers, which their own modules
already cover.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / 'scripts' / 'parallel.py'
TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d'
PHASE = 2
TASK = '3.1'
BRANCH = 'feature/PROJ-123'
DIGEST = 'a' * 64
CAPABILITIES = {
    'version': '2.0.29',
    'api': {'app.version': True, 'model.list': True, 'session.create': True,
            'session.get': True, 'session.prompt': True, 'session.interrupt': True,
            'session.context': True},
}


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def task_block(number, files=('src/a.py',), deps='none', concurrency=None, done=False):
    head = '### Task {}: work on {}'.format(number, number)
    lines = [head,
             '- **Files:** {}'.format(', '.join('`{}`'.format(path) for path in files)),
             '- **Depends on:** {}'.format(deps),
             '- **Route:** light',
             '- **Test:** none — nothing to test']
    if concurrency:
        lines.append('- **Concurrency:** {}'.format(concurrency))
    lines.append('- [{}] do the work'.format('x' if done else ' '))
    return '\n'.join(lines)


def iteration(number, *blocks):
    return '## Iteration {}: iteration {}\n\n{}'.format(number, number, '\n\n'.join(blocks))


TWO_TASKS = ('# Tasklist\n\n' + iteration(2, task_block('2.1', files=('src/a.py',)),
                                          task_block('2.2', files=('src/b.py',)))
             + '\n\n' + iteration(3, task_block('3.1', files=('src/c.py',))))
CHAIN = ('# Tasklist\n\n' + iteration(2, task_block('2.1'),
                                      task_block('2.2', deps='2.1')))
EXCLUSIVE = ('# Tasklist\n\n' + iteration(
    2, task_block('2.1', concurrency='exclusive — the fixture database is shared'),
    task_block('2.2')))


class CliCase(unittest.TestCase):
    """A temporary repository with the coordinator on a feature branch and its config."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.space = Path(os.path.realpath(self._tmp.name))
        self.make_repo()

    def make_repo(self, name='repo'):
        """A fresh checkout on the feature branch (re-callable for per-state fixtures)."""
        self.repo = self.space / name
        git(self.space, 'init', '-q', str(self.repo))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.repo, 'config', key, value)
        self.write('.gitignore', '.artel/\n')
        self.write('app.txt', 'v1\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'init')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        git(self.repo, 'branch', '-m', 'main')
        git(self.repo, 'checkout', '-q', '-b', BRANCH)
        self.write_config()
        return self.repo

    def tearDown(self):
        self._tmp.cleanup()

    # --- fixture helpers ----------------------------------------------------------------

    def write(self, rel, text, root=None):
        path = (root or self.repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def write_config(self, config=None, root=None):
        value = config if config is not None else {
            'specs': {'dir': 'specs/.current'}, 'ticket': {'projectKey': 'PROJ'},
            'parallel': {'seats': 2}, 'verify': {'commands': []}}
        return self.write('.artel/config.json', json.dumps(value), root=root)

    def manifest_path(self, run_id=RUN_ID):
        return (self.repo / '.artel' / 'run' / TICKET / 'parallel' / run_id /
                'manifest.json')

    def lock_path(self):
        common = git(self.repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        return Path(common) / 'artel' / 'parallel' / (TICKET + '.json')

    def run_state_path(self, ticket=TICKET):
        return self.repo / '.artel' / 'run' / ticket / 'run-state.json'

    def write_run_state(self, completed=True, ticket=None, run_active=False, extra=None):
        """The run-state lives at the ticket-top-level directory; its `ticket` field carries
        the phase suffix (docs/autonomous-run.md §2)."""
        value = {'ticket': ticket or '{}-{}'.format(TICKET, PHASE), 'run_active': run_active,
                 'completed': completed, 'pause_reason': None,
                 'started_at': '2026-10-16T00:00:00Z',
                 'counters': {'verify': 0, 'review_rounds': 0, 'escalations': 0,
                              'correction_rounds': 0},
                 'effective_mode': 'yolo', 'gates_confirmed': ['TASKLIST_READY'],
                 'deviation_files': []}
        value.update(extra or {})
        path = self.run_state_path(TICKET)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        return path

    def record_path(self, task=TASK, attempt=1, run_id=RUN_ID):
        return (self.repo / '.artel' / 'run' / TICKET / 'parallel' / run_id /
                'integration' / '{}-a{}.json'.format(task, attempt))

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    # --- the CLI ------------------------------------------------------------------------

    def cli(self, *argv, request=None, stdin=False, cwd=None, input=None):
        """Run the CLI; `request` goes to a file, or to stdin with `stdin=True`.

        `input` is raw stdin for commands that read their own document from stdin (the
        request then comes from a file).
        """
        command = [sys.executable, str(CLI)] + [str(a) for a in argv]
        payload = input
        if request is not None:
            if stdin:
                command += ['--request', '-']
                payload = json.dumps(request)
            else:
                path = self.space / 'request-{}.json'.format(abs(hash(json.dumps(
                    request, sort_keys=True))) % (10 ** 8))
                path.write_text(json.dumps(request), encoding='utf-8')
                command += ['--request', str(path)]
        proc = subprocess.run(command, cwd=str(cwd or self.repo), input=payload,
                              capture_output=True, text=True, timeout=120)
        envelope = None
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                try:
                    envelope = json.loads(line)
                except ValueError:
                    envelope = None
                break
        return proc, envelope

    def ok(self, *argv, **kwargs):
        proc, envelope = self.cli(*argv, **kwargs)
        self.assertEqual(proc.returncode, 0,
                         '{}: {}'.format(proc.returncode, proc.stderr or proc.stdout))
        self.assertIsInstance(envelope, dict)
        self.assertTrue(envelope['ok'], envelope)
        return envelope['data']

    def refused(self, code, *argv, **kwargs):
        proc, envelope = self.cli(*argv, **kwargs)
        self.assertEqual(proc.returncode, code,
                         '{}: {}'.format(proc.returncode, proc.stderr or proc.stdout))
        self.assertIsInstance(envelope, dict)
        self.assertFalse(envelope['ok'], envelope)
        return envelope['error']

    def kind(self, expected_code, *argv, **kwargs):
        return self.refused(expected_code, *argv, **kwargs)['kind']

    # --- the run's durable state --------------------------------------------------------

    def acquire(self, run_id=RUN_ID, phase=PHASE, owner=None):
        return self.ok('owner', 'acquire', request={
            'repo': str(self.repo), 'ticket': TICKET,
            'owner': owner or {'schema': 1, 'ticket': TICKET, 'run_id': run_id,
                               'phase': phase}})

    def create_run(self, seats=2, run_id=RUN_ID, phase=PHASE):
        return self.ok('run', 'create', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': phase, 'run_id': run_id,
            'seats': seats, 'graph_digest': DIGEST})

    def transition(self, task, expected, target, revision, fields=None, run_id=RUN_ID):
        request = {'repo': str(self.repo), 'ticket': TICKET, 'run_id': run_id,
                   'expected_revision': revision, 'task': task, 'expected': expected,
                   'target': target}
        if fields is not None:
            request['fields'] = fields
        return self.ok('run', 'transition', request=request)

    WALK = ('preparing', 'running', 'verifying/reviewing', 'prepared', 'integrating',
            'published')
    SIDE_PATHS = {'blocked': ('reserved', 'blocked'),
                  'failed': ('reserved', 'preparing', 'failed'),
                  'cancelled': ('reserved', 'cancelled')}

    def walk(self, task=TASK, to='published'):
        """Walk one task from admission to `to` through the real CLI transitions."""
        revision = self.read_json(self.manifest_path())['revision']
        if to == 'reserved':
            steps = ('reserved',)
        elif to in self.WALK:
            steps = ('reserved',) + self.WALK[:self.WALK.index(to) + 1]
        else:
            steps = self.SIDE_PATHS[to]
        expected = None
        for target in steps:
            fields = {'decision': {'reason': 'fixture'}} if target in (
                'blocked', 'failed', 'cancelled') else None
            self.transition(task, expected, target, revision, fields=fields)
            expected = target
            revision += 1
        return revision

    def published_run(self, task=TASK):
        """The lock record plus a fully published manifest and a completed run-state."""
        record = self.acquire()
        self.create_run()
        self.walk(task)
        self.write_run_state(completed=True)
        return record, self.read_json(self.manifest_path())


# ============================================================================================
# routing, envelopes and malformed input
# ============================================================================================

class TestEnvelopeAndRouting(CliCase):
    def test_a_file_request_and_a_stdin_request_are_equivalent(self):
        from_file = self.ok('config', request={'repo': str(self.repo)})
        from_stdin = self.ok('config', request={'repo': str(self.repo)}, stdin=True)
        self.assertEqual(from_file, from_stdin)
        self.assertEqual(from_file, {'seats': 2})

    def test_the_envelope_shape_is_the_deterministic_script_one(self):
        proc, envelope = self.cli('config', request={'repo': str(self.repo)})
        self.assertEqual(sorted(envelope), ['data', 'elapsed_ms', 'ok', 'verb'])
        self.assertEqual(envelope['verb'], 'parallel-config')
        self.assertIsInstance(envelope['elapsed_ms'], int)
        error = self.refused(2, 'owner', 'acquire',
                             request={'repo': str(self.repo), 'ticket': 'not a ticket',
                                      'owner': {'schema': 1, 'ticket': 'not a ticket',
                                                'run_id': RUN_ID, 'phase': PHASE}})
        self.assertEqual(error['kind'], 'invalid_ticket')

    def test_malformed_json_is_an_invalid_argument(self):
        path = self.space / 'broken.json'
        path.write_text('{not json', encoding='utf-8')
        proc, envelope = self.cli('config', '--request', str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['kind'], 'invalid_argument')
        self.assertEqual(envelope['verb'], 'parallel-config')

    def test_a_missing_request_file_is_an_invalid_argument(self):
        proc, envelope = self.cli('config', '--request', str(self.space / 'nope.json'))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(envelope['error']['kind'], 'invalid_argument')

    def test_a_request_must_be_a_json_object(self):
        proc, envelope = self.cli('config', request=['not', 'an', 'object'])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(envelope['error']['kind'], 'invalid_request')

    def test_unknown_and_missing_request_keys_are_refused(self):
        error = self.refused(2, 'run', 'inspect',
                             request={'repo': str(self.repo), 'ticket': TICKET,
                                      'run_id': RUN_ID, 'extra': True})
        self.assertEqual(error['kind'], 'invalid_request')
        error = self.refused(2, 'run', 'inspect',
                             request={'repo': str(self.repo), 'ticket': TICKET})
        self.assertEqual(error['kind'], 'invalid_request')

    def test_wrong_typed_routing_keys_are_refused(self):
        error = self.refused(2, 'run', 'inspect',
                             request={'repo': str(self.repo), 'ticket': TICKET,
                                      'run_id': 7})
        self.assertEqual(error['kind'], 'invalid_request')

    def test_an_unknown_command_or_action_is_refused(self):
        proc, envelope = self.cli('not-a-command', request={})
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['kind'], 'invalid_argument')
        error = self.refused(2, 'owner', 'steal', request={})
        self.assertEqual(error['kind'], 'invalid_argument')

    def test_the_request_flag_is_required(self):
        proc, envelope = self.cli('config')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(envelope['error']['kind'], 'invalid_argument')


# ============================================================================================
# config and preflight: side-effect-free reads
# ============================================================================================

class TestConfig(CliCase):
    def test_an_absent_section_keeps_the_serial_default(self):
        self.write_config({'specs': {'dir': 'specs/.current'}})
        self.assertEqual(self.ok('config', request={'repo': str(self.repo)}), {'seats': 1})

    def test_seats_two_resolves(self):
        self.assertEqual(self.ok('config', request={'repo': str(self.repo)}), {'seats': 2})

    def test_a_boolean_is_not_an_integer(self):
        self.write_config({'parallel': {'seats': True}})
        self.assertEqual(self.kind(2, 'config', request={'repo': str(self.repo)}),
                         'invalid_config')

    def test_zero_a_string_and_a_float_are_configuration_errors(self):
        for value in (0, '2', 2.5):
            with self.subTest(value):
                self.write_config({'parallel': {'seats': value}})
                self.assertEqual(self.kind(2, 'config', request={'repo': str(self.repo)}),
                                 'invalid_config')

    def test_a_missing_config_file_is_the_serial_default(self):
        (self.repo / '.artel' / 'config.json').unlink()
        self.assertEqual(self.ok('config', request={'repo': str(self.repo)}), {'seats': 1})

    def test_config_writes_nothing(self):
        before = sorted(p.relative_to(self.repo).as_posix()
                        for p in self.repo.rglob('*') if p.is_file())
        self.ok('config', request={'repo': str(self.repo)})
        after = sorted(p.relative_to(self.repo).as_posix()
                       for p in self.repo.rglob('*') if p.is_file())
        self.assertEqual(before, after)


class TestPreflight(CliCase):
    def test_a_clean_feature_branch_with_the_capability_report_passes(self):
        data = self.ok('preflight', request={'repo': str(self.repo),
                                             'capabilities': CAPABILITIES})
        self.assertTrue(data['ok'])
        self.assertEqual(data['branch'], BRANCH)
        self.assertEqual(data['head'], git(self.repo, 'rev-parse', 'HEAD'))
        self.assertTrue(data['capabilities']['ok'])

    def test_the_default_branch_is_refused(self):
        git(self.repo, 'checkout', '-q', 'main')
        self.assertEqual(self.kind(1, 'preflight',
                                   request={'repo': str(self.repo),
                                            'capabilities': CAPABILITIES}),
                         'default_branch')

    def test_a_dirty_coordinator_is_refused_and_preserved(self):
        self.write('app.txt', 'dirty\n')
        self.assertEqual(self.kind(1, 'preflight',
                                   request={'repo': str(self.repo),
                                            'capabilities': CAPABILITIES}),
                         'coordinator_dirty')
        self.assertEqual((self.repo / 'app.txt').read_text(encoding='utf-8'), 'dirty\n')

    def test_an_unsupported_capability_report_is_refused_before_effects(self):
        wrong = dict(CAPABILITIES, version='2.0.30')
        self.assertEqual(self.kind(1, 'preflight',
                                   request={'repo': str(self.repo), 'capabilities': wrong}),
                         'unsupported_version')
        missing = dict(CAPABILITIES, api=dict(CAPABILITIES['api'], **{'session.create': False}))
        self.assertEqual(self.kind(1, 'preflight',
                                   request={'repo': str(self.repo), 'capabilities': missing}),
                         'unsupported_api')

    def test_preflight_is_read_only(self):
        common = git(self.repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        self.ok('preflight', request={'repo': str(self.repo), 'capabilities': CAPABILITIES})
        self.assertFalse((Path(common) / 'artel').exists())
        self.assertFalse(self.manifest_path().exists())

    def test_a_non_repository_is_an_environment_failure(self):
        plain = self.space / 'plain'
        plain.mkdir()
        self.assertEqual(self.kind(2, 'preflight', request={
            'repo': str(plain), 'capabilities': CAPABILITIES}), 'not_a_repository')


# ============================================================================================
# ownership
# ============================================================================================

class TestOwner(CliCase):
    def test_acquire_writes_the_repository_common_lock(self):
        record = self.acquire()
        self.assertTrue(self.lock_path().is_file())
        self.assertEqual(self.read_json(self.lock_path()), record)
        self.assertEqual(record['run_id'], RUN_ID)
        self.assertEqual(record['phase'], PHASE)

    def test_re_acquiring_the_same_run_is_idempotent(self):
        first = self.acquire()
        second = self.acquire()
        self.assertEqual(first, second)

    def test_another_run_is_refused(self):
        self.acquire()
        self.assertEqual(self.kind(1, 'owner', 'acquire', request={
            'repo': str(self.repo), 'ticket': TICKET,
            'owner': {'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN, 'phase': PHASE}}),
            'ticket_owned')
        self.assertEqual(self.read_json(self.lock_path())['run_id'], RUN_ID)

    def test_release_needs_the_matching_completed_quiescent_run(self):
        record, _ = self.published_run()
        request = {'repo': str(self.repo), 'ticket': TICKET,
                   'owner': {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
                             'token': record['token'], 'completed': True, 'quiescent': False}}
        self.assertEqual(self.kind(1, 'owner', 'release', request=request),
                         'premature_release')
        self.assertTrue(self.lock_path().is_file())
        request['owner']['quiescent'] = True
        receipt = self.ok('owner', 'release', request=request)
        self.assertTrue(receipt['released'])
        self.assertFalse(self.lock_path().exists())

    def test_a_foreign_token_never_releases(self):
        record, _ = self.published_run()
        request = {'repo': str(self.repo), 'ticket': TICKET,
                   'owner': {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
                             'token': 'f' * 32, 'completed': True, 'quiescent': True}}
        self.assertEqual(self.kind(1, 'owner', 'release', request=request), 'foreign_owner')
        self.assertEqual(self.read_json(self.lock_path())['token'], record['token'])


class TestReleaseSafety(CliCase):
    """Important 1: the caller's `completed`/`quiescent` bytes are never proof — the
    authoritative manifest must be quiescent and the run-state completed for the exact run."""

    def owner(self, token, completed=True, quiescent=True):
        return {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
                'token': token, 'completed': completed, 'quiescent': quiescent}

    def release(self, token, **kwargs):
        request = {'repo': str(self.repo), 'ticket': TICKET,
                   'owner': self.owner(token, **kwargs)}
        return self.cli('owner', 'release', request=request)

    def release_kind(self, token, code=1, **kwargs):
        proc, envelope = self.release(token, **kwargs)
        self.assertEqual(proc.returncode, code, envelope)
        self.assertFalse(envelope['ok'], envelope)
        return envelope['error']['kind']

    def test_forged_completion_flags_cannot_release_an_unfinished_run(self):
        states = ('reserved', 'running', 'verifying/reviewing', 'prepared', 'integrating',
                  'blocked', 'failed', 'cancelled')
        for state in states:
            with self.subTest(state=state):
                self.make_repo('repo-{}'.format(state.replace('/', '-')))
                record = self.acquire()
                self.create_run()
                self.walk(TASK, to=state)
                self.write_run_state(completed=True)  # the serial completion is forged too
                kind = self.release_kind(record['token'])
                self.assertEqual(kind, 'premature_release')
                self.assertTrue(self.lock_path().is_file(), 'the lock was released')

    def test_an_unfinished_owned_claim_blocks_release(self):
        record = self.acquire()
        self.create_run()
        self.ok('claim', 'prepare', request={'repo': str(self.repo), 'ticket': TICKET,
                                             'run_id': RUN_ID, 'expected_revision': 1})
        self.write_run_state(completed=True)
        self.assertEqual(self.release_kind(record['token']), 'premature_release')
        self.assertTrue(self.lock_path().is_file())

    def test_a_mapped_claim_blocks_release_until_its_task_is_published(self):
        record = self.acquire()
        self.create_run()
        intent = self.ok('claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 1})['intent']
        self.ok('claim', 'record', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'intent_id': intent['intent_id'],
            'row': {'task_id': 41, 'title': 'I2 · 3.1 · work', 'status': 'in_progress',
                    'actor': intent['actor'], 'phase': PHASE, 'task': TASK}})
        self.walk(TASK, to='published')
        self.write_run_state(completed=True)
        # the claim's row is completed at publication, so the published task releases it
        receipt = self.ok('owner', 'release', request={
            'repo': str(self.repo), 'ticket': TICKET, 'owner': self.owner(record['token'])})
        self.assertTrue(receipt['released'])
        self.assertFalse(self.lock_path().exists())

    def test_a_mapped_claim_for_an_unpublished_task_blocks_release(self):
        record = self.acquire()
        self.create_run()
        intent = self.ok('claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 1})['intent']
        self.ok('claim', 'record', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'intent_id': intent['intent_id'],
            'row': {'task_id': 41, 'title': 'I2 · 3.1 · work', 'status': 'in_progress',
                    'actor': intent['actor'], 'phase': PHASE, 'task': TASK}})
        self.write_run_state(completed=True)
        self.assertEqual(self.release_kind(record['token']), 'premature_release')
        self.assertTrue(self.lock_path().is_file())

    def test_a_stopped_run_blocks_release(self):
        record = self.acquire()
        self.create_run()
        intent = self.ok('claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 1})['intent']
        self.ok('claim', 'record', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'intent_id': intent['intent_id'],
            'row': {'task_id': 41, 'title': 'I2 · 3.1 · work', 'status': 'in_progress',
                    'actor': 'artel@someone-else#1:1', 'phase': PHASE, 'task': TASK}})
        self.walk(TASK, to='published')
        self.write_run_state(completed=True)
        self.assertEqual(self.release_kind(record['token']), 'premature_release')
        self.assertTrue(self.lock_path().is_file())

    def test_a_missing_corrupt_or_empty_manifest_blocks_release(self):
        record = self.acquire()
        self.assertEqual(self.release_kind(record['token']), 'missing_manifest')
        self.assertTrue(self.lock_path().is_file())
        self.create_run()
        self.manifest_path().write_text('{not json', encoding='utf-8')
        self.assertEqual(self.release_kind(record['token'], code=2), 'corrupt_manifest')
        self.manifest_path().write_text('', encoding='utf-8')
        self.assertEqual(self.release_kind(record['token'], code=2), 'corrupt_manifest')
        self.assertTrue(self.lock_path().is_file())

    def test_a_foreign_manifest_blocks_release(self):
        record = self.acquire()
        self.create_run()
        manifest = self.read_json(self.manifest_path())
        manifest['run_id'] = OTHER_RUN
        self.manifest_path().write_text(json.dumps(manifest, indent=2) + '\n',
                                        encoding='utf-8')
        self.assertEqual(self.release_kind(record['token']), 'manifest_mismatch')
        self.assertTrue(self.lock_path().is_file())

    def test_a_missing_corrupt_or_unfinished_run_state_blocks_release(self):
        record = self.acquire()
        self.create_run()
        self.walk(TASK, to='published')
        self.assertEqual(self.release_kind(record['token']), 'missing_run_state')
        self.assertTrue(self.lock_path().is_file())
        self.run_state_path().write_text('{not json', encoding='utf-8')
        self.assertEqual(self.release_kind(record['token'], code=2), 'corrupt_run_state')
        self.write_run_state(completed=False)
        self.assertEqual(self.release_kind(record['token']), 'premature_release')
        self.write_run_state(completed=True, ticket='OTHER-9')
        self.assertEqual(self.release_kind(record['token']), 'run_state_mismatch')
        self.write_run_state(completed=True, extra={'pause_reason': 'cap-escalation'})
        self.assertEqual(self.release_kind(record['token']), 'premature_release')
        self.assertTrue(self.lock_path().is_file())

    def test_a_completed_quiescent_matching_run_releases(self):
        record, manifest = self.published_run()
        self.assertEqual(manifest['tasks'][TASK]['state'], 'published')
        receipt = self.ok('owner', 'release', request={
            'repo': str(self.repo), 'ticket': TICKET, 'owner': self.owner(record['token'])})
        self.assertTrue(receipt['released'])
        self.assertFalse(self.lock_path().exists())


class TestReleaseFromLinkedCheckout(CliCase):
    """The lock is repository-common; the manifest and run-state resolve under the explicit
    coordinator checkout, which may itself be a linked worktree."""

    def test_a_linked_coordinator_releases_its_own_completed_run(self):
        git(self.repo, 'checkout', '-q', 'main')
        linked = self.space / 'linked'
        git(self.repo, 'worktree', 'add', '-q', str(linked), BRANCH)
        main = self.repo
        self.repo = linked
        self.write_config()
        record = self.acquire()
        self.create_run()
        self.walk(TASK, to='published')
        self.write_run_state(completed=True)
        self.assertTrue((linked / '.artel' / 'run' / TICKET / 'run-state.json').is_file())
        self.assertFalse((main / '.artel' / 'run').exists())
        receipt = self.ok('owner', 'release', request={
            'repo': str(self.repo), 'ticket': TICKET,
            'owner': {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
                      'token': record['token'], 'completed': True, 'quiescent': True}})
        self.assertTrue(receipt['released'])
        self.assertFalse(self.lock_path().exists())


# ============================================================================================
# the run manifest: create, inspect, transition
# ============================================================================================

class TestRunState(CliCase):
    def test_create_persists_the_manifest_and_it_survives_reopen(self):
        self.acquire()
        written = self.create_run()
        self.assertEqual(written['revision'], 1)
        self.assertEqual(written['seats'], 2)
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk, written)
        reopened = self.ok('run', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID})
        self.assertEqual(reopened, on_disk)

    def test_create_without_ownership_has_no_effect(self):
        self.assertEqual(self.kind(1, 'run', 'create', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE, 'run_id': RUN_ID,
            'seats': 2, 'graph_digest': DIGEST}), 'not_owned')
        self.assertFalse(self.manifest_path().exists())

    def test_create_under_a_foreign_lock_has_no_effect(self):
        self.acquire(run_id=OTHER_RUN)
        self.assertEqual(self.kind(1, 'run', 'create', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE, 'run_id': RUN_ID,
            'seats': 2, 'graph_digest': DIGEST}), 'foreign_owner')
        self.assertFalse(self.manifest_path().exists())

    def test_create_refuses_seats_that_disagree_with_the_host_config(self):
        self.acquire()
        self.assertEqual(self.kind(2, 'run', 'create', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE, 'run_id': RUN_ID,
            'seats': 3, 'graph_digest': DIGEST}), 'invalid_seats')
        self.assertFalse(self.manifest_path().exists())

    def test_create_over_an_existing_manifest_is_a_revision_refusal(self):
        self.acquire()
        self.create_run()
        self.assertEqual(self.kind(1, 'run', 'create', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE, 'run_id': RUN_ID,
            'seats': 2, 'graph_digest': DIGEST}), 'stale_revision')

    def test_inspecting_a_missing_run_is_a_state_refusal(self):
        self.assertEqual(self.kind(1, 'run', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID}),
            'missing_manifest')

    def test_transition_persists_and_reopens(self):
        self.acquire()
        self.create_run()
        written = self.transition(TASK, None, 'reserved', 1)
        self.assertEqual(written['revision'], 2)
        self.assertEqual(written['tasks'][TASK]['state'], 'reserved')
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['tasks'][TASK]['state'], 'reserved')

    def test_a_stale_expected_revision_fails_before_any_effect(self):
        self.acquire()
        self.create_run()
        self.transition(TASK, None, 'reserved', 1)
        before = self.manifest_path().read_bytes()
        self.assertEqual(self.kind(1, 'run', 'transition', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 1, 'task': TASK, 'expected': None, 'target': 'reserved'}),
            'stale_revision')
        self.assertEqual(self.manifest_path().read_bytes(), before)

    def test_a_wrong_expected_state_fails_before_any_effect(self):
        self.acquire()
        self.create_run()
        self.transition(TASK, None, 'reserved', 1)
        before = self.manifest_path().read_bytes()
        self.assertEqual(self.kind(1, 'run', 'transition', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'task': TASK, 'expected': 'preparing',
            'target': 'running'}), 'state_mismatch')
        self.assertEqual(self.manifest_path().read_bytes(), before)

    def test_an_illegal_transition_is_refused(self):
        self.acquire()
        self.create_run()
        self.transition(TASK, None, 'reserved', 1)
        self.assertEqual(self.kind(1, 'run', 'transition', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'task': TASK, 'expected': 'reserved',
            'target': 'published'}), 'illegal_transition')

    def test_a_side_entry_without_a_decision_is_refused(self):
        self.acquire()
        self.create_run()
        self.transition(TASK, None, 'reserved', 1)
        self.assertEqual(self.kind(2, 'run', 'transition', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'task': TASK, 'expected': 'reserved',
            'target': 'blocked'}), 'invalid_fields')

    def test_a_transition_under_a_foreign_lock_has_no_effect(self):
        self.acquire()
        self.create_run()
        self.transition(TASK, None, 'reserved', 1)
        common = git(self.repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        lock = Path(common) / 'artel' / 'parallel' / (TICKET + '.json')
        lock.write_text(json.dumps({'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN,
                                    'phase': PHASE, 'token': 'f' * 32,
                                    'acquired_at': '2026-01-01T00:00:00Z'}),
                        encoding='utf-8')
        before = self.manifest_path().read_bytes()
        self.assertEqual(self.kind(1, 'run', 'transition', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'task': TASK, 'expected': 'reserved',
            'target': 'preparing'}), 'foreign_owner')
        self.assertEqual(self.manifest_path().read_bytes(), before)


# ============================================================================================
# claims: intent before the queue call, the returned row decides
# ============================================================================================

def row(intent, status='in_progress', actor=None, task_id=41, task=TASK, phase=PHASE):
    return {'task_id': task_id, 'title': 'I2 · {} · work'.format(task),
            'status': status, 'actor': actor or intent['actor'], 'phase': phase, 'task': task}


class TestClaims(CliCase):
    def arm(self):
        self.acquire()
        self.create_run()

    def prepare(self, revision):
        return self.ok('claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': revision})

    def test_prepare_persists_the_intent_before_the_queue_call(self):
        self.arm()
        data = self.prepare(1)
        self.assertEqual(data['manifest']['revision'], 2)
        self.assertEqual(data['intent']['state'], 'pending')
        self.assertTrue(data['intent']['actor'].startswith('artel@{}#'.format(RUN_ID)))
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['claims']['intents'], [data['intent']])

    def test_a_second_prepare_waits_for_reconciliation(self):
        self.arm()
        self.prepare(1)
        self.assertEqual(self.kind(1, 'claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2}), 'unresolved_claim')

    def test_record_maps_the_own_row_and_authorizes_the_run(self):
        self.arm()
        intent = self.prepare(1)['intent']
        data = self.ok('claim', 'record', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'intent_id': intent['intent_id'],
            'row': row(intent)})
        self.assertEqual(data['action']['kind'], 'run')
        self.assertEqual(data['action']['task'], TASK)
        self.assertEqual(data['action']['claim'], intent['actor'])
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['claims']['intents'][0]['state'], 'mapped')

    def test_record_stops_on_a_foreign_holder_and_never_adopts(self):
        self.arm()
        intent = self.prepare(1)['intent']
        data = self.ok('claim', 'record', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'intent_id': intent['intent_id'],
            'row': row(intent, actor='artel@someone-else#1:1')})
        self.assertEqual(data['action']['kind'], 'stop')
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['claims']['intents'][0]['state'], 'foreign')
        self.assertEqual(on_disk['stop']['reason'], 'unexpected-holder')
        self.assertEqual(self.kind(1, 'claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 3}), 'admission_stopped')

    def test_reconcile_resolves_a_pending_intent_against_fresh_rows(self):
        self.arm()
        intent = self.prepare(1)['intent']
        data = self.ok('claim', 'reconcile', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'rows': [row(intent)]})
        self.assertEqual(data['actions'][0]['kind'], 'release')
        self.assertEqual(data['actions'][0]['reason'], 'orphaned-claim')
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['claims']['intents'][0]['state'], 'orphaned')

    def test_reconcile_marks_a_claim_that_never_landed_lost(self):
        self.arm()
        self.prepare(1)
        data = self.ok('claim', 'reconcile', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'rows': []})
        self.assertEqual(data['actions'], [])
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['claims']['intents'][0]['state'], 'lost')

    def test_reconcile_never_touches_a_foreign_row(self):
        self.arm()
        self.prepare(1)
        data = self.ok('claim', 'reconcile', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 2, 'rows': [row({'actor': 'x'}, actor='artel@other#1:1')]})
        self.assertEqual(data['actions'], [])

    def test_a_stale_revision_fails_before_any_claim_effect(self):
        self.arm()
        before = self.manifest_path().read_bytes()
        self.assertEqual(self.kind(1, 'claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': 0}), 'stale_revision')
        self.assertEqual(self.manifest_path().read_bytes(), before)


# ============================================================================================
# admission: the pure selector over the real tasklist
# ============================================================================================

class TestAdmit(CliCase):
    def arm(self):
        self.acquire()
        return self.create_run()

    def admit(self, text=TWO_TASKS, revision=None, run_id=RUN_ID):
        tasklist = self.write('specs/.current/{}/tasklist.md'.format(TICKET), text)
        return self.ok('admit', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': run_id,
            'tasklist': str(tasklist)})

    def test_only_the_current_unfinished_iteration_is_admitted(self):
        self.arm()
        data = self.admit()
        self.assertEqual(data['start'], ['2.1', '2.2'])
        self.assertEqual(data['claim_capacity'], 2)
        self.assertFalse(data['drain'])
        self.assertNotIn('3.1', data['start'])

    def test_a_dependency_waits_for_its_predecessor(self):
        self.arm()
        data = self.admit(CHAIN)
        self.assertEqual(data['start'], ['2.1'])

    def test_an_exclusive_task_holds_the_run_alone(self):
        self.arm()
        data = self.admit(EXCLUSIVE)
        self.assertEqual(data['start'], ['2.1'])
        self.assertTrue(any('exclusive' in reason for reason in data['reasons']))

    def test_an_integrating_task_drains_the_run(self):
        self.arm()
        self.transition(TASK, None, 'reserved', 1)
        self.transition(TASK, 'reserved', 'preparing', 2)
        self.transition(TASK, 'preparing', 'running', 3)
        self.transition(TASK, 'running', 'verifying/reviewing', 4)
        self.transition(TASK, 'verifying/reviewing', 'prepared', 5)
        self.transition(TASK, 'prepared', 'integrating', 6)
        data = self.admit()
        self.assertTrue(data['drain'])
        self.assertEqual(data['start'], [])
        self.assertEqual(data['resume'], [])
        self.assertEqual(data['claim_capacity'], 0)

    def test_a_malformed_tasklist_is_invalid_input(self):
        self.arm()
        self.assertEqual(self.kind(2, 'admit', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'tasklist': str(self.write('specs/.current/{}/tasklist.md'.format(TICKET),
                                       '# Tasklist\n\n- [ ] a loose checkbox\n'))}),
            'tasklist_malformed')

    def test_the_tasklist_can_arrive_on_stdin(self):
        self.arm()
        proc, envelope = self.cli('admit', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'tasklist': '-'}, input=TWO_TASKS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(envelope['ok'], envelope)
        self.assertEqual(envelope['data']['start'], ['2.1', '2.2'])

    def test_a_missing_tasklist_is_invalid_input(self):
        self.arm()
        self.assertEqual(self.kind(2, 'admit', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'tasklist': str(self.space / 'nope.md')}), 'tasklist_not_found')


# ============================================================================================
# worktrees and integration: real Git effects
# ============================================================================================

class WorktreeCase(CliCase):
    def worker_root(self, task=TASK, attempt=1):
        return (self.repo / '.claude' / 'worktrees' / 'parallel' / TICKET / RUN_ID /
                '{}-a{}'.format(task, attempt))

    def artifact_root(self, task=TASK, attempt=1):
        return self.worker_root(task, attempt) / '.artel' / 'artifacts' / \
            '{}-a{}'.format(task, attempt)

    def assignment(self, task=TASK, attempt=1, route='light', **overrides):
        value = {'schema': 1, 'ticket': TICKET, 'phase': PHASE, 'task': task,
                 'run_id': RUN_ID, 'attempt': attempt, 'claim': 'artel@host',
                 'base_commit': self.base, 'files': ['app.txt'], 'tests': [],
                 'route': route, 'model': 'provider/model',
                 'worker_root': str(self.worker_root(task, attempt)),
                 'artifact_root': str(self.artifact_root(task, attempt))}
        value.update(overrides)
        return value

    def result(self, assignment, **overrides):
        value = dict(assignment)
        value.update({'status': 'succeeded', 'changed_files': ['app.txt'], 'verify': [],
                      'review': {'required': False, 'route': 'light', 'verdict': 'passed',
                                 'rounds': 0},
                      'report': None, 'deviations': []})
        value.update(overrides)
        return value

    def arm(self):
        self.acquire()
        self.create_run()

    def to_integrating(self, task=TASK):
        self.transition(task, None, 'reserved', 1)
        self.transition(task, 'reserved', 'preparing', 2)
        self.transition(task, 'preparing', 'running', 3)
        self.transition(task, 'running', 'verifying/reviewing', 4)
        self.transition(task, 'verifying/reviewing', 'prepared', 5)
        self.transition(task, 'prepared', 'integrating', 6)

    def prepare_worker(self):
        """A real worker root with one code change, its result file and an integrating run."""
        self.arm()
        assignment = self.assignment()
        self.ok('worktree', 'create', request={'repo': str(self.repo),
                                               'assignment': assignment})
        (self.worker_root() / 'app.txt').write_text('v2\n', encoding='utf-8')
        result = self.result(assignment)
        self.write('.artel/artifacts/{}-a1/result.json'.format(TASK),
                   json.dumps(result), root=self.worker_root())
        self.to_integrating()
        return assignment, result


class TestWorktreeCli(WorktreeCase):
    def test_create_inspect_and_refuse_a_foreign_lock(self):
        self.arm()
        data = self.ok('worktree', 'create', request={
            'repo': str(self.repo), 'assignment': self.assignment()})
        self.assertTrue(data['created'])
        self.assertTrue((self.worker_root() / '.artel' / 'parallel-worker.json').is_file())
        inspected = self.ok('worktree', 'inspect', request={
            'repo': str(self.repo), 'assignment': self.assignment()})
        self.assertEqual(inspected['state'], 'ready')
        self.assertEqual(inspected['root'], str(self.worker_root()))

    def test_create_without_ownership_has_no_worktree_effect(self):
        self.assertEqual(self.kind(1, 'worktree', 'create', request={
            'repo': str(self.repo), 'assignment': self.assignment()}), 'not_owned')
        self.assertFalse(self.worker_root().exists())

    def test_cleanup_refuses_without_a_valid_publication_and_retains_the_root(self):
        self.arm()
        self.ok('worktree', 'create', request={
            'repo': str(self.repo), 'assignment': self.assignment()})
        error = self.refused(2, 'worktree', 'cleanup', request={
            'repo': str(self.repo), 'assignment': self.assignment(), 'publication': {}})
        self.assertEqual(error['kind'], 'invalid_publication')
        self.assertTrue(self.worker_root().exists())


class TestIntegrationCli(WorktreeCase):
    def test_prepare_apply_and_commit_a_real_cycle(self):
        assignment, result = self.prepare_worker()
        transaction = self.ok('integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result})
        self.assertEqual(transaction['stage'], 'prepared')
        self.assertEqual(transaction['route'], 'light')
        self.assertEqual(self.read_json(self.record_path()), transaction)

        applied = self.ok('integration', 'apply', request={
            'repo': str(self.repo), 'assignment': assignment, 'prepared': transaction})
        self.assertEqual(applied['stage'], 'applied')
        self.assertTrue((self.repo / 'app.txt').read_text(encoding='utf-8').startswith('v2'))

        committed = self.ok('integration', 'commit', request={
            'repo': str(self.repo), 'transaction': applied,
            'subject': 'feat: parallel cli fixture'})
        self.assertEqual(committed['stage'], 'committed')
        cycle = committed['cycle_commit']
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), cycle)
        self.assertEqual(git(self.repo, 'log', '-1', '--format=%s'), 'feat: parallel cli fixture')
        reopened = self.read_json(self.record_path())
        self.assertEqual(reopened['stage'], 'committed')

    def test_recover_recognizes_the_landed_commit_exactly_once(self):
        assignment, result = self.prepare_worker()
        transaction = self.ok('integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result})
        applied = self.ok('integration', 'apply', request={
            'repo': str(self.repo), 'assignment': assignment, 'prepared': transaction})
        committed = self.ok('integration', 'commit', request={
            'repo': str(self.repo), 'transaction': applied,
            'subject': 'feat: parallel cli fixture'})
        recovered = self.ok('integration', 'recover', request={
            'repo': str(self.repo), 'transaction': committed})
        self.assertEqual(recovered['stage'], 'recovered')
        self.assertEqual(recovered['cycle_commit'], committed['cycle_commit'])
        again = self.ok('integration', 'recover', request={
            'repo': str(self.repo), 'transaction': recovered})
        self.assertEqual(again['cycle_commit'], committed['cycle_commit'])
        self.assertEqual(git(self.repo, 'rev-list', '--count', 'HEAD'), '2')

    def test_prepare_without_ownership_has_no_effect(self):
        self.arm()
        assignment = self.assignment()
        self.ok('worktree', 'create', request={'repo': str(self.repo),
                                               'assignment': assignment})
        (self.worker_root() / 'app.txt').write_text('v2\n', encoding='utf-8')
        result = self.result(assignment)
        self.to_integrating()
        common = git(self.repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        (Path(common) / 'artel' / 'parallel' / (TICKET + '.json')).unlink()
        self.assertEqual(self.kind(1, 'integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result}),
            'not_owned')

    def test_apply_refuses_a_dirty_coordinator_and_preserves_it(self):
        assignment, result = self.prepare_worker()
        transaction = self.ok('integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result})
        self.write('unrelated.txt', 'someone else\n')
        error = self.refused(1, 'integration', 'apply', request={
            'repo': str(self.repo), 'assignment': assignment, 'prepared': transaction})
        self.assertEqual(error['kind'], 'coordinator_dirty')
        self.assertEqual((self.repo / 'unrelated.txt').read_text(encoding='utf-8'),
                         'someone else\n')
        self.assertEqual(self.read_json(self.record_path())['stage'], 'prepared')


# ============================================================================================
# publication: plan fresh, persist the record before the manifest
# ============================================================================================

class PublicationCase(WorktreeCase):
    def integrate(self):
        assignment, result = self.prepare_worker()
        transaction = self.ok('integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result})
        applied = self.ok('integration', 'apply', request={
            'repo': str(self.repo), 'assignment': assignment, 'prepared': transaction})
        committed = self.ok('integration', 'commit', request={
            'repo': str(self.repo), 'transaction': applied,
            'subject': 'feat: parallel cli fixture'})
        self.assignment_value = assignment
        return assignment, committed

    def source_root(self, transaction):
        return (Path(transaction['worker_root']) / '.artel' / 'artifacts' /
                '{}-a1'.format(TASK))

    def collected_root(self):
        return str(self.repo / '.artel' / 'run' / TICKET / 'parallel' / RUN_ID /
                   '{}-a1'.format(TASK))

    def observed(self, transaction, collected=None, disposition='succeeded'):
        source = self.source_root(transaction)
        entries = [{'path': 'result.json',
                    'sha256': hashlib.sha256(
                        (source / 'result.json').read_bytes()).hexdigest()}]
        return {'schema': 1, 'transaction': transaction,
                'lifecycle': {'disposition': disposition},
                'store': {'mode': 'files', 'documents': [], 'notes': None},
                'queue': {'row': None},
                'evidence': {'source_root': str(source),
                             'collected_root': self.collected_root(),
                             'entries': entries, 'collected': collected}}

    def next_actions(self, transaction, **kwargs):
        return self.ok('publication', 'next', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'task': TASK,
            'observed': self.observed(transaction, **kwargs)})

    def record(self, transaction, action, state, evidence):
        receipt = {'schema': 1,
                   'publication': transaction.get('publication'), 'action': action,
                   'state': state, 'evidence': evidence}
        return self.ok('publication', 'record', request={
            'repo': str(self.repo), 'transaction': transaction, 'task': TASK,
            'action_id': action['action_id'], 'receipt': receipt})

    def collect(self, action):
        """Perform the collect-evidence effect the way the coordinator does."""
        payload = action['payload']
        destination = Path(payload['collected_root'])
        destination.mkdir(parents=True, exist_ok=True)
        entries = []
        for entry in payload['entries']:
            shutil.copy2(str(Path(payload['source_root']) / entry['path']),
                         str(destination / entry['path']))
            entries.append(dict(entry))
        return {'collected_root': payload['collected_root'], 'entries': entries}

    def test_the_plan_collects_evidence_then_promotes(self):
        _, transaction = self.integrate()
        actions = self.next_actions(transaction)
        self.assertEqual([action['kind'] for action in actions], ['collect-evidence'])
        self.assertEqual(actions[0]['state'], 'pending')

    def test_record_persists_the_intent_then_the_completion(self):
        _, transaction = self.integrate()
        collect = self.next_actions(transaction)[0]

        planned = self.record(transaction, collect, 'planned', None)
        self.assertEqual(planned['publication']['intents'][0]['action_id'],
                         collect['action_id'])
        self.assertEqual(planned['publication']['receipts'], [])
        self.assertFalse(planned['publication']['complete'])
        on_disk = self.read_json(self.record_path())
        self.assertEqual(on_disk['publication']['intents'][0]['action_id'],
                         collect['action_id'])
        self.assertEqual(self.read_json(self.manifest_path())['tasks'][TASK]['state'],
                         'integrating')

        evidence = self.collect(collect)
        transaction = self.read_json(self.record_path())
        receipt = self.record(transaction, collect, 'applied', evidence)
        self.assertTrue(receipt['publication']['complete'])
        self.assertTrue(receipt['publication']['proof']['published'])
        self.assertEqual(receipt['manifest']['tasks'][TASK]['state'], 'published')
        on_disk = self.read_json(self.manifest_path())
        self.assertEqual(on_disk['tasks'][TASK]['state'], 'published')
        stored = self.read_json(self.record_path())
        self.assertTrue(stored['publication']['complete'])
        self.assertEqual(self.next_actions(stored, collected=evidence['entries']), [])

    def test_the_publication_record_is_persisted_before_the_manifest(self):
        if os.name == 'nt':
            self.skipTest('directory permissions are not a write barrier on Windows')
        _, transaction = self.integrate()
        collect = self.next_actions(transaction)[0]
        self.record(transaction, collect, 'planned', None)
        evidence = self.collect(collect)
        transaction = self.read_json(self.record_path())

        run_dir = self.manifest_path().parent
        original = run_dir.stat().st_mode
        os.chmod(str(run_dir), stat.S_IRUSR | stat.S_IXUSR)
        try:
            receipt = {'schema': 1, 'publication': transaction['publication'],
                       'action': collect, 'state': 'applied', 'evidence': evidence}
            proc, envelope = self.cli('publication', 'record', request={
                'repo': str(self.repo), 'transaction': transaction, 'task': TASK,
                'action_id': collect['action_id'], 'receipt': receipt})
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse(envelope['ok'])
            stored = self.read_json(self.record_path())
            self.assertTrue(stored['publication']['complete'])
            self.assertEqual(self.read_json(self.manifest_path())['tasks'][TASK]['state'],
                             'integrating')
        finally:
            os.chmod(str(run_dir), original)

        # The complete record + the integrating manifest recover through one fresh promote.
        transaction = self.read_json(self.record_path())
        promote = self.next_actions(transaction, collected=evidence['entries'])[0]
        self.assertEqual(promote['kind'], 'promote')
        published = self.record(transaction, promote, 'observed', None)
        self.assertEqual(published['manifest']['tasks'][TASK]['state'], 'published')
        self.assertEqual(self.read_json(self.manifest_path())['tasks'][TASK]['state'],
                         'published')

    def test_a_receipt_without_its_intent_is_refused_and_changes_nothing(self):
        _, transaction = self.integrate()
        collect = self.next_actions(transaction)[0]
        evidence = self.collect(collect)
        before = self.record_path().read_bytes()
        self.assertEqual(self.kind(1, 'publication', 'record', request={
            'repo': str(self.repo), 'transaction': transaction, 'task': TASK,
            'action_id': collect['action_id'],
            'receipt': {'schema': 1, 'publication': None, 'action': collect,
                        'state': 'applied', 'evidence': evidence}}), 'missing_intent')
        self.assertEqual(self.record_path().read_bytes(), before)

    def test_a_stale_caller_never_clobbers_the_durable_record(self):
        _, transaction = self.integrate()
        collect = self.next_actions(transaction)[0]
        self.record(transaction, collect, 'planned', None)
        durable = self.read_json(self.record_path())
        stale = dict(transaction)  # the pre-intent view
        before = self.record_path().read_bytes()
        self.assertEqual(self.kind(1, 'publication', 'record', request={
            'repo': str(self.repo), 'transaction': stale, 'task': TASK,
            'action_id': collect['action_id'],
            'receipt': {'schema': 1, 'publication': None, 'action': collect,
                        'state': 'applied',
                        'evidence': self.collect(collect)}}), 'stale_publication')
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.read_json(self.record_path())['publication'],
                         durable['publication'])

    def test_an_unsuccessful_disposition_blocks_publication(self):
        _, transaction = self.integrate()
        self.assertEqual(self.kind(1, 'publication', 'next', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'task': TASK,
            'observed': self.observed(transaction, disposition='interrupted')}),
            'worker_not_succeeded')

    def complete_record(self):
        """A complete durable publication with the manifest still integrating.

        The last required receipt's manifest write is blocked by a real read-only-directory
        barrier, so the durable state is exactly the one a crashed coordinator leaves.
        """
        _, transaction = self.integrate()
        collect = self.next_actions(transaction)[0]
        self.record(transaction, collect, 'planned', None)
        evidence = self.collect(collect)
        transaction = self.read_json(self.record_path())
        receipt = {'schema': 1, 'publication': transaction['publication'], 'action': collect,
                   'state': 'applied', 'evidence': evidence}
        run_dir = self.manifest_path().parent
        original = run_dir.stat().st_mode
        os.chmod(str(run_dir), stat.S_IRUSR | stat.S_IXUSR)
        try:
            proc, envelope = self.cli('publication', 'record', request={
                'repo': str(self.repo), 'transaction': transaction, 'task': TASK,
                'action_id': collect['action_id'], 'receipt': receipt})
            self.assertEqual(proc.returncode, 2, envelope)
        finally:
            os.chmod(str(run_dir), original)
        stored = self.read_json(self.record_path())
        self.assertTrue(stored['publication']['complete'])
        self.assertEqual(self.read_json(self.manifest_path())['tasks'][TASK]['state'],
                         'integrating')
        return evidence

    def promote_action(self, evidence):
        transaction = self.read_json(self.record_path())
        promote = self.next_actions(transaction, collected=evidence['entries'])[0]
        self.assertEqual(promote['kind'], 'promote')
        return transaction, promote

    def promote_request(self, transaction, promote):
        return {'repo': str(self.repo), 'transaction': transaction, 'task': TASK,
                'action_id': promote['action_id'],
                'receipt': {'schema': 1, 'publication': transaction['publication'],
                            'action': promote, 'state': 'observed', 'evidence': None}}

    def test_a_missing_foreign_or_ambiguous_lock_refuses_the_pure_promote(self):
        evidence = self.complete_record()
        transaction, promote = self.promote_action(evidence)
        request = self.promote_request(transaction, promote)
        head = git(self.repo, 'rev-parse', 'HEAD')
        record_before = self.record_path().read_bytes()
        manifest_before = self.manifest_path().read_bytes()
        saved = self.lock_path().read_bytes()

        self.lock_path().unlink()
        self.assertEqual(self.kind(1, 'publication', 'record', request=request), 'not_owned')
        self.assertEqual(self.record_path().read_bytes(), record_before)
        self.assertEqual(self.manifest_path().read_bytes(), manifest_before)
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), head)

        self.lock_path().write_text(json.dumps(
            {'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN, 'phase': PHASE,
             'token': 'f' * 32, 'acquired_at': '2026-01-01T00:00:00Z'}), encoding='utf-8')
        self.assertEqual(self.kind(1, 'publication', 'record', request=request),
                         'foreign_owner')
        self.assertEqual(self.record_path().read_bytes(), record_before)
        self.assertEqual(self.manifest_path().read_bytes(), manifest_before)

        self.lock_path().write_text('{not json', encoding='utf-8')
        self.assertEqual(self.kind(1, 'publication', 'record', request=request),
                         'ambiguous_owner')
        self.assertEqual(self.record_path().read_bytes(), record_before)
        self.assertEqual(self.manifest_path().read_bytes(), manifest_before)

        # the real owner promotes exactly once, record first then manifest
        self.lock_path().write_bytes(saved)
        published = self.record(transaction, promote, 'observed', None)
        self.assertEqual(published['manifest']['tasks'][TASK]['state'], 'published')
        self.assertEqual(self.read_json(self.manifest_path())['tasks'][TASK]['state'],
                         'published')
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), head)

    def test_a_corrupt_integration_record_is_a_typed_refusal(self):
        _, transaction = self.integrate()
        collect = self.next_actions(transaction)[0]
        self.record_path().write_text('{not json', encoding='utf-8')
        request = {'repo': str(self.repo), 'transaction': transaction, 'task': TASK,
                   'action_id': collect['action_id'],
                   'receipt': {'schema': 1, 'publication': None, 'action': collect,
                               'state': 'planned', 'evidence': None}}
        self.assertEqual(self.kind(2, 'publication', 'record', request=request),
                         'corrupt_record')


# ============================================================================================
# the budget commands: routing, envelopes and malformed input
# ============================================================================================

class TestBudgetCli(CliCase):
    def test_the_budget_commands_route_with_the_envelope(self):
        self.write_run_state(run_active=True)
        proc, envelope = self.cli('budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE})
        self.assertEqual(proc.returncode, 0, envelope)
        self.assertEqual(envelope['verb'], 'parallel-budget-inspect')
        self.assertEqual(envelope['data']['cap'], 8)
        reserved = self.ok('budget', 'reserve', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
            'kind': 'review', 'scope': '2.1', 'attempt': 1, 'ordinal': 1,
            'expected_revision': 0})
        self.assertFalse(reserved['replayed'])
        proc, envelope = self.cli('budget', 'reserve', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
            'kind': 'review', 'scope': '2.1', 'attempt': 1, 'ordinal': 1,
            'expected_revision': 1})
        self.assertEqual(proc.returncode, 0, envelope)
        self.assertEqual(envelope['verb'], 'parallel-budget-reserve')
        self.assertTrue(envelope['data']['replayed'])

    def test_budget_malformed_input_is_refused(self):
        self.write_run_state()
        self.assertEqual(self.kind(2, 'budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE, 'extra': 1}),
            'invalid_request')
        self.assertEqual(self.kind(2, 'budget', 'reserve', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
            'kind': 'review', 'scope': '2.1', 'attempt': 1}), 'invalid_request')
        self.assertEqual(self.kind(2, 'budget', 'reserve', request={
            'repo': str(self.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
            'kind': 'review', 'scope': '2.1', 'attempt': '1', 'ordinal': 1,
            'expected_revision': 0}), 'invalid_request')
        self.assertEqual(self.kind(2, 'budget', 'reset', request={}), 'invalid_argument')

    def test_budget_inspect_on_a_missing_run_state_fails_closed(self):
        self.assertEqual(self.kind(1, 'budget', 'inspect', request={
            'repo': str(self.repo), 'ticket': TICKET, 'phase': PHASE}),
            'missing_run_state')


# ============================================================================================
# the operator contract in the docs
# ============================================================================================

class TestCoordinatorDocs(unittest.TestCase):
    def test_the_parallel_contract_documents_the_cli_and_the_loop(self):
        text = (ROOT / 'docs' / 'opencode-parallel.md').read_text(encoding='utf-8')
        for phrase in ('scripts/parallel.py', '--request', '`config`', '`preflight`',
                       '`owner acquire`', '`owner release`', '`run create`', '`run inspect`',
                       '`run transition`', '`claim prepare`', '`claim record`',
                       '`claim reconcile`', '`admit`', '`worktree create`',
                       '`integration prepare`', '`publication next`', '`publication record`',
                       '`budget inspect`', '`budget reserve`',
                       'exit `1`', 'exit `2`', 'never evaluates', 'revision'):
            self.assertIn(phrase, text, phrase)

    def test_the_contract_documents_the_durable_correction_budget(self):
        text = (ROOT / 'docs' / 'opencode-parallel.md').read_text(encoding='utf-8')
        for phrase in ('scripts/parallel_budget.py', 'budget inspect', 'budget reserve',
                       'correction_rounds', 'correction_reservations', 'stable key',
                       'expected_revision', 'stale_revision', 'budget_exhausted',
                       'scope_cap', 'run-state.json', 'no reset',
                       'MAX_TOTAL_CORRECTION_ROUNDS', 'MAX_TASK_REVIEW_ROUNDS',
                       'MAX_REVIEW_ROUNDS'):
            self.assertIn(phrase, text, phrase)

    def test_autonomous_run_documents_the_reservation_metadata(self):
        flat = ' '.join((ROOT / 'docs' / 'autonomous-run.md').read_text(
            encoding='utf-8').split())
        self.assertIn('correction_reservations', flat)
        self.assertIn('preserved', flat)

    def test_the_parallel_contract_separates_seats_from_the_seat(self):
        text = (ROOT / 'docs' / 'opencode-parallel.md').read_text(encoding='utf-8')
        self.assertIn('`parallel.seats`', text)
        self.assertIn('`seat.enabled`', text)
        flat = ' '.join(text.split())
        self.assertIn('not `seat.enabled`', flat)
        self.assertIn('runs the tail inline', flat)

    def test_the_loop_contract_names_drain_pauses_and_the_quiescent_finish(self):
        text = (ROOT / 'docs' / 'opencode-parallel.md').read_text(encoding='utf-8')
        flat = ' '.join(text.split())
        for phrase in ('preflight/lock/reconcile', 'fresh `resume`',
                       'persist `preparing`', 'durable admission latch',
                       'safe return', 'stop admission',
                       'quiescen', 'release'):
            self.assertIn(phrase, flat, phrase)

    def test_config_documents_seats_as_worker_concurrency(self):
        flat = ' '.join((ROOT / 'docs' / 'config.md').read_text(encoding='utf-8').split())
        self.assertIn('worker concurrency', flat)
        self.assertIn('not `seat.enabled`', flat)

    def test_the_opencode_host_doc_mentions_the_parallel_procedure(self):
        flat = ' '.join((ROOT / 'docs' / 'opencode.md').read_text(encoding='utf-8').split())
        self.assertIn('parallel.seats', flat)
        self.assertIn('parallel.md', flat)


if __name__ == '__main__':
    unittest.main()
