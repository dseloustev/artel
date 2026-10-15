"""The OpenCode build generator: the install copy's Markdown baked in place, every
canonical skill emitted with baked paths and OpenCode-legal frontmatter, plus a
command wrapper. Guards the zero-regression promise by asserting what the
generated files contain (and omit).

Docs-style guard, same family as tests/test_using_artel_docs.py: a generated or
installed file that still says `${ARTEL_ROOT}`, or uses the old
`/artel:` namespace, breaks in a
session nobody is watching.
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
        # The installer's own flow: the canonical trees copied under the install root,
        # then the generator bakes that copy in place and writes <out>.
        for tree in ('skills', 'agents', 'docs'):
            shutil.copytree(ROOT / tree, cls.root / tree)
        cls.build()

    @classmethod
    def build(cls):
        proc = subprocess.run(
            [sys.executable, str(BUILD), '--root', str(cls.root), '--out', str(cls.out)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError('build failed: ' + proc.stderr)
        cls.proc = proc

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestInstallCopyBake(BuildBase):
    """The install root's Markdown is baked in place: an agent on OpenCode reads a real
    path, never an unset variable. Python files are never rewritten."""

    def test_docs_are_baked(self):
        """docs/ was copied verbatim, so an agent on OpenCode read an unset variable."""
        self.build()
        text = (self.root / 'docs' / 'spec-storage.md').read_text(encoding='utf-8')
        self.assertNotIn('${ARTEL_ROOT}', text)
        self.assertIn(str(self.root.resolve()), text)

    def test_the_sentence_describing_the_token_survives(self):
        """Baking must not rewrite the paragraph that explains the mechanism."""
        self.build()
        text = (self.root / 'docs' / 'opencode.md').read_text(encoding='utf-8')
        # the doc names the token WITHOUT braces, so the build cannot rewrite its own explanation;
        # Task 5 renames this to $ARTEL_ROOT along with the token itself
        self.assertIn('$ARTEL_ROOT', text)

    def test_only_markdown_is_baked(self):
        """A hook docstring's token is a source-level pointer to a contract file; nothing
        expands it, so hooks/ and scripts/ are never rewritten — and a non-Markdown file
        inside one of the walked trees (skills/, agents/, docs/) is left alone too."""
        hook = self.root / 'hooks' / 'demo.py'
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text('Contract: ${ARTEL_ROOT}/docs/x.md\n', encoding='utf-8')
        note = self.root / 'skills' / 'artel-demo' / 'notes.txt'
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text('Contract: ${ARTEL_ROOT}/docs/x.md\n', encoding='utf-8')
        self.build()
        for path in (hook, note):
            self.assertEqual(path.read_text(encoding='utf-8'),
                             'Contract: ${ARTEL_ROOT}/docs/x.md\n', str(path))


class TestSourceCheckoutGuard(unittest.TestCase):
    """The builder refuses to bake the source checkout — that would replace the canonical
    placeholders throughout the tree — while the installer's run from inside an install copy
    (no tests/ tree, no tracked builder) stays allowed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def stub(self, name, tests=False, tracked=False):
        """A buildable root: the three trees and a copy of the builder. `tests/` plus a
        git-tracked builder make it a source checkout."""
        root = self.tmp / name
        (root / 'scripts').mkdir(parents=True)
        for tree in ('skills', 'agents', 'docs'):
            (root / tree).mkdir()
        demo = root / 'skills' / 'artel-demo'
        demo.mkdir()
        (demo / 'SKILL.md').write_text(
            '---\nname: artel-demo\ndescription: "Demo."\n---\n\n${ARTEL_ROOT}/docs/x.md\n',
            encoding='utf-8')
        shutil.copy(BUILD, root / 'scripts' / 'build_opencode.py')
        if tests:
            (root / 'tests').mkdir()
        if tracked:
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'add', 'scripts/build_opencode.py'], cwd=root, check=True)
            subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                            'commit', '-qm', 'init'], cwd=root, check=True)
        return root

    def run_build(self, root):
        return subprocess.run(
            [sys.executable, str(root / 'scripts' / 'build_opencode.py'),
             '--root', str(root)], capture_output=True, text=True)

    @unittest.skipUnless(shutil.which('git'), 'the guard asks git what is tracked')
    def test_the_source_checkout_is_detected(self):
        self.assertTrue(build_opencode.is_source_checkout(ROOT))

    def test_an_install_copy_is_not(self):
        # no tests/ tree: the installer's own layout — allowed, or every install breaks
        self.assertFalse(build_opencode.is_source_checkout(self.stub('install')))

    @unittest.skipUnless(shutil.which('git'), 'the guard asks git what is tracked')
    def test_the_builder_refuses_the_source_checkout_before_any_write(self):
        root = self.stub('source', tests=True, tracked=True)
        marker = root / 'skills' / 'artel-demo' / 'SKILL.md'
        before = marker.read_text(encoding='utf-8')
        proc = self.run_build(root)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn('source checkout', proc.stderr)
        self.assertEqual(marker.read_text(encoding='utf-8'), before,
                         'the source checkout was written before the refusal')
        self.assertFalse((root / 'opencode').exists())

    def test_the_builder_bakes_an_install_copy(self):
        root = self.stub('copy')
        proc = self.run_build(root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(
            '${ARTEL_ROOT}',
            (root / 'skills' / 'artel-demo' / 'SKILL.md').read_text(encoding='utf-8'))


class TestSkillBuild(BuildBase):
    """The install copy is the artifact: each canonical skill's SKILL.md is baked in place
    under <root>/skills/ and keeps its OpenCode frontmatter."""

    def test_build_succeeds_and_reports_the_count(self):
        self.assertEqual(self.proc.returncode, 0)
        self.assertIn('{} command wrappers'.format(len(source_skills())), self.proc.stdout)

    def test_every_skill_is_baked_in_the_install_copy(self):
        for name in source_skills():
            path = self.root / 'skills' / name / 'SKILL.md'
            self.assertTrue(path.is_file(), 'missing ' + str(path))

    def test_installed_names_are_opencode_legal(self):
        for path in sorted((self.root / 'skills').glob('*/SKILL.md')):
            self.assertRegex(path.parent.name, VALID_NAME)

    def test_frontmatter_keeps_description_and_drops_claude_fields(self):
        for path in sorted((self.root / 'skills').glob('*/SKILL.md')):
            head = path.read_text(encoding='utf-8').split('---')[1]
            self.assertIn('name: ' + path.parent.name, head)
            self.assertIn('description: "', head, path.parent.name + ' lost its description')
            for field in ('model:', 'disable-model-invocation:'):
                self.assertNotIn(field, head,
                                 path.parent.name + ' keeps Claude-only ' + field)

    def test_description_within_opencode_limit(self):
        for path in sorted((self.root / 'skills').glob('*/SKILL.md')):
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.startswith('description: '):
                    self.assertLessEqual(len(line), len('description: ') + MAX_DESCRIPTION,
                                         path.parent.name + ' description too long')
                    break

    def test_plugin_root_is_baked(self):
        for path in sorted((self.root / 'skills').glob('*/SKILL.md')):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('${ARTEL_ROOT}', text,
                             path.parent.name + ' still names the install token')

    def test_the_canonical_sources_are_not_baked(self):
        """Baking happens in the install copy; the checkout keeps its token."""
        canonical = (ROOT / 'skills' / 'artel-analysis' / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('${ARTEL_ROOT}', canonical)

    def test_no_artel_namespace_survives_but_ast_index_refs_do(self):
        """The old `/artel:` namespace is gone from the sources, so no baked skill may
        carry one — this is the residue check; the unrelated `/ast-index:` namespace stays."""
        for path in sorted((self.root / 'skills').glob('*/SKILL.md')):
            with self.subTest(name=path.parent.name):
                self.assertNotRegex(path.read_text(encoding='utf-8'), r'/artel:[a-z0-9-]')
        router = (self.root / 'skills' / 'artel-using-artel' / 'SKILL.md').read_text(
            encoding='utf-8')
        self.assertIn('/ast-index:ast-index', router)
        self.assertIn('artel-feature-development', router)


class TestDistCommandsOnly(BuildBase):
    """The spec: dist holds command wrappers only. Skills and agents are installed from the
    baked canonical trees, so a `dist/skills` or `dist/agents` tree is the old snapshot build
    creeping back."""

    def test_dist_holds_commands_and_nothing_else(self):
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['commands'],
                         'dist holds more than the commands tree')
        for path in sorted(self.out.rglob('*')):
            self.assertEqual(path.relative_to(self.out).parts[0], 'commands', str(path))

    def test_no_skill_or_agent_snapshots(self):
        self.assertFalse((self.out / 'skills').exists())
        self.assertFalse((self.out / 'agents').exists())


class TestCommandBuild(BuildBase):
    def test_a_command_wrapper_per_skill(self):
        for name in source_skills():
            path = self.out / 'commands' / (name + '.md')
            self.assertTrue(path.is_file(), 'missing ' + str(path))

    def test_wrapper_passes_arguments_and_stays_in_the_main_session(self):
        for name in source_skills():
            text = (self.out / 'commands' / (name + '.md')).read_text(
                encoding='utf-8')
            self.assertIn('agent: build', text)
            self.assertNotIn('subtask: true', text)
            self.assertIn('$ARGUMENTS', text)
            self.assertIn('`' + name + '`', text)

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
        for name in ('artel-using-artel', 'artel-agents-md-generator', 'artel-setup'):
            with self.subTest(name):
                text = (self.out / 'commands' / (name + '.md')).read_text(
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
    """Every agent this build emits, by file stem — README.md is not an agent."""
    return sorted(p.stem for p in (ROOT / 'agents').glob('*.md')
                  if p.name != 'README.md')


class TestAgentBuild(BuildBase):
    """agents/ is baked in place too: each agent file is the installed artifact."""

    def test_every_agent_is_baked_in_the_install_copy(self):
        for name in source_agents():
            path = self.root / 'agents' / (name + '.md')
            self.assertTrue(path.is_file(), 'missing ' + str(path))

    def test_agent_names_are_opencode_legal(self):
        for path in sorted((self.root / 'agents').glob('artel-*.md')):
            self.assertRegex(path.stem, VALID_NAME)

    def test_readme_is_not_an_agent(self):
        self.assertFalse((self.root / 'agents' / 'artel-README.md').is_file())

    def test_frontmatter_is_opencode_shape(self):
        for path in sorted((self.root / 'agents').glob('artel-*.md')):
            head = path.read_text(encoding='utf-8').split('---')[1]
            self.assertIn('description: "', head)
            self.assertIn('mode: subagent', head)
            self.assertNotIn('model:', head, path.name + ' keeps a Claude model tier')
            self.assertNotRegex(head, r'(?m)^name:', path.name + ' keeps a frontmatter name')

    def test_agent_carries_a_baked_root(self):
        for path in sorted((self.root / 'agents').glob('artel-*.md')):
            self.assertNotIn('${ARTEL_ROOT}', path.read_text(encoding='utf-8'),
                             path.name + ' still names the install token')

    def test_build_reports_the_baked_counts(self):
        self.assertIn('{} agents baked'.format(len(source_agents())), self.proc.stdout)


class TestDeterminism(BuildBase):
    def test_rebuild_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as second:
            out2 = Path(second) / 'dist'
            proc = subprocess.run(
                [sys.executable, str(BUILD), '--root', str(self.root), '--out', str(out2)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Pointers bake to the install root, which both builds share, so the two
            # command trees must agree byte for byte — and the second bake must leave the
            # already-baked install copy alone.
            first = sorted((self.out / 'commands').rglob('*.md'))
            twin = sorted((out2 / 'commands').rglob('*.md'))
            self.assertEqual([p.relative_to(self.out) for p in first],
                             [p.relative_to(out2) for p in twin])
            for path in first:
                rel = path.relative_to(self.out)
                self.assertEqual(path.read_text(encoding='utf-8'),
                                 (out2 / rel).read_text(encoding='utf-8'),
                                 str(rel) + ' differs between builds')
            self.assertFalse((out2 / 'skills').exists())
            self.assertFalse((out2 / 'agents').exists())


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
    references — are baked with their skill in the install copy, and a pointer into a skill's
    directory lands on the installed copy."""

    SKILL = 'artel-feature-development'
    PARTS = ('heads/full.md', 'heads/lean.md', 'heads/bug.md', 'tail.md')

    def part(self, rel):
        return (self.root / 'skills' / self.SKILL / rel).read_text(encoding='utf-8')

    def test_every_other_markdown_file_is_baked_with_its_skill(self):
        pairs = source_companions()
        for rel in self.PARTS:
            self.assertIn(('artel-feature-development', rel), pairs)
        for skill, rel in pairs:
            path = self.root / 'skills' / skill / rel
            self.assertTrue(path.is_file(), 'missing ' + str(path))
            self.assertNotIn('${ARTEL_ROOT}', path.read_text(encoding='utf-8'),
                             str(path) + ' is not baked')

    def test_the_head_and_tail_files_are_baked(self):
        docs = str(self.root.resolve()) + '/docs/'
        for rel in self.PARTS:
            with self.subTest(rel):
                text = self.part(rel)
                self.assertNotIn('${ARTEL_ROOT}', text)
                self.assertNotRegex(text, r'/artel:[a-z0-9-]')
                self.assertIn(docs, text)
        self.assertIn('Next: /artel-feature-development <TICKET_ID> --head=full',
                      self.part('heads/lean.md'))

    def test_they_get_no_frontmatter(self):
        for rel in self.PARTS:
            with self.subTest(rel):
                self.assertTrue(self.part(rel).startswith('# The '))

    def test_a_file_with_no_dialect_in_it_is_baked_unchanged(self):
        rel = Path('assets') / 'templates' / 'idea.template.md'
        self.assertEqual(
            (ROOT / 'skills' / 'artel-generate-idea' / rel).read_text(encoding='utf-8'),
            (self.root / 'skills' / 'artel-generate-idea' / rel).read_text(encoding='utf-8'))

    def test_the_skill_points_at_the_install_copies(self):
        skill = self.part('SKILL.md')
        here = str((self.root / 'skills' / self.SKILL).resolve()) + '/'
        for rel in self.PARTS:
            self.assertIn('`' + here + rel + '`', skill, rel)
        self.assertNotIn(str(self.out.resolve()) + '/skills/', skill)
        # the head files point at each other and at the tail the same way
        self.assertIn('`' + here + 'heads/full.md`', self.part('heads/lean.md'))
        self.assertIn('`' + here + 'heads/lean.md`', self.part('heads/bug.md'))
        self.assertIn('`' + here + 'SKILL.md`', self.part('tail.md'))

    def test_every_pointer_into_a_skill_directory_resolves(self):
        install_copy = re.compile(re.escape(str((self.root / 'skills').resolve())) + r'/[\w./-]+')
        files = (sorted((self.root / 'skills').rglob('*.md'))
                 + sorted((self.root / 'agents').glob('*.md')))
        found = 0
        for path in files:
            text = path.read_text(encoding='utf-8')
            for ref in install_copy.findall(text):
                found += 1
                target = Path(ref)
                self.assertTrue(target.exists(), '{} points at {}'.format(path.name, ref))
                if target.is_file():
                    self.assertNotIn('${ARTEL_ROOT}', target.read_text(encoding='utf-8'),
                                     ref + ' is not baked')
        self.assertGreater(found, len(self.PARTS))

    def test_a_pointer_into_a_skill_directory_stays_under_the_install_root(self):
        body = ('read `${ARTEL_ROOT}/skills/artel-demo/heads/a.md`, '
                '`${ARTEL_ROOT}/skills/artel-demo/assets/t.html` and '
                '`${ARTEL_ROOT}/docs/x.md`')
        self.assertEqual(build_opencode.bake(body, Path('/root')),
                         'read `/root/skills/artel-demo/heads/a.md`, '
                         '`/root/skills/artel-demo/assets/t.html` and `/root/docs/x.md`')

    def test_agents_land_on_the_install_copy_too(self):
        agent = (self.root / 'agents' / 'artel-implementer.md').read_text(encoding='utf-8')
        self.assertIn(str((self.root / 'skills' / 'artel-inner-loop' / 'SKILL.md').resolve()),
                      agent)


class TestCollaborativeReviewBuild(BuildBase):
    """The interactive PR review ships as a skill plus its dedicated worker, baked with the
    rest of the install copy."""

    SKILL = 'artel-collaborative-review'
    AGENT = 'artel-collaborative-reviewer'

    def test_collaborative_review_skill_and_agent_are_baked(self):
        skill = self.root / 'skills' / self.SKILL / 'SKILL.md'
        agent = self.root / 'agents' / (self.AGENT + '.md')
        self.assertTrue(skill.is_file(), 'missing ' + str(skill))
        self.assertTrue(agent.is_file(), 'missing ' + str(agent))
        head = skill.read_text(encoding='utf-8').split('---')[1]
        self.assertIn('description: "', head)
        agent_head = agent.read_text(encoding='utf-8').split('---')[1]
        self.assertIn('mode: subagent', agent_head)
        self.assertNotIn('model:', agent_head,
                         self.AGENT + ' keeps a Claude model tier')

class TestAutoinvoke(BuildBase):
    """A manual-only skill's `metadata.opencode/autoinvoke: false` rides through the bake,
    so it stays loadable by id but leaves the model's skill list."""

    def head(self, name):
        text = (self.root / 'skills' / name / 'SKILL.md').read_text(encoding='utf-8')
        return text.split('---')[1]

    def test_manual_only_skills_hide_from_the_model(self):
        manual = [p.parent.name for p in (ROOT / 'skills').glob('*/SKILL.md')
                  if 'opencode/autoinvoke: false' in p.read_text(encoding='utf-8')]
        self.assertIn('artel-inner-loop', manual)
        for name in manual:
            with self.subTest(name):
                head = self.head(name)
                self.assertIn('metadata:', head)
                self.assertIn('opencode/autoinvoke: false', head)

    def test_model_invocable_skills_get_no_metadata_block(self):
        self.assertNotIn('opencode/autoinvoke', self.head('artel-feature-development'))


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
        # The installed skill points into the install copy, which the build baked in place.
        install_copy = oc / 'artel' / cls.SKILL
        cls.pointers = {}
        for rel in cls.PARTS:
            target = install_copy / rel
            cls.pointers[rel] = ('`' + str(target) + '`' in text,
                                 target.is_file(),
                                 target.is_file()
                                 and '${ARTEL_ROOT}' not in
                                 target.read_text(encoding='utf-8'))

        # What is installed is the baked copy itself, byte for byte — not a regenerated
        # snapshot — and dist held command wrappers only.
        cls.fidelity = {}
        for rel in cls.PARTS + ('SKILL.md',):
            installed = oc / cls.SKILL / rel
            baked = install_copy / rel
            cls.fidelity[rel] = (installed.is_file() and baked.is_file()
                                 and installed.read_bytes() == baked.read_bytes())
        cls.agent_fidelity = {}
        for name in ('artel-analyst.md', 'artel-implementer.md'):
            installed = oc / 'agents' / name
            baked = oc / 'artel' / 'agents' / name
            cls.agent_fidelity[name] = (installed.is_file() and baked.is_file()
                                        and installed.read_bytes() == baked.read_bytes())
        cls.commands = sorted(p.name for p in (oc / 'commands').glob('artel-*.md'))
        cls.dist_entries = sorted(p.name for p in (oc / 'artel' / 'opencode' / 'dist').iterdir())

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
        # (the pointer is in the installed skill, its target exists, the target is baked)
        self.assertEqual(self.pointers, {rel: (True, True, True) for rel in self.PARTS})

    def test_the_installed_files_are_the_baked_copies(self):
        self.assertEqual(self.fidelity, {rel: True for rel in self.PARTS + ('SKILL.md',)})
        self.assertEqual(self.agent_fidelity,
                         {name: True for name in ('artel-analyst.md', 'artel-implementer.md')})

    def test_dist_holds_command_wrappers_only(self):
        self.assertEqual(self.dist_entries, ['commands'])
        self.assertIn('artel-analysis.md', self.commands)
        self.assertIn('artel-feature-development.md', self.commands)

    def test_the_manifest_records_the_skill_directory(self):
        self.assertIn('skills/artel-feature-development', self.manifest)
        self.assertIn('agents/artel-analyst.md', self.manifest)
        self.assertIn('artel', self.manifest)

    def test_remove_takes_them_and_leaves_a_skill_of_the_user_s_own(self):
        self.assertEqual(self.left, [])
        self.assertFalse(self.copy_left)
        self.assertTrue(self.own_left)


@unittest.skipUnless(shutil.which('bash'), 'the installer is a bash script')
class TestInstallerRefresh(unittest.TestCase):
    """A refresh install over an existing one: installed names do not change here, so the
    manifest still has to prune a skill retired upstream — and a name that already carries
    the prefix must never be prefixed again. The checkout is a stub carrying exactly the
    skills the case names, so the generator is the real one and no other skill interferes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()
        self.env = dict(os.environ, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / 'config'))
        self.skills_dir = self.home / 'config' / 'opencode' / 'skills'

    def stub_checkout(self, skills):
        """A checkout that looks like artel's to the installer, holding exactly `skills`."""
        src = self.home / 'checkout'
        if src.exists():
            shutil.rmtree(src)
        src.mkdir(parents=True)
        (src / 'VERSION').write_text('0.0.0\n', encoding='utf-8')
        (src / 'opencode' / 'plugin').mkdir(parents=True)
        (src / 'opencode' / 'plugin' / 'artel.ts').write_text('// stub\n', encoding='utf-8')
        for tree in ('hooks', 'docs', 'agents'):
            (src / tree).mkdir()
        (src / 'scripts').mkdir()
        shutil.copy(BUILD, src / 'scripts' / 'build_opencode.py')
        shutil.copy(ROOT / 'scripts' / 'install-opencode.sh', src / 'scripts')
        for name in sorted(skills):
            folder = src / 'skills' / name
            folder.mkdir(parents=True)
            (folder / 'SKILL.md').write_text(
                '---\nname: {name}\ndescription: "Stub skill {name}."\n---\n\nStub body.\n'
                .format(name=name), encoding='utf-8')
        return src

    def install(self, skills):
        src = self.stub_checkout(skills)
        proc = subprocess.run(['bash', str(src / 'scripts' / 'install-opencode.sh')],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_refresh_install_prunes_a_retired_skill(self):
        """Installed names do not change here, so the manifest still has to prune."""
        self.install(skills={'artel-alpha', 'artel-beta'})
        self.install(skills={'artel-alpha'})            # beta retired upstream
        self.assertFalse((self.skills_dir / 'artel-beta').exists())
        self.assertTrue((self.skills_dir / 'artel-alpha').exists())
        self.assertFalse((self.skills_dir / 'artel-artel-alpha').exists())


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
                       'a flow, structure or chart sketch is a `mermaid` block',
                       "never baselines or stop-gates another checkout's sessions"):
            self.assertIn(phrase, doc, phrase)


if __name__ == '__main__':
    unittest.main()
