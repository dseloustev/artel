import sys
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
    def test_parses_fast_files_timeout(self):
        fast, files, timeout = verify.parse_args(
            ['--fast', '--files', 'a.py,b.py', '--timeout', '90'])
        self.assertTrue(fast)
        self.assertEqual(files, ['a.py', 'b.py'])
        self.assertEqual(timeout, 90)

    def test_defaults(self):
        fast, files, timeout = verify.parse_args([])
        self.assertFalse(fast)
        self.assertIsNone(files)
        self.assertEqual(timeout, verify.DEFAULT_TIMEOUT)

    def test_rejects_blank_files(self):
        for blank in ('', '   ', ',,,'):
            with self.assertRaises(ValueError):
                verify.parse_args(['--files', blank])

    def test_rejects_unknown_flag(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['--paths', 'a.py'])


if __name__ == '__main__':
    unittest.main()
