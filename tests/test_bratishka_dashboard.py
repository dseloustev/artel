"""The read-only bratishka dashboard client."""
import http.client
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka_dashboard as dash  # noqa: E402


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.urls = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return Response(self.payload)


class TestGet(unittest.TestCase):
    def test_builds_the_url_with_filters(self):
        opener = Opener({'tasks': []})
        d = dash.Dashboard('https://b.example', opener=opener)
        d.tasks(repo='o/r', initiator='me', limit=5)
        self.assertIn('repo=o%2Fr', opener.urls[0])
        self.assertIn('initiator=me', opener.urls[0])
        self.assertIn('limit=5', opener.urls[0])

    def test_an_unreachable_dashboard_is_reported_not_fatal(self):
        opener = Opener(error=OSError('no route'))
        d = dash.Dashboard('https://b.example', opener=opener)
        self.assertIsNone(d.overview())
        self.assertEqual(len(d.errors), 1)

    def test_a_truncated_body_is_reported_not_fatal(self):
        opener = Opener(error=http.client.IncompleteRead(b'', 1))
        d = dash.Dashboard('https://b.example', opener=opener)
        self.assertIsNone(d.overview())
        self.assertEqual(len(d.errors), 1)
        self.assertIn('IncompleteRead', d.errors[0])


class TestFind(unittest.TestCase):
    def test_find_task_by_run_id(self):
        opener = Opener({'tasks': [
            {'task_id': 't1', 'description': 'Run id: artel-1'},
            {'task_id': 't2', 'description': 'other'}]})
        d = dash.Dashboard('https://b.example', opener=opener)
        self.assertEqual(d.find_task('artel-1')['task_id'], 't1')

    def test_find_task_for_pr_reads_the_description(self):
        opener = Opener({'tasks': [
            {'task_id': 't1', 'description': 'Implement ... PR o/r#7.',
             'created_at': '2026-11-05T07:00:00Z', 'initiator': 'me'}]})
        d = dash.Dashboard('https://b.example', opener=opener)
        task = d.find_task_for_pr('o/r', 7, initiator='me')
        self.assertEqual(task['task_id'], 't1')

    def test_since_excludes_older_tasks(self):
        opener = Opener({'tasks': [
            {'task_id': 'old', 'description': 'PR o/r#7',
             'created_at': '2026-11-04T07:00:00Z'}]})
        d = dash.Dashboard('https://b.example', opener=opener)
        self.assertIsNone(d.find_task_for_pr('o/r', 7, since='2026-11-05T00:00:00Z'))

    def test_active_tasks_ignore_the_initiator(self):
        opener = Opener({'tasks': [
            {'task_id': 'live', 'status': 'active', 'description': 'PR o/r#7',
             'initiator': 'someone-else', 'created_at': '2026-11-05T07:00:00Z'},
            {'task_id': 'done', 'status': 'completed', 'description': 'PR o/r#7',
             'initiator': 'me', 'created_at': '2026-11-05T08:00:00Z'}]})
        d = dash.Dashboard('https://b.example', opener=opener)
        self.assertEqual([task['task_id'] for task in d.active_tasks_for_pr('o/r', 7)],
                         ['live'])

    def test_an_older_active_task_is_not_masked_by_a_newer_completed_one(self):
        opener = Opener({'tasks': [
            {'task_id': 'old-live', 'status': 'active', 'description': 'PR o/r#7',
             'initiator': 'me', 'created_at': '2026-11-05T07:00:00Z'},
            {'task_id': 'new-done', 'status': 'completed', 'description': 'PR o/r#7',
             'initiator': 'me', 'created_at': '2026-11-05T09:00:00Z'}]})
        d = dash.Dashboard('https://b.example', opener=opener)
        self.assertEqual(d.find_task_for_pr('o/r', 7)['task_id'], 'new-done')
        self.assertEqual([task['task_id'] for task in d.active_tasks_for_pr('o/r', 7)],
                         ['old-live'])
