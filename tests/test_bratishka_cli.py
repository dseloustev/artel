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
