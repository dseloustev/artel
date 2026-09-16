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
        # Toothless as two independent substring checks: a skill that CLEARED the prefix would
        # still mention the key and migrate-prs somewhere. Assert the preserving statement.
        self.assertIn('left exactly as it was', self.text)
        self.assertIn('vcs.mcpToolPrefix', self.text)
        self.assertIn('migrate-prs', self.text)

    def test_a_failed_remote_add_is_rolled_back(self):
        # Step 1 has already renamed `origin` away. If the add fails on a name collision the
        # repository is left with NO origin, and the idempotency test ("origin's URL already
        # equals the target") then has nothing to read on a re-run.
        self.assertIn('git remote rename <the name step 1 used> origin', self.text)
        self.assertIn('no** `origin`', self.text)

    def test_confirms_before_touching_remotes(self):
        self.assertIn('AskUserQuestion', self.text)

    def test_never_force_pushes_or_deletes_a_remote(self):
        # Assert the prohibition is stated, not that the string is absent -- the skill has to
        # spell `--force` out in order to forbid it.
        self.assertIn('Never any `--force` variant', self.text)
        self.assertIn('Never delete a remote', self.text)
        self.assertNotIn('git push --force', self.text)
        self.assertNotIn('git remote remove', self.text)
        self.assertNotIn('git remote rm', self.text)


class MigratePrsDocsCase(unittest.TestCase):
    def setUp(self):
        self.text = body('migrate-prs')

    def test_frontmatter_names_the_skill(self):
        self.assertTrue(self.text.startswith('---\nname: migrate-prs\n'))

    def test_is_idempotent_via_an_existing_pr_check(self):
        self.assertIn('gh pr list --head', self.text)
        self.assertIn('PR_EXISTS', self.text)
        # Ordering, not just presence: a check moved after the push would satisfy both
        # substrings above while duplicating branches on every re-run.
        self.assertLess(self.text.index('gh pr list --head'),
                        self.text.index('git push origin'))

    def test_never_force_pushes(self):
        self.assertIn('Never any `--force` variant', self.text)
        self.assertNotIn('git push --force', self.text)

    def test_carries_provenance(self):
        self.assertIn('Migrated from', self.text)

    def test_does_not_write_to_the_old_platform(self):
        self.assertIn('declined by hand', self.text)
        self.assertIn('Never writes to Bitbucket', self.text)

    def test_untrusted_pr_text_never_reaches_a_shell_command(self):
        # The title is as attacker-authored as the body; both must stay out of the command line.
        self.assertIn('--body-file', self.text)
        self.assertIn('"$(cat', self.text)
        self.assertNotIn('--title "<title>"', self.text)


if __name__ == '__main__':
    unittest.main()
