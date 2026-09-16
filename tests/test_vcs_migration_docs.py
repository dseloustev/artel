"""The two migration skills' SKILL.md files carry the contract their spec fixes.

Contract: docs/superpowers/specs/2026-09-16-vcs-platform-migration-design.md sections 3 and 4.
"""
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / 'skills'


def body(name):
    return (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')


class SetHomeDocsCase(unittest.TestCase):
    def setUp(self):
        self.text = body('set-home')

    def test_frontmatter_names_the_skill(self):
        self.assertTrue(self.text.startswith('---\nname: set-home\n'))

    def test_takes_a_repo_url_argument(self):
        self.assertIn('argument-hint: "<repo-url>"', self.text)

    def test_refreshes_origin_head(self):
        # pr-create and agents/reviewer.md resolve the default branch through
        # refs/remotes/origin/HEAD, which is stale garbage after the remote swap.
        self.assertIn('git remote set-head origin -a', self.text)

    def test_renames_rather_than_replaces_the_old_remote(self):
        self.assertIn('git remote rename origin', self.text)
        self.assertNotIn('git remote set-url origin', self.text)

    def test_preserves_the_mcp_tool_prefix(self):
        self.assertIn('vcs.mcpToolPrefix', self.text)
        self.assertIn('migrate-prs', self.text)

    def test_confirms_before_touching_remotes(self):
        self.assertIn('AskUserQuestion', self.text)

    def test_never_force_pushes_or_deletes_a_remote(self):
        # Assert the prohibition is stated, not that the string is absent -- the skill has to
        # spell `--force` out in order to forbid it.
        self.assertIn('Never any `--force` variant', self.text)
        self.assertIn('Never delete a remote', self.text)
        self.assertNotIn('git push --force', self.text)
        self.assertNotIn('git remote remove', self.text)



if __name__ == '__main__':
    unittest.main()
