"""Doc-contract tests for sub-project 2b: one orchestrator.

Prompts have no code path, so a phrase dropped from one fails a test here instead of a run
nobody is watching. Phrases are matched with whitespace collapsed.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FD = 'skills/feature-development/'


def raw(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(rel):
    """`rel` with whitespace runs collapsed, so re-wrapping a paragraph never breaks a pin."""
    return re.sub(r'\s+', ' ', raw(rel))


def between(text, start, end):
    """The slice of `text` from `start` up to the next `end`, or to the end of the text."""
    i = text.index(start)
    j = text.find(end, i + len(start))
    return text[i:] if j == -1 else text[i:j]


class TestFloorTwoDocs(unittest.TestCase):
    """Route floor 2 reads the ticket's spec trail; both contracts say so in one sentence."""

    RULE = ('the task carries a `[HITL: …]` tag and at least one of its `Files:` lies outside '
            'the ticket\'s spec trail (`<specs.dir>/<TICKET_ID>/`)')

    def test_the_grammar_states_the_rule(self):
        floors = between(flat('docs/task-grammar.md'), '- **Route floors.**', '`route_effective`')
        self.assertIn(self.RULE + ' — `HITL tag`', floors)
        self.assertIn('keeps its declared route', floors)

    def test_the_run_contract_states_the_same_rule(self):
        which = between(flat('docs/autonomous-run.md'), '### 16.1', '### 16.2')
        self.assertIn('2. ' + self.RULE + ';', which)

    def test_the_old_wording_is_gone(self):
        self.assertNotIn('the task carries a `[HITL: …]` tag — `HITL tag`',
                         flat('docs/task-grammar.md'))
        self.assertNotIn('the task carries a `[HITL: …]` tag;', flat('docs/autonomous-run.md'))


class TestLeanWriter(unittest.TestCase):
    """The tasklist-writer is the lean head's and the bug head's writer: a vision is optional,
    a spike answer and a diagnosis are inputs, and work too large for it is raised."""

    AGENT = 'agents/tasklist-writer.md'
    SKILL = 'skills/generate-tasklist/SKILL.md'

    def setUp(self):
        self.agent = flat(self.AGENT)
        self.skill = flat(self.SKILL)

    def test_the_vision_is_optional(self):
        inputs = between(self.agent, '## Input', '## Output')
        self.assertIn('**The vision is optional.**', inputs)
        self.assertIn('If the idea file is missing, return an error message and stop', inputs)
        self.assertNotIn('If the idea or vision file is missing', self.agent)
        self.assertIn('The vision file is optional', self.skill)
        self.assertNotIn('Run /artel:generate-vision first', self.skill)

    def test_the_dispatch_names_every_input(self):
        prompt = between(raw(self.SKILL), '## Context', '## Instructions')
        for line in ('- **Vision file (input):** <resolved vision path>, or none',
                     '- **Spike (input):** <specs.dir>/<TICKET_ID>/spike.md, or none',
                     '- **Diagnosis (input):** <specs.dir>/<TICKET_ID>/diagnosis.md (<status>),'
                     ' or none',
                     '- **PRD (input, requirement IDs only):** <specs.dir>/<TICKET_ID>/prd.md,'
                     ' or none'):
            self.assertIn(line, prompt)

    def test_the_writer_raises_instead_of_inventing(self):
        step = between(self.agent, '### Step 1 — Draft and list questions', '### Step 1b')
        for phrase in ('`RAISE: <reason>; <reason>`', 'and nothing else',
                       'an open product question',
                       'with no vision, the work needs more than one iteration',
                       'These are the only two conditions'):
            self.assertIn(phrase, step)
        self.assertIn('**With no vision, one iteration.**', self.agent)

    def test_a_raise_ends_the_skill(self):
        phase1 = between(self.skill, '### Phase 1: Draft Tasklist', '### Phase 1b')
        for phrase in ('**A raise ends the skill.**', 'print that line unchanged',
                       '`Next: /artel:feature-development <TICKET_ID> --head=full`',
                       'stop before Phase 1b'):
            self.assertIn(phrase, phase1)
        self.assertIn('**A `RAISE` is not a failure.**', self.skill)

    def test_a_diagnosis_shapes_the_work_list_only_when_diagnosed(self):
        rule = between(self.agent, '## From a diagnosis', '## Per-run workflow')
        for phrase in ('whose status is `DIAGNOSED`', 'Task `1.1` writes the failing test',
                       'names it in `Test:`', '**Fix Origin**',
                       'A diagnosis with status `NOT_REPRODUCED` is context only',
                       'A `DIAGNOSED_STRUCTURAL` diagnosis is never passed to you'):
            self.assertIn(phrase, rule)
        self.assertIn('A `DIAGNOSED_STRUCTURAL` diagnosis is never passed', self.skill)

    def test_the_tasklist_declares_its_status(self):
        template = between(raw(self.AGENT), '### Required structure',
                           '### Rules for the Progress')
        self.assertIn('title: "{Feature Title}"\nstatus: TASKLIST_READY\nschema: 1', template)
        self.assertIn('the draft carries `status: DRAFT`', self.agent)

    def test_local_skips_the_mirror(self):
        self.assertIn('argument-hint: "[ticket-id] [idea-file] [vision-file] [--local]"',
                      raw(self.SKILL))
        phase4 = between(self.skill, '### Phase 4:', '### Completion')
        self.assertIn('a run invoked with `--local` mirrors nothing', phase4)
        self.assertIn('- **Invocation:** `/artel:generate-tasklist [ticket-id] [idea-file]'
                      ' [vision-file] [--local]`', raw('docs/skills-reference.md'))

    def test_the_skill_is_the_heads_writer(self):
        overview = between(self.skill, '## Overview', '## Ticket Resolution')
        self.assertIn("the writer of `feature-development`'s lean head and bug head", overview)
        for retired in ('`dev` orchestrator', "`dev`'s mini-interview",
                        'This is the **lean path**'):
            self.assertNotIn(retired, self.skill)


