"""The local testing-PR fallback: branch, merges, conflict stop."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka  # noqa: E402


def git(cwd, *args):
    return subprocess.run(['git'] + list(args), cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


class FakeGh:
    def __init__(self):
        self.created = []

    def base_sha(self, repo, ref):
        return 'base'

    def create_pr(self, repo, base, head, title, body_file, assignee=None):
        self.created.append((base, head, title))
        return 'https://github.com/o/r/pull/9'


class RepoFixture:
    """A real checkout with a bare `origin`, two heads and a clean master."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / 'remote.git'
        self.work = self.root / 'work'
        git(self.root, 'init', '--bare', str(self.remote))
        git(self.root, 'clone', str(self.remote), str(self.work))
        git(self.work, 'config', 'user.email', 't@t')
        git(self.work, 'config', 'user.name', 't')
        (self.work / 'a.txt').write_text('a')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '-m', 'base')
        git(self.work, 'branch', '-M', 'master')
        git(self.work, 'push', '-u', 'origin', 'master')
        git(self.work, 'checkout', '-b', 'f1')
        (self.work / 'b.txt').write_text('b')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '-m', 'f1')
        git(self.work, 'push', '-u', 'origin', 'f1')
        git(self.work, 'checkout', 'master')
        git(self.work, 'checkout', '-b', 'f2')
        (self.work / 'b.txt').write_text('other')
        git(self.work, 'add', '.')
        git(self.work, 'commit', '-m', 'f2')
        git(self.work, 'push', '-u', 'origin', 'f2')
        git(self.work, 'checkout', 'master')


class LocalCase(RepoFixture, unittest.TestCase):
    def build(self, heads):
        gh = FakeGh()
        tasks = [{'number': i + 1, 'head': head, 'title': 'T'} for i, head in enumerate(heads)]
        return gh, bratishka.local_build(
            str(self.work), str(self.root / 'wt'), 'feature/testing-03-11-26',
            'master', tasks, 'body', gh)

    def test_a_clean_build_pushes_and_opens_the_pr(self):
        gh, out = self.build(['f1'])
        self.assertEqual(out['conflicts'], [])
        self.assertEqual(gh.created[0][1], 'feature/testing-03-11-26')
        git(self.remote, 'rev-parse', 'feature/testing-03-11-26')

    def test_a_conflict_stops_without_a_push(self):
        gh, out = self.build(['f1', 'f2'])
        self.assertEqual(out['conflicts'], ['b.txt'])
        self.assertEqual(gh.created, [])
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.remote, 'rev-parse', 'feature/testing-03-11-26')

    def test_an_allowed_red_verify_still_opens_the_pr(self):
        gh = FakeGh()
        tasks = [{'number': 1, 'head': 'f1', 'title': 'T'}]
        out = bratishka.local_build(
            str(self.work), str(self.root / 'wt-red'), 'feature/testing-03-11-27',
            'master', tasks, 'body', gh, verify_commands=['false'],
            allow_red_verify=True)
        self.assertEqual(out['conflicts'], [])
        self.assertTrue(out['pr_url'])
        self.assertIn('failed', out['verification'][0])
        self.assertEqual(len(gh.created), 1)


class CliGh(FakeGh):
    """A `gh` double for the command: one reviewed source PR, checks unknown."""

    def __init__(self, prs):
        super().__init__()
        self.prs = prs
        self.bodies = []

    def repo_slug(self):
        return 'o/r'

    def default_branch(self, repo):
        return 'master'

    def open_prs(self, repo, author=None):
        return self.prs

    def reviews(self, repo, number):
        return [{'login': 'dev', 'state': 'APPROVED', 'submitted_at': 't', 'body': ''}]

    def team_members(self, org, team):
        return {'dev'}

    def base_sha(self, repo, ref):
        return 'abc1234'

    def checks(self, repo, number):
        raise bratishka.GhError('failed', 'no checks reported')

    def create_pr(self, repo, base, head, title, body_file, assignee=None):
        self.created.append((base, head, title))
        self.repo_arg = repo
        self.bodies.append(Path(body_file).read_text(encoding='utf-8'))
        return 'https://github.com/o/r/pull/9'


def source_pr(number=10, head='f1'):
    return {'number': number, 'title': 'AW-10: bundle me',
            'url': 'https://github.com/o/r/pull/10', 'head': head,
            'author': 'bratishka-bot', 'draft': False, 'state': 'OPEN',
            'created_at': '2026-11-04T10:00:00Z',
            'body': ('**Notes for QA**\n- Check the title.\n\n'
                     '**Platforms to test**\n- [x] Android\n'),
            'review_decision': '', 'mergeable': 'MERGEABLE', 'merge_state': 'CLEAN'}


