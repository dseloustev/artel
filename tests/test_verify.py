import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import verify  # noqa: E402


class TestNormalizeKeys(unittest.TestCase):
    def test_strips_ansi_and_digits(self):
        out = '\x1b[31msrc/app.ts:10:5 error no-unused-vars\x1b[0m'
        self.assertEqual(verify.normalize_keys(out, 0),
                         ['s0:src/app.ts:: error no-unused-vars'])

    def test_collapses_whitespace_and_drops_empty_lines(self):
        out = 'a   b\n\n   \n\tc  d\n'
        self.assertEqual(verify.normalize_keys(out, 1), ['s1:a b', 's1:c d'])

    def test_dedupes_repeated_lines(self):
        out = 'warn: x1\nwarn: x2\nother\n'
        # digit-stripping folds x1/x2 into one key
        self.assertEqual(verify.normalize_keys(out, 0), ['s0:warn: x', 's0:other'])

    def test_caps_at_max_keys_per_stage(self):
        # digit-free lines so digit-stripping cannot fold them together
        lines = '\n'.join('issue-' + ('x' * (i + 1)) for i in range(250))
        keys = verify.normalize_keys(lines, 0)
        self.assertEqual(len(keys), verify.MAX_KEYS_PER_STAGE)

    def test_prefixes_stage_index(self):
        self.assertEqual(verify.normalize_keys('boom', 3), ['s3:boom'])


class TestSubstituteFiles(unittest.TestCase):
    def test_replaces_placeholder_with_quoted_paths(self):
        cmd = verify.substitute_files('eslint {files}', ['a.ts', 'dir with space/b.ts'])
        self.assertEqual(cmd, "eslint a.ts 'dir with space/b.ts'")

    def test_without_placeholder_returns_command_unchanged(self):
        self.assertEqual(verify.substitute_files('npm run lint', ['a.ts']), 'npm run lint')


class TestClassifyExit(unittest.TestCase):
    def test_zero_is_ok(self):
        self.assertEqual(verify.classify_exit(0), 'ok')

    def test_126_and_127_are_env_errors(self):
        self.assertEqual(verify.classify_exit(126), 'env_error')
        self.assertEqual(verify.classify_exit(127), 'env_error')

    def test_other_nonzero_is_findings(self):
        for code in (1, 2, 3, 100, 255):
            self.assertEqual(verify.classify_exit(code), 'findings')


class TestParseArgs(unittest.TestCase):
    def test_parses_legacy_fast_files_timeout(self):
        inv = verify.parse_args(['--fast', '--files', 'a.py,b.py', '--timeout', '90'])
        self.assertIsNone(inv['gate'])
        self.assertTrue(inv['fast'])
        self.assertEqual(inv['files'], ['a.py', 'b.py'])
        self.assertEqual(inv['timeout'], 90)
        self.assertFalse(inv['record_baseline'])
        self.assertIsNone(inv['ticket'])

    def test_defaults(self):
        inv = verify.parse_args([])
        self.assertEqual(inv, {'gate': None, 'fast': False, 'files': None,
                               'timeout': verify.DEFAULT_TIMEOUT,
                               'record_baseline': False, 'ticket': None})

    def test_rejects_blank_files(self):
        for blank in ('', '   ', ',,,'):
            with self.assertRaises(ValueError):
                verify.parse_args(['--files', blank])

    def test_rejects_unknown_flag(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['--paths', 'a.py'])

    def test_task_gate_needs_files(self):
        inv = verify.parse_args(['task', '--files', 'lib/a.dart'])
        self.assertEqual(inv['gate'], 'task')
        self.assertEqual(inv['files'], ['lib/a.dart'])
        with self.assertRaises(ValueError) as ctx:
            verify.parse_args(['task'])
        self.assertIn('--files', str(ctx.exception))

    def test_checkpoint_gate_flags(self):
        inv = verify.parse_args(['checkpoint', '--record-baseline', '--ticket', 'AW-12-3'])
        self.assertEqual(inv['gate'], 'checkpoint')
        self.assertTrue(inv['record_baseline'])
        self.assertEqual(inv['ticket'], 'AW-12-3')
        self.assertIsNone(inv['files'])

    def test_unknown_gate_token_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            verify.parse_args(['final'])
        self.assertIn('final', str(ctx.exception))

    def test_checkpoint_only_flags_are_rejected_elsewhere(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['--record-baseline'])
        with self.assertRaises(ValueError):
            verify.parse_args(['task', '--files', 'a.py', '--ticket', 'AW-1'])
        with self.assertRaises(ValueError):
            verify.parse_args(['--ticket', 'AW-1'])

    def test_fast_belongs_to_the_legacy_form(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['task', '--fast', '--files', 'a.py'])

    def test_blank_ticket_is_rejected(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['checkpoint', '--ticket', '   '])
        with self.assertRaises(ValueError):
            verify.parse_args(['checkpoint', '--ticket'])