class TestQuestionMode(unittest.TestCase):
    """`/artel:researcher --question`: a spike is answered from the ticket alone, into
    `spike.md`, and the research document keeps its one meaning."""

    AGENT = 'agents/researcher.md'
    SKILL = 'skills/researcher/SKILL.md'
    LAST = '`Spike answered: <one-line answer> — <specs.dir>/<TICKET_ID>/spike.md`'

    def setUp(self):
        self.agent = between(flat(self.AGENT), '## Question mode', '## Rules')
        self.skill = flat(self.SKILL)
        self.mode = between(self.skill, '### Question mode', '### Completion')

    def test_the_hint_and_the_reference_carry_the_flag(self):
        hint = '[ticket-id] or [ticket-id]-[phase] [--question] [--local]'
        self.assertIn('argument-hint: "{}"'.format(hint), raw(self.SKILL))
        self.assertIn('- **Invocation:** `/artel:researcher {}`'.format(hint),
                      raw('docs/skills-reference.md'))

    def test_no_prd_is_read_or_required(self):
        self.assertIn('No PRD is read or required', self.mode)
        self.assertIn('None is read and none is required', self.agent)

    def test_the_agent_learns_the_mode_from_the_context_block(self):
        line = '- **Mode:** <"question (--question was passed)" | "research">'
        self.assertEqual(raw(self.SKILL).count(line), 2)  # the Phase 1 prompt, the resume
        self.assertIn('**Mode:** `question`', self.agent)

    def test_the_answer_goes_to_spike_md_answer_first(self):
        self.assertIn('never `research.md`', self.agent)
        self.assertIn('`type: spike`, `produced_by: artel:researcher`, no status', self.agent)
        headings = ['`## Answer`', '`## Evidence`', '`## Prior Decisions`',
                    '`## What It Would Take`', '`## Open Questions`']
        places = [self.agent.index(heading) for heading in headings]
        self.assertEqual(places, sorted(places))
        self.assertIn('`research.md` is not written', self.mode)

    def test_question_mode_is_the_one_mode_that_asks(self):
        for phrase in ('the one mode in which this skill asks', '`AskUserQuestion`',
                       'Nothing is written to `open-questions.md`',
                       'a headless run'):
            self.assertIn(phrase, self.mode)
        self.assertIn('Outside question mode this skill never asks the user', self.skill)

    def test_it_reads_and_reasons_and_does_not_build(self):
        self.assertIn('Read and reason; do not build', self.agent)
        self.assertIn('described, not run', self.agent)

    def test_the_last_line_is_the_answer(self):
        self.assertIn(self.LAST, self.agent)
        self.assertIn(self.LAST, between(self.skill, '### Completion', '\0'))

    def test_question_mode_is_ticket_wide(self):
        self.assertIn("Note: phase argument ignored — a spike answers the ticket's question.",
                      self.skill)
        self.assertIn('the refuse-and-ask rule does not apply', self.agent)


