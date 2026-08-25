"""The using-artel SessionStart hook: inert without a config, JSON with the
router body and a host status with one, exit 0 no matter what."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
HOOK = HOOKS / 'using_artel.py'
sys.path.insert(0, str(HOOKS))
import using_artel as ua  # noqa: E402

CONFIG = {
    'ticket': {'projectKey': 'AW'},
    'specs': {'dir': 'specs/.current'},
    'knowledge': {'adapter': 'kartoteka', 'baseUrl': 'http://127.0.0.1:8734'},
}


def write_config(root, config=CONFIG):
    (root / '.artel').mkdir(parents=True, exist_ok=True)
    (root / '.artel' / 'config.json').write_text(json.dumps(config), encoding='utf-8')


def run_hook(cwd):
    return subprocess.run([sys.executable, str(HOOK)], cwd=str(cwd), input='{}',
                          capture_output=True, text=True, timeout=30)


class TestUnconfiguredHost(unittest.TestCase):
    def test_prints_nothing_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hook(tmp)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')


class TestConfiguredHost(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_config(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def context(self):
        result = run_hook(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        output = payload['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'SessionStart')
        return output['additionalContext']

    def test_emits_the_router_body_without_frontmatter(self):
        ctx = self.context()
        self.assertIn('<SUBAGENT-STOP>', ctx)
        self.assertNotIn('\nname: using-artel', ctx)
        self.assertNotIn('\ndescription:', ctx)

    def test_reports_the_adapter_and_base_url(self):
        self.assertIn('knowledge.adapter: kartoteka (http://127.0.0.1:8734)', self.context())

    def test_reports_no_active_ticket(self):
        self.assertIn('active ticket: none', self.context())

    def test_reports_the_active_ticket_verbatim_with_phase(self):
        specs = self.root / 'specs' / '.current'
        specs.mkdir(parents=True)
        (specs / '.active_ticket').write_text('\nAW-1234-2\n', encoding='utf-8')
        self.assertIn('active ticket: AW-1234-2', self.context())

    def test_reports_whether_ast_index_is_on_path(self):
        ctx = self.context()
        self.assertIn('- ast-index: ', ctx)
        self.assertTrue('ast-index: on PATH' in ctx or 'ast-index: not on PATH' in ctx)

    def test_names_an_unparseable_config_instead_of_defaulting_silently(self):
        (self.root / '.artel' / 'config.json').write_text('{not json', encoding='utf-8')
        ctx = self.context()
        self.assertIn('config: present but NOT valid JSON', ctx)
        self.assertIn('knowledge.adapter: none', ctx)


class TestPureFunctions(unittest.TestCase):
    def test_strip_frontmatter_removes_the_leading_block(self):
        text = '---\nname: x\ndescription: "y"\n---\n\nBody line\n'
        self.assertEqual(ua.strip_frontmatter(text), 'Body line\n')

    def test_strip_frontmatter_leaves_text_without_one(self):
        self.assertEqual(ua.strip_frontmatter('Body only\n'), 'Body only\n')

    def test_host_status_defaults_the_adapter_to_none(self):
        status = ua.host_status({})
        self.assertIn('knowledge.adapter: none', status)
        self.assertIn('active ticket: none', status)
        self.assertIn('config: present', status)

    def test_host_status_reports_ast_index_on_path(self):
        with mock.patch.object(ua.shutil, 'which', return_value='/opt/homebrew/bin/ast-index'):
            self.assertIn('- ast-index: on PATH', ua.host_status({}))

    def test_host_status_reports_ast_index_missing(self):
        with mock.patch.object(ua.shutil, 'which', return_value=None):
            self.assertIn('- ast-index: not on PATH', ua.host_status({}))


class TestFailOpen(unittest.TestCase):
    def test_unreadable_skill_exits_zero_with_a_stderr_line(self):
        original_path, original_cwd = ua.SKILL_PATH, os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_config(root)
            os.chdir(root)
            ua.SKILL_PATH = root / 'missing.md'
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    code = ua.main()
            finally:
                ua.SKILL_PATH = original_path
                os.chdir(original_cwd)
        self.assertEqual(code, 0)
        self.assertIn('using-artel hook error', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
