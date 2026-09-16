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

    def test_splits_on_a_background_ampersand(self):
        # `&&` must still win over the single `&`: the alternation order is what does it.
        self.assertEqual(vg._segments('sleep 1 & gh pr create'),
                         [['sleep', '1'], ['gh', 'pr', 'create']])

    def test_command_substitution_is_a_boundary(self):
        self.assertIn(['gh', 'pr', 'create'], vg._segments('echo $(gh pr create)'))

    def test_backticks_are_a_boundary(self):
        self.assertIn(['gh', 'pr', 'create'], vg._segments('echo `gh pr create`'))

    def test_a_substituted_argument_still_leaves_the_gh_segment(self):
        # Splitting inside `--title "$(cat f)"` only ADDS segments; the one starting with
        # `gh` survives, which is all the guard reads.
        segments = vg._segments('gh pr create --title "$(cat f)"')
        self.assertTrue(any(s[:3] == ['gh', 'pr', 'create'] for s in segments), segments)

    def test_recurses_into_bash_dash_c(self):
        self.assertIn(['gh', 'pr', 'create', '--title', 'x'],
                      vg._segments('bash -c "gh pr create --title x"'))

    def test_recurses_into_a_wrapped_shell(self):
        self.assertIn(['gh', 'pr', 'create'], vg._segments('sudo sh -c "gh pr create"'))

    def test_shell_without_dash_c_recurses_into_nothing(self):
        self.assertEqual(vg._segments('bash script.sh'), [['bash', 'script.sh']])