class LocalCommandCase(RepoFixture, unittest.TestCase):
    """The CLI around `local_build`, against the same real temp repository."""

    def setUp(self):
        super().setUp()
        self.write_config(['true'])
        self.today = date.today().strftime('%d-%m-%y')
        self.branch = 'feature/testing-' + self.today
        self.worktree = (self.work / '.artel' / 'run' / 'bratishka' /
                         ('testing-pr-' + date.today().isoformat()))

    def write_config(self, commands):
        path = self.work / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'bratishka': {'repo': 'o/r', 'teams': {'codeReview': 'devs', 'qa': ''}},
            'verify': {'commands': commands},
        }), encoding='utf-8')

    def run_cli(self, gh, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bratishka.main(['testing-pr-local', '--repo', str(self.work)] + list(args),
                                  gh=gh)
        return code, json.loads(out.getvalue())

    def test_without_yes_the_plan_is_shown_and_nothing_is_written(self):
        gh = CliGh([source_pr()])
        code, out = self.run_cli(gh)
        self.assertEqual(code, 1)
        self.assertEqual(out['error']['kind'], 'confirmation_required')
        self.assertEqual([task['number'] for task in out['error']['tasks']], [10])
        self.assertEqual(gh.created, [])
        self.assertFalse(self.worktree.exists())
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.remote, 'rev-parse', self.branch)

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        gh = CliGh([source_pr()])
        code, out = self.run_cli(gh, '--dry-run', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['branch'], self.branch)
        self.assertEqual(out['data']['title'], 'Testing ' + self.today)
        self.assertEqual(out['data']['verify_commands'], ['true'])
        self.assertEqual(gh.created, [])
        self.assertFalse(self.worktree.exists())

    def test_an_existing_remote_branch_is_refused(self):
        git(self.work, 'push', 'origin', 'master:{}'.format(self.branch))
        gh = CliGh([source_pr()])
        code, out = self.run_cli(gh, '--yes')
        self.assertEqual(code, 1)
        self.assertEqual(out['error']['kind'], 'already_exists')
        self.assertIn(self.branch, out['error']['message'])
        self.assertEqual(gh.created, [])

    def test_an_existing_testing_pr_is_refused(self):
        testing = source_pr(number=20, head=self.branch)
        testing['title'] = 'Testing ' + self.today
        gh = CliGh([source_pr(), testing])
        code, out = self.run_cli(gh, '--yes')
        self.assertEqual(code, 1)
        self.assertEqual(out['error']['kind'], 'already_exists')
        self.assertIn('#20', out['error']['message'])
        self.assertEqual(gh.created, [])
        self.assertFalse(self.worktree.exists())

    def test_yes_builds_pushes_and_opens_the_pr_with_the_body(self):
        # `b.txt` exists only after the f1 merge, so a green check proves the
        # verify command ran in the worktree, not in the host checkout.
        self.write_config(['test -f b.txt'])
        gh = CliGh([source_pr()])
        body_out = self.root / 'body.md'
        code, out = self.run_cli(gh, '--yes', '--json', '--body-out', str(body_out))
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['pr_url'], 'https://github.com/o/r/pull/9')
        self.assertEqual(gh.created, [('master', self.branch, 'Testing ' + self.today)])
        self.assertEqual(gh.repo_arg, 'o/r')
        git(self.remote, 'rev-parse', self.branch)
        self.assertFalse(self.worktree.exists())
        body = gh.bodies[0]
        self.assertIn('## Merge notes', body)
        self.assertIn('Merged #10 (`f1`) without conflicts.', body)
        self.assertIn('`test -f b.txt` — passed', body)
        self.assertIn('**#10** — checks not read', body)
        self.assertIn('Single testing PR for {}'.format(date.today().isoformat()), body)
        self.assertEqual(body_out.read_text(encoding='utf-8').strip(), body.strip())
        self.assertEqual(out['data']['body_path'], str(body_out))

    def test_include_freezes_the_confirmed_set(self):
        # Both sources conflict on b.txt, so only a narrowed plan can build: the
        # confirmed #10 must be the one merged, and #11 must stay out.
        gh = CliGh([source_pr(), source_pr(number=11, head='f2')])
        code, out = self.run_cli(gh, '--yes', '--json', '--include', '10')
        self.assertEqual(code, 0)
        self.assertEqual([task['number'] for task in out['data']['tasks']], [10])
        self.assertEqual(len(gh.created), 1)
        self.assertIn('Merged #10 (`f1`) without conflicts.', gh.bodies[0])
        self.assertNotIn('#11', gh.bodies[0])

    def test_a_red_verify_stops_before_the_push_and_keeps_the_worktree(self):
        self.write_config(['false', 'true'])
        gh = CliGh([source_pr()])
        code, out = self.run_cli(gh, '--yes')
        self.assertEqual(code, 1)
        self.assertEqual(out['error']['kind'], 'verification_failed')
        self.assertEqual(len(out['error']['verification']), 1)
        self.assertIn('`false` — failed', out['error']['verification'][0])
        self.assertEqual(gh.created, [])
        self.assertTrue(self.worktree.exists())
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.remote, 'rev-parse', self.branch)

    def test_a_conflict_reports_the_files_and_does_not_push(self):
        gh = CliGh([source_pr(), source_pr(number=11, head='f2')])
        code, out = self.run_cli(gh, '--yes')
        self.assertEqual(code, 1)
        self.assertEqual(out['error']['kind'], 'conflict')
        self.assertEqual(out['error']['conflicts'], ['b.txt'])
        self.assertIn('not pushed', out['error']['message'])
        self.assertEqual(gh.created, [])
        kept = self.worktree.resolve()
        self.assertTrue(kept.exists())
        self.assertEqual(out['error']['worktree'], str(kept))
        self.assertIn(str(kept), out['error']['message'])
        unmerged = git(self.worktree, 'diff', '--name-only', '--diff-filter=U')
        self.assertEqual(unmerged.split(), ['b.txt'])
        self.assertTrue(git(self.worktree, 'status', '--porcelain').strip())
        self.assertIn(self.branch, git(self.work, 'branch', '--list', self.branch))
        with self.assertRaises(subprocess.CalledProcessError):
            git(self.remote, 'rev-parse', self.branch)

    def test_a_leftover_worktree_is_refused(self):
        self.worktree.mkdir(parents=True)
        gh = CliGh([source_pr()])
        code, out = self.run_cli(gh, '--yes')
        self.assertEqual(code, 1)
        self.assertEqual(out['error']['kind'], 'worktree_exists')
        self.assertIn(str(self.worktree), out['error']['message'])
        self.assertEqual(gh.created, [])
