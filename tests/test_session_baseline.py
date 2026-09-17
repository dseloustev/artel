"""The SessionStart baseline: records the findings that already existed when the session
opened, so the verify stop gate can block on NEW ones only.

The one subtlety worth pinning: an environment error must leave the baseline ABSENT rather
than writing an empty one, because an empty baseline would make every pre-existing finding
look new to the stop gate.
"""
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
import session_baseline as sb  # noqa: E402

SESSION = 'sess-1'
CONFIG = {'ticket': {'projectKey': 'AW'}}


def envelope(*keys):
    return {'data': {'stages': [{'keys': list(keys)}]}}


class BaselineCase(unittest.TestCase):
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
    def baseline(self):
        return self.root / '.artel' / 'run' / '.hooks' / 'baseline-{}.json'.format(SESSION)

    def invoke(self, changed=('src/a.py',), verify=(1, None), session=SESSION):
        code, env = verify
        payload = {'session_id': session} if session is not None else {}
        with mock.patch.object(h, 'changed_files', return_value=list(changed)), \
                mock.patch.object(h, 'run_fast_verify',
                                  return_value=(code, env if env is not None else {})) as run, \
                mock.patch.object(h, 'read_hook_input', return_value=payload):
            self.assertEqual(sb.main(), 0)
        return run

    def keys(self):
        return json.loads(self.baseline.read_text(encoding='utf-8'))['keys']


class TestUnconfiguredHost(BaselineCase):
    def test_writes_nothing_without_a_config(self):
        self.invoke()
        self.assertFalse(self.baseline.exists())
        self.assertFalse((self.root / '.artel').exists(), 'zero footprint on an unconfigured host')


class TestCapture(BaselineCase):
    def setUp(self):
        super().setUp()
        self.write_config()

    def test_records_sorted_findings(self):
        self.invoke(verify=(1, envelope('b:2', 'a:1')))
        self.assertEqual(self.keys(), ['a:1', 'b:2'])

    def test_unions_keys_across_stages(self):
        self.invoke(verify=(1, {'data': {'stages': [{'keys': ['s0:a']}, {'keys': ['s1:b']}]}}))
        self.assertEqual(self.keys(), ['s0:a', 's1:b'])

    def test_green_verify_records_an_empty_baseline(self):
        self.invoke(verify=(0, envelope()))
        self.assertEqual(self.keys(), [])

    def test_no_changed_files_skips_verify_entirely(self):
        run = self.invoke(changed=())
        run.assert_not_called()
        self.assertEqual(self.keys(), [])

    def test_missing_session_id_falls_back_to_unknown(self):
        self.invoke(verify=(0, envelope()), session=None)
        fallback = self.root / '.artel' / 'run' / '.hooks' / 'baseline-unknown.json'
        self.assertTrue(fallback.is_file())

    def test_verify_is_given_the_changed_files_and_a_timeout(self):
        run = self.invoke(changed=('src/a.py', 'src/b.py'), verify=(0, envelope()))
        run.assert_called_once_with(['src/a.py', 'src/b.py'], timeout=100)


class TestEnvironmentError(BaselineCase):
    def setUp(self):
        super().setUp()
        self.write_config()

    def test_leaves_the_baseline_absent(self):
        """An empty baseline here would make every pre-existing finding look new."""
        self.invoke(verify=(2, {'error': {'kind': 'toolchain-missing'}}))
        self.assertFalse(self.baseline.exists())


class TestIdempotence(BaselineCase):
    def setUp(self):
        super().setUp()
        self.write_config()

    def test_an_existing_baseline_is_never_overwritten(self):
        """SessionStart fires again on clear/compact; the session's first sight must win."""
        self.invoke(verify=(1, envelope('original:1')))
        run = self.invoke(verify=(1, envelope('later:2')))
        run.assert_not_called()
        self.assertEqual(self.keys(), ['original:1'])


class TestInputBeforeConfig(BaselineCase):
    def test_config_is_checked_after_the_input_is_read(self):
        # read_hook_input() may move the process into a worktree that has the config the
        # starting directory lacks; a config check before it would see the wrong tree.
        def enter_configured_worktree():
            self.write_config()
            return {'session_id': SESSION}
        with mock.patch.object(h, 'changed_files', return_value=[]), \
                mock.patch.object(h, 'read_hook_input', side_effect=enter_configured_worktree):
            self.assertEqual(sb.main(), 0)
        self.assertTrue(self.baseline.exists())


if __name__ == '__main__':
    unittest.main()
