"""The `--local` flag is spelled identically everywhere it appears.

Not a behaviour test: these are prompts, and there is no code path to exercise.
It guards one token across nine files -- and its deliberate absence from a
tenth, `skills/dev/SKILL.md` -- the drift this repo has already had once
("docs: fix stale knowledge-mirror hook counts and the setup interview gap").
It also guards the promise skills-reference.md opens with, that every Invocation
line is its skill's own frontmatter hint: asking whether the token appears
*somewhere* in that file could not see two entries going stale, because it does
appear, in a different entry. Prose is deliberately not asserted on; only the
spelling is.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILL_FILES = (
    'skills/analysis/SKILL.md',
    'skills/researcher/SKILL.md',
    'skills/feature-development/SKILL.md',
    'skills/deep-review/SKILL.md',
    'skills/issue-draft/SKILL.md',
)

# `dev` invokes neither `analysis` nor `researcher`, so it never consults and the
# flag could have no effect there. It is guarded for the absence, not the presence.
DEV_SKILL = 'skills/dev/SKILL.md'

DOC_FILES = (
    'docs/config.md',
    'docs/skills-reference.md',
    'docs/orchestrator-common.md',
    'docs/knowledge-consultation.md',
)

REFERENCE = 'docs/skills-reference.md'

# The plausible drifts. Each is a real spelling somebody would reach for.
NEAR_MISSES = ('--no-kartoteka', '--local-only', '--offline', '--no-knowledge')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def argument_hint(rel):
    """The skill's frontmatter `argument-hint`, unquoted."""
    for line in read(rel).splitlines():
        if line.startswith('argument-hint:'):
            return line.split(':', 1)[1].strip().strip('"')
    return None


def invocation_line(skill_name):
    """The Invocation line under skills-reference.md's `### <skill_name>` entry."""
    entry = None
    for line in read(REFERENCE).splitlines():
        if line.startswith('### '):
            entry = line[4:].strip()
        elif entry == skill_name and line.startswith('- **Invocation:**'):
            return line
    return None


class LocalFlagSpelling(unittest.TestCase):

    def test_every_consulting_skill_documents_the_flag(self):
        for rel in SKILL_FILES:
            with self.subTest(rel):
                self.assertIn('--local', read(rel))

    def test_the_flag_is_in_each_argument_hint(self):
        for rel in SKILL_FILES:
            with self.subTest(rel):
                hints = [ln for ln in read(rel).splitlines()
                         if ln.startswith('argument-hint:')]
                self.assertEqual(1, len(hints), 'exactly one argument-hint line')
                self.assertIn('--local', hints[0])

    def test_the_docs_document_the_flag(self):
        for rel in DOC_FILES:
            with self.subTest(rel):
                self.assertIn('--local', read(rel))

    def test_dev_does_not_carry_the_flag(self):
        """No PRD/plan gates in `dev` -- it invokes neither consulting skill."""
        self.assertNotIn('--local', read(DEV_SKILL))

    def test_each_invocation_line_is_that_skill_s_own_argument_hint(self):
        """skills-reference.md's own rule: copied verbatim, in slash form."""
        for rel in SKILL_FILES + (DEV_SKILL,):
            name = Path(rel).parent.name
            with self.subTest(name):
                hint = argument_hint(rel)
                self.assertIsNotNone(hint, 'skill has an argument-hint')
                self.assertEqual(f'- **Invocation:** `/artel:{name} {hint}`',
                                 invocation_line(name))

    def test_no_near_miss_spellings_anywhere(self):
        for rel in SKILL_FILES + (DEV_SKILL,) + DOC_FILES:
            with self.subTest(rel):
                text = read(rel)
                for wrong in NEAR_MISSES:
                    self.assertNotIn(wrong, text)


class TestWriteDirectionIsNoLongerClaimedAbsent(unittest.TestCase):
    """The doc used to say artel reaches kartoteka through the hook and nothing
    else, and that this run's own `## tasks` block is a lagging mirror whose
    files are authoritative. The task queue makes both false, and a stale
    justification is what the next reader builds on."""

    def setUp(self):
        self.text = (ROOT / 'docs/knowledge-consultation.md').read_text(encoding='utf-8')

    def test_points_at_the_write_direction(self):
        self.assertIn('docs/task-queue.md', self.text)

    def test_no_longer_claims_the_hook_is_the_only_write_path(self):
        self.assertNotIn('and through nothing else', self.text)

    def test_artifacts_are_a_lagging_mirror_and_tasks_are_authoritative(self):
        """Both blocks are ignored here; the doc has to say why each one is.

        Was an assertNotIn on the old text's exact line wrapping -- which any
        reflow satisfies -- plus two assertIns on tokens the paragraph could not
        lose. The substantive claim is the asymmetry: `## artifacts` is skipped
        because the files on disk are ahead of it, `## tasks` because the queue
        is authoritative but out of scope during an interview or a scan.
        """
        block = self.text.split('`## artifacts` block')[1].split(
            '- **`search_knowledge')[0]
        self.assertIn('which can lag the files', block)
        self.assertIn('those files are authoritative', block)
        self.assertIn('The `## tasks` block is **not** a lagging mirror:', block)
        self.assertIn('the queue is authoritative for what to work on', block)
        self.assertIn('ignore it as out of scope, not as stale', block)


if __name__ == '__main__':
    unittest.main()