class StripWrappersCase(unittest.TestCase):
    def test_bare_wrapper(self):
        self.assertEqual(vg._strip_wrappers(['sudo', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_wrapper_flag_with_a_separate_value(self):
        self.assertEqual(vg._strip_wrappers(['sudo', '-u', 'ci', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_timeout_duration_operand(self):
        self.assertEqual(vg._strip_wrappers(['timeout', '60', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])
        self.assertEqual(vg._strip_wrappers(['timeout', '1.5s', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_nice_flag_and_value(self):
        self.assertEqual(vg._strip_wrappers(['nice', '-n', '10', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_xargs(self):
        self.assertEqual(vg._strip_wrappers(['xargs', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_a_flag_never_swallows_the_gh_token(self):
        # `-I{}` takes a value; `gh` is the wrapped command, not that value.
        self.assertEqual(vg._strip_wrappers(['xargs', '-I{}', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_nested_wrappers(self):
        self.assertEqual(vg._strip_wrappers(['sudo', 'timeout', '60', 'gh', 'pr', 'create']),
                         ['gh', 'pr', 'create'])

    def test_a_wrapper_alone_keeps_its_last_token(self):
        self.assertEqual(vg._strip_wrappers(['sudo', 'gh']), ['gh'])

    def test_a_foreign_operand_ends_the_scan(self):
        self.assertEqual(vg._strip_wrappers(['sudo', 'make', 'release']), ['make', 'release'])


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

    # `status`, `checks` and `diff` are read verbs for `gh` and NOUNS in a tool name. If the
    # MCP side used the `gh` read set, first-match-wins would call each of these a read.
    def test_status_is_a_noun_not_the_verb(self):
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_status_set', 'bitbucket'), 'set')

    def test_build_status_post_is_a_write(self):
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_build_status_post', 'bitbucket'),
                         'post')

    def test_checks_is_a_noun_not_the_verb(self):
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_checks_create', 'bitbucket'),
                         'create')

    def test_diff_is_a_noun_not_the_verb(self):
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_diff_comment_add', 'bitbucket'),
                         'comment')

    def test_the_singular_comment_read_still_reads(self):
        # migrate-prs depends on this one; "any write verb anywhere wins" would break it.
        self.assertEqual(vg._mcp_verb('mcp__vcs__bitbucket_get_pr_comment', 'bitbucket'), 'get')

    def test_a_tool_name_without_the_mcp_prefix_still_resolves(self):
        # OpenCode does not use Claude Code's `mcp__` prefix.
        self.assertEqual(vg._mcp_verb('bitbucket_create_pr', 'bitbucket'), 'create')


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

    # `gh api --help`: "The default HTTP request method is GET normally and POST if any
    # parameters were added" -- a field flag with no explicit method is a POST.
    def test_field_flags_make_it_a_post(self):
        for flag in ('-f', '--raw-field', '-F', '--field'):
            with self.subTest(flag=flag):
                self.assertEqual(
                    vg._gh_api_class(['gh', 'api', 'repos/o/r/issues/1/comments', flag,
                                      'body=hi']), 'write')

    def test_field_flag_equals_form_makes_it_a_post(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', 'x', '--field=body=hi']), 'write')

    def test_input_flag_makes_it_a_post(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '--input', 'body.json', 'x']), 'write')

    def test_an_explicit_method_still_wins_over_fields(self):
        self.assertEqual(
            vg._gh_api_class(['gh', 'api', '--method', 'GET', 'x', '-f', 'per_page=10']), 'read')

    def test_attached_short_method_flag(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '-XPOST', 'x']), 'write')

    def test_attached_short_method_flag_with_equals(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '-X=DELETE', 'x']), 'write')

    def test_attached_short_get_is_still_a_read(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '-XGET', 'x', '-f', 'a=b']), 'read')

    # pflag's attached shorthand, the same spelling class already closed for `-X`. `gh` 2.100
    # consumes `-fbody=hi` as `--raw-field` (the control `-zbody=hi` is an unknown shorthand),
    # and `-f`/`-F` are the ONLY shorthands on this surface, so no read flag can collide.
    def test_attached_short_field_flags_make_it_a_post(self):
        for arg in ('-fbody=hi', '-Fbody=@body.txt', '-ftitle=x'):
            with self.subTest(arg=arg):
                self.assertEqual(
                    vg._gh_api_class(['gh', 'api', 'repos/o/r/issues/1/comments', arg]), 'write')

    def test_an_explicit_method_still_wins_over_an_attached_field_flag(self):
        self.assertEqual(vg._gh_api_class(['gh', 'api', '-XGET', 'repos/o/r', '-fbody=hi']),
                         'read')

    def test_long_field_flags_are_not_matched_by_the_short_prefix(self):
        # `--field=x` and `--raw-field=x` begin with `--`, so they keep going through the
        # exact / `=`-split path -- which must therefore stay.
        for arg in ('--field=body=hi', '--raw-field=body=hi'):
            with self.subTest(arg=arg):
                self.assertFalse(arg.startswith('-f') or arg.startswith('-F'))
                self.assertEqual(vg._gh_api_class(['gh', 'api', 'x', arg]), 'write')

    def test_a_field_value_is_not_read_as_a_flag(self):
        self.assertEqual(vg._gh_api_method(['gh', 'api', 'x']), 'GET')

    def test_resolved_method_is_reported(self):
        self.assertEqual(vg._gh_api_method(['gh', 'api', '--method=PATCH', 'x']), 'PATCH')
        self.assertEqual(vg._gh_api_method(['gh', 'api', '-XPOST', 'x']), 'POST')
        self.assertEqual(vg._gh_api_method(['gh', 'api', '-X', '$M', 'x']), '')


class ClassifyCase(unittest.TestCase):
    def test_read(self):
        self.assertEqual(vg._classify('get'), 'read')

    def test_write(self):
        self.assertEqual(vg._classify('create'), 'write')

    def test_none_is_unknown(self):
        self.assertEqual(vg._classify(None), 'unknown')

    def test_unrecognized_is_unknown(self):
        self.assertEqual(vg._classify('frobnicate'), 'unknown')

    def test_gh_only_read_verbs(self):
        # `gh repo clone`, `gh pr checkout`, `gh release download` are reads the design
        # promises will keep working.
        for verb in ('clone', 'checkout', 'download'):
            with self.subTest(verb=verb):
                self.assertEqual(vg._classify(verb), 'read')

    def test_fork_and_sync_are_not_reads(self):
        # Both write to the remote, so neither belongs in the read set.
        for verb in ('fork', 'sync'):
            with self.subTest(verb=verb):
                self.assertNotEqual(vg._classify(verb), 'read')

    def test_the_two_read_sets_differ_by_exactly_three_verbs(self):
        self.assertEqual(vg.GH_READ_VERBS - vg.MCP_READ_VERBS, {'status', 'checks', 'diff'})

    def test_status_reads_for_gh_but_not_for_a_tool_name(self):
        self.assertEqual(vg._classify('status'), 'read')
        self.assertEqual(vg._classify('status', vg.MCP_READ_VERBS), 'unknown')


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

    def test_gh_api_with_an_unresolvable_method_is_denied(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNotNone(self.bash('gh api -X $METHOD repos/o/r/pulls'))

    def test_deny_label_names_the_method_whatever_the_spelling(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        for command in ('gh api --method=POST x', 'gh api -XPOST x', 'gh api -X POST x',
                        'gh api x -f body=hi'):
            with self.subTest(command=command):
                reason = self.bash(command)
                self.assertIsNotNone(reason, command)
                self.assertIn('gh api POST', reason.splitlines()[0])

    # --- the perimeter, end to end ------------------------------------------
    def test_foreign_writes_are_denied(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        for command in (
            'gh api repos/o/r/issues/1/comments -f body=hi',
            'gh api repos/o/r/pulls -F title=x',
            'gh api --input body.json repos/o/r/issues/1/comments',
            'gh api -XPOST repos/o/r/issues/1/comments',
            'echo $(gh pr create --title x)',
            'echo `gh pr create --title x`',
            'sleep 1 & gh pr create --title x',
            'bash -c "gh pr create --title x"',
            'timeout 60 gh pr create --title x',
            'xargs gh pr create --title x',
            'sudo -u ci gh pr create --title x',
            'gh alias set nuke "pr create"',
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(self.bash(command), command)

    def test_foreign_reads_are_allowed(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        for command in (
            'gh api repos/o/r/pulls/1',
            'gh api --method GET repos/o/r/pulls/1 -f per_page=10',
            'gh repo clone owner/repo',
            'gh pr checkout 12',
            'gh release download v1',
            'gh auth login',
            'gh search prs --repo o/r',
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.bash(command), command)

    def test_attached_field_flags_are_denied(self):
        # `gh api` switches GET->POST as soon as a parameter is added, and pflag accepts the
        # value attached to the shorthand. Each of these posts for real.
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        for command in (
            'gh api repos/o/r/issues/1/comments -fbody=hi',
            'gh api repos/o/r/issues/1/comments -Fbody=@body.txt',
            'gh api repos/o/r/pulls -ftitle=x -fhead=b -fbase=main',
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(self.bash(command), command)

    def test_an_explicit_get_with_an_attached_field_flag_is_still_a_read(self):
        self.config(vcs={'adapter': 'bitbucket-mcp'})
        self.assertIsNone(self.bash('gh api -XGET repos/o/r -fbody=hi'))

    def test_foreign_mcp_writes_are_denied(self):
        # Every one of these was ALLOWED while the MCP surface used the `gh` read set, where
        # `status`, `checks` and `diff` masked the real verb.
        self.config(vcs={'adapter': 'github-cli'})
        for tool in ('mcp__vcs__bitbucket_status_set',
                     'mcp__vcs__bitbucket_build_status_post',
                     'mcp__vcs__bitbucket_checks_create',
                     'mcp__vcs__bitbucket_diff_comment_add'):
            with self.subTest(tool=tool):
                self.assertIsNotNone(self.mcp(tool), tool)

    def test_foreign_mcp_reads_are_allowed(self):
        self.config(vcs={'adapter': 'github-cli'})
        for tool in ('mcp__vcs__bitbucket_get_pr_comments',
                     'mcp__vcs__bitbucket_get_pr_comment',
                     'mcp__vcs__bitbucket_list_repo_prs'):
            with self.subTest(tool=tool):
                self.assertIsNone(self.mcp(tool), tool)

    def test_a_tool_without_the_mcp_prefix_is_still_guarded(self):
        # OpenCode names MCP tools without Claude Code's `mcp__` prefix; requiring it left
        # that host's Bitbucket surface unguarded.
        self.config(vcs={'adapter': 'github-cli'})
        self.assertIsNotNone(self.mcp('bitbucket_create_pr_comment'))
        self.assertIsNone(self.mcp('bitbucket_get_pr_comments'))

    # --- a malformed adapter must not unguard its domain ---------------------
    def test_unrecognized_vcs_adapter_denies_foreign_writes(self):
        self.config(vcs={'adapter': 'github'})  # a plausible typo for github-cli
        reason = self.mcp('mcp__vcs__bitbucket_create_pr_comment')
        self.assertIsNotNone(reason)
        self.assertIn('not a recognized', reason)
        self.assertIsNotNone(self.bash('gh pr create --title x'))

    def test_unrecognized_tracker_adapter_denies_foreign_writes(self):
        self.config(tracker={'adapter': 'jira'})  # a plausible typo for jira-mcp
        self.assertIsNotNone(self.mcp('mcp__tracker__jira_add_comment'))

    def test_unrecognized_adapter_still_allows_reads(self):
        self.config(vcs={'adapter': 'github'})
        self.assertIsNone(self.mcp('mcp__vcs__bitbucket_get_pr_comments'))

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
