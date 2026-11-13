"""The gh layer: commands, JSON shapes, and the write surface."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka_gh as ghmod  # noqa: E402


class Fake:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        for needle, answer in self.answers:
            if any(needle in arg for arg in args):
                return answer
        raise AssertionError('unexpected gh call: {}'.format(args))


def client(answers):
    fake = Fake(answers)
    return ghmod.Gh(call=fake), fake


def review_threads_page(nodes, has_next=False, cursor=None):
    """One GraphQL answer for `reviewThreads`, page info included."""
    return {'data': {'repository': {'pullRequest': {'reviewThreads': {
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'nodes': nodes}}}}}


def review_thread(**overrides):
    """A raw `reviewThreads` node: one bot root comment and one human reply."""
    node = {
        'id': 'RT_1', 'isResolved': False, 'isOutdated': True,
        'path': 'a.dart', 'line': None, 'startLine': None, 'originalLine': 4,
        'diffSide': 'RIGHT',
        'comments': {'nodes': [
            {'author': {'login': 'bratishka-bot'}, 'body': 'fix this',
             'createdAt': 't1', 'url': 'u1',
             'diffHunk': '@@ -2,3 +2,3 @@\n a\n-b\n+c'},
            {'author': {'login': 'reviewer'}, 'body': 'not like that',
             'createdAt': 't2', 'url': 'u2',
             'diffHunk': '@@ -2,3 +2,3 @@\n a\n-b\n+c'},
        ]},
    }
    node.update(overrides)
    return node


class TestReads(unittest.TestCase):
    def test_repo_slug(self):
        gh, fake = client([('view', 'o/r')])
        self.assertEqual(gh.repo_slug(), 'o/r')
        self.assertIn('nameWithOwner', fake.calls[0])

    def test_open_prs_normalizes(self):
        raw = [{'number': 7, 'title': 'AW-1 x', 'url': 'u', 'headRefName': 'agent/x',
                'author': {'login': 'bratishka-bot'}, 'isDraft': False, 'state': 'OPEN',
                'createdAt': '2026-11-03T11:55:49Z', 'body': 'b',
                'reviewDecision': 'APPROVED', 'mergeable': 'MERGEABLE',
                'mergeStateStatus': 'CLEAN'}]
        gh, fake = client([('list', json.dumps(raw))])
        prs = gh.open_prs('o/r', 'bratishka-bot')
        self.assertEqual(prs[0]['number'], 7)
        self.assertEqual(prs[0]['head'], 'agent/x')
        self.assertEqual(prs[0]['author'], 'bratishka-bot')
        self.assertEqual(prs[0]['merge_state'], 'CLEAN')
        self.assertIn('--author', fake.calls[0])

    def test_reviews_normalizes(self):
        raw = [{'user': {'login': 'qa'}, 'state': 'APPROVED',
                'submitted_at': '2026-11-05T05:00:00Z', 'body': ''}]
        gh, _ = client([('reviews', json.dumps(raw))])
        self.assertEqual(gh.reviews('o/r', 7)[0]['login'], 'qa')

    def test_checks_normalizes(self):
        raw = [{'name': 'build', 'state': 'SUCCESS', 'bucket': 'pass', 'link': 'l'}]
        gh, _ = client([('checks', json.dumps(raw))])
        self.assertEqual(gh.checks('o/r', 7)[0]['bucket'], 'pass')

    def test_review_threads_flattens_graphql(self):
        raw = review_threads_page([review_thread()])
        gh, _ = client([('graphql', json.dumps(raw))])
        threads = gh.review_threads('o/r', 7)
        self.assertEqual(threads[0]['path'], 'a.dart')
        self.assertEqual(threads[0]['body'], 'fix this')

    def test_review_threads_carry_the_code_anchor(self):
        raw = review_threads_page([review_thread()])
        gh, _ = client([('graphql', json.dumps(raw))])
        thread = gh.review_threads('o/r', 7)[0]
        self.assertEqual(thread['id'], 'RT_1')
        self.assertTrue(thread['outdated'])
        self.assertFalse(thread['resolved'])
        self.assertIsNone(thread['line'])
        self.assertEqual(thread['original_line'], 4)
        self.assertEqual(thread['side'], 'RIGHT')
        self.assertIn('+c', thread['hunk'])
        self.assertEqual(thread['url'], 'u1')
        self.assertEqual([c['author'] for c in thread['comments']],
                         ['bratishka-bot', 'reviewer'])
        self.assertEqual(thread['comments'][1]['body'], 'not like that')

    def test_review_threads_ask_for_the_anchor_context(self):
        gh, fake = client([('graphql', json.dumps(review_threads_page([])))])
        gh.review_threads('o/r', 7)
        sent = ' '.join(fake.calls[0])
        for field in ('isOutdated', 'originalLine', 'diffHunk', 'pageInfo',
                      'endCursor', 'url'):
            self.assertIn(field, sent, field)
        self.assertNotIn('after=', sent)

    def test_review_threads_follow_page_info(self):
        pages = [review_threads_page([review_thread(path='a.dart')], True, 'c1'),
                 review_threads_page([review_thread(path='b.dart')], False, None)]
        calls = []

        def call(args):
            calls.append(list(args))
            return json.dumps(pages[len(calls) - 1])

        threads = ghmod.Gh(call=call).review_threads('o/r', 7)
        self.assertEqual([t['path'] for t in threads], ['a.dart', 'b.dart'])
        self.assertIn('after=c1', ' '.join(calls[1]))

    def test_review_threads_stop_at_the_page_ceiling(self):
        calls = []

        def call(args):
            calls.append(list(args))
            return json.dumps(review_threads_page([], True, 'c'))

        ghmod.Gh(call=call).review_threads('o/r', 7)
        self.assertEqual(len(calls), ghmod.THREAD_PAGES)

    def test_team_members_returns_a_set(self):
        gh, _ = client([('members', 'qa1\nqa2\n')])
        self.assertEqual(gh.team_members('org', 'qa-team'), {'qa1', 'qa2'})

    def test_a_missing_team_is_not_found(self):
        def boom(args):
            raise ghmod.GhError('not_found', 'HTTP 404')
        gh = ghmod.Gh(call=boom)
        with self.assertRaises(ghmod.GhError) as ctx:
            gh.team_members('org', 'nope')
        self.assertEqual(ctx.exception.kind, 'not_found')

    def test_base_sha(self):
        gh, _ = client([('commits', 'abc123')])
        self.assertEqual(gh.base_sha('o/r', 'master'), 'abc123')


class TestWrites(unittest.TestCase):
    def test_comment(self):
        gh, fake = client([('comment', '')])
        gh.comment_pr('o/r', 7, '/tmp/body.md')
        self.assertEqual(fake.calls[0][:4], ['pr', 'comment', '7', '--repo'])

    def test_close(self):
        gh, fake = client([('close', '')])
        gh.close_pr('o/r', 7, 'done', delete_branch=True)
        self.assertIn('--delete-branch', fake.calls[0])
        self.assertIn('--comment', fake.calls[0])

    def test_merge_is_squash(self):
        gh, fake = client([('merge', '')])
        gh.merge_pr('o/r', 7)
        self.assertIn('--squash', fake.calls[0])
        self.assertNotIn('--delete-branch', fake.calls[0])

    def test_update_branch(self):
        gh, fake = client([('api', '')])
        gh.update_branch('o/r', 7)
        self.assertIn('api', fake.calls[0])
        self.assertIn('PUT', fake.calls[0])
        self.assertTrue(any('update-branch' in a for a in fake.calls[0]))

    def test_create_pr(self):
        gh, fake = client([('create', 'https://github.com/o/r/pull/9')])
        url = gh.create_pr('o/r', 'master', 'feature/testing-01-11-26',
                           'Testing 01-11-26', '/tmp/body.md', assignee='me')
        self.assertIn('--assignee', fake.calls[0])
        self.assertEqual(url, 'https://github.com/o/r/pull/9')
