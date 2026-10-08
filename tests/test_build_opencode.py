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

    def test_command_description_shows_the_argument_hint(self):
        # The TUI command list reads the wrapper's description, so the hint travels there.
        text = (self.out / 'commands' / 'artel-researcher.md').read_text(encoding='utf-8')
        head = text.split('---')[1]
        self.assertIn('args: [ticket-id]', head)

    def test_commands_without_a_hint_get_no_args_suffix(self):
        # using-artel and agents-md-generator declare no hint; setup declares "".
        for name in ('using-artel', 'agents-md-generator', 'setup'):
            with self.subTest(name):
                text = (self.out / 'commands' / ('artel-' + name + '.md')).read_text(
                    encoding='utf-8')
                self.assertNotIn('args:', text.split('---')[1])


class TestCommandDescription(unittest.TestCase):
    """The wrapper description carries the skill's argument hint (the TUI shows only the
    description), within the OpenCode limit and never clipped."""

    def test_a_hint_is_appended_verbatim(self):
        command = build_opencode.build_command('artel-demo', 'Demo the thing', '[ticket-id]')
        self.assertIn('description: "Demo the thing · args: [ticket-id]"', command)

    def test_no_hint_keeps_the_description_alone(self):
        command = build_opencode.build_command('artel-demo', 'Demo the thing', '')
        head = command.split('---')[1]
        self.assertIn('description: "Demo the thing"', head)
        self.assertNotIn('args:', head)

    def test_the_hint_survives_a_long_description(self):
        hint = '[ticket-id] or [ticket-id]-[phase]'
        command = build_opencode.build_command('artel-demo', 'x' * 2000, hint)
        line = next(l for l in command.splitlines() if l.startswith('description: '))
        display = line[len('description: "'):-1]
        self.assertLessEqual(len(display), MAX_DESCRIPTION)
        self.assertTrue(display.endswith(' · args: ' + hint), display[-60:])

    def test_a_hint_with_quotes_is_yaml_escaped(self):
        # tasks' hint carries "<title>"; the description must stay valid YAML.
        command = build_opencode.build_command('artel-demo', 'Demo', '"<title>"')
        self.assertIn(r'args: \"<title>\"', command)


def source_agents():
    """Every agent this build emits, by file stem — README.md is not an agent and
    build_opencode.AGENTS_EXCLUDE (the seat) is not emitted."""
    return sorted(p.stem for p in (ROOT / 'agents').glob('*.md')
                  if p.name != 'README.md' and p.stem not in build_opencode.AGENTS_EXCLUDE)


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

    def test_the_seat_is_not_generated(self):
        self.assertFalse((self.out / 'agents' / 'artel-seat.md').is_file())

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


