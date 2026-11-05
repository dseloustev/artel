"""The approval model, queue buckets and merge readiness."""
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka_config as cfg  # noqa: E402
import bratishka_rules as rules  # noqa: E402

CONFIG = dict(cfg.DEFAULTS)
CONFIG['teams'] = {'codeReview': 'devs', 'qa': 'qa-team'}
MEMBERS = {'codeReview': {'dev'}, 'qa': {'qa'}}


def pr(number, title='AW-1 x', head='agent/x', created='2026-11-03T10:00:00Z',
       mergeable='MERGEABLE', merge_state='CLEAN', draft=False, state='OPEN',
       body='**Notes for QA**\n- check it\n\n**Platforms to test**\n- [x] iOS'):
    return {'number': number, 'title': title, 'url': 'https://x/' + str(number),
            'head': head, 'author': 'bratishka-bot', 'draft': draft, 'state': state,
            'created_at': created, 'body': body, 'review_decision': '',
            'mergeable': mergeable, 'merge_state': merge_state}


def review(login, state, at='2026-11-05T05:00:00Z'):
    return {'login': login, 'state': state, 'submitted_at': at, 'body': ''}


class TestApprovals(unittest.TestCase):
    def test_qa_team_approval(self):
        out = rules.classify(pr(1), [review('qa', 'APPROVED')], MEMBERS, CONFIG)
        self.assertTrue(out['qa_approved'])

    def test_a_codeowners_approval_is_not_qa(self):
        out = rules.classify(pr(1), [review('owner', 'APPROVED')], MEMBERS, CONFIG)
        self.assertFalse(out['qa_approved'])
        self.assertEqual(out['bucket'], 'needs-review')

    def test_qa_only_is_not_a_code_review(self):
        out = rules.classify(pr(1), [review('qa', 'APPROVED')], MEMBERS, CONFIG)
        self.assertFalse(out['code_reviewed'])

    def test_a_dismissed_approval_does_not_count(self):
        reviews = [review('dev', 'DISMISSED', '2026-11-05T06:00:00Z')]
        self.assertFalse(rules.classify(pr(1), reviews, MEMBERS, CONFIG)['code_reviewed'])

    def test_a_later_approval_wins(self):
        reviews = [review('dev', 'DISMISSED', '2026-11-04T06:00:00Z'),
                   review('dev', 'APPROVED', '2026-11-05T06:00:00Z')]
        self.assertTrue(rules.classify(pr(1), reviews, MEMBERS, CONFIG)['code_reviewed'])

    def test_an_unreadable_team_is_unknown_not_approved(self):
        members = {'codeReview': {'dev'}, 'qa': None}
        out = rules.classify(pr(1), [review('dev', 'APPROVED')], members, CONFIG)
        self.assertTrue(out['qa_unknown'])
        self.assertFalse(out['qa_approved'])

    def test_a_configured_unreadable_code_team_is_unknown(self):
        # `teams.codeReview` is configured but the team read failed: the
        # outsider fallback must not turn a QA approval into a mergeable PR.
        members = {'codeReview': None, 'qa': {'qa'}}
        reviews = [review('qa', 'APPROVED'), review('owner', 'APPROVED')]
        out = rules.classify(pr(1), reviews, members, CONFIG)
        self.assertFalse(out['code_reviewed'])
        self.assertTrue(out['code_unknown'])
        self.assertEqual(out['bucket'], 'needs-review')
        self.assertIn('code-review-unknown', out['flags'])
        merged = rules.merge_candidates([pr(1)], {1: reviews}, members, CONFIG)
        self.assertEqual(merged['ready'], [])

    def test_an_unset_code_team_keeps_the_outsider_fallback(self):
        config = dict(CONFIG, teams={'codeReview': '', 'qa': 'qa-team'})
        members = {'codeReview': None, 'qa': {'qa'}}
        reviews = [review('qa', 'APPROVED'), review('owner', 'APPROVED')]
        out = rules.classify(pr(1), reviews, members, config)
        self.assertTrue(out['code_reviewed'])
        self.assertFalse(out['code_unknown'])
        self.assertEqual(out['bucket'], 'qa-approved')

    def test_a_later_comment_does_not_withdraw_an_approval(self):
        reviews = [review('dev', 'APPROVED', '2026-11-05T05:00:00Z'),
                   review('dev', 'COMMENTED', '2026-11-05T06:00:00Z')]
        self.assertTrue(rules.classify(pr(1), reviews, MEMBERS, CONFIG)['code_reviewed'])

    def test_a_later_changes_requested_withdraws_an_approval(self):
        reviews = [review('dev', 'APPROVED', '2026-11-05T05:00:00Z'),
                   review('dev', 'CHANGES_REQUESTED', '2026-11-05T06:00:00Z')]
        self.assertFalse(rules.classify(pr(1), reviews, MEMBERS, CONFIG)['code_reviewed'])

    def test_a_later_dismissed_withdraws_an_approval(self):
        reviews = [review('dev', 'APPROVED', '2026-11-05T05:00:00Z'),
                   review('dev', 'DISMISSED', '2026-11-05T06:00:00Z')]
        self.assertFalse(rules.classify(pr(1), reviews, MEMBERS, CONFIG)['code_reviewed'])


