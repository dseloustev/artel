"""Doc contract for per-dispatch models: step-up rounds and the reviewer side.

Spec: docs/superpowers/specs/2026-09-30-model-selection-design.md (gitignored).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return re.sub(r'\s+', ' ', text)


def frontmatter(rel):
    return read(rel).split('---')[1]


def between(text, start, end=None):
    head = text.index(start)
    return text[head:text.index(end, head)] if end else text[head:]


class TestImplementerSkill(unittest.TestCase):
    def setUp(self):
        self.skill = flat(read('skills/implementer/SKILL.md'))

    def test_the_model_flag(self):
        self.assertIn('[--model sonnet|opus|fable]', frontmatter('skills/implementer/SKILL.md'))
        for phrase in ('`--model <sonnet|opus|fable>`: dispatch the agent on this model instead '
                       'of its frontmatter `opus`',
                       'Any other value is an invocation error',
                       'is re-dispatched once without it',
                       '`model` set to the `--model` value when one was given'):
            self.assertIn(phrase, self.skill)

    def test_no_per_task_model_rule(self):
        self.assertNotIn('route --next', self.skill)

    def test_skills_reference(self):
        ref = flat(read('docs/skills-reference.md'))
        self.assertIn('`/artel:implementer [ticket-id] or [ticket-id]-[phase] [--local] '
                      '[--model sonnet|opus|fable]`', ref)
        self.assertIn("**Model:** the agent's frontmatter `opus`; `--model` overrides it for "
                      'one dispatch', ref)


FD = 'skills/feature-development/SKILL.md'
DEV = 'skills/dev/SKILL.md'


class TestOrchestrators(unittest.TestCase):
    def test_no_model_line_on_the_orchestrators(self):
        for rel in (FD, DEV):
            self.assertNotRegex(frontmatter(rel), r'(?m)^model:')

    def test_step_up_rounds(self):
        fd, dev = flat(read(FD)), flat(read(DEV))
        review = ('`--model fable` when the findings come from a `review.md` whose '
                  '`**Review round:**` is 2 or more')
        for text in (fd, dev):
            self.assertIn(review, text)
            self.assertEqual(text.count('--model fable'), 2)
        self.assertIn('plus `--model fable` on the second round', fd)
        self.assertIn('the second round passes `--model fable`', dev)

    def test_the_route_journal_line_carries_no_model(self):
        for rel in (FD, DEV):
            self.assertNotIn('; model <value>', read(rel))

    def test_step_up_rounds_are_documented(self):
        caps = between(flat(read('docs/autonomous-run.md')), '## 5. Capped loops', '## 6.')
        for phrase in ('**Step-up rounds.** A fix round that follows a failed one runs one tier '
                       'up, on `fable`',
                       '`**Review round:**` 2 or more',
                       "a checkpoint's second `## Verify Fixes` round",
                       "every other implementer dispatch runs on the agent's frontmatter `opus`",
                       'counts toward `MAX_TOTAL_CORRECTION_ROUNDS`'):
            self.assertIn(phrase, caps)


if __name__ == '__main__':
    unittest.main()
