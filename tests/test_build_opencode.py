"""The OpenCode build generator: every canonical skill becomes a prefixed
`artel-<name>` skill with a host glossary, baked paths and OpenCode-legal
frontmatter, plus a command wrapper. Guards the zero-regression promise by
asserting what the generated files contain (and omit).

Docs-style guard, same family as tests/test_using_artel_docs.py: a generated
skill that still says `${CLAUDE_PLUGIN_ROOT}` or `/artel:` breaks in a session
nobody is watching.
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'scripts' / 'build_opencode.py'

VALID_NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
GLOSSARY_MARKER = '<OPENCODE-HOST-NOTES>'
MAX_DESCRIPTION = 1024


def source_skills():
    """Every skill shipped under skills/, by folder name."""
    return sorted(p.parent.name for p in (ROOT / 'skills').glob('*/SKILL.md'))


class BuildBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.root = tmp / 'artel'
        cls.out = tmp / 'dist'
        cls.root.mkdir()
        proc = subprocess.run(
            [sys.executable, str(BUILD), '--root', str(cls.root), '--out', str(cls.out)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError('build failed: ' + proc.stderr)
        cls.proc = proc

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestSkillBuild(BuildBase):
    def test_build_succeeds_and_reports_the_count(self):
        self.assertEqual(self.proc.returncode, 0)
        self.assertIn('{} skills'.format(len(source_skills())), self.proc.stdout)

    def test_every_skill_is_built_prefixed(self):
        for name in source_skills():
            path = self.out / 'skills' / ('artel-' + name) / 'SKILL.md'
            self.assertTrue(path.is_file(), 'missing ' + str(path))

    def test_generated_names_are_opencode_legal(self):
        for path in sorted((self.out / 'skills').glob('*/SKILL.md')):
            self.assertRegex(path.parent.name, VALID_NAME)

    def test_frontmatter_keeps_description_and_drops_claude_fields(self):
        for path in sorted((self.out / 'skills').glob('*/SKILL.md')):
            head = path.read_text(encoding='utf-8').split('---')[1]
            self.assertIn('description: "', head, path.parent.name + ' lost its description')
            for field in ('argument-hint:', 'model:', 'disable-model-invocation:'):
                self.assertNotIn(field, head,
                                 path.parent.name + ' keeps Claude-only ' + field)

    def test_description_within_opencode_limit(self):
        for path in sorted((self.out / 'skills').glob('*/SKILL.md')):
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.startswith('description: '):
                    self.assertLessEqual(len(line), len('description: ') + MAX_DESCRIPTION,
                                         path.parent.name + ' description too long')
                    break

    def test_plugin_root_is_baked(self):
        for path in sorted((self.out / 'skills').glob('*/SKILL.md')):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('${CLAUDE_PLUGIN_ROOT}', text,
                             path.parent.name + ' still names the Claude variable')

    def test_every_generated_skill_carries_the_host_glossary(self):
        for path in sorted((self.out / 'skills').glob('*/SKILL.md')):
            self.assertIn(GLOSSARY_MARKER, path.read_text(encoding='utf-8'),
                          path.parent.name + ' has no glossary')

    def test_artel_refs_prefixed_but_ast_index_refs_survive(self):
        router = (self.out / 'skills' / 'artel-using-artel' / 'SKILL.md').read_text(
            encoding='utf-8')
        self.assertNotRegex(router, r'/artel:[a-z0-9-]')
        self.assertIn('/ast-index:ast-index', router)
        self.assertIn('artel-feature-development', router)


class TestCommandBuild(BuildBase):
    def test_a_command_wrapper_per_skill(self):
        for name in source_skills():
            path = self.out / 'commands' / ('artel-' + name + '.md')
            self.assertTrue(path.is_file(), 'missing ' + str(path))

    def test_wrapper_passes_arguments_and_stays_in_the_main_session(self):
        for name in source_skills():
            text = (self.out / 'commands' / ('artel-' + name + '.md')).read_text(
                encoding='utf-8')
            self.assertIn('agent: build', text)
            self.assertNotIn('subtask: true', text)
            self.assertIn('$ARGUMENTS', text)
            self.assertIn('`artel-' + name + '`', text)

    def test_wrapper_quotes_the_argument_hint(self):
        # researcher carries an argument-hint in its frontmatter.
        text = (self.out / 'commands' / 'artel-researcher.md').read_text(encoding='utf-8')
        self.assertIn('[ticket-id]', text)


def source_agents():
    """Every agent shipped under agents/, by file stem (README.md is not an agent)."""
    return sorted(p.stem for p in (ROOT / 'agents').glob('*.md') if p.name != 'README.md')


class TestAgentBuild(BuildBase):
    def test_every_agent_is_built_prefixed(self):
        for name in source_agents():
            path = self.out / 'agents' / ('artel-' + name + '.md')
            self.assertTrue(path.is_file(), 'missing ' + str(path))

    def test_agent_names_are_opencode_legal(self):
        for path in sorted((self.out / 'agents').glob('artel-*.md')):
            self.assertRegex(path.stem, VALID_NAME)

    def test_readme_is_not_an_agent(self):
        self.assertFalse((self.out / 'agents' / 'artel-README.md').is_file())

    def test_frontmatter_is_opencode_shape(self):
        for path in sorted((self.out / 'agents').glob('artel-*.md')):
            head = path.read_text(encoding='utf-8').split('---')[1]
            self.assertIn('description: "', head)
            self.assertIn('mode: subagent', head)
            self.assertNotIn('model:', head, path.name + ' keeps a Claude model tier')

    def test_agent_carries_glossary_and_baked_root(self):
        for path in sorted((self.out / 'agents').glob('artel-*.md')):
            text = path.read_text(encoding='utf-8')
            self.assertIn(GLOSSARY_MARKER, text)
            self.assertNotIn('${CLAUDE_PLUGIN_ROOT}', text)

    def test_build_reports_agent_count(self):
        self.assertIn('{} agents'.format(len(source_agents())), self.proc.stdout)


class TestDeterminism(BuildBase):
    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as second:
            out2 = Path(second) / 'dist'
            proc = subprocess.run(
                [sys.executable, str(BUILD), '--root', str(self.root), '--out', str(out2)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for path in sorted((self.out / 'skills').glob('*/SKILL.md')):
                twin = out2 / 'skills' / path.parent.name / 'SKILL.md'
                self.assertTrue(twin.is_file(), 'second build missed ' + path.parent.name)
                self.assertEqual(path.read_text(encoding='utf-8'),
                                 twin.read_text(encoding='utf-8'),
                                 path.parent.name + ' differs between builds')


class TestPluginSource(unittest.TestCase):
    """The OpenCode host's VCS-guard binding, asserted against the plugin source.

    There is no TypeScript runner in this repo, so source-level checks are the only ones
    available -- which is why they assert placement and casing rather than mere presence."""

    def setUp(self):
        self.source = (ROOT / 'opencode' / 'plugin' / 'artel.ts').read_text(encoding='utf-8')

    def test_binds_the_vcs_guard(self):
        self.assertIn('vcs_guard.py', self.source)
        self.assertIn('artel vcs guard', self.source)

    def test_binding_precedes_the_edit_tools_early_return(self):
        # A binding placed after that return never sees a bash or MCP call -- neither is an
        # edit tool -- so the guard would look installed and enforce nothing.
        self.assertLess(self.source.index('vcs_guard.py'),
                        self.source.index('EDIT_TOOLS.has(input.tool)'))

    def test_binding_is_not_limited_to_the_mcp_prefix(self):
        # `mcp__` is a Claude Code convention; OpenCode names MCP tools without it, so a
        # prefix test would forward bash only and leave Bitbucket MCP writes unguarded here.
        # hooks/vcs_guard.py classifies any non-Bash tool by the platform token in its name.
        self.assertNotIn('startsWith("mcp__")', self.source)
        self.assertIn('PLATFORM_TOKENS', self.source)
        for token in ('"bitbucket"', '"github"', '"jira"'):
            self.assertIn(token, self.source)
        self.assertIn('isPlatformTool(input.tool)', self.source)
        self.assertLess(self.source.index('isPlatformTool(input.tool)'),
                        self.source.index('EDIT_TOOLS.has(input.tool)'))

    def test_payload_carries_claude_code_tool_casing(self):
        # hooks/vcs_guard.py matches `tool == 'Bash'` exactly; OpenCode's tool name is the
        # lowercase `bash`. A lowercase payload would silently guard nothing.
        self.assertIn('"Bash"', self.source)
        self.assertLess(self.source.index('"Bash"'),
                        self.source.index('EDIT_TOOLS.has(input.tool)'))


if __name__ == '__main__':
    unittest.main()