class TestDiagnoseMode(unittest.TestCase):
    """`/artel:debugging <ticket> --diagnose`: the cause on record, and nothing else changed."""

    SKILL = 'skills/debugging/SKILL.md'
    LAST = '`Diagnosis: <status> — <specs.dir>/<TICKET_ID>/diagnosis.md`'

    def setUp(self):
        self.skill = flat(self.SKILL)
        self.mode = between(self.skill, '## 6. Diagnose mode', '## Rules')
        self.contract = flat('docs/debugging.md')

    def test_the_hint_and_the_reference_carry_the_mode(self):
        hint = '[symptom | failing test | error text] or <ticket-id> --diagnose [--local]'
        self.assertIn('argument-hint: "{}"'.format(hint), raw(self.SKILL))
        self.assertIn('- **Invocation:** `/artel:debugging {}`'.format(hint),
                      raw('docs/skills-reference.md'))
        description = re.search(r'(?m)^description: "(.*)"$', raw(self.SKILL)).group(1)
        self.assertIn('--diagnose', description)
        self.assertLessEqual(len(description), 1024)

    def test_diagnose_mode_stops_before_the_fix(self):
        for phrase in ('**no fix, and nothing left in the tree.**', '§2.1–§2.3',
                       'Stop before §2.4', 'Probes are undone', '`.artel/run/repro/`',
                       '`git status --porcelain` shows nothing this mode added outside'
                       ' `.artel/`'):
            self.assertIn(phrase, self.mode)

    def test_the_diagnosis_document(self):
        self.assertIn('`type: diagnosis`, `produced_by: artel:debugging`, and `status`',
                      self.mode)
        headings = ['`## Symptom`', '`## Reproduction`', '`## Root Cause`', '`## Evidence`',
                    '`## Fix Origin`', '`## Structural`']
        places = [self.mode.index(heading) for heading in headings]
        self.assertEqual(places, sorted(places))

    def test_the_three_statuses(self):
        for row in ('| `DIAGNOSED` |', '| `DIAGNOSED_STRUCTURAL` |', '| `NOT_REPRODUCED` |'):
            self.assertIn(row, self.mode)
        self.assertIn("step 4's ticket question is not asked", self.mode)
        self.assertIn('`## Root Cause` says `not established`', self.mode)

    def test_an_existing_diagnosis_is_kept_unless_it_did_not_reproduce(self):
        self.assertIn('**An existing diagnosis.**', self.mode)
        self.assertIn('`NOT_REPRODUCED` → run again and replace it', self.mode)

    def test_the_last_line_replaces_the_chat_report(self):
        self.assertIn(self.LAST, self.mode)
        self.assertIn("Step 5's report is not printed", self.mode)

    def test_the_spec_trail_rule_has_its_one_exception(self):
        rules = between(self.skill, '## Rules', '\0')
        self.assertIn('**Never touches the spec trail**', rules)
        self.assertIn('The one exception is `diagnosis.md`, in diagnose mode', rules)

    def test_local_skips_the_knowledge_search(self):
        self.assertIn('With `--local` in the arguments, skip this step as well', self.skill)

    def test_the_contract_lists_both_new_readers(self):
        table = between(self.contract, '## 7. Where this applies in artel', '\0')
        for phrase in ('the bug head (`heads/bug.md`',
                       'a red-gate halt (`tail.md`, "Debug it here first")',
                       '`DIAGNOSED_STRUCTURAL`', '`NOT_REPRODUCED`'):
            self.assertIn(phrase, table)
        structural = between(self.contract, '## 4. When the fix is structural', '## 5.')
        self.assertIn('**In diagnose mode**', structural)


if __name__ == '__main__':
    unittest.main()
