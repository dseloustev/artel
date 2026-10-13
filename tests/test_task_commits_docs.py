#!/usr/bin/env python3
"""Doc-contract pins for the task-commit rule (spec 2026-10-13).

Not a behaviour test: the rule is prompt prose, and there is no code path to
exercise. Same guard as tests/test_task_queue_docs.py, for the same reason --
the orchestrator acts from its skill, so a rule that drifts in the prose
silently stops executing in a run nobody is watching.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import build_opencode  # noqa: E402

FD = 'skills/feature-development/'


def raw(path):
    return (ROOT / path).read_text(encoding='utf-8')


def flat(path):
    return re.sub(r'\s+', ' ', raw(path))


class TestTaskCommitProcedure(unittest.TestCase):
    """Spec §1: the trigger, the subjects, and what the procedure never does."""

    def setUp(self):
        self.raw = raw(FD + 'tail.md')
        self.section = self.raw[self.raw.index('\n## Task commits\n'):]

    def test_the_section_exists_and_holds_no_subheading(self):
        self.assertIn('\n## Task commits\n', self.raw)
        self.assertNotIn('\n### ', self.section)

    def test_the_trigger_is_the_completed_cycle(self):
        for phrase in ('one commit per completed implementer cycle',
                       '`## Code Review Fixes`', '`## Runtime Fixes`', '`## Verify Fixes`',
                       'A `Routes` return'):
            self.assertIn(phrase, self.section)

    def test_subjects(self):
        self.assertIn('<type>: <TICKET_ID> - <task title>', self.section)
        self.assertIn('wip: <TICKET_ID> - <task> (run stopped: <reason>)', self.section)

    def test_it_never_pushes(self):
        self.assertIn('never pushes', self.section)
        self.assertIn('the next phase-end checkpoint', self.section)

    def test_idempotence_the_branch_guard_and_the_clean_tree(self):
        self.assertIn('git diff --cached --quiet', self.section)
        self.assertIn("the checkpoint procedure's rule 4", self.section)
        self.assertIn('nothing dirty', self.section)

    def test_no_placeholders(self):
        self.assertNotRegex(self.section, r'\b(TBD|TODO)\b')


class TestOpencodeTransforms(unittest.TestCase):
    """Spec §4.4/§8: a reworded prompt must not silently drop an OpenCode rewrite.

    scripts/build_opencode.py exits 2 when a transform's source text is absent
    from the tree, so this is the guard that makes edits to the tail safe.
    """

    def test_every_transform_source_is_present(self):
        self.assertEqual(build_opencode.missing_transforms(ROOT), [])


if __name__ == '__main__':
    unittest.main()
