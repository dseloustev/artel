import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