class TestModelTransforms(BuildBase):
    """The build translates the canonical model passages and the glossary; the canonical
    Claude Code text itself stays untouched (pinned by test_model_selection_docs)."""

    def text_of(self, *parts):
        return self.out.joinpath(*parts).read_text(encoding='utf-8')

    def flat(self, *parts):
        return ' '.join(self.text_of(*parts).split())

    def test_the_canonical_sources_keep_the_claude_dialect(self):
        for rel, phrase in (('skills/implementer/SKILL.md', '`--model <sonnet|opus|fable>`'),
                            ('skills/run-reviewer/SKILL.md', '`--model <sonnet|opus|fable>`'),
                            ('skills/feature-development/tail.md', '--model fable'),
                            ('skills/deep-review/SKILL.md', '- `model`: `"fable"`')):
            self.assertIn(phrase, (ROOT / rel).read_text(encoding='utf-8'), rel)

    def test_the_glossary_states_the_model_rule(self):
        glossary = self.flat('skills', 'artel-implementer', 'SKILL.md')
        self.assertIn('aliases (`sonnet`, `opus`, `fable`) do not resolve here', glossary)
        self.assertIn("an omitted model inherits your session's model", glossary)
        self.assertIn('scripts/models.py resolve --site <key>', glossary)
        self.assertIn('reviewForecaster', glossary)

    def test_the_glossary_bakes_the_resolver_path(self):
        self.assertIn(str(self.root.resolve()) + '/scripts/models.py resolve --site <key>',
                      self.flat('skills', 'artel-implementer', 'SKILL.md'))

    def test_the_implementer_resolves_sites(self):
        text = self.flat('skills', 'artel-implementer', 'SKILL.md')
        root = str(self.root.resolve())
        self.assertIn(root + '/scripts/models.py resolve --site implementer.fix', text)
        self.assertIn(root + '/scripts/models.py resolve --site implementer.<route_effective>',
                      text)
        for gone in ('--model <sonnet|opus|fable>', 'frontmatter `opus`', 'Model: sonnet',
                     'Model: fable', 'Model: opus'):
            self.assertNotIn(gone, text)

    def test_the_reviewer_resolves_sites(self):
        text = self.flat('skills', 'artel-run-reviewer', 'SKILL.md')
        root = str(self.root.resolve())
        self.assertIn(root + '/scripts/models.py resolve --site', text)
        for site in ('reviewer.task', 'reviewer.phase', 'reviewer.plan'):
            self.assertIn(site, text)
        self.assertNotIn('--model <sonnet|opus|fable>', text)
        self.assertNotIn('frontmatter `opus`', text)

    def test_the_tail_resolves_sites(self):
        text = self.flat('skills', 'artel-feature-development', 'tail.md')
        root = str(self.root.resolve())
        for site in ('reviewer.task', 'reviewer.reReview', 'implementer.stepUp'):
            self.assertIn(root + '/scripts/models.py resolve --site ' + site, text)
        self.assertNotRegex(text, r'\b(sonnet|opus|fable)\b')

    def test_deep_review_resolves_its_site(self):
        text = self.flat('skills', 'artel-deep-review', 'SKILL.md')
        self.assertIn(str(self.root.resolve()) + '/scripts/models.py resolve --site '
                      'reviewer.deepReview', text)
        self.assertNotIn('- `model`: `"fable"`', text)

    def test_a_resolver_error_is_not_an_inherit(self):
        text = self.flat('skills', 'artel-implementer', 'SKILL.md')
        self.assertIn('an error (exit `2`, e.g. `invalid_config`) is reported and stops the run',
                      text)
        self.assertIn('a resolver exit `2` is reported and stops the run', text)
        self.assertNotIn('a `null` route, a `null` model or exit `2` → no model', text)

    def test_an_undecodable_transform_source_is_reported(self):
        source_dir = tempfile.TemporaryDirectory()
        self.addCleanup(source_dir.cleanup)
        source = Path(source_dir.name)
        stub = source / 'skills' / 'implementer'
        stub.mkdir(parents=True)
        (stub / 'SKILL.md').write_bytes(
            b'---\nname: implementer\ndescription: "stub"\n---\n\xff\n')
        out_dir = tempfile.TemporaryDirectory()
        self.addCleanup(out_dir.cleanup)
        proc = subprocess.run(
            [sys.executable, str(BUILD), '--root', out_dir.name, '--source', str(source),
             '--out', out_dir.name],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('transform source absent', proc.stderr)
        self.assertIn('implementer/SKILL.md', proc.stderr)
        self.assertNotIn('Traceback', proc.stderr)

    def test_missing_transform_source_stops_the_build(self):
        source_dir = tempfile.TemporaryDirectory()
        self.addCleanup(source_dir.cleanup)
        source = source_dir.name
        stub = Path(source) / 'skills' / 'implementer'
        stub.mkdir(parents=True)
        (stub / 'SKILL.md').write_text(
            '---\nname: implementer\ndescription: "stub"\n---\n\nStub body.\n',
            encoding='utf-8')
        out_dir = tempfile.TemporaryDirectory()
        self.addCleanup(out_dir.cleanup)
        out = out_dir.name
        proc = subprocess.run(
            [sys.executable, str(BUILD), '--root', out, '--source', source, '--out', out],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('transform source absent', proc.stderr)
        self.assertIn('implementer/SKILL.md', proc.stderr)


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

    def test_a_mermaid_sketch_keeps_its_fence_in_the_question(self):
        glossary = self.glossary_of('artel-analysis')
        self.assertIn('A sketch the agent wrote as a ```mermaid block keeps that fence — the '
                      'client renders the diagram; a `preview` sketch stays a monospace block.',
                      glossary)

    def test_a_shape_sketch_is_a_mermaid_block_on_the_agent_side(self):
        text = (self.out / 'agents' / 'artel-analyst.md').read_text(encoding='utf-8')
        flat = ' '.join(text.split())
        self.assertIn('A `preview` sketch for a flow, a structure or a chart is written as a '
                      '```mermaid block (fence `mermaid`, not `preview`)', flat)
        self.assertIn('A screen-layout sketch stays plain ASCII: Mermaid cannot draw a wireframe.',
                      flat)

    def test_glossary_names_the_v2_subagent_tool(self):
        glossary = self.glossary_of('artel-analysis')
        self.assertIn('the `subagent` tool with the `artel-<name>` agent', glossary)
        self.assertNotIn('the `task` tool', glossary)

    def test_glossary_maps_plan_mode(self):
        glossary = self.glossary_of('artel-merge-conflicts')
        self.assertIn('`EnterPlanMode` / "plan mode" — OpenCode has no plan-mode tool', glossary)
        self.assertNotIn('`/init`', glossary)

    def test_glossary_drops_the_retired_conventions_entries_and_names_v2_tools(self):
        glossary = self.glossary_of('artel-implementer')
        self.assertNotIn('`CLAUDE.md`', glossary)
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


@unittest.skipUnless(shutil.which('bash'), 'the installer is a bash script')
class TestInstallerVersionProbe(unittest.TestCase):
    """The bridge is v2-only: installing on a v1 CLI must warn (and only warn)."""

    def run_installer(self, version):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            bindir = home / 'bin'
            bindir.mkdir()
            fake = bindir / 'opencode'
            fake.write_text('#!/bin/sh\necho "{}"\n'.format(version), encoding='utf-8')
            fake.chmod(0o755)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                       PATH=str(bindir) + os.pathsep + os.environ.get('PATH', ''))
            return subprocess.run(['bash', str(ROOT / 'scripts' / 'install-opencode.sh')],
                                  capture_output=True, text=True, env=env)

    def test_warns_on_a_v1_cli(self):
        proc = self.run_installer('opencode v1.18.34')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('OpenCode 2.x', proc.stderr)

    def test_silent_on_a_v2_cli(self):
        proc = self.run_installer('opencode v2.0.19')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn('OpenCode 2.x', proc.stderr)

    def test_silent_on_a_bare_v2_version(self):
        proc = self.run_installer('2.0.19')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn('OpenCode 2.x', proc.stderr)

    def test_warns_when_a_v1_version_mentions_v2(self):
        proc = self.run_installer('opencode v1.18.34 (bridge v2.0)')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('OpenCode 2.x', proc.stderr)

    def test_warns_on_a_v0_cli(self):
        proc = self.run_installer('opencode v0.9.1')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('OpenCode 2.x', proc.stderr)

    def test_warns_on_an_unparseable_version(self):
        proc = self.run_installer('opencode (development build)')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('OpenCode 2.x', proc.stderr)


class TestOperatorDoc(unittest.TestCase):
    def test_the_doc_describes_the_v2_host(self):
        doc = ' '.join((ROOT / 'docs' / 'opencode.md').read_text(encoding='utf-8').split())
        for phrase in ('OpenCode 2.x',
                       'the bridge plugin requires OpenCode 2.x',
                       '`session.execution.succeeded`',
                       '`metadata.opencode/autoinvoke: false`',
                       '`mcp.servers`',
                       'the TUI command list shows what arguments the skill expects',
                       'a flow, structure or chart sketch is a `mermaid` block'):
            self.assertIn(phrase, doc, phrase)


if __name__ == '__main__':
    unittest.main()
