#!/usr/bin/env python3
"""Doc-contract pins for the task-commit rule (spec 2026-10-13).

Not a behaviour test: the rule is prompt prose, and there is no code path to
exercise. Same guard as tests/test_task_queue_docs.py, for the same reason --
the orchestrator acts from its skill, so a rule that drifts in the prose
silently stops executing in a run nobody is watching.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FD = 'skills/artel-feature-development/'


def raw(path):
    return (ROOT / path).read_text(encoding='utf-8')


def flat(path):
    return re.sub(r'\s+', ' ', raw(path))


def between(text, start, end):
    """From `start` up to the first `end` after it."""
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


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


class TestTheRunUsesIt(unittest.TestCase):
    """Spec §3: gate 5's commit, the boundary check, the halts and the report."""

    def setUp(self):
        self.tail = flat(FD + 'tail.md')
        self.raw = raw(FD + 'tail.md')

    def test_gate_5_commits_and_checks_the_boundary(self):
        gate5 = self.raw.split('| 5 | `IMPLEMENT_STEP_OK`')[1].split('\n')[0]
        self.assertIn('`## Task commits`', gate5)
        self.assertIn('task-commit: recovered', gate5)

    def test_the_debug_fix_lands_in_the_next_cycle(self):
        self.assertNotIn('The next checkpoint commits the fix.', self.tail)
        self.assertIn('the next cycle commits the fix', self.tail)

    def test_the_report_names_the_task_commits(self):
        report = between(self.raw, '## Final report', '\n## Checkpoint commits & pushes\n')
        self.assertIn('task commits: <n> (<first>..<last>)', report)

    def test_the_checkpoint_no_longer_claims_to_be_the_only_procedure(self):
        self.assertNotIn('The one commit and push procedure of a run.', self.tail)
        self.assertIn('the phase-level commit and push procedure of a run', self.tail)

    def test_the_push_follows_a_green_or_skipped_gate(self):
        procedure = self.raw[self.raw.index('\n## Checkpoint commits & pushes\n'):]
        self.assertIn('green or `skipped`', procedure)

    def test_the_halts_commit_the_in_flight_cycle(self):
        section = self.raw[self.raw.index('\n## Task commits\n'):]
        for phrase in ('MAX_VERIFY_ITERATIONS', 'MAX_TOTAL_CORRECTION_ROUNDS = 8',
                       'a deviation escalation', 'task-commit: wip <hash> (<reason>)'):
            self.assertIn(phrase, section)

    def test_a_task_commit_is_not_a_gate(self):
        gates = raw('docs/gates.md')
        self.assertIn('a task commit is not a gate', gates)


class TestTheAuthorizationCoversThem(unittest.TestCase):
    """Spec §4.1: the pause authorises the task commits too, in every head."""

    def test_every_head_widens_the_note(self):
        for head in ('full', 'lean', 'bug'):
            text = flat(FD + 'heads/{}.md'.format(head))
            with self.subTest(head=head):
                self.assertIn('task and checkpoint commits, and the checkpoint pushes', text)

    def test_the_skill_says_the_same(self):
        text = flat(FD + 'SKILL.md')
        self.assertIn('task and checkpoint commits, and the checkpoint pushes', text)


class TestTheImplementerNeverCommits(unittest.TestCase):
    """Spec §4.2: the invariant lives in the agent body, not only in prose around it."""

    def test_the_agent_says_so(self):
        text = flat('agents/artel-implementer.md')
        self.assertIn('never commit, never stage and never push', text)
        self.assertIn('the orchestrator commits your cycle', text)

    def test_the_snapshot_rationale_is_current(self):
        for rel in ('scripts/review_package.py', 'tests/test_review_package.py'):
            with self.subTest(rel=rel):
                self.assertIn('at each completed cycle and at the phase checkpoints', raw(rel))


class TestTheRunContract(unittest.TestCase):
    """Spec §4.3: docs/autonomous-run.md is the contract of the run."""

    def setUp(self):
        self.run = flat('docs/autonomous-run.md')
        self.section14 = between(raw('docs/autonomous-run.md'), '## 14.', '## 15.')

    def test_section_14_carries_both_rules(self):
        self.assertIn('task commits', self.section14)
        self.assertIn('one commit per completed implementer cycle', self.section14)
        self.assertIn('green or `skipped`', self.section14)

    def test_section_4_names_the_exception(self):
        section4 = between(raw('docs/autonomous-run.md'), '## 4.', '## 5.')
        self.assertIn('task and checkpoint commits', section4)

    def test_section_11_journals_the_entries(self):
        section11 = between(raw('docs/autonomous-run.md'), '## 11.', '## 12.')
        self.assertIn('task-commit:', section11)


