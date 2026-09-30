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
TAIL = 'skills/feature-development/tail.md'
DEV = 'skills/dev/SKILL.md'


class TestOrchestrators(unittest.TestCase):
    def test_no_model_line_on_the_orchestrators(self):
        for rel in (FD, DEV):
            self.assertNotRegex(frontmatter(rel), r'(?m)^model:')

    def test_step_up_rounds(self):
        fd, dev = flat(read(TAIL)), flat(read(DEV))
        review = ('`--model fable` when the findings come from a `review.md` whose '
                  '`**Review round:**` is 2 or more')
        for text in (fd, dev):
            self.assertIn(review, text)
            self.assertEqual(text.count('--model fable'), 2)
        self.assertIn('plus `--model fable` on the second round', fd)
        self.assertIn('the second round passes `--model fable`', dev)

    def test_the_route_journal_line_carries_no_model(self):
        for rel in (TAIL, DEV):
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


class TestReviewerSide(unittest.TestCase):
    def test_the_forecaster_runs_on_sonnet(self):
        self.assertRegex(frontmatter('agents/review-forecaster.md'), r'(?m)^model: sonnet$')

    def test_deep_review_runs_its_reviewer_on_fable(self):
        text = flat(read('skills/deep-review/SKILL.md'))
        step3 = between(text, '## Step 3:', '## Step 4:')
        self.assertIn('- `model`: `"fable"`', step3)
        self.assertIn('re-dispatch once with the same prompt and model', step3)
        self.assertNotIn('"fable"', between(text, '## Step 4:', '## Step 5:'))

    def test_the_models_section(self):
        agents = flat(read('docs/agents.md'))
        section = between(agents, '## Models', '## Why this file')
        for phrase in ('| `implementer` — every task, and a first fix round | `opus` (frontmatter) |',
                       '| `implementer` — a fix round after a failed one (review round 2 or more, '
                       'checkpoint verify round 2) | `fable` |',
                       "| `reviewer` — `deep-review`'s whole-branch review | `fable` |",
                       '| `review-forecaster` | `sonnet` (frontmatter) |',
                       '`CLAUDE_CODE_SUBAGENT_MODEL`', '`availableModels`',
                       'is re-dispatched once without `model`',
                       'measured and rejected on 2026-09-30',
                       "**A skill's `model:` applies only when the person types the skill.**",
                       '156 of 166', '0 of 230'):
            self.assertIn(phrase, section)

    def test_the_opencode_row(self):
        self.assertIn('a per-dispatch model has no effect', flat(read('docs/opencode.md')))


class TestPinnedDefaults(unittest.TestCase):
    """The step-up rule and the `## Models` table rest on these defaults."""

    def test_the_implementer_and_the_reviewer_default_to_opus(self):
        for rel in ('agents/implementer.md', 'agents/reviewer.md'):
            self.assertRegex(frontmatter(rel), r'(?m)^model: opus$')

    def test_run_reviewer_passes_no_model(self):
        body = read('skills/run-reviewer/SKILL.md').split('---', 2)[2]
        for token in ('`model`', '--model', '"fable"'):
            self.assertNotIn(token, body)


class TestChangelog(unittest.TestCase):
    def test_the_orchestrators_change_is_stated_for_typed_runs(self):
        log = flat(read('CHANGELOG.md'))
        self.assertIn('typed directly it moved the whole run to `sonnet`', log)
        self.assertNotIn('as they always did', log)
        self.assertIn('If you type `/artel:feature-development` or `/artel:dev` directly', log)


if __name__ == '__main__':
    unittest.main()
