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


if __name__ == '__main__':
    unittest.main()