class TestTheOperatorDocs(unittest.TestCase):
    """Spec §4.3: the guides a person reads must not contradict the run."""

    def test_the_four_skill_claim_holds(self):
        guide = flat('docs/workflow-guide.md')
        self.assertIn('task and checkpoint commits', guide)
        self.assertIn('`implementer` changes source', guide)

    def test_the_sweep_stays_checkpoint_level(self):
        storage = flat('docs/spec-storage.md')
        self.assertIn('a task commit stages no image', storage)

    def test_orchestrator_common_names_both(self):
        self.assertIn('task and checkpoint commits', flat('docs/orchestrator-common.md'))


class TestTheReviewFindings(unittest.TestCase):
    """The five Important findings of the whole-branch review, pinned so they cannot regress."""

    def setUp(self):
        self.raw = raw(FD + 'tail.md')
        self.section = self.raw[self.raw.index('\n## Task commits\n'):]
        self.procedure = self.raw[self.raw.index('\n## Checkpoint commits & pushes\n'):]

    def test_a_task_commit_never_sweeps_in_pre_staged_work(self):
        # Finding 1: `git commit` commits the whole index, so a person's pre-staged work would
        # ride into a run commit unless the staging rule forbids it.
        self.assertIn('git diff --cached --name-only', self.procedure)
        self.assertIn('never swept into a run commit', self.procedure)

    def test_a_cycle_that_died_before_its_commit_lands_on_resume(self):
        # Finding 2: the tick is written before the commit, so a dead session leaves a task that
        # resume skips — and its work uncommitted.
        self.assertIn('a session that died between the tick and the commit', self.section)
        self.assertIn('lands as its own commit', self.section)

    def test_a_refused_commit_does_not_recurse(self):
        # Finding 3: the halt rule cannot demand a commit the branch guard or a host hook refuses.
        self.assertIn('task-commit: failed (<first error line>)', self.section)
        self.assertIn('never attempt another commit', self.section)

    def test_the_fix_cycles_are_per_dispatch_and_cited(self):
        # Finding 4: a fix round is a loop of dispatches (autonomous-run.md §16.2 step 4), so each
        # return is its own cycle — and every fix callsite must cite the procedure.
        self.assertIn('one per dispatch', self.section)
        self.assertNotIn('the same return ticks them all', self.section)
        for gate in ('| 7 | `REVIEW_OK` |', '| 8 | `RUNTIME_OK` |'):
            row = self.raw.split(gate)[1].split('\n')[0]
            self.assertIn('`## Task commits`', row, gate)

    def test_the_trigger_clauses_are_whole(self):
        # Finding 5: the earlier pins asserted tokens, so four in-memory reversals passed the suite.
        self.assertIn('exits 0 → skip the commit and write no journal entry', self.section)
        self.assertIn('a task commit is never made on the default branch', self.section)
        self.assertIn("`git status --porcelain` over that cycle's paths", self.raw)

    def test_the_non_goals_hold(self):
        # Finding 5's missing guards: no hook, and the à la carte skills unchanged.
        # hooks.json is gone; the hook layer is hooks/*.py plus its only producer, the bridge.
        hook_layer = ' '.join(p.read_text(encoding='utf-8')
                              for p in sorted((ROOT / 'hooks').glob('*.py')))
        self.assertNotIn('task-commit', hook_layer + raw('opencode/plugin/artel.ts'))
        self.assertIn('Never commits, pushes or opens a PR', flat('skills/artel-debugging/SKILL.md'))
        self.assertNotIn('git commit', flat('skills/artel-implementer/SKILL.md'))


class TestNoNinthCompletionFact(unittest.TestCase):
    """Spec non-goals: the completion gate still reads exactly eight facts."""

    def test_the_fact_list_is_unchanged(self):
        completion = between(raw(FD + 'tail.md'), '## Completion gate', '## PR description')
        self.assertEqual(re.findall(r'(?m)^\| `([A-Z_]+)`', completion),
                         ['PLAN_APPROVED', 'TASKLIST_READY', 'IMPLEMENT_STEP_OK', 'REVIEW_OK',
                          'RUNTIME_OK', 'CHECKPOINT_OK', 'DOCS_UPDATED', 'AUTOMATION_REMOVED'])
        self.assertNotIn('TASK_COMMIT', completion)


if __name__ == '__main__':
    unittest.main()
