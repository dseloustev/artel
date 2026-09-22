import json
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


SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'plan_check.py'


class TestCliStdin(unittest.TestCase):
    def test_stdin_plan_is_checked_like_a_file(self):
        import tempfile
        plan = 'Creates new:Thing and touches `scripts/plan_check.py`.\n'
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False) as handle:
            handle.write(plan)
        self.addCleanup(Path(handle.name).unlink)
        repo = Path(__file__).resolve().parent.parent
        from_file = subprocess.run([sys.executable, str(SCRIPT), '--plan', handle.name],
                                   cwd=repo, capture_output=True, text=True)
        from_stdin = subprocess.run([sys.executable, str(SCRIPT), '--plan', '-'],
                                    cwd=repo, input=plan, capture_output=True, text=True)
        self.assertEqual(from_stdin.returncode, from_file.returncode)
        a, b = json.loads(from_file.stdout), json.loads(from_stdin.stdout)
        self.assertEqual(a['data'], b['data'])

    EMPTY_INPUT = ('no document on stdin; if it was piped from spec_store.py get, that command '
                   'failed — its exit status and stderr say why (run the pipe with set -o '
                   'pipefail)')

    def run_stdin(self, raw):
        repo = Path(__file__).resolve().parent.parent
        proc = subprocess.run([sys.executable, str(SCRIPT), '--plan', '-'], cwd=repo, input=raw,
                              capture_output=True)
        return proc.returncode, json.loads(proc.stdout.decode('utf-8'))

    def run_file(self, text):
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False,
                                         encoding='utf-8') as handle:
            handle.write(text)
        self.addCleanup(Path(handle.name).unlink)
        repo = Path(__file__).resolve().parent.parent
        proc = subprocess.run([sys.executable, str(SCRIPT), '--plan', handle.name], cwd=repo,
                              capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)

    def test_empty_stdin_is_refused_not_read_as_an_empty_plan(self):
        # A failed `spec_store.py get` upstream prints nothing: without this, a
        # plan that could not be read would pass the anti-hallucination check.
        for raw in (b'', b' \n\t\r\n'):
            code, out = self.run_stdin(raw)
            self.assertEqual((code, out['ok']), (2, False), raw)
            self.assertEqual(out['error'], {'kind': 'empty_input', 'message': self.EMPTY_INPUT})

    def test_crlf_stdin_gives_the_same_data_as_the_lf_file(self):
        plan = ('Creates new:Thing here.\n'
                'Also touch `scripts/plan_check.py` and `no/such/file.py` normally.\n')
        _, from_file = self.run_file(plan)
        _, from_stdin = self.run_stdin(plan.replace('\n', '\r\n').encode('utf-8'))
        self.assertEqual(from_stdin['data'], from_file['data'])

    def test_bare_cr_line_endings_on_stdin_split_lines_like_the_file(self):
        # Path.read_text() turns a lone \r into a line break; stdin must too, or
        # the line-scoped `new:` marker would cover the whole document.
        plan = ('Creates new:Thing here.\n'
                'Also touch `scripts/plan_check.py` and `no/such/file.py` normally.\n')
        _, from_file = self.run_file(plan)
        _, from_stdin = self.run_stdin(plan.replace('\n', '\r').encode('utf-8'))
        self.assertEqual(from_stdin['data'], from_file['data'])

    def test_an_empty_plan_file_keeps_todays_behaviour(self):
        code, out = self.run_file('')
        self.assertEqual((code, out['ok'], out['data']['checked']), (0, True, 0))


if __name__ == '__main__':
    unittest.main()