class GateCase(unittest.TestCase):
    """A temp checkout as cwd, with .artel/config.json written per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, previous)
        os.makedirs('.artel', exist_ok=True)

    def write_config(self, verify_cfg=None, **extra):
        cfg = {'verify': verify_cfg or {}}
        cfg.update(extra)
        Path('.artel/config.json').write_text(json.dumps(cfg), encoding='utf-8')

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = verify.main(argv)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1, 'exactly one envelope line')
        return code, json.loads(lines[0])


GREEN = "sh -c 'echo ok {files}; exit 0'"
RED = "sh -c 'echo lib/a.py:3:1 error boom; exit 1'"
RED_OTHER = "sh -c 'echo test/a_test.py:9 FAILED; exit 1'"
MISSING = 'no-such-command-artel-gate-test'


class TestLegacyForm(GateCase):
    def test_no_commands_is_skipped_exit_0(self):
        self.write_config({})
        code, env = self.run_main([])
        self.assertEqual(code, 0)
        self.assertTrue(env['ok'])
        self.assertEqual(env['verb'], 'verify')
        self.assertEqual(env['data'], {'skipped': True, 'stages': []})

    def test_green_commands_keep_the_old_fields_and_gain_a_name(self):
        self.write_config({'commands': [GREEN, GREEN]})
        code, env = self.run_main([])
        self.assertEqual(code, 0)
        self.assertFalse(env['data']['skipped'])
        self.assertEqual([s['name'] for s in env['data']['stages']], ['s0', 's1'])
        for stage in env['data']['stages']:
            for field in ('command', 'exit_code', 'ok', 'keys', 'tail'):
                self.assertIn(field, stage)
            self.assertTrue(stage['ok'])
            self.assertEqual(stage['keys'], [])

    def test_red_stage_stops_the_chain_and_exits_1(self):
        self.write_config({'commands': [RED, GREEN]})
        code, env = self.run_main([])
        self.assertEqual(code, 1)
        self.assertFalse(env['ok'])
        self.assertEqual(len(env['data']['stages']), 1)
        self.assertEqual(env['data']['stages'][0]['keys'], ['s0:lib/a.py:: error boom'])

    def test_missing_command_is_an_environment_error(self):
        self.write_config({'commands': [MISSING]})
        code, env = self.run_main([])
        self.assertEqual(code, 2)
        self.assertEqual(env['error']['kind'], 'command_not_found')

    def test_fast_form_substitutes_files(self):
        self.write_config({'fast': GREEN})
        code, env = self.run_main(['--fast', '--files', 'a.py,b.py'])
        self.assertEqual(code, 0)
        self.assertIn('a.py b.py', env['data']['stages'][0]['command'])
        self.assertEqual(env['data']['stages'][0]['name'], 's0')


if __name__ == '__main__':
    unittest.main()
