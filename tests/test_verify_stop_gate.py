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
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
sys.path.insert(0, str(HOOKS))
import hook_common as h  # noqa: E402
import verify_stop_gate as vsg  # noqa: E402

SESSION = 'sess-1'
CONFIG = {'ticket': {'projectKey': 'AW'}}


def envelope(*keys):
    return {'data': {'stages': [{'keys': list(keys)}]}}


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


if __name__ == '__main__':
    unittest.main()
