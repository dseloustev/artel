"""The PreToolUse VCS guard: a project configured for one platform never writes to the other.

Contract: docs/config.md (the `guard` section) and
docs/superpowers/specs/2026-09-16-vcs-platform-migration-design.md section 2.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
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

    def test_global_repo_flag_before_the_noun(self):
        self.assertEqual(vg._gh_noun_verb(['gh', '--repo', 'owner/repo', 'pr', 'view', '12']),
                         ('pr', 'view'))

    def test_short_repo_flag_before_the_noun(self):
        self.assertEqual(vg._gh_noun_verb(['gh', '-R', 'owner/repo', 'pr', 'create']),
                         ('pr', 'create'))

    def test_repo_flag_equals_form_has_no_separate_value(self):
        self.assertEqual(vg._gh_noun_verb(['gh', '--repo=owner/repo', 'pr', 'view']),
                         ('pr', 'view'))


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


class DecisionCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)
        (self.root / '.artel').mkdir()

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def config(self, **sections):
        base = {'vcs': {'adapter': 'github-cli'}, 'tracker': {'adapter': 'jira-mcp'}}
        base.update(sections)
        (self.root / '.artel' / 'config.json').write_text(json.dumps(base), encoding='utf-8')

    def run_guard(self, payload):
        """Returns the permissionDecisionReason, or None when the call was allowed."""
        envelope = self.run_guard_raw(payload)
        if envelope is None:
            return None
        return envelope['hookSpecificOutput']['permissionDecisionReason']

    def run_guard_raw(self, payload):
        """Returns the full parsed envelope dict, or None when the call was allowed."""
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(json.dumps(payload))):
            with contextlib.redirect_stdout(out):
                vg.main()
        text = out.getvalue().strip()
        if not text:
            return None
        return json.loads(text)

    def bash(self, command):
        return self.run_guard({'tool_name': 'Bash', 'tool_input': {'command': command}})

    def mcp(self, tool):
        return self.run_guard({'tool_name': tool, 'tool_input': {}})

    # --- native platform: never touched -------------------------------------
    def test_native_github_write_allowed(self):
        self.config()
        self.assertIsNone(self.bash('gh pr create --title x'))

    def test_native_bitbucket_write_allowed(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNone(self.mcp('mcp__vcs__bitbucket_create_pr'))

    # --- foreign reads still work (migrate-prs depends on this) --------------
    def test_foreign_read_allowed(self):
        self.config()
        self.assertIsNone(self.mcp('mcp__vcs__bitbucket_get_pr_comments'))

    def test_foreign_gh_read_allowed_under_bitbucket(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNone(self.bash('gh pr view 12 --json title'))

    # --- foreign writes are the whole point ---------------------------------
    def test_foreign_write_denied(self):
        self.config()
        reason = self.mcp('mcp__vcs__bitbucket_create_pr_comment')
        self.assertIsNotNone(reason)
        self.assertIn('bitbucket', reason)
        self.assertIn('github-cli', reason)

    def test_foreign_gh_write_denied_under_bitbucket(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('gh pr create --title x'))

    def test_unknown_verb_denied(self):
        self.config()
        self.assertIsNotNone(self.mcp('mcp__vcs__bitbucket_fetch_activity'))

    def test_env_prefixed_write_is_still_seen(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('GH_CONFIG_DIR=~/.config/gh-work gh pr create -t x'))

    def test_piped_write_is_still_seen(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('cat body.md | gh pr create --body-file -'))

    def test_absolute_path_gh_is_still_seen(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('/opt/homebrew/bin/gh pr create -t x'))

    def test_sudo_wrapped_gh_is_still_seen(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('sudo gh pr create -t x'))

    def test_env_command_wrapper_is_still_seen(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('env FOO=1 gh pr create -t x'))

    def test_non_platform_gh_nouns_are_not_guarded(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNone(self.bash('gh search prs --repo o/r --state open'))
        self.assertIsNone(self.bash('gh auth login'))
        self.assertIsNone(self.bash('gh run rerun 1'))

    # --- domain routing -----------------------------------------------------
    def test_gh_issue_is_governed_by_the_tracker_adapter(self):
        # vcs is bitbucket, tracker is github-issues: `gh issue comment` is NATIVE.
        self.config(vcs={'adapter': 'bitbucket-mcp'}, tracker={'adapter': 'github-issues'})
        self.assertIsNone(self.bash('gh issue comment 7 --body x'))

    def test_gh_pr_is_governed_by_the_vcs_adapter(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'}, tracker={'adapter': 'github-issues'})
        self.assertIsNotNone(self.bash('gh pr create --title x'))

    def test_tracker_none_leaves_the_tracker_domain_unenforced(self):
        self.config(tracker={'adapter': 'none'})
        self.assertIsNone(self.bash('gh issue comment 7 --body x'))
        self.assertIsNone(self.mcp('mcp__tracker__jira_add_comment'))

    def test_foreign_tracker_write_denied(self):
        self.config(tracker={'adapter': 'github-issues'})
        self.assertIsNotNone(self.mcp('mcp__tracker__jira_add_comment'))

    def test_bitbucket_tool_named_issue_is_still_the_vcs_domain(self):
        # The hole: 'issue' in the NAME must not route a Bitbucket write into the
        # unenforced tracker domain under the default tracker.adapter "none".
        self.config(tracker={'adapter': 'none'})
        self.assertIsNotNone(self.mcp('mcp__vcs__bitbucket_create_issue_comment'))

    def test_github_mcp_write_denied_when_native_to_neither_domain(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'}, tracker={'adapter': 'jira-mcp'})
        self.assertIsNotNone(self.mcp('mcp__github__add_issue_comment'))

    def test_github_mcp_write_allowed_when_native_to_the_tracker(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'}, tracker={'adapter': 'github-issues'})
        self.assertIsNone(self.mcp('mcp__github__add_issue_comment'))

    def test_github_mcp_write_allowed_when_native_to_the_vcs(self):
        self.config(vcs={'adapter': 'github-cli'}, tracker={'adapter': 'jira-mcp'})
        self.assertIsNone(self.mcp('mcp__github__create_pull_request'))

    # --- gh api -------------------------------------------------------------
    def test_gh_api_get_allowed_when_foreign(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNone(self.bash('gh api repos/o/r/pulls/1'))

    def test_gh_api_post_denied_when_foreign(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('gh api -X POST repos/o/r/issues/1/comments'))

    def test_deny_label_survives_flags(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        reason = self.bash('gh api -X POST repos/o/r/issues/1/comments -f body=x')
        self.assertIsNotNone(reason)
        self.assertNotIn('-X', reason.splitlines()[0])

    # --- escape hatch -------------------------------------------------------
    def test_extra_read_tools_rescues_an_unknown_verb(self):
        self.config(guard={'extraReadTools': ['bitbucket_fetch_activity']})
        self.assertIsNone(self.mcp('mcp__vcs__bitbucket_fetch_activity'))

    def test_extra_read_tools_of_a_wrong_type_is_ignored(self):
        self.config(guard={'extraReadTools': 'not-a-list'})
        self.assertIsNotNone(self.mcp('mcp__vcs__bitbucket_fetch_activity'))

    def test_extra_read_tools_does_not_rescue_a_recognized_write(self):
        self.config(guard={'extraReadTools': ['comment']})
        self.assertIsNotNone(self.mcp('mcp__vcs__bitbucket_create_pr_comment'))

    # --- fail open where artel is not in charge -----------------------------
    def test_missing_config_allows(self):
        self.assertIsNone(self.mcp('mcp__vcs__bitbucket_create_pr_comment'))

    def test_unparseable_config_allows(self):
        (self.root / '.artel' / 'config.json').write_text('{oops', encoding='utf-8')
        self.assertIsNone(self.mcp('mcp__vcs__bitbucket_create_pr_comment'))

    def test_unrelated_tool_allows(self):
        self.config()
        self.assertIsNone(self.mcp('mcp__kartoteka__artifact_put'))

    def test_unrelated_bash_allows(self):
        self.config()
        self.assertIsNone(self.bash('git status --porcelain'))

    def test_non_dict_tool_input_allows_without_crashing(self):
        self.config()
        self.assertIsNone(self.run_guard({'tool_name': 'Bash', 'tool_input': 'gh pr create'}))

    # --- the deny envelope ---------------------------------------------------
    def test_deny_envelope_is_exact(self):
        self.config()
        envelope = self.run_guard_raw(
            {'tool_name': 'mcp__vcs__bitbucket_create_pr_comment', 'tool_input': {}})
        hook_output = envelope['hookSpecificOutput']
        self.assertEqual(hook_output['hookEventName'], 'PreToolUse')
        self.assertEqual(hook_output['permissionDecision'], 'deny')
        self.assertIn('bitbucket', hook_output['permissionDecisionReason'])


if __name__ == '__main__':
    unittest.main()
