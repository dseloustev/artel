"""`scripts/review_package.py`: snapshots that never touch the index, packages that carry
the whole task diff.

The implementer does not commit, so the per-task review has no BASE..HEAD to read. The
script's whole contract is: a snapshot before the dispatch, a package after it, and no
side effect on the checkout in between — the phase checkpoint stages explicitly, and a
snapshot that staged files would silently widen that commit.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import review_package  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'review_package.py'


def run(cmd, cwd, check=True):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError('{} failed: {}'.format(cmd, proc.stderr))
    return proc


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        run(['git', 'init', '-q'], self.root)
        run(['git', 'config', 'user.email', 'test@example.com'], self.root)
        run(['git', 'config', 'user.name', 'Test'], self.root)
        (self.root / '.gitignore').write_text('ignored.log\n', encoding='utf-8')
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'app.py').write_text('print(1)\n', encoding='utf-8')
        run(['git', 'add', '-A'], self.root)
        run(['git', 'commit', '-q', '-m', 'init'], self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def porcelain(self):
        return run(['git', 'status', '--porcelain'], self.root).stdout

    def staged(self):
        return run(['git', 'diff', '--cached', '--name-only'], self.root).stdout


class TestSnapshot(RepoCase):
    def test_snapshot_is_a_tree_and_leaves_the_index_alone(self):
        (self.root / 'src' / 'new.py').write_text('x = 1\n', encoding='utf-8')
        before = self.porcelain()
        tree = review_package.snapshot_tree(self.root)
        self.assertEqual(review_package.object_type(self.root, tree), 'tree')
        self.assertEqual(self.staged(), '', 'snapshot staged files in the real index')
        self.assertEqual(self.porcelain(), before, 'snapshot changed git status')

    def test_snapshot_includes_untracked_and_respects_gitignore(self):
        (self.root / 'src' / 'new.py').write_text('x = 1\n', encoding='utf-8')
        (self.root / 'ignored.log').write_text('noise\n', encoding='utf-8')
        tree = review_package.snapshot_tree(self.root)
        listing = run(['git', 'ls-tree', '-r', '--name-only', tree], self.root).stdout
        self.assertIn('src/new.py', listing)
        self.assertNotIn('ignored.log', listing)

    def test_same_tree_twice_when_nothing_changed(self):
        self.assertEqual(review_package.snapshot_tree(self.root),
                         review_package.snapshot_tree(self.root))


class TestPackage(RepoCase):
    def test_package_carries_modified_and_new_files_only(self):
        base = review_package.snapshot_tree(self.root)
        (self.root / 'src' / 'app.py').write_text('print(2)\n', encoding='utf-8')
        (self.root / 'src' / 'new.py').write_text('x = 1\n', encoding='utf-8')
        (self.root / 'ignored.log').write_text('noise\n', encoding='utf-8')
        head = review_package.snapshot_tree(self.root)
        text, files = review_package.build_package(self.root, base, head)
        self.assertEqual(sorted(files), ['src/app.py', 'src/new.py'])
        self.assertIn('-print(1)', text)
        self.assertIn('+print(2)', text)
        self.assertIn('+x = 1', text)
        self.assertNotIn('ignored.log', text)
        self.assertTrue(text.startswith('# Review package: {}..{}'.format(base[:7], head[:7])))

    def test_empty_diff_is_an_explicit_package(self):
        base = review_package.snapshot_tree(self.root)
        text, files = review_package.build_package(self.root, base, base)
        self.assertEqual(files, [])
        self.assertIn('(no changes)', text)

    def test_a_commit_is_accepted_as_base(self):
        (self.root / 'src' / 'app.py').write_text('print(3)\n', encoding='utf-8')
        head = review_package.snapshot_tree(self.root)
        _text, files = review_package.build_package(self.root, 'HEAD', head)
        self.assertEqual(files, ['src/app.py'])


class TestCli(RepoCase):
    def cli(self, *args, cwd=None):
        return run([sys.executable, str(SCRIPT), *args], cwd or self.root, check=False)

    def test_snapshot_then_diff_writes_the_package_and_reports_counts(self):
        base = self.cli('snapshot').stdout.strip()
        self.assertEqual(review_package.object_type(self.root, base), 'tree')
        (self.root / 'src' / 'app.py').write_text('print(2)\n', encoding='utf-8')
        out = self.root / '.artel' / 'run' / 'T-1' / 'reports' / '001-task.diff'
        proc = self.cli('diff', base, '--out', str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(out.is_file(), 'package not written (parent dirs must be created)')
        self.assertEqual(proc.stdout.strip(),
                         'wrote {}: 1 file(s), {} bytes'.format(out, len(out.read_bytes())))
        self.assertEqual(self.staged(), '')

    def test_diff_runs_from_a_subdirectory(self):
        base = self.cli('snapshot').stdout.strip()
        (self.root / 'src' / 'app.py').write_text('print(2)\n', encoding='utf-8')
        out = self.root / 'pkg.diff'
        proc = self.cli('diff', base, '--out', str(out), cwd=self.root / 'src')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('src/app.py', out.read_text(encoding='utf-8'))

    def test_unknown_base_is_exit_2(self):
        proc = self.cli('diff', 'deadbeef', '--out', str(self.root / 'x.diff'))
        self.assertEqual(proc.returncode, 2)
        self.assertIn('BASE', proc.stderr)
        self.assertFalse((self.root / 'x.diff').exists())

    def test_outside_a_repo_is_exit_2(self):
        with tempfile.TemporaryDirectory() as bare:
            env_home = os.environ.get('HOME')
            proc = subprocess.run([sys.executable, str(SCRIPT), 'snapshot'], cwd=bare,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, (proc.stdout, proc.stderr, env_home))


if __name__ == '__main__':
    unittest.main()
