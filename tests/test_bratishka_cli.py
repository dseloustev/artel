"""The bratishka CLI envelope and the status board data."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka  # noqa: E402


class FakeGh:
    def __init__(self, prs, reviews=None, members=None):
        self.prs = prs
        self.reviews_by_pr = reviews or {}
        self.members = members or {'devs': {'dev'}, 'qa-team': {'qa'}}
        self.calls = []

    def repo_slug(self):
        return 'o/r'

    def default_branch(self, repo):
        return 'master'

    def current_login(self):
        return 'me'

    def open_prs(self, repo, author=None):
        return self.prs

    def reviews(self, repo, number):
        return self.reviews_by_pr.get(number, [])

    def team_members(self, org, team):
        return self.members[team]


class FakeDash:
    def __init__(self, tasks=None):
        self._tasks = tasks or []
        self.errors = []

    def overview(self):
        return {'waiting': 0, 'queued': 1, 'active': 2}

    def tasks(self, **filters):
        return self._tasks

    def task(self, task_id):
        for task in self._tasks:
            if task.get('task_id') == task_id:
                return task
        return None

    def find_task(self, run_id):
        for task in self._tasks:
            if run_id in task.get('description', ''):
                return task
        return None

    def find_task_for_pr(self, repo, number, initiator=None, since=None):
        needle = 'PR {}#{}'.format(repo, number)
        for task in self._tasks:
            if needle in task.get('description', ''):
                return task
        return None


def pr(number, title='AW-1 x', head='agent/x', merge_state='CLEAN'):
    return {'number': number, 'title': title, 'url': 'u', 'head': head,
            'author': 'bratishka-bot', 'draft': False, 'state': 'OPEN',
            'created_at': '2026-11-03T10:00:00Z', 'body': '',
            'review_decision': '', 'mergeable': 'MERGEABLE',
            'merge_state': merge_state}


def run(argv, gh=None, dashboard=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bratishka.main(argv, gh=gh, dashboard=dashboard)
    return code, json.loads(out.getvalue())


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)

    def write_config(self):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'bratishka': {'teams': {'codeReview': 'devs',
                                                           'qa': 'qa-team'}}}),
                        encoding='utf-8')

    def test_status_envelope_and_buckets(self):
        self.write_config()
        reviews = {1: [{'login': 'dev', 'state': 'APPROVED', 'submitted_at': 't',
                        'body': ''}],
                   2: [{'login': 'dev', 'state': 'APPROVED', 'submitted_at': 't', 'body': ''},
                       {'login': 'qa', 'state': 'APPROVED', 'submitted_at': 't', 'body': ''}],
                   3: []}
        code, out = run(['status', '--repo', self.repo.name],
                        gh=FakeGh([pr(1), pr(2), pr(3)], reviews),
                        dashboard=FakeDash())
        self.assertEqual(code, 0)
        self.assertTrue(out['ok'])
        self.assertEqual(out['data']['queue']['needs-qa'][0]['number'], 1)
        self.assertEqual(out['data']['queue']['qa-approved'][0]['number'], 2)
        self.assertEqual(out['data']['queue']['needs-review'][0]['number'], 3)

    def test_offline_skips_the_dashboard(self):
        self.write_config()
        code, out = run(['status', '--repo', self.repo.name, '--offline'],
                        gh=FakeGh([pr(1)]), dashboard=FakeDash())
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['live_tasks'], [])

    def test_invalid_config_exits_2(self):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True)
        path.write_text('{', encoding='utf-8')
        code, out = run(['status', '--repo', self.repo.name], gh=FakeGh([]))
        self.assertEqual(code, 2)
        self.assertFalse(out['ok'])

    def test_unknown_command_exits_2(self):
        code, out = run(['nope'], gh=FakeGh([]))
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')


class TestPlanCommand(unittest.TestCase):
    def test_plan_envelope(self):
        with tempfile.TemporaryDirectory() as repo:
            path = Path(repo) / '.artel' / 'config.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'bratishka': {'teams': {'codeReview': 'devs',
                                                               'qa': 'qa-team'}}}))
            gh = FakeGh([pr(1)], {1: [{'login': 'dev', 'state': 'APPROVED',
                                       'submitted_at': 't', 'body': ''}]})
            gh.base_sha = lambda repo_name, ref: 'abc'
            code, out = run(['testing-pr-plan', '--repo', repo], gh=gh,
                            dashboard=FakeDash())
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['tasks'][0]['number'], 1)
        self.assertEqual(out['data']['base_sha'], 'abc')

    def test_exclude_drops_a_candidate(self):
        with tempfile.TemporaryDirectory() as repo:
            path = Path(repo) / '.artel' / 'config.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'bratishka': {'teams': {'codeReview': 'devs',
                                                               'qa': 'qa-team'}}}))
            review = [{'login': 'dev', 'state': 'APPROVED', 'submitted_at': 't',
                       'body': ''}]
            gh = FakeGh([pr(1), pr(2)], {1: review, 2: review})
            gh.base_sha = lambda repo_name, ref: 'abc'
            code, out = run(['testing-pr-plan', '--repo', repo, '--exclude', '2'],
                            gh=gh)
        self.assertEqual(code, 0)
        self.assertEqual([task['number'] for task in out['data']['tasks']], [1])

    def test_prompt_is_written_with_its_run_id(self):
        with tempfile.TemporaryDirectory() as repo:
            path = Path(repo) / '.artel' / 'config.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'bratishka': {'teams': {'codeReview': 'devs',
                                                               'qa': 'qa-team'}}}))
            gh = FakeGh([pr(1)], {1: [{'login': 'dev', 'state': 'APPROVED',
                                       'submitted_at': 't', 'body': ''}]})
            gh.base_sha = lambda repo_name, ref: 'abc'
            target = Path(repo) / 'run' / 'testing-pr-prompt.md'
            code, out = run(['testing-pr-prompt', '--repo', repo, '--out',
                             str(target)], gh=gh)
            text = target.read_text(encoding='utf-8')
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['prompt_path'], str(target))
        self.assertIn('PR #1, head branch agent/x', text)
        self.assertIn('Run id: ' + out['data']['run_id'], text)

    def test_prompt_freezes_the_confirmed_numbers(self):
        with tempfile.TemporaryDirectory() as repo:
            path = Path(repo) / '.artel' / 'config.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'bratishka': {'teams': {'codeReview': 'devs',
                                                               'qa': 'qa-team'}}}))
            review = [{'login': 'dev', 'state': 'APPROVED', 'submitted_at': 't',
                       'body': ''}]
            gh = FakeGh([pr(1), pr(2)], {1: review, 2: review})
            gh.base_sha = lambda repo_name, ref: 'abc'
            target = Path(repo) / 'run' / 'testing-pr-prompt.md'
            code, out = run(['testing-pr-prompt', '--repo', repo, '--out',
                             str(target), '--include', '1'], gh=gh)
            text = target.read_text(encoding='utf-8')
        self.assertEqual(code, 0)
        self.assertEqual([task['number'] for task in out['data']['tasks']], [1])
        self.assertIn('PR #1, head branch agent/x', text)
        self.assertNotIn('PR #2', text)


class TestCancel(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)

    def write_config(self):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'bratishka': {'botLogin': 'bratishka-bot'}}),
                        encoding='utf-8')

    def gh(self):
        gh = FakeGh([
            pr(1, title='Testing 03-11-26', head='feature/testing-03-11-26'),
            pr(2, title='Testing 02.11.26', head='testing/aw-2986-2026-11-02'),
            pr(3, title='AW-9 ordinary', head='agent/x'),
        ])
        gh.closed = []
        gh.close_pr = lambda repo, n, comment=None, delete_branch=False: \
            gh.closed.append((n, delete_branch))
        return gh

    def test_without_yes_it_requires_confirmation(self):
        self.write_config()
        code, out = run(['testing-pr-cancel', '--repo', self.repo.name], gh=self.gh())
        self.assertEqual(code, 1)
        self.assertTrue(out['error']['confirmation_required'])
        self.assertEqual([c['number'] for c in out['error']['candidates']], [1, 2])

    def test_yes_closes_only_stale_testing_prs(self):
        self.write_config()
        gh = self.gh()
        code, out = run(['testing-pr-cancel', '--repo', self.repo.name, '--yes', '--json'],
                        gh=gh)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(gh.closed), [(1, False), (2, False)])

    def test_today_is_left_alone(self):
        self.write_config()
        today = __import__('datetime').date.today()
        name = today.strftime('%d-%m-%y')
        gh = FakeGh([pr(9, title='Testing ' + name,
                        head='feature/testing-' + name)])
        gh.closed = []
        gh.close_pr = lambda *a, **k: gh.closed.append(a[1])
        code, out = run(['testing-pr-cancel', '--repo', self.repo.name, '--yes'],
                        gh=gh)
        self.assertEqual(code, 1)
        self.assertEqual(gh.closed, [])


class TestVerify(unittest.TestCase):
    def test_a_failed_task_with_the_pr_is_success(self):
        gh = FakeGh([pr(9, title='Testing 03-11-26', head='feature/testing-03-11-26')])
        gh.open_prs = lambda repo, author=None: gh.prs
        dash = FakeDash([{'task_id': 't1', 'status': 'failed',
                          'description': 'Run id: artel-1',
                          'result_detail': {'error': 'final_tools_gate_not_satisfied'},
                          'result_summary': 'delivered'}])
        from datetime import date
        out = bratishka.verify_testing_pr(
            {'repo': 'o/r', 'testingPr': {'titleTemplate': 'Testing {dd}-{mm}-{yy}',
                                          'branchTemplate': 'feature/testing-{dd}-{mm}-{yy}'}},
            gh, dash, 'artel-1', date(2026, 11, 3))
        self.assertEqual(out['pr']['number'], 9)
        self.assertTrue(out['gate_failed'])

    def test_no_task_and_no_pr_is_not_an_error(self):
        from datetime import date
        out = bratishka.verify_testing_pr({'repo': 'o/r'}, FakeGh([]), FakeDash(),
                                          'artel-1', date(2026, 11, 3))
        self.assertIsNone(out['task'])
        self.assertIsNone(out['pr'])


class TestFindings(unittest.TestCase):
    def test_unresolved_only(self):
        gh = FakeGh([])
        gh.review_threads = lambda repo, n: [
            {'path': 'a.dart', 'line': 4, 'body': 'fix it', 'author': 'bratishka-bot',
             'resolved': False},
            {'path': 'b.dart', 'line': 9, 'body': 'done', 'author': 'bratishka-bot',
             'resolved': True}]
        out = bratishka.findings_data({'repo': 'o/r'}, gh, 7)
        self.assertEqual([t['path'] for t in out['threads']], ['a.dart'])

    def test_compose_one_comment(self):
        body = bratishka.compose_fix_body(
            [{'path': 'a.dart', 'line': 4, 'body': 'fix it'}], [0], 'and test it',
            {'fixCommand': '/agent'})
        self.assertTrue(body.startswith('/agent'))
        self.assertIn('a.dart:4', body)
        self.assertIn('and test it', body)

    def test_an_active_coder_task_blocks(self):
        dash = FakeDash([{'task_id': 't', 'status': 'active',
                          'description': 'PR o/r#7', 'initiator': 'me',
                          'created_at': '2026-11-05T07:00:00Z'}])
        out = bratishka.task_find_data({'repo': 'o/r'}, FakeGh([]), dash, 7)
        self.assertEqual(out['task']['status'], 'active')

    def test_the_head_ref_fallback(self):
        dash = FakeDash([{'task_id': 't', 'status': 'active',
                          'description': 'no PR token here',
                          'head_ref': 'agent/x', 'initiator': 'me',
                          'created_at': '2026-11-05T07:00:00Z'}])
        gh = FakeGh([pr(7)])
        gh.pr = lambda repo, number: pr(7)
        out = bratishka.task_find_data({'repo': 'o/r'}, gh, dash, 7,
                                       since='2026-11-05T00:00:00Z')
        self.assertEqual(out['match'], 'head_ref')


class TestMergePlan(unittest.TestCase):
    def test_groups(self):
        approved = [{'login': 'dev', 'state': 'APPROVED', 'submitted_at': 't', 'body': ''},
                    {'login': 'qa', 'state': 'APPROVED', 'submitted_at': 't', 'body': ''}]
        reviews = {1: approved, 2: approved, 3: approved}
        gh = FakeGh([pr(1, merge_state='CLEAN'), pr(2, merge_state='BEHIND'),
                     pr(3, merge_state='BLOCKED')], reviews)
        out = bratishka.merge_plan_data(
            {'repo': 'o/r', 'teams': {'codeReview': 'devs', 'qa': 'qa-team'}}, gh)
        self.assertEqual([x['number'] for x in out['ready']], [1])
        self.assertEqual([x['number'] for x in out['needs_update']], [2])
        self.assertEqual([x['number'] for x in out['blocked']], [3])
