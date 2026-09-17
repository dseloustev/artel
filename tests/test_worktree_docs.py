"""The worktree skills and init-branch carry the contract docs/worktrees.md fixes.

Contract: docs/worktrees.md; design: docs/superpowers/specs/2026-09-17-worktree-isolation-design.md
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / 'skills'
NEW_SKILLS = ('move-to-worktree', 'return-from-worktree')


def body(name):
    return (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class WorktreeSkillsCase(unittest.TestCase):
    def test_frontmatter_names_each_skill(self):
        for name in NEW_SKILLS:
            self.assertTrue(body(name).startswith('---\nname: {}\n'.format(name)), name)

    def test_both_are_user_invoked_only(self):
        # They move the session and change two checkouts: never auto-triggered.
        for name in NEW_SKILLS:
            self.assertIn('disable-model-invocation: true', body(name), name)

    def test_both_take_an_optional_ticket(self):
        for name in NEW_SKILLS:
            self.assertIn('argument-hint: "[ticket-id]"', body(name), name)

    def test_both_go_through_the_script(self):
        for name in NEW_SKILLS:
            self.assertIn('${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py', body(name), name)
            self.assertIn('${CLAUDE_PLUGIN_ROOT}/docs/worktrees.md', body(name), name)

    def test_both_confirm_first(self):
        for name in NEW_SKILLS:
            self.assertIn('AskUserQuestion', body(name), name)

    def test_neither_forces_nor_deletes_a_branch(self):
        # Assert the prohibition is stated, not merely that the string is absent.
        for name in NEW_SKILLS:
            text = body(name)
            self.assertIn('Never any `--force` variant', text, name)
            self.assertNotIn('git branch -D', text, name)
            self.assertNotIn('git branch -d', text, name)
            self.assertNotIn('git worktree remove', text, name)

    def test_move_enters_the_worktree(self):
        self.assertIn('`EnterWorktree`', body('move-to-worktree'))

    def test_return_only_keeps(self):
        text = body('return-from-worktree')
        self.assertIn('`ExitWorktree` with `action: "keep"`', text)
        self.assertNotIn('"remove"', text)

    def test_return_checks_before_it_asks(self):
        text = body('return-from-worktree')
        self.assertLess(text.index('--check'), text.index('### Step 3: Confirm'))

    def test_both_name_the_opencode_fallback(self):
        for name in NEW_SKILLS:
            self.assertIn('**OpenCode:**', body(name), name)


class WorktreeContractCase(unittest.TestCase):
    def setUp(self):
        self.text = read('docs/worktrees.md')

    def test_names_every_status(self):
        for status in ('`ok`', '`refused`', '`conflict`', '`rolled-back`', '`error`'):
            self.assertIn(status, self.text)

    def test_names_the_location_and_the_include_file(self):
        self.assertIn('.claude/worktrees/<name>', self.text)
        self.assertIn('.worktreeinclude', self.text)

    def test_the_procedures_ask_before_anything_moves(self):
        self.assertIn('**Ask first.** Nothing moves without the user\'s answer', self.text)
        self.assertIn('**Ask.** Confirm once (header `Hand back`)', self.text)
        self.assertLess(self.text.index('**Ask first.**'), self.text.index('move-in --ticket'))
        self.assertLess(self.text.index('**Ask.** Confirm once'),
                        self.text.index('**Leave** the worktree'))

    def test_the_reference_has_an_entry_per_skill(self):
        reference = read('docs/skills-reference.md')
        for name in NEW_SKILLS:
            self.assertIn('\n### {}\n'.format(name), reference, name)
            self.assertIn('/artel:{} [ticket-id]'.format(name), reference, name)


if __name__ == '__main__':
    unittest.main()
