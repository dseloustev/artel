"""The OpenCode build generator: every canonical skill becomes a prefixed
`artel-<name>` skill with a host glossary, baked paths and OpenCode-legal
frontmatter, plus a command wrapper. Guards the zero-regression promise by
asserting what the generated files contain (and omit).

Docs-style guard, same family as tests/test_using_artel_docs.py: a generated
skill that still says `${CLAUDE_PLUGIN_ROOT}` or `/artel:` breaks in a session
nobody is watching.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'scripts' / 'build_opencode.py'
sys.path.insert(0, str(ROOT / 'scripts'))
import build_opencode  # noqa: E402

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

    def test_glossary_routes_worktree_tools_to_the_opencode_fallback(self):
        text = (self.out / 'skills' / 'artel-move-to-worktree' / 'SKILL.md').read_text(
            encoding='utf-8')
        glossary = text[text.index(GLOSSARY_MARKER):]
        self.assertIn('`EnterWorktree` / `ExitWorktree` — not available on OpenCode', glossary)

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

    def test_agent_glossary_names_the_subagent_tool(self):
        text = (self.out / 'agents' / 'artel-implementer.md').read_text(encoding='utf-8')
        self.assertIn('the `subagent` tool', text)
        self.assertNotIn('the `task` tool', text)

    def test_agent_glossary_maps_the_v2_tool_names(self):
        text = (self.out / 'agents' / 'artel-implementer.md').read_text(encoding='utf-8')
        self.assertIn('`read`, `edit`, `write`, `grep`, `shell`', text)


class TestDeterminism(BuildBase):
    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as second:
            out2 = Path(second) / 'dist'
            proc = subprocess.run(
                [sys.executable, str(BUILD), '--root', str(self.root), '--out', str(out2)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # A pointer into a skill's directory is baked to the output directory, so two
            # builds agree once that one path is put back.
            here, there = str(self.out.resolve()), str(out2.resolve())
            for path in sorted((self.out / 'skills').rglob('*.md')):
                rel = path.relative_to(self.out)
                twin = out2 / rel
                self.assertTrue(twin.is_file(), 'second build missed ' + str(rel))
                self.assertEqual(path.read_text(encoding='utf-8'),
                                 twin.read_text(encoding='utf-8').replace(there, here),
                                 str(rel) + ' differs between builds')


def source_companions():
    """Every other Markdown file under a skill's directory, as (skill folder, relative path)."""
    pairs = []
    for skill in sorted((ROOT / 'skills').glob('*/SKILL.md')):
        for path in sorted(skill.parent.rglob('*.md')):
            if path != skill:
                pairs.append((skill.parent.name, path.relative_to(skill.parent).as_posix()))
    return pairs


