import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import plan_check  # noqa: E402


class TestExtractAnchors(unittest.TestCase):
    def test_ref_and_new_tokens_with_trailing_punct_trim(self):
        md = 'Uses ref:UserService, creates new:CacheRebuilder.'
        self.assertEqual(plan_check.extract_anchors(md),
                         [('ref', 'UserService'), ('new', 'CacheRebuilder')])

    def test_dedupes_by_kind_and_value(self):
        md = 'ref:Foo then ref:Foo again\nref:Foo once more'
        self.assertEqual(plan_check.extract_anchors(md), [('ref', 'Foo')])

    def test_backticked_repo_path_is_implicit_ref(self):
        md = 'Touch `src/api/client.ts` here.'
        self.assertEqual(plan_check.extract_anchors(md), [('path', 'src/api/client.ts')])

    def test_backticked_token_without_slash_or_extension_ignored(self):
        md = 'See `README` and `Makefile.am is odd` and `docs/design` too.'
        self.assertEqual(plan_check.extract_anchors(md), [])

    def test_line_scoped_new_anchor_suppresses_backticked_path(self):
        md = 'Create new:CacheRebuilder in `lib/data/cache_rebuilder.dart` here.'
        self.assertEqual(plan_check.extract_anchors(md), [('new', 'CacheRebuilder')])

    def test_line_scoped_new_file_marker_suppresses_backticked_path(self):
        md = 'Add `src/util/helpers.py` (new file).'
        self.assertEqual(plan_check.extract_anchors(md), [])

    def test_exact_value_new_suppresses_document_wide(self):
        md = 'new:src/util/helpers.py\nLater we flesh out `src/util/helpers.py` fully.'
        self.assertEqual(plan_check.extract_anchors(md), [('new', 'src/util/helpers.py')])

    def test_path_on_other_line_without_marker_still_checked(self):
        md = 'Create new:Thing here.\nAlso touch `src/other/file.py` normally.'
        self.assertEqual(plan_check.extract_anchors(md),
                         [('new', 'Thing'), ('path', 'src/other/file.py')])


class TestClassifyAstIndexOutput(unittest.TestCase):
    def test_nonempty_json_list_is_hit(self):
        self.assertEqual(
            plan_check._classify_ast_index_output(0, '[{"name": "Foo", "file": "a.py"}]'),
            'hit')

    def test_empty_json_list_is_miss(self):
        self.assertEqual(plan_check._classify_ast_index_output(0, '[]'), 'miss')

    def test_missing_index_message_with_returncode_0_is_unusable(self):
        stdout = "Index not found. Run 'ast-index rebuild' first.\n"
        self.assertEqual(plan_check._classify_ast_index_output(0, stdout), 'unusable')

    def test_nonzero_returncode_with_valid_json_is_unusable(self):
        self.assertEqual(plan_check._classify_ast_index_output(1, '[]'), 'unusable')


class TestResolvePassAstIndexFallback(unittest.TestCase):
    """resolve_pass must not report a real symbol as a hallucination just because
    ast-index has no index built yet — it should fall back to git grep per symbol."""

    def test_unusable_ast_index_falls_back_to_git_grep_and_resolves(self):
        def fake_run(cmd, timeout):
            if cmd[:2] == ['ast-index', 'symbol']:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="Index not found. Run 'ast-index rebuild' first.\n",
                    stderr='')
            if cmd[:2] == ['git', 'grep']:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout='hooks/hook_common.py\n', stderr='')
            raise AssertionError('unexpected cmd: {}'.format(cmd))

        with patch.object(plan_check, '_run', side_effect=fake_run):
            unresolved, ast_index_misses = plan_check.resolve_pass(
                [('ref', 'resolve_active_ticket')], True)
        self.assertEqual(unresolved, [])
        self.assertEqual(ast_index_misses, [])

    def test_real_ast_index_miss_is_reported_and_retry_eligible(self):
        def fake_run(cmd, timeout):
            if cmd[:2] == ['ast-index', 'symbol']:
                return subprocess.CompletedProcess(cmd, 0, stdout='[]', stderr='')
            raise AssertionError('unexpected cmd: {}'.format(cmd))

        with patch.object(plan_check, '_run', side_effect=fake_run):
            unresolved, ast_index_misses = plan_check.resolve_pass(
                [('ref', 'TotallyMadeUpSymbol')], True)
        self.assertEqual(unresolved, [{'ref': 'TotallyMadeUpSymbol', 'reason': 'symbol not found'}])
        self.assertEqual(ast_index_misses, ['TotallyMadeUpSymbol'])


if __name__ == '__main__':
    unittest.main()
