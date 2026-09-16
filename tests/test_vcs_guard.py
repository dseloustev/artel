"""The PreToolUse VCS guard: a project configured for one platform never writes to the other.

Contract: docs/config.md (the `guard` section) and
docs/superpowers/specs/2026-09-16-vcs-platform-migration-design.md section 2.
"""
import sys
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
sys.path.insert(0, str(HOOKS))
import vcs_guard as vg  # noqa: E402


class SegmentsCase(unittest.TestCase):
    def test_splits_on_pipe(self):
        self.assertEqual(vg._segments('cat body.md | gh pr create'),
                         [['cat', 'body.md'], ['gh', 'pr', 'create']])

    def test_splits_on_and_or_and_semicolon(self):
        self.assertEqual(vg._segments('git push && gh pr create; echo done'),
                         [['git', 'push'], ['gh', 'pr', 'create'], ['echo', 'done']])

    def test_strips_leading_env_assignments(self):
        self.assertEqual(vg._segments('GH_CONFIG_DIR=~/.config/gh-adguard gh pr create'),
                         [['gh', 'pr', 'create']])

    def test_gh_as_an_argument_is_not_a_command(self):
        self.assertEqual(vg._segments('echo gh'), [['echo', 'gh']])

    def test_unbalanced_quotes_fall_back_to_whitespace_split(self):
        self.assertEqual(vg._segments('gh pr create --title "unclosed'),
                         [['gh', 'pr', 'create', '--title', '"unclosed']])

    def test_empty_command(self):
        self.assertEqual(vg._segments(''), [])


class McpPlatformCase(unittest.TestCase):
    def test_bitbucket(self):
        self.assertEqual(vg._mcp_platform('mcp__vcs__bitbucket_get_pr'), 'bitbucket')

    def test_jira(self):
        self.assertEqual(vg._mcp_platform('mcp__tracker__jira_add_comment'), 'jira')

    def test_unrelated_server_has_no_platform(self):
        self.assertIsNone(vg._mcp_platform('mcp__kartoteka__artifact_put'))


class McpVerbCase(unittest.TestCase):
    def test_read_verb_wins_over_a_noun_that_looks_like_a_write(self):
        # Substring matching would see the write token 'comment' here and deny a READ that
        # migrate-prs depends on. Verb extraction is positional, not substring.
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_get_pr_comments', 'bitbucket'), 'get')

    def test_write_verb(self):
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_create_pr_comment', 'bitbucket'),
                         'create')

    def test_server_named_after_the_platform_skips_the_noun(self):
        self.assertEqual(vg._mcp_verb('mcp__bitbucket__pr_create', 'bitbucket'), 'create')

    def test_whoami(self):
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_whoami', 'bitbucket'), 'whoami')

    def test_no_recognized_verb(self):
        self.assertIsNone(vg._mcp_verb('mcp__vcs__bitbucket_fetch_activity', 'bitbucket'))


class GhNounVerbCase(unittest.TestCase):
    def test_pr_view(self):
        self.assertEqual(vg._gh_noun_verb(['gh', 'pr', 'view', '12', '--json', 'title']),
                         ('pr', 'view'))

    def test_issue_comment(self):
        self.assertEqual(vg._gh_noun_verb(['gh', 'issue', 'comment', '7', '--body', 'x']),
                         ('issue', 'comment'))

    def test_auth_status(self):
        self.assertEqual(vg._gh_noun_verb(['gh', 'auth', 'status']), ('auth', 'status'))

    def test_noun_without_a_verb(self):
        self.assertEqual(vg._gh_noun_verb(['gh', 'pr']), ('pr', None))


class GhApiClassCase(unittest.TestCase):
    def test_defaults_to_read(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', 'repos/o/r/pulls/1']), 'read')

    def test_mutating_method_is_a_write(self):
        self.assertEqual(
            vg._gh_api_class(['gh', 'api', '-X', 'POST', 'repos/o/r/issues/1/comments']),
            'write')

    def test_method_equals_form(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '--method=PATCH', 'x']), 'write')

    def test_explicit_get_is_a_read(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '-X', 'GET', 'x']), 'read')

    def test_unresolvable_method_is_unknown(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '-X', '$METHOD', 'x']), 'unknown')


class ClassifyCase(unittest.TestCase):
    def test_read(self):
        self.assertEqual(vg._classify('get'), 'read')

    def test_write(self):
        self.assertEqual(vg._classify('create'), 'write')

    def test_none_is_unknown(self):
        self.assertEqual(vg._classify(None), 'unknown')

    def test_unrecognized_is_unknown(self):
        self.assertEqual(vg._classify('frobnicate'), 'unknown')


if __name__ == '__main__':
    unittest.main()
