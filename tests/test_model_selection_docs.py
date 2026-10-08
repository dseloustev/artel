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
                       '`model` set to the chosen model'):
            self.assertIn(phrase, self.skill)

    def test_the_model_choice_precedence(self):
        for phrase in ('**Choosing the model**', 'first match wins',
                       'Fix-list dispatch', 'Iteration dispatch',
                       '`## Code Review Fixes`', '`## Runtime Fixes`', '`## Verify Fixes`',
                       'Final Verification', 'route --next', '`data.model`',
                       'phase-<PHASE_NUM>/tasks.md', 'spec_store.py get'):
            self.assertIn(phrase, self.skill)

    def test_the_model_relay_line(self):
        for phrase in ('`Model:` line', 'after `Verify iterations:` and before `Deviations:`',
                       'predicted task', 'route lookup failed', '`Model: fable (--model)`',
                       'carry no `Model:` line'):
            self.assertIn(phrase, self.skill)

    def test_skills_reference(self):
        ref = flat(read('docs/skills-reference.md'))
        self.assertIn('`/artel:implementer [ticket-id] or [ticket-id]-[phase] [--local] '
                      '[--model sonnet|opus|fable]`', ref)
        self.assertIn('**Model:** an iteration task follows its route — `sonnet` on `light`, '
                      '`opus` on `full`', ref)


FD = 'skills/feature-development/SKILL.md'
TAIL = 'skills/feature-development/tail.md'


class TestOrchestrators(unittest.TestCase):
    def test_no_model_line_on_the_orchestrators(self):
        for rel in (FD,):
            self.assertNotRegex(frontmatter(rel), r'(?m)^model:')

    def test_step_up_rounds(self):
        tail = flat(read(TAIL))
        self.assertIn('`--model fable` when the findings come from a `review.md` whose '
                      '`**Review round:**` is 2 or more', tail)
        self.assertEqual(tail.count('--model fable'), 2)
        self.assertIn('plus `--model fable` on the second round', tail)

    def test_the_route_journal_line_carries_the_model(self):
        self.assertIn('; model <value>', read(TAIL))

    def test_the_fix_sections_are_named(self):
        tail = read(TAIL)
        self.assertIn('the invocation names `## Runtime Fixes`', flat(tail))
        self.assertIn('naming `## Verify Fixes`', flat(tail))

    def test_step_up_rounds_are_documented(self):
        caps = between(flat(read('docs/autonomous-run.md')), '## 5. Capped loops', '## 6.')
        for phrase in ('**Step-up rounds.** A fix round that follows a failed one runs one tier '
                       'up, on `fable`',
                       '`**Review round:**` 2 or more',
                       "a checkpoint's second `## Verify Fixes` round",
                       "A fix list's first round runs on `opus`, whatever the task's route",
                       'counts toward `MAX_TOTAL_CORRECTION_ROUNDS`'):
            self.assertIn(phrase, caps)


class TestReviewerSide(unittest.TestCase):
    def test_deep_review_runs_its_reviewer_on_fable(self):
        text = flat(read('skills/deep-review/SKILL.md'))
        step3 = between(text, '## Step 3:', '## Step 4:')
        self.assertIn('- `model`: `"fable"`', step3)
        self.assertIn('re-dispatch once with the same prompt and model', step3)
        self.assertNotIn('"fable"', between(text, '## Step 4:', '## Step 5:'))

    def test_the_tail_scales_reviews(self):
        tail = flat(read(TAIL))
        self.assertIn('`Skill: run-reviewer --task …` with `--model sonnet`', tail)
        self.assertIn('the re-review passes `--model sonnet`', tail)

    def test_the_models_section(self):
        agents = flat(read('docs/agents.md'))
        section = between(agents, '## Models', '## Why this file')
        for phrase in ('| `implementer` — an iteration task on route `light` | `sonnet` '
                       '(route helper) |',
                       '| `implementer` — an iteration task on route `full` | `opus` '
                       '(route helper) |',
                       '| `reviewer` — a task review (route `full` tasks) | `sonnet` '
                       '(dispatched) |',
                       '| `reviewer` — a re-review after a fix round | `sonnet` (dispatched) |',
                       "| `reviewer` — `deep-review`'s whole-branch review | `fable` |",
                       '`CLAUDE_CODE_SUBAGENT_MODEL`', '`availableModels`',
                       'is re-dispatched once without `model`',
                       "superpowers 6.4.1's Model Selection",
                       "**A skill's `model:` applies only when the person types the skill.**",
                       '156 of 166', '0 of 230'):
            self.assertIn(phrase, section)

    def test_the_opencode_row(self):
        text = flat(read('docs/opencode.md'))
        self.assertIn('per-dispatch models are resolved from `models.opencode`', text)
        self.assertIn("unset → the child inherits the caller's model", text)
        self.assertNotIn('a per-dispatch model has no effect', text)

    def test_no_doc_still_denies_per_dispatch_models(self):
        for rel in ('docs/opencode.md', 'docs/agents.md', 'docs/autonomous-run.md'):
            text = flat(read(rel))
            for stale in ('ignores per-dispatch models', 'a per-dispatch model has no effect',
                          'no per-dispatch models', 'OpenCode ignores the per-dispatch model'):
                self.assertNotIn(stale, text, rel + ' still says: ' + stale)


class TestPinnedDefaults(unittest.TestCase):
    """The step-up rule and the `## Models` table rest on these defaults."""

    def test_the_implementer_and_the_reviewer_default_to_opus(self):
        for rel in ('agents/implementer.md', 'agents/reviewer.md'):
            self.assertRegex(frontmatter(rel), r'(?m)^model: opus$')

    def test_run_reviewer_takes_a_model_flag(self):
        body = read('skills/run-reviewer/SKILL.md')
        self.assertIn('[--model sonnet|opus|fable]', frontmatter('skills/run-reviewer/SKILL.md'))
        for phrase in ('`--model <sonnet|opus|fable>`', 'is re-dispatched once without it',
                       '`model` set to the `--model` value'):
            self.assertIn(phrase, flat(body))


class TestChangelog(unittest.TestCase):
    def test_the_orchestrators_change_is_stated_for_typed_runs(self):
        log = flat(read('CHANGELOG.md'))
        self.assertIn('typed directly it moved the whole run to `sonnet`', log)
        self.assertNotIn('as they always did', log)
        self.assertIn('If you type `/artel:feature-development` or `/artel:dev` directly', log)


if __name__ == '__main__':
    unittest.main()
