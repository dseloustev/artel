"""The context-store skills keep AGENTS.md in the working tree.

`AGENTS.md` is the conventions doc OpenCode and Claude Code 2.1.277+ read — not a
declutter target. `save-context` mirrors it into `.artel/context/` but never removes it;
`restore-context` fills the gap only, never overwriting a live working-tree copy with a
store copy.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / 'skills'


def body(name):
    return (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')


class SaveContextCase(unittest.TestCase):
    def setUp(self):
        self.text = body('save-context')

    def test_agents_md_is_never_removed(self):
        # Assert the prohibition is stated, not merely that the string is absent.
        self.assertIn('is mirrored, never removed', self.text)

    def test_the_description_says_agents_md_stays(self):
        self.assertIn('AGENTS.md is mirrored but stays in the working tree', self.text)

    def test_the_clear_loop_names_only_the_changelog(self):
        self.assertIn('for f in CHANGELOG.md; do', self.text)
        self.assertNotIn('for f in AGENTS.md CHANGELOG.md; do', self.text)

    def test_agents_md_is_still_mirrored_into_the_store(self):
        self.assertIn('cp AGENTS.md "$STORE_ROOT/root/AGENTS.md"', self.text)
        self.assertIn('cp AGENTS.md "$STORE_ROOT/tickets/${ACTIVE}/root/AGENTS.md"', self.text)

    def test_the_verify_step_asserts_agents_md_survived(self):
        self.assertIn('AGENTS.md kept', self.text)


class RestoreContextCase(unittest.TestCase):
    def setUp(self):
        self.text = body('restore-context')

    def test_agents_md_is_restored_only_when_missing(self):
        self.assertIn('[ ! -f AGENTS.md ]', self.text)
        self.assertIn('never overwritten', self.text)

    def test_the_common_loop_names_only_the_changelog(self):
        self.assertIn('for f in CHANGELOG.md; do', self.text)
        self.assertNotIn('for f in AGENTS.md CHANGELOG.md; do', self.text)


class NoSkillRemovesAgentsMd(unittest.TestCase):
    """Defense in depth: no shipped skill body removes AGENTS.md, directly or in prose."""

    REMOVER = re.compile(r'\brm\b[^\n]*AGENTS\.md|AGENTS\.md[^\n]*\brm\b')

    def test_no_skill_body_removes_agents_md(self):
        for path in sorted(SKILLS.glob('*/SKILL.md')):
            text = path.read_text(encoding='utf-8')
            for n, line in enumerate(text.splitlines(), 1):
                with self.subTest('{}:{}'.format(path.parent.name, n)):
                    self.assertIsNone(self.REMOVER.search(line), line)


if __name__ == '__main__':
    unittest.main()
