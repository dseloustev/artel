"""The latching verify stop gate: blocks a stop while the fast gate is red with findings
NEW relative to the session baseline, then latches open after two blocks.

The latch is the subtle part. At the cap the counter must STAY at the cap rather than reset,
because resetting re-arms a 3-cycle block/pass pattern that can phase-lock against the
orchestrator stop gate's 6-cycle one and block indefinitely.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / 'hooks'
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(ROOT / 'scripts'))
import hook_common as h  # noqa: E402
import parallel_state  # noqa: E402
import task_worktree  # noqa: E402
import verify_stop_gate as vsg  # noqa: E402

SESSION = 'sess-1'
CONFIG = {'ticket': {'projectKey': 'AW'}}
WORKER_TICKET = 'PROJ-123'
WORKER_RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
WORKER_PHASE = 2


def envelope(*keys):
    return {'data': {'stages': [{'keys': list(keys)}]}}


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def make_worker_root(space):
    """A real worker root from the reviewed lifecycle; returns (root, artifact_root)."""
    main = space / 'main'
    git(space, 'init', '-q', str(main))
    for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                       ('commit.gpgsign', 'false')):
        git(main, 'config', key, value)
    (main / '.gitignore').write_text('.artel/\n', encoding='utf-8')
    (main / 'app.txt').write_text('v1\n', encoding='utf-8')
    git(main, 'add', '.')
    git(main, 'commit', '-q', '-m', 'init')
    base = git(main, 'rev-parse', 'HEAD')
    coord = main / '.claude' / 'worktrees' / WORKER_TICKET
    git(main, 'worktree', 'add', '-q', '-b', 'feature/PROJ-123', str(coord))
    config = coord / '.artel' / 'config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'specs': {'dir': 'specs/.current'},
                                  'ticket': {'projectKey': 'PROJ'}}), encoding='utf-8')
    parallel_state.acquire_ticket(str(coord), WORKER_TICKET, {
        'schema': 1, 'ticket': WORKER_TICKET, 'run_id': WORKER_RUN_ID,
        'phase': WORKER_PHASE, 'coordinator_id': 'ses_stop-gate-fixture'})
    root = (main / '.claude' / 'worktrees' / 'parallel tasks' / WORKER_TICKET /
            WORKER_RUN_ID / '3.1-a1')
    artifact = root / '.artel' / 'run' / 'parallel' / '3.1-a1'
    task_worktree.create_worker(str(coord), {
        'schema': 1, 'ticket': WORKER_TICKET, 'phase': WORKER_PHASE, 'task': '3.1',
        'run_id': WORKER_RUN_ID, 'attempt': 1, 'claim': 'artel@host', 'base_commit': base,
        'files': ['app.txt'], 'tests': [], 'route': 'full', 'model': None,
        'worker_root': str(root), 'artifact_root': str(artifact)})
    return root, artifact


class GateCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def write_config(self):
        (self.root / '.artel').mkdir(exist_ok=True)
        (self.root / '.artel' / 'config.json').write_text(json.dumps(CONFIG), encoding='utf-8')

    @property
    def state_dir(self):
        return self.root / '.artel' / 'run' / '.hooks'

    def write_baseline(self, *keys):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / 'baseline-{}.json'.format(SESSION)).write_text(
            json.dumps({'keys': sorted(keys)}), encoding='utf-8')

    def baseline_keys(self):
        path = self.state_dir / 'baseline-{}.json'.format(SESSION)
        return json.loads(path.read_text(encoding='utf-8'))['keys']

    def counter(self):
        path = self.state_dir / 'stopblocks-{}.json'.format(SESSION)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding='utf-8'))['consecutive']

    def invoke(self, changed=('src/a.py',), verify=(1, None)):
        code, env = verify
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(h, 'changed_files', return_value=list(changed)), \
                mock.patch.object(h, 'run_fast_verify',
                                  return_value=(code, env if env is not None else {})), \
                mock.patch.object(h, 'read_hook_input', return_value={'session_id': SESSION}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(vsg.main(), 0, 'the gate always exits 0')
        return out.getvalue(), err.getvalue()

    def assertPassed(self, **kwargs):
        stdout, stderr = self.invoke(**kwargs)
        self.assertEqual(stdout, '', 'a clean pass prints nothing on stdout')
        return stderr

    def assertBlocked(self, **kwargs):
        stdout, _ = self.invoke(**kwargs)
        payload = json.loads(stdout)
        self.assertEqual(payload['decision'], 'block')
        return payload['reason']


class TestInert(GateCase):
    def test_no_config_writes_nothing(self):
        self.assertPassed()
        self.assertFalse((self.root / '.artel').exists())


class TestPasses(GateCase):
    def setUp(self):
        super().setUp()
        self.write_config()

    def test_no_changed_files_passes_and_resets(self):
        self.write_baseline('a:1')
        self.assertPassed(changed=())
        self.assertEqual(self.counter(), 0)

    def test_green_verify_passes_and_resets(self):
        self.write_baseline('a:1')
        self.assertPassed(verify=(0, envelope()))
        self.assertEqual(self.counter(), 0)

    def test_environment_error_passes_loudly(self):
        self.write_baseline()
        stderr = self.assertPassed(verify=(2, {'error': {'kind': 'toolchain-missing'}}))
        self.assertIn('environment error', stderr)
        self.assertIn('toolchain-missing', stderr)

    def test_findings_already_in_the_baseline_pass(self):
        self.write_baseline('a:1', 'b:2')
        self.assertPassed(verify=(1, envelope('a:1', 'b:2')))
        self.assertEqual(self.counter(), 0)


class TestFirstSight(GateCase):
    def setUp(self):
        super().setUp()
        self.write_config()

    def test_absent_baseline_is_captured_lazily_and_passes(self):
        """SessionStart may have hit an environment error; treat what is here as pre-existing."""
        self.assertPassed(verify=(1, envelope('a:1')))
        self.assertEqual(self.baseline_keys(), ['a:1'])

    def test_the_captured_baseline_then_suppresses_those_findings(self):
        self.assertPassed(verify=(1, envelope('a:1')))
        self.assertPassed(verify=(1, envelope('a:1')))


class TestBlocks(GateCase):
    def setUp(self):
        super().setUp()
        self.write_config()
        self.write_baseline('old:1')

    def test_new_finding_blocks(self):
        reason = self.assertBlocked(verify=(1, envelope('old:1', 'new:2')))
        self.assertIn('new:2', reason)
        self.assertNotIn('old:1', reason)
        self.assertEqual(self.counter(), 1)

    def test_reason_carries_the_reproduction_command(self):
        reason = self.assertBlocked(changed=('src/a.py', 'src/b.py'),
                                    verify=(1, envelope('new:2')))
        self.assertIn('--fast --files src/a.py,src/b.py', reason)
        self.assertIn(str(h.VERIFY_SCRIPT), reason)

    def test_second_block_increments(self):
        self.assertBlocked(verify=(1, envelope('new:2')))
        self.assertBlocked(verify=(1, envelope('new:2')))
        self.assertEqual(self.counter(), vsg.MAX_CONSECUTIVE_BLOCKS)

    def test_a_clean_pass_resets_the_counter(self):
        self.assertBlocked(verify=(1, envelope('new:2')))
        self.assertPassed(verify=(0, envelope()))
        self.assertEqual(self.counter(), 0)
        self.assertBlocked(verify=(1, envelope('new:2')))
        self.assertEqual(self.counter(), 1, 'the cycle starts over after a green run')


class TestLatch(GateCase):
    def setUp(self):
        super().setUp()
        self.write_config()
        self.write_baseline('old:1')

    def _exhaust(self):
        for _ in range(vsg.MAX_CONSECUTIVE_BLOCKS):
            self.assertBlocked(verify=(1, envelope('new:2')))

    def test_passes_with_a_system_message_at_the_cap(self):
        self._exhaust()
        stdout, _ = self.invoke(verify=(1, envelope('new:2')))
        payload = json.loads(stdout)
        self.assertNotIn('decision', payload)
        self.assertIn('still red after 2 blocks', payload['systemMessage'])
        self.assertIn('new:2', payload['systemMessage'])

    def test_the_counter_stays_latched_at_the_cap(self):
        """Resetting here would re-arm the block/pass cycle that can phase-lock."""
        self._exhaust()
        for _ in range(3):
            self.invoke(verify=(1, envelope('new:2')))
            self.assertEqual(self.counter(), vsg.MAX_CONSECUTIVE_BLOCKS)

    def test_a_green_run_releases_the_latch(self):
        self._exhaust()
        self.assertPassed(verify=(0, envelope()))
        self.assertEqual(self.counter(), 0)
        self.assertBlocked(verify=(1, envelope('new:2')))


class TestWorkerRootRegression(unittest.TestCase):
    """Task 6 preserves the verify stop gate for a validated worker root: a worker that
    introduced new findings must still be told to fix them before stopping, and the
    counter stays worker-local."""

    SESSION = 'sess-worker'

    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root, self.artifact = make_worker_root(Path(os.path.realpath(self._tmp.name)))
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def state_dir(self):
        return self.root / '.artel' / 'run' / '.hooks'

    def write_baseline(self, *keys):
        self.state_dir().mkdir(parents=True, exist_ok=True)
        (self.state_dir() / 'baseline-{}.json'.format(self.SESSION)).write_text(
            json.dumps({'keys': sorted(keys)}), encoding='utf-8')

    def counter(self):
        path = self.state_dir() / 'stopblocks-{}.json'.format(self.SESSION)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding='utf-8'))['consecutive']

    def invoke(self, verify=(1, None)):
        code, env = verify
        out = io.StringIO()
        with mock.patch.object(h, 'changed_files', return_value=['app.txt']), \
                mock.patch.object(h, 'run_fast_verify',
                                  return_value=(code, env if env is not None else {})), \
                mock.patch.object(h, 'read_hook_input',
                                  return_value={'session_id': self.SESSION}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vsg.main(), 0, 'the gate always exits 0')
        return out.getvalue()

    def test_new_findings_still_block_a_worker_stop(self):
        self.write_baseline('old:1')
        payload = json.loads(self.invoke(verify=(1, envelope('old:1', 'new:2'))))
        self.assertEqual(payload['decision'], 'block')
        self.assertIn('new:2', payload['reason'])
        self.assertEqual(self.counter(), 1,
                         'the counter is written in the worker root, not the coordinator')

    def test_a_green_pass_resets_the_worker_counter(self):
        self.write_baseline('old:1')
        self.invoke(verify=(1, envelope('new:2')))
        self.invoke(verify=(0, envelope()))
        self.assertEqual(self.counter(), 0)


if __name__ == '__main__':
    unittest.main()
