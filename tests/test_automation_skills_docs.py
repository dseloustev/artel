"""The automation skills' derived-path and verify contracts stay spelled out.

Not a behaviour test: these are prompts, and there is no code path to exercise.
It guards four invariants that were wrong once and fail quietly when they
regress -- the scaffold still applies, and only the rollback, the commit check
or the verify step misbehaves, on a host whose scaffold happens to create a
directory or whose `verify.fast` happens to carry `{files}`.

Prose is deliberately not asserted on; only the tokens that carry the contract.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ADD = 'skills/add-automation/SKILL.md'
REMOVE = 'skills/remove-automation/SKILL.md'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class DerivedPathListTest(unittest.TestCase):
    """Step 4.1 reads the scaffold's paths; plain --porcelain collapses new directories."""

    def test_file_list_derivation_uses_uall_and_z(self):
        for rel in (ADD, REMOVE):
            with self.subTest(skill=rel):
                line = next(
                    ln for ln in read(rel).splitlines() if 'Determine what changed' in ln
                )
                self.assertIn('--porcelain -uall -z', line)

    def test_no_bare_porcelain_derives_a_path_list(self):
        # A bare `--porcelain` is still correct for the preflight emptiness check,
        # so this pins only the occurrences that feed paths downstream.
        for rel in (ADD, REMOVE):
            with self.subTest(skill=rel):
                body = read(rel)
                for match in re.finditer(r'`git status --porcelain([^`]*)`', body):
                    if 'uall' in match.group(1):
                        continue
                    # The emptiness check names its intent right after the command.
                    following = body[match.end():match.end() + 80]
                    self.assertIn(
                        'must be empty', following,
                        f'{rel}: bare --porcelain outside the emptiness check: {match.group(0)}',
                    )


class VerifyFastScopeTest(unittest.TestCase):
    """Skill bodies run config commands directly, so they own the {files} substitution."""

    def test_both_skills_substitute_the_files_token(self):
        for rel in (ADD, REMOVE):
            with self.subTest(skill=rel):
                body = read(rel)
                self.assertIn('`{files}` token', body)
                self.assertIn('shell-quoted', body)
                self.assertIn('scripts/verify.py', body)


class RollbackAndGuardTest(unittest.TestCase):
    """A failed apply can leave debris; a skill that pushes needs the branch guard."""

    def test_add_does_not_claim_a_failed_apply_is_clean(self):
        self.assertNotIn('nothing to roll back yet', read(ADD))

    def test_both_skills_guard_the_default_branch(self):
        for rel in (ADD, REMOVE):
            with self.subTest(skill=rel):
                self.assertIn('Never on the default branch', read(rel))


class ScaffoldRemovalCompletenessTest(unittest.TestCase):
    """Git lists files, never directories, and a host command's exit 0 is not proof.

    The remove skill must (a) sweep the directories the scaffold created once
    their files are gone -- an ignored leftover such as an editor's or Finder's
    droppings keeps `rmdir` from succeeding while `git status` stays clean --
    and (b) cross-check the tree against the paths the add commit introduced,
    so a host `runtime.scaffold.remove` that forgot a file cannot be committed
    as a removal.
    """

    def test_remove_sweeps_directories_the_scaffold_emptied(self):
        body = read(REMOVE)
        self.assertIn('--porcelain --ignored -uall -z', body)
        self.assertIn('rmdir', body)

    def test_remove_stops_on_untracked_leftovers_in_a_swept_directory(self):
        self.assertIn('`??`', read(REMOVE))

    def test_remove_cross_checks_against_the_enable_commit(self):
        body = read(REMOVE)
        self.assertIn('--diff-filter=A', body)
        self.assertIn("--grep='chore: enable agent UI automation'", body)

    def test_add_commit_subject_is_the_fixed_string_remove_greps_for(self):
        self.assertIn('git commit -m "chore: enable agent UI automation"', read(ADD))


if __name__ == '__main__':
    unittest.main()
