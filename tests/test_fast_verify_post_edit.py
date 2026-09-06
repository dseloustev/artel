"""The PostToolUse fast-verify hook: feedback on the file just edited, never a block.

It speaks up on exactly one condition — the fast gate came back red (exit 1) on a file that
is on the verify surface and exists on disk. Green, skipped and environment-error runs stay
silent, because per-edit noise on those helps nobody.
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
import fast_verify_post_edit as fv  # noqa: E402


def failing(tail, ok=False):
    return {'data': {'stages': [{'ok': ok, 'tail': tail}]}}


class FastVerifyCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def write_config(self, config=None):
        (self.root / '.artel').mkdir(exist_ok=True)
        (self.root / '.artel' / 'config.json').write_text(
            json.dumps(config or {}), encoding='utf-8')

    def touch(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x', encoding='utf-8')
        return path

    def invoke(self, rel='src/a.py', verify=(1, None)):
        code, env = verify
        payload = {'tool_input': {'file_path': rel}, 'cwd': str(self.root)}
        out = io.StringIO()
        with mock.patch.object(h, 'run_fast_verify',
                               return_value=(code, env if env is not None else {})) as run, \
                mock.patch.object(h, 'read_hook_input', return_value=payload), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fv.main(), 0, 'the hook never blocks')
        self.output = out.getvalue()
        return run

    def assertSilent(self, **kwargs):
        self.invoke(**kwargs)
        self.assertEqual(self.output, '')

    def assertContext(self, **kwargs):
        self.invoke(**kwargs)
        self.assertNotEqual(self.output, '', 'expected feedback')
        block = json.loads(self.output)['hookSpecificOutput']
        self.assertEqual(block['hookEventName'], 'PostToolUse')
        return block['additionalContext']


class TestSilentPaths(FastVerifyCase):
    def test_unconfigured_host(self):
        self.touch('src/a.py')
        run = self.invoke()
        run.assert_not_called()
        self.assertEqual(self.output, '')

    def test_file_not_on_disk(self):
        """A Write that failed, or a path outside the repo — nothing to report on."""
        self.write_config()
        run = self.invoke()
        run.assert_not_called()

    def test_empty_file_path(self):
        self.write_config()
        self.invoke(rel='')
        self.assertEqual(self.output, '')

    def test_off_surface_file_is_skipped(self):
        self.write_config({'verify': {'surface': ['lib/**/*.dart']}})
        self.touch('docs/readme.md')
        run = self.invoke(rel='docs/readme.md')
        run.assert_not_called()

    def test_excluded_by_a_negative_glob(self):
        self.write_config({'verify': {'surface': ['lib/**/*.dart', '!*.g.dart']}})
        self.touch('lib/app/model.g.dart')
        self.invoke(rel='lib/app/model.g.dart').assert_not_called()

    def test_green_verify_is_silent(self):
        self.write_config()
        self.touch('src/a.py')
        self.assertSilent(verify=(0, failing('', ok=True)))

    def test_environment_error_is_silent(self):
        self.write_config()
        self.touch('src/a.py')
        self.assertSilent(verify=(2, {'error': {'kind': 'toolchain-missing'}}))

    def test_red_with_no_tail_is_silent(self):
        self.write_config()
        self.touch('src/a.py')
        self.assertSilent(verify=(1, failing('   \n\n')))


class TestFeedback(FastVerifyCase):
    def setUp(self):
        super().setUp()
        self.write_config()
        self.touch('src/a.py')

    def test_names_the_file_and_carries_the_tail(self):
        context = self.assertContext(verify=(1, failing('error: undefined name')))
        self.assertIn('src/a.py', context)
        self.assertIn('error: undefined name', context)

    def test_passing_stages_are_excluded(self):
        env = {'data': {'stages': [{'ok': True, 'tail': 'analyzer clean'},
                                   {'ok': False, 'tail': 'test failed'}]}}
        context = self.assertContext(verify=(1, env))
        self.assertIn('test failed', context)
        self.assertNotIn('analyzer clean', context)

    def test_blank_lines_are_dropped(self):
        context = self.assertContext(verify=(1, failing('first\n\n   \nsecond')))
        self.assertEqual(context.splitlines()[1:], ['first', 'second'])

    def test_keeps_the_last_lines_not_the_first(self):
        """Most linters print the failure summary last, so the tail end is the useful end."""
        lines = ['line{}'.format(n) for n in range(20)]
        context = self.assertContext(verify=(1, failing('\n'.join(lines))))
        body = context.splitlines()[1:]
        self.assertEqual(len(body), fv.MAX_LINES)
        self.assertEqual(body[-1], 'line19')
        self.assertNotIn('line0', body)

    def test_verify_is_scoped_to_the_edited_file(self):
        run = self.invoke(verify=(1, failing('boom')))
        run.assert_called_once_with(['src/a.py'], timeout=120)

    def test_absolute_path_is_made_repo_relative(self):
        context = self.assertContext(rel=str(self.root / 'src/a.py'),
                                     verify=(1, failing('boom')))
        self.assertIn('fast verify findings on src/a.py', context)


if __name__ == '__main__':
    unittest.main()
