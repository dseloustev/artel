"""scripts/worktree.py: moving a ticket's work into its own worktree and handing it back.

Every test builds a real bare origin plus a clone, so the git behavior under test is git's
own. The invariants worth the most: nothing uncommitted is ever lost (a stash is dropped
only after it applied), the branch is never deleted, and a refused run changes nothing.

Contract: docs/worktrees.md
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'worktree.py'
BRANCH = 'feature/T-1-work'

sys.path.insert(0, str(ROOT / 'scripts'))
import worktree  # noqa: E402


def git(cwd, *args):
    return subprocess.run(['git'] + list(args), cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self._tmp.name))
        origin = base / 'origin.git'
        git(base, 'init', '-q', '--bare', str(origin))
        git(origin, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        self.root = base / 'host'
        git(base, 'clone', '-q', str(origin), str(self.root))
        git(self.root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.root, 'config', key, value)
        self.write('.gitignore', '.artel/\n.claude/\n.mcp.json\nbuild/\n')
        self.write('app.txt', 'v1\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'init')
        git(self.root, 'push', '-q', 'origin', 'main')
        git(self.root, 'branch', BRANCH)
        self.target = self.root / '.claude' / 'worktrees' / 'T-1'

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text, base=None):
        path = (base or self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def read(self, rel, base=None):
        return ((base or self.root) / rel).read_text(encoding='utf-8')

    def run_script(self, *args, cwd=None):
        proc = subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                              cwd=cwd or self.root, capture_output=True, text=True)
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            self.fail('no JSON report: stdout={!r} stderr={!r}'.format(proc.stdout, proc.stderr))
        return proc.returncode, report

    def move_in(self, *extra, branch=BRANCH, name='T-1', cwd=None):
        return self.run_script('move-in', '--ticket', 'T-1', '--name', name, '--branch', branch,
                               '--base', 'main', *extra, cwd=cwd)

    def hand_back(self, *extra, cwd=None):
        return self.run_script('hand-back', '--ticket', 'T-1', *extra, cwd=cwd)

    def branch_of(self, checkout):
        return git(checkout, 'branch', '--show-current')

    def stashes(self):
        return git(self.root, 'stash', 'list')

    def status(self, checkout):
        return git(checkout, 'status', '--porcelain', '--untracked-files=all')

    def worktree_paths(self):
        return [line[len('worktree '):] for line in
                git(self.root, 'worktree', 'list', '--porcelain').splitlines()
                if line.startswith('worktree ')]


class TestMoveInGit(RepoCase):
    def test_moves_an_existing_branch(self):
        code, report = self.move_in()
        self.assertEqual((code, report['status']), (0, 'ok'), report)
        self.assertEqual(self.branch_of(self.target), BRANCH)
        self.assertEqual(self.branch_of(self.root), 'main')
        self.assertFalse(report['created'])
        self.assertFalse(report['stashApplied'])
        self.assertEqual(report['path'], str(self.target))

    def test_creates_a_new_branch_from_a_ref(self):
        code, report = self.move_in('--create-from', 'origin/main', branch='feature/T-1-new')
        self.assertEqual(code, 0, report)
        self.assertTrue(report['created'])
        self.assertEqual(self.branch_of(self.target), 'feature/T-1-new')
        self.assertEqual(git(self.target, 'rev-parse', 'HEAD'),
                         git(self.root, 'rev-parse', 'origin/main'))

    def test_tracks_an_origin_only_branch(self):
        git(self.root, 'push', '-q', 'origin', BRANCH)
        git(self.root, 'branch', '-D', BRANCH)
        code, report = self.move_in('--create-from', 'origin/' + BRANCH, '--track')
        self.assertEqual(code, 0, report)
        self.assertEqual(git(self.target, 'rev-parse', '--abbrev-ref', '@{u}'), 'origin/' + BRANCH)

    def test_carries_uncommitted_work_off_the_ticket_branch(self):
        git(self.root, 'checkout', '-q', BRANCH)
        self.write('app.txt', 'v1\nwip\n')
        self.write('notes.txt', 'untracked\n')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        self.assertTrue(report['stashApplied'])
        self.assertEqual(report['mainNowOn'], 'main')
        self.assertEqual(self.branch_of(self.root), 'main')
        self.assertEqual(self.status(self.root), '')
        self.assertEqual(self.read('app.txt', self.target), 'v1\nwip\n')
        self.assertEqual(self.read('notes.txt', self.target), 'untracked\n')
        self.assertEqual(self.stashes(), '')

    def test_leaves_other_sessions_stashes_alone(self):
        self.write('app.txt', 'someone else\n')
        git(self.root, 'stash', 'push', '-q', '-m', 'another session')
        git(self.root, 'checkout', '-q', BRANCH)
        self.write('app.txt', 'v1\nwip\n')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        self.assertEqual(self.read('app.txt', self.target), 'v1\nwip\n')
        self.assertEqual(git(self.root, 'stash', 'list', '--format=%gs'), 'On main: another session')

    def test_a_second_run_enters_the_same_worktree(self):
        self.move_in()
        self.write('app.txt', 'dirty\n')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        self.assertTrue(report['alreadyExisted'])
        self.assertTrue(report['mainDirty'])
        self.assertEqual(self.read('app.txt'), 'dirty\n', 'a re-run leaves the main checkout alone')

    def test_refused_inside_a_worktree(self):
        self.move_in()
        code, report = self.move_in(branch='feature/T-1-other', name='T-1-other', cwd=self.target)
        self.assertEqual((code, report['status']), (1, 'refused'), report)

    def test_refused_when_the_branch_lives_in_another_worktree(self):
        elsewhere = self.root.parent / 'elsewhere'
        git(self.root, 'worktree', 'add', '-q', str(elsewhere), BRANCH)
        self.write('app.txt', 'dirty\n')
        code, report = self.move_in()
        self.assertEqual((code, report['status']), (1, 'refused'), report)
        self.assertEqual(self.read('app.txt'), 'dirty\n')
        self.assertEqual(self.stashes(), '')

    def test_refused_when_the_target_is_not_a_worktree(self):
        self.write('.claude/worktrees/T-1/stray.txt', 'x\n')
        code, report = self.move_in()
        self.assertEqual((code, report['status']), (1, 'refused'), report)

    def test_a_failed_worktree_add_rolls_back(self):
        git(self.root, 'checkout', '-q', BRANCH)
        self.write('app.txt', 'v1\nwip\n')
        self.write('.claude/worktrees', 'a file where the directory should be\n')
        code, report = self.move_in()
        self.assertEqual((code, report['status']), (1, 'rolled-back'), report)
        self.assertEqual(self.branch_of(self.root), BRANCH)
        self.assertEqual(self.read('app.txt'), 'v1\nwip\n')
        self.assertEqual(self.stashes(), '')

    def test_a_stash_conflict_keeps_the_stash(self):
        git(self.root, 'checkout', '-q', '-b', 'feature/T-1-diverged')
        self.write('app.txt', 'v2\n')
        git(self.root, 'commit', '-q', '-am', 'diverge')
        git(self.root, 'checkout', '-q', 'main')
        self.write('app.txt', 'v1\nwip\n')
        self.write('.artel/config.json', '{}\n')
        code, report = self.move_in(branch='feature/T-1-diverged')
        self.assertEqual((code, report['status']), (1, 'conflict'), report)
        self.assertIn(report['stash'], git(self.root, 'stash', 'list', '--format=%H'))
        self.assertIn('app.txt', report['files'])
        self.assertTrue((self.target / '.artel' / 'config.json').is_file(),
                        'the environment is in place before the stash is applied')
        subjects = git(self.root, 'stash', 'list', '--format=%H %gs').splitlines()
        mine = [s for s in subjects if s.startswith(report['stash'] + ' ')]
        self.assertEqual(len(mine), 1)
        self.assertIn(': artel move-to-worktree T-1 ', mine[0])

    def test_excludes_worktrees_locally_when_the_host_does_not(self):
        self.write('.gitignore', '.artel/\n')
        git(self.root, 'commit', '-q', '-am', 'narrow ignore')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        exclude = self.read('.git/info/exclude')
        self.assertIn('/.claude/worktrees/', exclude)
        self.assertEqual(self.status(self.root), '')


class TestMoveInEnvironment(RepoCase):
    def test_copies_the_artel_footprint_but_not_run_or_context(self):
        self.write('.artel/config.json', '{"version": 1}\n')
        self.write('.artel/sensitive-paths.json', '{}\n')
        self.write('.artel/templates/issue-draft.md', '# t\n')
        self.write('.artel/run/T-9/run-state.json', '{}\n')
        self.write('.artel/context/root/CLAUDE.md', '# c\n')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        for rel in ('.artel/config.json', '.artel/sensitive-paths.json',
                    '.artel/templates/issue-draft.md'):
            self.assertEqual(self.read(rel, self.target), self.read(rel), rel)
        self.assertFalse((self.target / '.artel/run/T-9').exists())
        self.assertTrue(self.root.joinpath('.artel/run/T-9').is_dir(), 'other tickets stay')

    def test_links_the_context_store(self):
        self.write('.artel/context/root/CLAUDE.md', '# c\n')
        code, report = self.move_in()
        self.assertTrue(report['contextLinked'])
        link = self.target / '.artel' / 'context'
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.root / '.artel' / 'context').resolve())

    def test_no_link_without_a_store(self):
        code, report = self.move_in()
        self.assertFalse(report['contextLinked'])
        self.assertFalse(os.path.lexists(self.target / '.artel' / 'context'))

    def test_moves_the_ticket_run_state(self):
        self.write('.artel/run/T-1/run-state.json', '{"run_active": true}\n')
        code, report = self.move_in()
        self.assertTrue(report['runMoved'])
        self.assertFalse((self.root / '.artel/run/T-1').exists())
        self.assertEqual(self.read('.artel/run/T-1/run-state.json', self.target),
                         '{"run_active": true}\n')

    def test_copies_session_baselines_but_not_logs(self):
        self.write('.artel/run/.hooks/baseline-s1.json', '{"keys": []}\n')
        self.write('.artel/run/.hooks/stopblocks-s1.json', '{"consecutive": 0}\n')
        self.write('.artel/run/.hooks/knowledge-mirror.log', 'log\n')
        self.move_in()
        hooks = self.target / '.artel/run/.hooks'
        self.assertTrue((hooks / 'baseline-s1.json').is_file())
        self.assertTrue((hooks / 'stopblocks-s1.json').is_file())
        self.assertFalse((hooks / 'knowledge-mirror.log').exists())
        self.assertTrue((self.root / '.artel/run/.hooks/baseline-s1.json').is_file(), 'copied')

    def test_default_host_files(self):
        self.write('.claude/settings.json', '{}\n')
        self.write('.claude/tools/agent/agent.dart', 'main() {}\n')
        self.write('.mcp.json', '{}\n')
        self.write('build/out.bin', 'binary\n')
        code, report = self.move_in()
        for rel in ('.claude/settings.json', '.claude/tools/agent/agent.dart', '.mcp.json'):
            self.assertTrue((self.target / rel).is_file(), rel)
            self.assertIn(rel, report['copied'])
        self.assertFalse((self.target / 'build').exists())

    def test_worktreeinclude_replaces_the_default(self):
        self.write('.worktreeinclude', '/.mcp.json\nbuild/keep.txt\n')
        git(self.root, 'add', '.worktreeinclude')
        git(self.root, 'commit', '-q', '-m', 'include')
        self.write('.claude/settings.json', '{}\n')
        self.write('.mcp.json', '{}\n')
        self.write('build/keep.txt', 'k\n')
        self.write('build/drop.txt', 'd\n')
        self.write('.artel/config.json', '{}\n')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        self.assertTrue((self.target / '.mcp.json').is_file())
        self.assertTrue((self.target / 'build/keep.txt').is_file())
        self.assertFalse((self.target / 'build/drop.txt').exists())
        self.assertFalse((self.target / '.claude').exists())
        self.assertTrue((self.target / '.artel/config.json').is_file(), 'artel is always handled')

    def test_never_copies_tracked_or_untracked_files(self):
        self.write('.worktreeinclude', '*.txt\n')
        git(self.root, 'add', '.worktreeinclude')
        git(self.root, 'commit', '-q', '-m', 'include')
        code, report = self.move_in()
        self.assertNotIn('app.txt', report['copied'])

    def test_never_overwrites_a_file_the_branch_tracks(self):
        git(self.root, 'checkout', '-q', BRANCH)
        self.write('.mcp.json', 'from the branch\n')
        git(self.root, 'add', '-f', '.mcp.json')
        git(self.root, 'commit', '-q', '-m', 'track mcp on the branch')
        git(self.root, 'checkout', '-q', 'main')
        mine = self.write('.mcp.json', 'from main\n')
        future = time.time() + 3600  # newer than the checkout: only the tracked filter saves it
        os.utime(mine, (future, future))
        code, report = self.move_in()
        self.assertEqual(code, 0, report)
        self.assertEqual(self.read('.mcp.json', self.target), 'from the branch\n')
        self.assertNotIn('.mcp.json', report['copied'])

    def test_never_copies_nested_worktrees(self):
        self.write('.claude/settings.json', '{}\n')
        self.move_in()
        git(self.root, 'branch', 'feature/T-2-work')
        code, report = self.run_script('move-in', '--ticket', 'T-2', '--name', 'T-2',
                                       '--branch', 'feature/T-2-work', '--base', 'main')
        self.assertEqual(code, 0, report)
        second = self.root / '.claude/worktrees/T-2'
        self.assertTrue((second / '.claude/settings.json').is_file())
        self.assertFalse((second / '.claude/worktrees').exists())

    def test_writes_a_manifest(self):
        self.write('.mcp.json', '{}\n')
        self.move_in()
        manifest = json.loads(self.read('.artel/worktree.json', self.target))
        self.assertEqual(manifest['ticket'], 'T-1')
        self.assertEqual(manifest['branch'], BRANCH)
        self.assertEqual(manifest['copied'], ['.mcp.json'])
        self.assertEqual(manifest['main'], str(self.root))


class TestMoveInRecovery(RepoCase):
    """Failure paths that need a fault injected mid-run: nothing is lost, and the report
    points at the kept stash and at where the main checkout really is."""

    def setUp(self):
        super().setUp()
        old_cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, old_cwd)
        git(self.root, 'checkout', '-q', BRANCH)
        self.write('app.txt', 'v1\nwip\n')

    def args(self):
        return worktree.parse(['move-in', '--ticket', 'T-1', '--name', 'T-1',
                               '--branch', BRANCH, '--base', 'main'])

    def failing(self, match):
        real = worktree.git

        def fake(*args, **kwargs):
            if match(args):
                return subprocess.CompletedProcess(args, 1, '', 'injected failure')
            return real(*args, **kwargs)
        return mock.patch.object(worktree, 'git', side_effect=fake)

    def run_move_in(self):
        with self.assertRaises(worktree.Stop) as caught:
            worktree.move_in(self.args())
        return caught.exception.report

    def assert_stash_kept(self, report):
        self.assertIn(report['stash'], git(self.root, 'stash', 'list', '--format=%H'))

    def test_an_exclude_failure_changes_nothing(self):
        with mock.patch.object(worktree, 'ensure_excluded', side_effect=OSError('read-only')):
            with self.assertRaises(OSError):
                worktree.move_in(self.args())
        self.assertEqual(self.branch_of(self.root), BRANCH)
        self.assertEqual(self.read('app.txt'), 'v1\nwip\n')
        self.assertEqual(self.stashes(), '')

    def test_a_failed_stash_reapply_is_an_error_that_names_the_stash(self):
        self.write('.claude/worktrees', 'a file where the directory should be\n')
        with self.failing(lambda args: args[:2] == ('stash', 'apply')):
            report = self.run_move_in()
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['mainNowOn'], BRANCH)
        self.assert_stash_kept(report)

    def test_a_failed_restore_checkout_keeps_the_stash_off_the_wrong_branch(self):
        self.write('.claude/worktrees', 'a file where the directory should be\n')
        with self.failing(lambda args: args == ('checkout', BRANCH)):
            report = self.run_move_in()
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['mainNowOn'], 'main')
        self.assert_stash_kept(report)
        self.assertEqual(self.status(self.root), '')

    def test_a_failure_after_the_worktree_exists_names_the_stash_and_path(self):
        with mock.patch.object(worktree, 'transfer_in', side_effect=OSError('disk full')):
            report = self.run_move_in()
        self.assertEqual(report['status'], 'error')
        self.assertIn('OSError: disk full', report['reason'])
        self.assertEqual(report['path'], str(self.target))
        self.assertEqual(report['mainNowOn'], 'main')
        self.assert_stash_kept(report)
        self.assertTrue(self.target.is_dir())


class HandBackCase(RepoCase):
    def setUp(self):
        super().setUp()
        self.write('.claude/settings.local.json', '{"allow": []}\n')
        code, report = self.move_in()
        self.assertEqual(code, 0, report)


class TestHandBack(HandBackCase):
    def test_returns_the_branch_with_its_work(self):
        self.write('app.txt', 'v1\ncommitted\n', self.target)
        git(self.target, 'commit', '-q', '-am', 'work')
        self.write('app.txt', 'v1\ncommitted\nwip\n', self.target)
        self.write('notes.txt', 'untracked\n', self.target)
        code, report = self.hand_back()
        self.assertEqual((code, report['status']), (0, 'ok'), report)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.worktree_paths(), [str(self.root)])
        self.assertEqual(self.branch_of(self.root), BRANCH)
        self.assertEqual(report['mainWasOn'], 'main')
        self.assertTrue(report['stashApplied'])
        self.assertEqual(self.read('app.txt'), 'v1\ncommitted\nwip\n')
        self.assertEqual(self.read('notes.txt'), 'untracked\n')
        self.assertEqual(self.stashes(), '')
        self.assertIn(BRANCH, git(self.root, 'branch', '--list', BRANCH))

    def test_a_clean_worktree_needs_no_stash(self):
        code, report = self.hand_back()
        self.assertEqual(code, 0, report)
        self.assertFalse(report['stashApplied'])
        self.assertEqual(self.branch_of(self.root), BRANCH)

    def test_check_changes_nothing(self):
        self.write('app.txt', 'wip\n', self.target)
        code, report = self.hand_back('--check')
        self.assertEqual(code, 0, report)
        self.assertTrue(report['check'])
        self.assertEqual(report['uncommitted'], 1)
        self.assertEqual(report['branch'], BRANCH)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(self.stashes(), '')
        self.assertEqual(self.branch_of(self.root), 'main')

    def test_refused_when_the_main_checkout_is_dirty(self):
        self.write('app.txt', 'someone else\n')
        code, report = self.hand_back()
        self.assertEqual((code, report['status']), (1, 'refused'), report)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(self.read('app.txt'), 'someone else\n')

    def test_refused_inside_the_worktree(self):
        code, report = self.hand_back(cwd=self.target)
        self.assertEqual((code, report['status']), (1, 'refused'), report)
        self.assertTrue(self.target.is_dir())

    def test_check_runs_from_inside_the_worktree(self):
        code, report = self.hand_back('--check', cwd=self.target)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['branch'], BRANCH)
        self.assertEqual(report['mainWasOn'], 'main')

    def test_refused_on_a_detached_worktree(self):
        git(self.target, 'checkout', '-q', '--detach')
        code, report = self.hand_back()
        self.assertEqual((code, report['status']), (1, 'refused'), report)

    def test_refused_with_nothing_to_hand_back(self):
        code, report = self.run_script('hand-back', '--ticket', 'T-404')
        self.assertEqual((code, report['status']), (1, 'refused'), report)

    def test_moves_run_state_and_baselines_back(self):
        self.write('.artel/run/T-1/run-state.json', '{"completed": true}\n', self.target)
        self.write('.artel/run/.hooks/baseline-s1.json', '{"keys": ["k"]}\n', self.target)
        code, report = self.hand_back()
        self.assertTrue(report['runMoved'])
        self.assertEqual(self.read('.artel/run/T-1/run-state.json'), '{"completed": true}\n')
        self.assertEqual(self.read('.artel/run/.hooks/baseline-s1.json'), '{"keys": ["k"]}\n')

    def test_newer_main_baseline_wins(self):
        self.write('.artel/run/.hooks/baseline-s1.json', 'worktree\n', self.target)
        old = time.time() - 60
        os.utime(self.target / '.artel/run/.hooks/baseline-s1.json', (old, old))
        self.write('.artel/run/.hooks/baseline-s1.json', 'main\n')
        self.hand_back()
        self.assertEqual(self.read('.artel/run/.hooks/baseline-s1.json'), 'main\n')

    def test_merges_a_store_the_worktree_grew(self):
        self.write('.artel/context/root/CLAUDE.md', '# saved in the worktree\n', self.target)
        code, report = self.hand_back()
        self.assertEqual(code, 0, report)
        self.assertEqual(self.read('.artel/context/root/CLAUDE.md'), '# saved in the worktree\n')

    def test_reports_environment_changes_without_copying_them(self):
        self.write('.claude/settings.local.json', '{"allow": ["Bash(make:*)"]}\n', self.target)
        self.write('.claude/new.json', '{}\n', self.target)
        code, report = self.hand_back()
        self.assertEqual(code, 0, report)
        self.assertEqual(sorted(report['envChanged']),
                         ['.claude/new.json', '.claude/settings.local.json'])
        self.assertEqual(self.read('.claude/settings.local.json'), '{"allow": []}\n')
        self.assertFalse((self.root / '.claude/new.json').exists())

    def test_marks_the_hand_back_done(self):
        self.hand_back()
        marker = json.loads(self.read('.artel/run/T-1/worktree.json'))
        self.assertEqual(marker['handBack'], 'done')
        self.assertEqual(marker['branch'], BRANCH)

    def test_a_refused_removal_rolls_back(self):
        self.write('app.txt', 'wip\n', self.target)
        self.write('.artel/run/T-1/run-state.json', '{}\n', self.target)
        git(self.root, 'worktree', 'lock', str(self.target))
        code, report = self.hand_back()
        self.assertEqual((code, report['status']), (1, 'rolled-back'), report)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(self.read('app.txt', self.target), 'wip\n')
        self.assertTrue((self.target / '.artel/run/T-1/run-state.json').is_file())
        self.assertFalse((self.root / '.artel/run/T-1').exists())
        self.assertEqual(self.stashes(), '')
        self.assertEqual(self.branch_of(self.root), 'main')

    def test_finishes_an_interrupted_hand_back(self):
        self.write('app.txt', 'wip\n', self.target)
        git(self.target, 'stash', 'push', '-q', '--include-untracked',
            '-m', 'artel return-from-worktree T-1')
        stash = git(self.root, 'rev-parse', 'stash@{0}')
        self.write('.artel/run/T-1/worktree.json', json.dumps({
            'ticket': 'T-1', 'branch': BRANCH, 'stash': stash, 'handBack': 'pending',
            'mainWasOn': 'main', 'runMoved': False, 'envChanged': []}))
        git(self.root, 'worktree', 'remove', str(self.target))
        code, report = self.hand_back()
        self.assertEqual((code, report['status']), (0, 'ok'), report)
        self.assertEqual(self.branch_of(self.root), BRANCH)
        self.assertEqual(self.read('app.txt'), 'wip\n')
        self.assertEqual(self.stashes(), '')


class TestHandBackRecovery(HandBackCase):
    """hand-back failures after the worktree's work was stashed must name the stash."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        self.write('app.txt', 'wip\n', self.target)

    def run_hand_back(self):
        with self.assertRaises(worktree.Stop) as caught:
            worktree.hand_back(worktree.parse(['hand-back', '--ticket', 'T-1']))
        return caught.exception.report

    def assert_stash_kept(self, report):
        self.assertIn(report['stash'], git(self.root, 'stash', 'list', '--format=%H'))

    def test_a_failure_after_the_stash_names_it(self):
        os.chdir(self.root)
        with mock.patch.object(worktree, 'transfer_out', side_effect=OSError('disk full')):
            report = self.run_hand_back()
        self.assertEqual(report['status'], 'error')
        self.assertIn('OSError: disk full', report['reason'])
        self.assertEqual(report['path'], str(self.target))
        self.assert_stash_kept(report)

    def test_a_failed_checkout_after_removal_names_the_stash_and_a_rerun_finishes(self):
        os.chdir(self.root)
        real = worktree.git

        def fake(*args, **kwargs):
            if args == ('checkout', BRANCH):
                raise worktree.Stop('error', 'git checkout {} failed: injected'.format(BRANCH))
            return real(*args, **kwargs)
        with mock.patch.object(worktree, 'git', side_effect=fake):
            report = self.run_hand_back()
        self.assertEqual(report['status'], 'error')
        self.assert_stash_kept(report)
        self.assertFalse(self.target.exists())
        code, again = self.hand_back()
        self.assertEqual((code, again['status']), (0, 'ok'), again)
        self.assertEqual(self.branch_of(self.root), BRANCH)
        self.assertEqual(self.read('app.txt'), 'wip\n')
        self.assertEqual(self.stashes(), '')


if __name__ == '__main__':
    unittest.main()
