"""The `--local` flag is spelled identically everywhere it appears.

Not a behaviour test: these are prompts, and there is no code path to exercise.
It guards one token across six files -- the drift this repo has already had once
("docs: fix stale knowledge-mirror hook counts and the setup interview gap").
Prose is deliberately not asserted on; only the spelling is.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILL_FILES = (
    'skills/analysis/SKILL.md',
    'skills/researcher/SKILL.md',
    'skills/feature-development/SKILL.md',
    'skills/dev/SKILL.md',
)

DOC_FILES = (
    'docs/config.md',
    'docs/skills-reference.md',
)

# The plausible drifts. Each is a real spelling somebody would reach for.
NEAR_MISSES = ('--no-kartoteka', '--local-only', '--offline', '--no-knowledge')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


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

    def test_no_near_miss_spellings_anywhere(self):
        for rel in SKILL_FILES + DOC_FILES:
            with self.subTest(rel):
                text = read(rel)
                for wrong in NEAR_MISSES:
                    self.assertNotIn(wrong, text)


if __name__ == '__main__':
    unittest.main()