class TestCompanionFiles(BuildBase):
    """A skill's other Markdown files — feature-development's head and tail files, templates,
    references — are generated next to its SKILL.md and baked like it, with no frontmatter and
    no glossary; and a pointer into a skill's directory lands on the generated copy."""

    SKILL = 'artel-feature-development'
    PARTS = ('heads/full.md', 'heads/lean.md', 'heads/bug.md', 'tail.md')

    def part(self, rel):
        return (self.out / 'skills' / self.SKILL / rel).read_text(encoding='utf-8')

    def test_every_other_markdown_file_is_generated_beside_its_skill(self):
        pairs = source_companions()
        for rel in self.PARTS:
            self.assertIn(('feature-development', rel), pairs)
        for skill, rel in pairs:
            path = self.out / 'skills' / ('artel-' + skill) / rel
            self.assertTrue(path.is_file(), 'missing ' + str(path))
        self.assertIn('{} other skill files'.format(len(pairs)), self.proc.stdout)

    def test_the_head_and_tail_files_are_baked(self):
        docs = str(self.root.resolve()) + '/docs/'
        for rel in self.PARTS:
            with self.subTest(rel):
                text = self.part(rel)
                self.assertNotIn('${CLAUDE_PLUGIN_ROOT}', text)
                self.assertNotRegex(text, r'/artel:[a-z0-9-]')
                self.assertIn(docs, text)
        self.assertIn('Next: artel-feature-development <TICKET_ID> --head=full',
                      self.part('heads/lean.md'))

    def test_they_get_no_frontmatter_and_no_glossary(self):
        for rel in self.PARTS:
            with self.subTest(rel):
                text = self.part(rel)
                self.assertTrue(text.startswith('# The '))
                self.assertNotIn(GLOSSARY_MARKER, text)

    def test_a_file_with_no_dialect_in_it_is_generated_unchanged(self):
        rel = Path('assets') / 'templates' / 'idea.template.md'
        self.assertEqual(
            (ROOT / 'skills' / 'generate-idea' / rel).read_text(encoding='utf-8'),
            (self.out / 'skills' / 'artel-generate-idea' / rel).read_text(encoding='utf-8'))

    def test_the_skill_points_at_the_generated_copies(self):
        skill = self.part('SKILL.md')
        here = str((self.out / 'skills' / self.SKILL).resolve()) + '/'
        for rel in self.PARTS:
            self.assertIn('`' + here + rel + '`', skill, rel)
        self.assertNotIn(str(self.root.resolve()) + '/skills/feature-development/', skill)
        # the head files point at each other and at the tail the same way
        self.assertIn('`' + here + 'heads/full.md`', self.part('heads/lean.md'))
        self.assertIn('`' + here + 'heads/lean.md`', self.part('heads/bug.md'))
        self.assertIn('`' + here + 'SKILL.md`', self.part('tail.md'))

    def test_every_pointer_into_a_skill_directory_resolves(self):
        generated = re.compile(re.escape(str((self.out / 'skills').resolve())) + r'/[\w./-]+')
        unbaked = re.compile(re.escape(str(self.root.resolve())) + r'/skills/[\w./-]+\.md')
        files = (sorted((self.out / 'skills').rglob('*.md'))
                 + sorted((self.out / 'agents').glob('*.md')))
        found = 0
        for path in files:
            text = path.read_text(encoding='utf-8')
            for ref in generated.findall(text):
                found += 1
                self.assertTrue(Path(ref).is_file(), '{} points at {}'.format(path.name, ref))
            self.assertIsNone(unbaked.search(text),
                              path.name + ' points at an unbaked Markdown file')
        self.assertGreater(found, len(self.PARTS))

    def test_a_pointer_at_a_file_the_build_does_not_emit_stays_under_the_install_root(self):
        generated = {('demo', 'heads/a.md'): Path('/out/skills/artel-demo/heads/a.md')}
        body = ('read `${CLAUDE_PLUGIN_ROOT}/skills/demo/heads/a.md`, '
                '`${CLAUDE_PLUGIN_ROOT}/skills/demo/assets/t.html` and '
                '`${CLAUDE_PLUGIN_ROOT}/docs/x.md`, then run /artel:demo')
        self.assertEqual(build_opencode.bake(body, Path('/root'), generated),
                         'read `/out/skills/artel-demo/heads/a.md`, '
                         '`/root/skills/demo/assets/t.html` and `/root/docs/x.md`, '
                         'then run artel-demo')
        # with no map a pointer keeps resolving under the install root, as before
        self.assertEqual(build_opencode.bake('`${CLAUDE_PLUGIN_ROOT}/skills/demo/heads/a.md`',
                                             Path('/root')),
                         '`/root/skills/demo/heads/a.md`')

    def test_agents_land_on_the_generated_skill_too(self):
        agent = (self.out / 'agents' / 'artel-implementer.md').read_text(encoding='utf-8')
        self.assertIn(str((self.out / 'skills' / 'artel-inner-loop' / 'SKILL.md').resolve()),
                      agent)


class TestGlossary(BuildBase):
    def glossary_of(self, skill):
        text = (self.out / 'skills' / skill / 'SKILL.md').read_text(encoding='utf-8')
        return ' '.join(text[text.index(GLOSSARY_MARKER):
                             text.index('</OPENCODE-HOST-NOTES>')].split())

    def test_a_preview_becomes_a_fenced_block_in_the_question(self):
        glossary = self.glossary_of('artel-analysis')
        self.assertIn('`AskUserQuestion` — the `question` tool. It has no `preview` field: when '
                      'an option carries a `preview`, put that sketch into the question text as a '
                      "fenced block, under the option's label.", glossary)

    def test_glossary_names_the_v2_subagent_tool(self):
        glossary = self.glossary_of('artel-analysis')
        self.assertIn('the `subagent` tool with the `artel-<name>` agent', glossary)
        self.assertNotIn('the `task` tool', glossary)

    def test_glossary_maps_plan_mode_and_init(self):
        glossary = self.glossary_of('artel-merge-conflicts')
        self.assertIn('`EnterPlanMode` / "plan mode" — OpenCode has no plan-mode tool', glossary)
        self.assertIn("`/init` — refresh the host project's conventions doc", glossary)

    def test_glossary_names_agents_md_and_v2_tool_names(self):
        glossary = self.glossary_of('artel-implementer')
        self.assertIn('OpenCode reads no `CLAUDE.md`', glossary)
        self.assertIn('`read`, `edit`, `write`, `grep`, `shell`', glossary)


