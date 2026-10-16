"""No Claude dialect survives in the canonical instruction surface.

The migration rewrote `skills/`, `agents/` and `docs/` into OpenCode idiom and renamed the
Claude-named identifiers out of the hook layer (`hooks/`, `scripts/`). This is the net under
it: a dialect token that creeps back into an instruction file fails the suite, naming the
file and the token — fix the file, never the guard's list. Declared port history —
`docs/design.md`, `docs/porting-plan.md`, the two `source-inventory-*.md` files and
`docs/comparisons/` — describes the Claude Code era on purpose and is excluded; so is
`docs/superpowers/`, gitignored controller scratch that is never repository documentation.

Same family as tests/test_build_opencode.py: a defect nobody sees until a session follows a
stale instruction.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every Markdown file an agent reads as instructions.
INSTRUCTION_TREES = ('skills', 'agents')

# The hook layer and the scripts it calls: executable surface, not prose.
CODE_TREES = ('hooks', 'scripts')

# Declared port history: describes the Claude Code era on purpose.
HISTORY = (
    'docs/design.md',
    'docs/porting-plan.md',
    'docs/source-inventory-workflow.md',
    'docs/source-inventory-informational.md',
    'docs/comparisons/',
)

# A bare model alias, not a concrete id: `tokenguard/sonnet-5` must pass.
BARE_ALIAS = re.compile(r'(?<![A-Za-z0-9_/-])(sonnet|opus|fable)(?![A-Za-z0-9_-])')

INSTRUCTION_TOKENS = (
    ('CLAUDE', re.compile(r'CLAUDE')),
    ('Claude Code', re.compile(r'claude code', re.IGNORECASE)),
    ('.claude', re.compile(r'\.claude')),
    ('AskUserQuestion', re.compile(r'AskUserQuestion')),
    ('SendMessage', re.compile(r'SendMessage')),
    ('EnterWorktree', re.compile(r'EnterWorktree')),
    ('ExitWorktree', re.compile(r'ExitWorktree')),
    ('disable-model-invocation', re.compile(r'disable-model-invocation')),
    ('mcp__', re.compile(r'mcp__')),
    ('`Read`', re.compile(r'`Read`')),
    ('`Edit`', re.compile(r'`Edit`')),
    ('`Write`', re.compile(r'`Write`')),
    ('`Grep`', re.compile(r'`Grep`')),
    ('`Bash`', re.compile(r'`Bash`')),
    ('a bare sonnet/opus/fable alias', BARE_ALIAS),
)

# The operator docs carry the executable subset only; bare Claude Code prose may stay.
DOC_TOKENS = (
    ('${CLAUDE_PLUGIN_ROOT}', re.compile(re.escape('${CLAUDE_PLUGIN_ROOT}'))),
    ('.claude/', re.compile(r'\.claude/')),
    ('AskUserQuestion', re.compile(r'AskUserQuestion')),
    ('SendMessage', re.compile(r'SendMessage')),
    ('EnterWorktree', re.compile(r'EnterWorktree')),
    ('disable-model-invocation', re.compile(r'disable-model-invocation')),
)

# The hook ABI's wire shape is deliberately kept (spec §6); only the Claude-named
# identifiers of the hook layer were renamed.
CODE_TOKENS = (
    ('${CLAUDE_PLUGIN_ROOT}', re.compile(re.escape('${CLAUDE_PLUGIN_ROOT}'))),
    ('$CLAUDE_PROJECT_DIR', re.compile(re.escape('$CLAUDE_PROJECT_DIR'))),
    ('claudePayload', re.compile(r'claudePayload')),
    ('claudeToolName', re.compile(r'claudeToolName')),
)


def relative(path):
    return path.relative_to(ROOT).as_posix()


def is_history(rel):
    return any(rel == entry or rel.startswith(entry) for entry in HISTORY)


def scan(text, tokens):
    """[(token, line number, line)] — every dialect token in one document."""
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for token, pattern in tokens:
            if pattern.search(line):
                findings.append((token, number, line.strip()))
    return findings


def markdown(root):
    return sorted(p for p in root.rglob('*.md') if p.is_file())


def instruction_paths():
    paths = []
    for tree in INSTRUCTION_TREES:
        paths.extend(markdown(ROOT / tree))
    return paths


def doc_paths():
    """Every docs Markdown file outside the declared history. `docs/superpowers/` is
    gitignored controller scratch, not repository documentation."""
    paths = []
    for path in markdown(ROOT / 'docs'):
        rel = relative(path)
        if rel.startswith('docs/superpowers/') or is_history(rel):
            continue
        paths.append(path)
    return paths


def code_paths():
    """Every source file under hooks/ and scripts/ (the hook ABI's two homes).
    `__pycache__` is generated, never authored."""
    paths = []
    for tree in CODE_TREES:
        for path in sorted((ROOT / tree).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                paths.append(path)
    return paths


class TestInstructionSurface(unittest.TestCase):
    def test_no_claude_residue_in_the_instruction_surface(self):
        """skills/** and agents/** are what an agent reads as instructions."""
        findings = []
        for path in instruction_paths():
            for token, number, line in scan(path.read_text(encoding='utf-8'),
                                            INSTRUCTION_TOKENS):
                findings.append('{}:{}: {} — {}'.format(relative(path), number, token, line))
        self.assertEqual(findings, [],
                         'Claude dialect in an instruction file:\n' + '\n'.join(findings))

    def test_no_executable_claude_residue_in_the_docs(self):
        findings = []
        for path in doc_paths():
            for token, number, line in scan(path.read_text(encoding='utf-8'), DOC_TOKENS):
                findings.append('{}:{}: {} — {}'.format(relative(path), number, token, line))
        self.assertEqual(findings, [], 'Claude dialect in the docs:\n' + '\n'.join(findings))

    def test_a_concrete_model_id_is_not_a_bare_alias(self):
        """tokenguard/sonnet-5 is a valid models.opencode value and must pass."""
        for concrete in ('"light": "tokenguard/sonnet-5"',
                         '`tokenguard/opus-5.5#xhigh`',
                         '`tokenguard/fable-5`'):
            self.assertIsNone(BARE_ALIAS.search(concrete), concrete)
        for bare in ('runs on `sonnet`', '`opus` (frontmatter)', '`fable` on the second round'):
            self.assertIsNotNone(BARE_ALIAS.search(bare), bare)

    def test_declared_port_history_is_excluded(self):
        """design.md, porting-plan.md, the source-inventory files and comparisons/ describe
        the Claude Code era on purpose; a guard that fails on them gets deleted."""
        # every declared path is skipped by the docs walk
        for entry in HISTORY:
            self.assertFalse(
                any(relative(p) == entry or relative(p).startswith(entry) for p in doc_paths()),
                entry)
        # the exclusion does real work: design.md still carries the tokens
        design = (ROOT / 'docs' / 'design.md').read_text(encoding='utf-8')
        self.assertTrue(scan(design, DOC_TOKENS), 'design.md no longer needs its exclusion')
        # and the guard still fails on a file outside the exclusion list
        probe = ROOT / 'docs' / '__residue_probe__.md'
        probe.write_text('AskUserQuestion\n', encoding='utf-8')
        self.addCleanup(probe.unlink)
        self.assertIn(probe, doc_paths())
        self.assertTrue(scan(probe.read_text(encoding='utf-8'), DOC_TOKENS))

    def test_the_instruction_walk_sees_a_planted_token(self):
        """A walk that silently stops finding files guards nothing: plant a token under
        each instruction tree and watch the guard fail on it."""
        for tree in INSTRUCTION_TREES:
            with self.subTest(tree=tree):
                probe = ROOT / tree / '__residue_probe__.md'
                probe.write_text('AskUserQuestion\n', encoding='utf-8')
                self.addCleanup(probe.unlink)
                self.assertIn(probe, instruction_paths())
                self.assertTrue(scan(probe.read_text(encoding='utf-8'), INSTRUCTION_TOKENS))

    def test_the_docs_superpowers_skip_is_deliberate(self):
        """The sixth exclusion: `docs/superpowers/` is gitignored controller scratch, never
        repository documentation. It is skipped by name, and the gitignore is the reason —
        not a silent hole that could hide real residue."""
        self.assertIn('docs/superpowers/',
                      (ROOT / '.gitignore').read_text(encoding='utf-8'))
        probe = ROOT / 'docs' / 'superpowers' / '__residue_probe__.md'
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text('AskUserQuestion\n', encoding='utf-8')

        def clean():
            probe.unlink()
            if probe.parent.is_dir() and not any(probe.parent.iterdir()):
                probe.parent.rmdir()

        self.addCleanup(clean)
        self.assertNotIn(probe, doc_paths())


class TestHookLayer(unittest.TestCase):
    def test_the_walk_sees_the_real_sources(self):
        """A walk that silently stops finding files guards nothing."""
        rels = [relative(p) for p in code_paths()]
        for rel in ('hooks/hook_common.py', 'hooks/vcs_guard.py',
                    'scripts/verify.py', 'scripts/install-opencode.sh'):
            self.assertIn(rel, rels)

    def test_no_claude_residue_in_the_hook_layer(self):
        """hooks/** and scripts/** are the executable hook surface. The wire shape itself
        is deliberately kept (spec §6); the four tokens are the names the migration
        renamed."""
        findings = []
        for path in code_paths():
            for token, number, line in scan(path.read_text(encoding='utf-8'), CODE_TOKENS):
                findings.append('{}:{}: {} — {}'.format(relative(path), number, token, line))
        self.assertEqual(findings, [], 'Claude dialect in the hook layer:\n' + '\n'.join(findings))


if __name__ == '__main__':
    unittest.main()