class TestBuckets(unittest.TestCase):
    def test_the_three_buckets(self):
        prs = [pr(1), pr(2), pr(3)]
        reviews = {1: [review('dev', 'APPROVED')],
                   2: [review('dev', 'APPROVED'), review('qa', 'APPROVED')],
                   3: []}
        out = {x['number']: x['bucket'] for x in rules.queue(prs, reviews, MEMBERS, CONFIG)}
        self.assertEqual(out, {1: 'needs-qa', 2: 'qa-approved', 3: 'needs-review'})

    def test_testing_prs_are_excluded(self):
        prs = [pr(1, title='Testing 03-11-26', head='feature/testing-03-11-26')]
        self.assertEqual(rules.testing_candidates(prs, {1: []}, MEMBERS, CONFIG), [])

    def test_a_legacy_testing_pr_is_detected(self):
        self.assertTrue(rules.is_testing_pr(
            pr(1, title='Testing 02.11.26', head='testing/aw-2986-2026-11-02'), CONFIG))

    def test_a_legacy_testing_pr_is_dated_by_created_at(self):
        self.assertEqual(rules.pr_date(pr(1, title='Testing 02.11.26', head='x',
                                          created='2026-11-02T13:50:50Z'), CONFIG),
                         date(2026, 11, 2))

    def test_drafts_and_excluded_titles_are_dropped(self):
        config = dict(CONFIG, excludeTitlePatterns=['nopreview'])
        approved = [review('dev', 'APPROVED')]
        self.assertEqual(rules.testing_candidates(
            [pr(1, title='ADG-1 update nopreview')], {1: approved}, MEMBERS, config), [])
        self.assertEqual(rules.testing_candidates(
            [pr(2, draft=True)], {2: approved}, MEMBERS, config), [])


class TestMerge(unittest.TestCase):
    def reviews(self, n):
        return {n: [review('dev', 'APPROVED'), review('qa', 'APPROVED')]}

    def test_clean_is_ready(self):
        out = rules.merge_candidates([pr(1)], self.reviews(1), MEMBERS, CONFIG)
        self.assertEqual([x['number'] for x in out['ready']], [1])

    def test_a_failing_non_required_check_stays_ready(self):
        out = rules.merge_candidates(
            [pr(1, merge_state='UNSTABLE')], self.reviews(1), MEMBERS, CONFIG)
        self.assertEqual([x['number'] for x in out['ready']], [1])

    def test_behind_needs_update(self):
        out = rules.merge_candidates(
            [pr(1, merge_state='BEHIND')], self.reviews(1), MEMBERS, CONFIG)
        self.assertEqual([x['number'] for x in out['needs_update']], [1])

    def test_blocked_is_reported(self):
        out = rules.merge_candidates(
            [pr(1, merge_state='BLOCKED')], self.reviews(1), MEMBERS, CONFIG)
        self.assertEqual([x['number'] for x in out['blocked']], [1])

    def test_a_qa_only_pr_is_not_a_merge_candidate(self):
        out = rules.merge_candidates([pr(1)], {1: [review('qa', 'APPROVED')]},
                                     MEMBERS, CONFIG)
        self.assertEqual(out, {'ready': [], 'needs_update': [], 'blocked': []})