class TestAutoinvoke(BuildBase):
    """`disable-model-invocation: true` becomes v2's `metadata.opencode/autoinvoke: false`,
    so a manual-only skill stays loadable by id but leaves the model's skill list."""

    def head(self, name):
        text = (self.out / 'skills' / ('artel-' + name) / 'SKILL.md').read_text(encoding='utf-8')
        return text.split('---')[1]

    def test_manual_only_skills_hide_from_the_model(self):
        manual = [p.parent.name for p in (ROOT / 'skills').glob('*/SKILL.md')
                  if 'disable-model-invocation: true' in p.read_text(encoding='utf-8')]
        self.assertIn('inner-loop', manual)
        for name in manual:
            with self.subTest(name):
                head = self.head(name)
                self.assertIn('metadata:', head)
                self.assertIn('opencode/autoinvoke: false', head)

    def test_model_invocable_skills_get_no_metadata_block(self):
        self.assertNotIn('opencode/autoinvoke', self.head('feature-development'))


@unittest.skipUnless(shutil.which('bash'), 'the installer is a bash script')
class TestInstaller(unittest.TestCase):
    """scripts/install-opencode.sh against a throwaway HOME: the head and tail files are placed
    with their skill, the manifest covers them, and --remove takes them away and nothing else.
    HOME and XDG_CONFIG_HOME both point into the temporary directory, so the real config is
    never read or written."""

    SKILL = Path('skills') / 'artel-feature-development'
    PARTS = ('heads/full.md', 'heads/lean.md', 'heads/bug.md', 'tail.md')

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        home = Path(cls._tmp.name).resolve()
        oc = home / 'config' / 'opencode'
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'))
        script = str(ROOT / 'scripts' / 'install-opencode.sh')

        cls.install = subprocess.run(['bash', script], capture_output=True, text=True, env=env)
        cls.placed = {rel: (oc / cls.SKILL / rel).is_file() for rel in cls.PARTS}
        manifest = oc / '.artel-install-manifest'
        cls.manifest = (manifest.read_text(encoding='utf-8').split('\n')
                        if manifest.is_file() else [])
        skill = oc / cls.SKILL / 'SKILL.md'
        text = skill.read_text(encoding='utf-8') if skill.is_file() else ''
        dist = oc / 'artel' / 'opencode' / 'dist' / cls.SKILL
        cls.pointers = {rel: ('`' + str(dist / rel) + '`' in text, (dist / rel).is_file())
                        for rel in cls.PARTS}

        # A skill of the user's own that happens to be named artel-…: never artel's to remove.
        own = oc / 'skills' / 'artel-mine'
        own.mkdir(parents=True, exist_ok=True)
        (own / 'SKILL.md').write_text('mine\n', encoding='utf-8')

        cls.remove = subprocess.run(['bash', script, '--remove'], capture_output=True,
                                    text=True, env=env)
        cls.left = [rel for rel in cls.PARTS + ('SKILL.md',) if (oc / cls.SKILL / rel).exists()]
        cls.copy_left = (oc / 'artel').exists()
        cls.own_left = (own / 'SKILL.md').is_file()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_install_and_the_removal_succeed(self):
        self.assertEqual(self.install.returncode, 0, self.install.stderr)
        self.assertEqual(self.remove.returncode, 0, self.remove.stderr)

    def test_the_head_and_tail_files_are_placed_with_their_skill(self):
        self.assertEqual(self.placed, {rel: True for rel in self.PARTS})

    def test_the_installed_skill_points_at_baked_copies_that_exist(self):
        self.assertEqual(self.pointers, {rel: (True, True) for rel in self.PARTS})

    def test_the_manifest_records_the_skill_directory(self):
        self.assertIn('skills/artel-feature-development', self.manifest)
        self.assertIn('artel', self.manifest)

    def test_remove_takes_them_and_leaves_a_skill_of_the_user_s_own(self):
        self.assertEqual(self.left, [])
        self.assertFalse(self.copy_left)
        self.assertTrue(self.own_left)


class TestOperatorDoc(unittest.TestCase):
    def test_the_doc_says_what_is_generated_and_where_pointers_land(self):
        doc = ' '.join((ROOT / 'docs' / 'opencode.md').read_text(encoding='utf-8').split())
        for phrase in ('| `~/.config/opencode/skills/artel-<name>/` | every artel skill, prefixed: '
                       "its `SKILL.md`, and beside it the skill's other Markdown files",
                       'baked the same way but with no frontmatter and no glossary',
                       'resolves to the generated copy under '
                       '`~/.config/opencode/artel/opencode/dist/skills/artel-<name>/`',
                       "The manifest records each generated skill's directory",
                       'the `question` tool has no such field'):
            self.assertIn(phrase, doc)


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
