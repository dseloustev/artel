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
        self.skill = flat(read('skills/artel-implementer/SKILL.md'))

    def test_the_model_flag(self):
        self.assertIn('[--model <provider/model[#variant]>]',
                      frontmatter('skills/artel-implementer/SKILL.md'))
        for phrase in ('`--model <provider/model[#variant]>`: dispatch the agent on this concrete '
                       'model',
                       'Any other value is an invocation error',
                       'is re-dispatched once without it',
                       '`model` set to the chosen model'):
            self.assertIn(phrase, self.skill)

    def test_the_model_choice_precedence(self):
        for phrase in ('**Choosing the model**', 'first match wins',
                       'Fix-list dispatch', 'Iteration dispatch',
                       '`## Code Review Fixes`', '`## Runtime Fixes`', '`## Verify Fixes`',
                       'Final Verification', 'route --next', '`route_effective`',
                       'implementer.fix', 'models.py resolve --site implementer.fix',
                       'phase-<PHASE_NUM>/tasks.md', 'spec_store.py get'):
            self.assertIn(phrase, self.skill)

    def test_the_model_relay_line(self):
        for phrase in ('`Model:` line', 'after `Verify iterations:` and before `Deviations:`',
                       'predicted task', 'route lookup failed', '`Model: <value>`',
                       'carry no `Model:` line'):
            self.assertIn(phrase, self.skill)

    def test_skills_reference(self):
        ref = flat(read('docs/skills-reference.md'))
        self.assertIn('`/artel-implementer [ticket-id] or [ticket-id]-[phase] [--local] '
                      '[--model <provider/model[#variant]>]`', ref)
        self.assertIn('**Model:** resolved from `models.opencode` — an iteration task follows '
                      'its route', ref)


FD = 'skills/artel-feature-development/SKILL.md'
TAIL = 'skills/artel-feature-development/tail.md'


class TestOrchestrators(unittest.TestCase):
    def test_no_model_line_on_the_orchestrators(self):
        for rel in (FD,):
            self.assertNotRegex(frontmatter(rel), r'(?m)^model:')

    def test_step_up_rounds(self):
        tail = flat(read(TAIL))
        self.assertIn('`--model <value>` when the findings come from a `review.md` whose '
                      '`**Review round:**` is 2 or more', tail)
        self.assertEqual(tail.count('--site implementer.stepUp'), 2)
        self.assertIn('plus `--model <value>` on the second round', tail)

    def test_the_route_journal_line_carries_the_model(self):
        self.assertIn('; model <value>', read(TAIL))

    def test_the_fix_sections_are_named(self):
        tail = read(TAIL)
        self.assertIn('the invocation names `## Runtime Fixes`', flat(tail))
        self.assertIn('naming `## Verify Fixes`', flat(tail))

    def test_step_up_rounds_are_documented(self):
        caps = between(flat(read('docs/autonomous-run.md')), '## 5. Capped loops', '## 6.')
        for phrase in ('**Step-up rounds.** A fix round that follows a failed one runs one tier '
                       'up',
                       '`**Review round:**` 2 or more',
                       "a checkpoint's second `## Verify Fixes` round",
                       "A fix list's first round resolves `implementer.fix`, whatever the task's route",
                       'counts toward `MAX_TOTAL_CORRECTION_ROUNDS`'):
            self.assertIn(phrase, caps)


class TestReviewerSide(unittest.TestCase):
    def test_deep_review_runs_its_reviewer_on_fable(self):
        text = flat(read('skills/artel-deep-review/SKILL.md'))
        step3 = between(text, '## Step 3:', '## Step 4:')
        self.assertIn('- `model`: resolve `reviewer.deepReview`', step3)
        self.assertIn('re-dispatch once with the same prompt and model', step3)
        self.assertNotIn('reviewer.deepReview', between(text, '## Step 4:', '## Step 5:'))

    def test_the_tail_scales_reviews(self):
        tail = flat(read(TAIL))
        self.assertIn('invoke the `artel-run-reviewer` skill with `--task …` and `--model <value>`', tail)
        self.assertIn('the re-review passes `--model <value>`', tail)

    def test_the_models_section(self):
        agents = flat(read('docs/agents.md'))
        section = between(agents, '## Models', '## Why this file')
        for phrase in ('| `implementer` — an iteration task on route `light` | '
                       '`implementer.light` |',
                       '| `implementer` — an iteration task on route `full` | '
                       '`implementer.full` |',
                       '| `reviewer` — a task review (route `full` tasks) | `reviewer.task` |',
                       '| `reviewer` — a re-review after a fix round | `reviewer.reReview` |',
                       "| `reviewer` — `deep-review`'s whole-branch review | `reviewer.deepReview` |",
                       're-dispatched once without one',
                       "superpowers 6.4.1's Model Selection",
                       '**A skill declares no `model:`.**',
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
    """No agent carries a frontmatter model tier: the dispatcher resolves the site."""

    def test_the_agents_declare_no_model_tier(self):
        for rel in ('agents/artel-implementer.md', 'agents/artel-reviewer.md'):
            self.assertNotRegex(frontmatter(rel), r'(?m)^model:')

    def test_run_reviewer_takes_a_model_flag(self):
        body = read('skills/artel-run-reviewer/SKILL.md')
        self.assertIn('[--model <provider/model[#variant]>]',
                      frontmatter('skills/artel-run-reviewer/SKILL.md'))
        for phrase in ('`--model <provider/model[#variant]>`', 'is re-dispatched once without it',
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
