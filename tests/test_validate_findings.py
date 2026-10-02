"""`scripts/validate_findings.py`: anchors that hold, anchors that don't, scope that lies.

The reviewer's evidence contract says a guessed line number is worse than none — the
forecaster attaches findings to change units by `file:line`, and `deep-review.md` cites
them. This suite pins the mechanical half: diff parsing that reads new-side hunks (and
never mistakes an added line for a file header), the per-finding checks, and the CLI's
exit codes. Contract: agents/reviewer.md, `## Evidence and anchoring`.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import validate_findings  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'validate_findings.py'

DIFF = '''\
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 line1
+added
 line2
 line3
@@ -10,3 +11,3 @@
 line10
-line11
+line11 changed
 line12
'''

# An added line whose content is `++ b/not-a-file.py` renders as `+++ b/not-a-file.py`
# inside the hunk — a naive `+++ ` scan reads it as a second file.
DECOY = '''\
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 line1
+++ b/not-a-file.py
 line2
'''


def finding(**kw):
    base = {'lens': 'architecture', 'severity': 'medium', 'file': 'src/app.py',
            'line': 2, 'finding': 'x', 'recommendation': 'y'}
    base.update(kw)
    return base


class TestParseDiff(unittest.TestCase):

    def test_reads_the_new_side_hunks(self):
        self.assertEqual(validate_findings.parse_diff(DIFF),
                         {'src/app.py': [(1, 4), (11, 13)]})

    def test_an_added_line_that_looks_like_a_header_is_not_one(self):
        self.assertEqual(validate_findings.parse_diff(DECOY),
                         {'src/app.py': [(1, 3)]})

    def test_an_omitted_count_means_one_line(self):
        text = '--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n'
        self.assertEqual(validate_findings.parse_diff(text), {'x.py': [(1, 1)]})

    def test_a_deleted_file_has_no_new_side(self):
        text = '--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n'
        self.assertEqual(validate_findings.parse_diff(text), {})


class TestChecks(unittest.TestCase):

    def setUp(self):
        self.hunks = validate_findings.parse_diff(DIFF)

    def check(self, **kw):
        return validate_findings.check_findings([finding(**kw)], self.hunks)[0]

    def test_a_line_inside_a_hunk_is_ok(self):
        self.assertTrue(self.check(line=2).ok)

    def test_a_line_outside_every_hunk_fails_with_the_ranges(self):
        result = self.check(line=5)
        self.assertFalse(result.ok)
        self.assertIn('1-4', result.reason)
        self.assertIn('11-13', result.reason)

    def test_a_file_not_in_the_diff_fails(self):
        result = self.check(file='src/other.py')
        self.assertFalse(result.ok)
        self.assertIn('not in the diff', result.reason)

    def test_a_file_level_finding_on_a_changed_file_is_ok(self):
        self.assertTrue(self.check(line=None).ok)

    def test_repository_scope_outside_the_hunks_is_ok(self):
        self.assertTrue(self.check(line=5, scope='repository').ok)

    def test_repository_scope_inside_a_hunk_fails(self):
        result = self.check(line=12, scope='repository')
        self.assertFalse(result.ok)
        self.assertIn('changed hunk', result.reason)

    def test_repository_scope_on_an_untouched_file_is_ok(self):
        self.assertTrue(self.check(file='src/other.py', line=3, scope='repository').ok)

    def test_a_missing_file_is_an_input_error(self):
        with self.assertRaises(validate_findings.InputError):
            self.check(file=None)

    def test_an_unknown_scope_is_an_input_error(self):
        with self.assertRaises(validate_findings.InputError):
            self.check(scope='sideways')

    def test_a_non_positive_line_is_an_input_error(self):
        with self.assertRaises(validate_findings.InputError):
            self.check(line=0)

    def test_a_string_line_is_an_input_error(self):
        with self.assertRaises(validate_findings.InputError):
            self.check(line='12')


class RepoCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        (self.root / 'src').mkdir()
        self.app = self.root / 'src' / 'app.py'
        self.app.write_text(''.join('line{}\n'.format(n) for n in range(1, 21)),
                            encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'init')

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True)

    def cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, input=stdin)

    def write_findings(self, *items):
        path = self.root / 'findings.json'
        path.write_text(json.dumps(list(items)), encoding='utf-8')
        return str(path)

    def change_line_ten(self):
        text = self.app.read_text(encoding='utf-8').replace('line10\n', 'line10 changed\n')
        self.app.write_text(text, encoding='utf-8')


class TestCli(RepoCase):

    def test_clean_findings_exit_zero_and_a_bad_anchor_exits_one(self):
        self.change_line_ten()
        good = self.write_findings(finding(line=10))
        proc = self.cli(good, '--range', 'HEAD')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('0 failed', proc.stdout)
        bad = self.write_findings(finding(line=2), finding(line=10))
        proc = self.cli(bad, '--range', 'HEAD')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('1 failed', proc.stdout)
        self.assertIn('FAIL', proc.stdout)

    def test_findings_from_stdin_with_a_diff_file(self):
        self.change_line_ten()
        patch_path = self.root / 'change.patch'
        patch_path.write_text(self.git('diff', 'HEAD').stdout, encoding='utf-8')
        proc = self.cli('-', '--diff', str(patch_path),
                        stdin=json.dumps([finding(line=10)]))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_invalid_json_is_exit_two(self):
        path = self.root / 'findings.json'
        path.write_text('{not json', encoding='utf-8')
        proc = self.cli(str(path), '--range', 'HEAD')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('JSON', proc.stderr)

    def test_a_finding_that_is_not_an_object_is_exit_two(self):
        path = self.write_findings('nope')
        proc = self.cli(path, '--range', 'HEAD')
        self.assertEqual(proc.returncode, 2)


if __name__ == '__main__':
    unittest.main()
