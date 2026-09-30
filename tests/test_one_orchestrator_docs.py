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


class TestHeadInputs(unittest.TestCase):
    """A bug ticket raised to the full head, or a ticket with a spike answer, reaches the
    analyst with what was already found."""

    def test_the_skill_names_both_documents_in_the_dispatch(self):
        prompt = between(raw('skills/analysis/SKILL.md'), '## Context', '## Instructions')
        self.assertIn('- **Diagnosis (input):** <specs.dir>/<TICKET_ID>/diagnosis.md (<status>),'
                      ' or none', prompt)
        self.assertIn('- **Spike (input):** <specs.dir>/<TICKET_ID>/spike.md, or none', prompt)
        self.assertIn('**Diagnosis and spike answer.**', flat('skills/analysis/SKILL.md'))

    def test_the_analyst_starts_from_them(self):
        inputs = between(flat('agents/analyst.md'), '## Input Artifacts', '## Output')
        for phrase in ('`<specs.dir>/<TICKET_ID>/diagnosis.md`', 'are facts, not questions',
                       '`<specs.dir>/<TICKET_ID>/spike.md`', 'is not asked again'):
            self.assertIn(phrase, inputs)


class TestPreviews(unittest.TestCase):
    """A visual choice may be sketched: the agents draw, the skills pass the sketch through."""

    LIMITS = ('`PREVIEW_MAX_LINES = 12`', '`PREVIEW_MAX_COLUMNS = 60`')

    def test_the_analyst_sketches_ux_choices_without_a_design(self):
        duties = between(flat('agents/analyst.md'), '## Interview duties', '## Phase Support')
        rule = between(duties, '- **Previews.**', '- **Termination.**')
        for phrase in self.LIMITS + ('a `preview`', 'Only on a single-choice question',
                                     'no `DESIGN_ANALYZED` design analysis',
                                     'never on a question that is not visual'):
            self.assertIn(phrase, rule)

    def test_the_vision_writer_sketches_architecture_choices(self):
        workflow = between(flat('agents/vision-writer.md'), '### Step 1', '### Step 2')
        rule = between(workflow, '**Previews.**', '\0')
        for phrase in self.LIMITS + ('a `preview`', 'differ in *shape*',
                                     'Only on a single-choice question'):
            self.assertIn(phrase, rule)

    def test_both_skills_pass_the_sketch_through(self):
        for rel, start, end in (
                ('skills/analysis/SKILL.md', '### Phase 2: Interview loop', '### Phase 3'),
                ('skills/generate-vision/SKILL.md', '### Phase 2: Ask', '### Phase 3')):
            with self.subTest(rel):
                phase = between(flat(rel), start, end)
                for phrase in ("as that option's `preview` field",
                               'single-choice questions only', 'as two calls'):
                    self.assertIn(phrase, phase)


class TestNewDocuments(unittest.TestCase):
    """`spike.md` and `diagnosis.md` are spec documents: stored, addressed and excluded like
    the rest, and the diagnosis is a gate document with three statuses."""

    STORAGE = 'docs/spec-storage.md'

    def test_the_storage_contract_lists_both(self):
        scope = between(raw(self.STORAGE), '## 1. What moves', '## 2. The storage decision')
        for name in ('spike.md', 'diagnosis.md'):
            self.assertIn(name, scope)
        self.assertIn('Both are ticket-wide only', flat(self.STORAGE))

    def test_the_diagnosis_statuses_are_header_statuses(self):
        header = between(flat(self.STORAGE), '### 3.2 The document header', '## 4. Operations')
        self.assertIn('`DIAGNOSED` / `DIAGNOSED_STRUCTURAL` / `NOT_REPRODUCED`', header)

    def test_both_are_addressed_like_any_document(self):
        rows = between(raw(self.STORAGE), '## 3. Addressing', '### 3.1')
        for stem in ('spike', 'diagnosis'):
            self.assertIn('| `<specs.dir>/PROJ-12/{0}.md` | `PROJ-12` | `{0}` | `{0}.md` |'
                          .format(stem), rows)

    def test_the_path_contract_lists_both(self):
        text = raw('docs/ticket-parsing.md')
        for phrase in ('├── spike.md', '├── diagnosis.md',
                       '| Spike answer | `<specs.dir>/<TICKET_ID>/spike.md`',
                       '| Diagnosis | `<specs.dir>/<TICKET_ID>/diagnosis.md`'):
            self.assertIn(phrase, text)

    def test_restore_context_excludes_exactly_the_stored_documents(self):
        import sys
        sys.path.insert(0, str(ROOT / 'hooks'))
        import kartoteka_http
        lists = re.findall(r'--exclude=\{([a-z_,-]+)\}\.md',
                           raw('skills/restore-context/SKILL.md'))
        self.assertEqual(len(lists), 3)  # the Branch A sentence and the two rsync comments
        stored = {name[:-len('.md')] for name in kartoteka_http.MIRRORED}
        for names in lists:
            self.assertEqual(set(names.split(',')), stored)


class TestTail(unittest.TestCase):
    """Plan 2, Task 1: the tail is one document — every gate after arming once, and no head
    named (spec §7)."""

    HEADINGS = ('# The tail', '## Phase traversal', '## Gates', '## Completion gate',
                '## PR description and the PR gate', '## Description-file sync',
                '## Final report', '## Checkpoint commits & pushes')
    FACTS = ('PLAN_APPROVED', 'TASKLIST_READY', 'IMPLEMENT_STEP_OK', 'REVIEW_OK', 'RUNTIME_OK',
             'CHECKPOINT_OK', 'DOCS_UPDATED', 'AUTOMATION_REMOVED')

    def setUp(self):
        self.raw = raw(FD + 'tail.md')
        self.tail = flat(FD + 'tail.md')

    def test_headings_in_order(self):
        self.assertTrue(self.raw.startswith('# The tail\n'))
        at = [('\n' + self.raw).index('\n' + heading + '\n') for heading in self.HEADINGS]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('\n### ', self.raw)

    def test_each_gate_sits_in_the_table_once(self):
        gates = between(self.raw, '\n## Gates\n', '\n## ')
        self.assertEqual(re.findall(r'(?m)^\| ([0-9.]+) \|', gates),
                         ['5', '6', '7', '8', '10', '10.5', '10.7'])
        for row in ('| 5 | `IMPLEMENT_STEP_OK` — every task `- [x]` |', '| 6 | `INDEX_UPDATED` |',
                    '| 7 | `REVIEW_OK` |', '| 8 | `RUNTIME_OK` |', '| 10 | `DOCS_UPDATED` |',
                    '| 10.5 | phase write-back (phase runs only) |',
                    '| 10.7 | `PHASE_CHECKPOINT` |'):
            self.assertEqual(self.raw.count('\n' + row), 1, row)
        for rule in ('**Journal (§11):**', '**Budget:**', '`MAX_TOTAL_CORRECTION_ROUNDS = 8`'):
            self.assertIn(rule, gates)

    def test_it_names_no_head(self):
        # `lean` as a word: "clean" is the tail's own and stays.
        self.assertIsNone(re.search(r'\blean\b', self.raw))
        for name in ('bug head', 'full head', 'heads/', 'chatty head', '`dev`'):
            self.assertNotIn(name, self.raw)

    def test_it_cites_the_shared_start_by_its_steps(self):
        for phrase in ('(`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md` steps 5–6)',
                       'then renew the decision as `SKILL.md` step 1.5 says',
                       '| Planning (`SKILL.md` step 5) | immediately after arming |',
                       '| Phase-end (gate 10.7) |',
                       'else proceed to the completion gate. |',
                       'All green → the PR description and the PR gate.',
                       'keyed on what exists for the ticket'):
            self.assertIn(phrase, self.tail)

    def test_the_traversal_and_the_remirror_open_it(self):
        opening = between(self.tail, '## Phase traversal', '## Gates | #')
        for phrase in ('(one pass of the `## Gates` table)',
                       'a phase boundary re-arms the wall-clock budget',
                       '(extract `phase-<N>/tasks.md` when missing)',
                       '**Re-mirror first.**', 'so a `--local` run skips it',
                       'A tasklist is mirrored by the skill that writes it, and only when it '
                       'writes it',
                       'For a task-format tasklist this step is also the first mirror when '
                       '`Skill: tasklist` wrote it',
                       'The step is create-only and idempotent'):
            self.assertIn(phrase, opening)

    def test_the_gates_keep_the_old_format_path(self):
        gate = between(self.raw, '\n| 5 | `IMPLEMENT_STEP_OK`', '\n| 6 |')
        for phrase in ('On an old-format tasklist there are no routes',
                       '`review.perTask: true` (config.md; off by default) wraps every '
                       'iteration-task dispatch in that procedure, as before',
                       'Fix-list dispatches are never wrapped'):
            self.assertIn(phrase, gate)
        self.assertIn('a `## Final Verification` section an older tasklist carries counts too',
                      between(self.tail, '## Completion gate', '## PR description'))

    def test_the_close_out_is_whole(self):
        gate = between(self.tail, '## Completion gate', '## PR description and the PR gate')
        for fact in self.FACTS:
            self.assertEqual(gate.count('| `' + fact + '`'), 1, fact)
        self.assertNotIn('Skill: validate', gate)
        close = between(self.tail, '## PR description and the PR gate',
                        '## Description-file sync')
        for phrase in ('`Skill: pr-description` with `$0`', '**Always regenerate:**',
                       '**PR gate:** in `plan-gate`, pause first',
                       'In `yolo`, proceed without pausing', '`Skill: pr-create` with `$0`',
                       'is **not** a gate failure', '`completed: true`, `run_active: false`'):
            self.assertIn(phrase, close)
        report = between(self.tail, '## Final report', '## Checkpoint commits & pushes')
        self.assertIn('--author artel:feature-development', report)
        self.assertIn('only when the PR gate was skipped', report)

    def test_the_checkpoint_procedure_keeps_its_seven_steps(self):
        procedure = self.tail[self.tail.rindex('## Checkpoint commits & pushes'):]
        steps = ('1. **Branch guard.**', '2. **Image sweep (kartoteka path), then idempotence.**',
                 '3. **Quality gate (phase-end only).**', '4. **Stage explicitly.**',
                 '5. **Commit.**', '6. **Push.**', '7. **Journal.**')
        at = [procedure.index(step) for step in steps]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('8. **', procedure)
        for phrase in ('The one commit and push procedure of a run.',
                       '`docs: <TICKET_ID> planning artifacts` when a plan exists, '
                       '`docs: <TICKET_ID> work list` otherwise',
                       '--author artel:feature-development',
                       'plus `--local` when the run holds it, plus `--model fable` on the '
                       'second round'):
            self.assertIn(phrase, procedure)
        self.assertNotIn('artel:<skill>', self.raw)

    def test_the_skill_no_longer_holds_it(self):
        skill = raw(FD + 'SKILL.md')
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md', skill)
        for moved in ('| 5 | `IMPLEMENT_STEP_OK`', '| 10.7 | `PHASE_CHECKPOINT` |',
                      '\n## Checkpoint commits & pushes\n', '1. **Branch guard.**',
                      'Confirm the eight facts below yourself'):
            self.assertNotIn(moved, skill)


class TestFullHead(unittest.TestCase):
    """Plan 2, Task 2: the full head is feature-development's gates 0.5–4.5, gate 4.2 and the
    one pause, moved with their numbers and wording (spec §4)."""

    def setUp(self):
        self.raw = raw(FD + 'heads/full.md')
        self.head = flat(FD + 'heads/full.md')

    def test_headings_in_order(self):
        self.assertTrue(self.raw.startswith('# The full head\n'))
        headings = [line for line in self.raw.split('\n') if line.startswith('#')]
        self.assertEqual(headings, ['# The full head', '## Gates',
                                    '### Gate 4.2 — the plan review',
                                    '## THE ONE PAUSE — plan+tasklist approval'])

    def test_the_gates_keep_their_numbers_and_names(self):
        gates = between(self.raw, '\n## Gates\n', '\n### ')
        self.assertEqual(re.findall(r'(?m)^\| ([0-9.]+) \|', gates),
                         ['0.5', '1', '2', '3', '3.5', '4', '4.2', '4.5'])
        for row in ('| 0.5 | `DESIGN_ANALYZED` —', '| 1 | `PRD_READY` —', '| 2 | `VISION_READY` —',
                    '| 3 | plan drafted —', '| 3.5 | `PLAN_GROUNDED` —', '| 4 | `TASKLIST_READY` —',
                    '| 4.2 | `PLAN_REVIEWED` —', '| 4.5 | phase extraction (phase runs only) |'):
            self.assertEqual(self.raw.count('\n' + row), 1, row)
        self.assertNotIn('`IDEA_READY`', self.raw)
        for cap in ('`N <= MAX_PLAN_CHECK_BOUNCES = 2`', '`MAX_PLAN_REVIEW_ROUNDS = 2`'):
            self.assertIn(cap, gates)

    def test_the_analyst_gets_the_diagnosis_and_the_spike(self):
        row = between(self.raw, '\n| 1 | `PRD_READY`', '\n| 2 |')
        self.assertIn('`Skill: analysis` with `$0 $1`, plus `--local` when this run was invoked '
                      'with it', row)
        self.assertIn('When the ticket has a `diagnosis.md` or a `spike.md`, the skill hands each '
                      'to the analyst as an input beside `idea.md`.', row)

    def test_it_is_the_unarmed_part_of_the_run(self):
        opening = between(self.head, '# The full head', '## Gates')
        for phrase in ('The head for an `architectural` size',
                       '`feature-development` step 4 '
                       '(`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`)',
                       'by a head that raises the size',
                       'every question here is a plain `AskUserQuestion` with no `pause_reason`',
                       "including planner regeneration and the pause's fold-back",
                       '`**Plan-check bounces:** 0`',
                       'Gate 4.2 has no artifact to skip on either'):
            self.assertIn(phrase, opening)

    def test_it_cites_arming_and_the_tail_by_their_new_homes(self):
        for phrase in ('for the pause and `SKILL.md` step 5, and end the gate',
                       '**Abort**. Proceed to `SKILL.md` step 5.',
                       'the run-start journal entry (`SKILL.md` step 5) lists every change',
                       "the command the tail's re-mirror runs, without `task_create`",
                       'see `tail.md`, `## Checkpoint commits & pushes`'):
            self.assertIn(phrase, self.head)
        for stale in ('step 4,', 'to step 4', '(step 4)', "step 5's re-mirror", '§3 fold-back',
                      '`dev`', '.dart'):
            self.assertNotIn(stale, self.head)

    def test_the_pause_keeps_its_three_answers_and_the_yolo_guardrail(self):
        pause = self.head[self.head.index('## THE ONE PAUSE'):]
        for phrase in ('- **Approve** →', '- **Request changes** →', '- **`yolo` only:**',
                       'plan becomes status `PLAN_APPROVED`',
                       'The HITL tags remain armed — yolo removes this pause only',
                       "a guardrail, not a pause preference — the lean head's confirmation makes "
                       'the same exception',
                       '**Routes at the pause**', '`<light|full> — set at approval`',
                       'An old-format tasklist shows no routes.'):
            self.assertIn(phrase, pause)

    def test_the_skill_keeps_gate_0_and_points_at_the_head(self):
        skill = raw(FD + 'SKILL.md')
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md', skill)
        self.assertEqual(skill.count('\n| 0 | `IDEA_READY` — `idea.md` exists |'), 1)
        for moved in ('| 0.5 | `DESIGN_ANALYZED`', '| 3.5 | `PLAN_GROUNDED`',
                      '| 4.2 | `PLAN_REVIEWED`', 'Gate 4.2 — the plan review\n',
                      '- **Request changes** →', '**Routes at the pause**'):
            self.assertNotIn(moved, skill)


class TestLayout(unittest.TestCase):
    """Plan 2, Task 3: SKILL.md is the shared start — its headings, its hint, the four files it
    sends the run to, arming and the rules (spec §1, §2)."""

    HEADINGS = ['## Workflow', '### 0. Config gate', '### 1. Set active ticket',
                '### 1.5 Spec store', '### 2. Import the ticket', '### 3. Size the work',
                '### 4. The head', '### 5. Arm the run', '### 6. The tail', '## Important']
    HINT = ('[ticket-id] or [ticket-id]-[phase] [description-file] [--head=full|lean|bug] '
            '[--mode=yolo|plan-gate|full-gates] [--dry-run] [--local]')

    def setUp(self):
        self.raw = raw(FD + 'SKILL.md')
        self.skill = flat(FD + 'SKILL.md')

    def test_headings_in_order(self):
        self.assertEqual([line for line in self.raw.split('\n') if line.startswith('#')],
                         self.HEADINGS)

    def test_the_frontmatter(self):
        head = self.raw.split('---')[1]
        self.assertIn('\nname: feature-development\n', head)
        self.assertIn('\nargument-hint: "' + self.HINT + '"\n', head)
        self.assertNotRegex(head, r'(?m)^model:')
        self.assertIn('- **Invocation:** `/artel:feature-development ' + self.HINT + '`\n',
                      raw('docs/skills-reference.md'))

    def test_it_names_the_four_files_and_when_each_is_read(self):
        for name in ('heads/full.md', 'heads/lean.md', 'heads/bug.md', 'tail.md'):
            self.assertIn('| `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/' + name + '` |',
                          self.raw)
        opening = between(self.skill, 'This file is the shared start', '## Workflow')
        for phrase in ('each read when the run reaches it — never all at once',
                       'Read exactly one head file per run, after sizing',
                       'and another only when a head hands the ticket on',
                       'On resume of an armed run read `tail.md` and no head file'):
            self.assertIn(phrase, opening)

    def test_every_flag_applies_to_every_head(self):
        flags = between(self.skill, '`--step` flag:', '## Workflow')
        for phrase in ('`--head=full|lean|bug` sets the size (step 3)',
                       '`--dry-run`: run steps 0–4 — the head to the end of its approval',
                       'write no `run-state.json`, never arm',
                       'Every flag applies to every head.',
                       '(`analysis`, `researcher`, `tasklist`, `generate-tasklist`, `debugging`, '
                       '`run-reviewer` and `implementer`)',
                       "fix rounds included (gates 7, 8 and 10.7, and the per-task review's "
                       'round)'):
            self.assertIn(phrase, flags)

    def test_gate_0_is_every_run_s_import(self):
        step = between(self.skill, '### 2. Import the ticket', '### 3. Size the work')
        for phrase in ('in every gate of this skill — the one below and every gate of a head file',
                       '| 0 | `IDEA_READY` — `idea.md` exists | `Skill: generate-idea` with '
                       '`$0 $1`, under **every** adapter',
                       'Every run imports: step 3 sizes the work from `idea.md` and every head '
                       'starts from it'):
            self.assertIn(phrase, step)
        self.assertNotIn('Gate 2 hard-requires', step)

    def test_step_4_reads_the_one_head_the_size_names(self):
        step = between(self.skill, '### 4. The head', '### 5. Arm the run')
        for size, name in (('architectural', 'full'), ('bounded', 'lean'), ('bug', 'bug')):
            self.assertIn('| `{}` | `${{CLAUDE_PLUGIN_ROOT}}/skills/feature-development/heads/'
                          '{}.md` |'.format(size, name), step)
        for phrase in ('A `spike` never reaches this step',
                       'plain `AskUserQuestion` calls with no `pause_reason`',
                       '- **its approval**', '- **a raise** (step 3, "The ratchet")',
                       '- **a stop the head names itself**',
                       'only when the person chooses **Treat it as a bounded change** there'):
            self.assertIn(phrase, step)

    def test_arming_is_one_text_for_every_head(self):
        arm = between(self.skill, '### 5. Arm the run', '### 6. The tail')
        for phrase in ('over what the head produced: plan + tasklist, or the confirmed work list '
                       'with `idea.md` and `vision.md` when present',
                       '`forced_floor: "full-gates"` ⇒ stop here',
                       '`--dry-run` ⇒ stop here too',
                       'the pull request the run ends with (opened without asking in `yolo`)',
                       '`requested_local`', '`deviation_files: []` (§2)',
                       '`gates_confirmed: ["TASKLIST_READY"]`',
                       "every task's effective route (§16.1)",
                       '`- size: <size> (<head> head; decided by <decided_by>)`',
                       "When gate 4.2 ran, that is the plan review's outcome",
                       '`plan check: <c> Critical, <i> Important, <m> Minor`',
                       '`plan check: skipped (old-format tasklist)`',
                       '`plan check: not run (<error.kind>)`',
                       '`plan check: run by generate-tasklist`',
                       'subject `docs: <TICKET_ID> planning artifacts` when a plan exists, '
                       '`docs: <TICKET_ID> work list` otherwise',
                       '`planning checkpoint: skipped — the spec trail is in kartoteka`',
                       '**Fresh arm only**'):
            self.assertIn(phrase, arm)
        self.assertNotIn('work-list checkpoint: skipped', self.skill)

    def test_the_tail_is_the_last_step(self):
        step = between(self.skill, '### 6. The tail', '## Important')
        for phrase in ('Run `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/tail.md` from '
                       '`## Phase traversal` to its final report',
                       'holds every `pause_reason` bracket after the head',
                       'A resumed armed run enters here'):
            self.assertIn(phrase, step)

    def test_the_rules(self):
        rules = self.skill[self.skill.index('## Important'):]
        for phrase in ('Every `AskUserQuestion` after step 5 has armed the run MUST be bracketed '
                       'by a `pause_reason` set/clear',
                       'Before that — steps 2–4, every head — a question is plain.',
                       "A size is raised by a head and lowered only by the person's flag.",
                       '`.artel/run/<TICKET_ID>/sizing.json`',
                       "a task's `Route:` line changed at the lean head's confirmation",
                       '**`STORE_UNAVAILABLE`**'):
            self.assertIn(phrase, rules)
        for gone in ('step-2.3', 'after step 3 MUST', 'dev never invokes'):
            self.assertNotIn(gone, self.skill)

    def test_the_stamps(self):
        self.assertEqual(self.raw.count('--decided-by feature-development'), 2)
        for gone in ('--decided-by dev', 'artel:dev', '`dev`'):
            self.assertNotIn(gone, self.raw)


class TestSizing(unittest.TestCase):
    """Plan 2, Task 3: sizing — the four sizes, the order of decision, the record, the line said
    aloud, the ratchet, the spike outcome and the armed run that skips it all (spec §2, §3)."""

    KEYS = ['size', 'head', 'reasons', 'decided_by', 'raised_from', 'answered', 'decided_at']

    def setUp(self):
        self.raw = raw(FD + 'SKILL.md')
        self.skill = flat(FD + 'SKILL.md')
        self.step = between(self.skill, '### 3. Size the work', '### 4. The head')

    def test_it_never_pauses_and_dispatches_no_agent(self):
        for phrase in ('record it, say it, and go on — never a pause',
                       'no agent is dispatched',
                       '`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §17 '
                       '(`## 17. Sizing and heads`)',
                       'stated here in full because this is the file you act from'):
            self.assertIn(phrase, self.step)

    def test_the_four_sizes_and_their_heads(self):
        for row in ('| `spike` | none — "The spike outcome" below |',
                    '| `bug` | `bug` — `heads/bug.md` |',
                    '| `bounded` | `lean` — `heads/lean.md` |',
                    '| `architectural` | `full` — `heads/full.md` |'):
            self.assertEqual(self.step.count(row), 1, row)
        for phrase in ('names no change to ship', "the tracker's issue type is Bug",
                       'no open product question', 'or acceptance that is unclear'):
            self.assertIn(phrase, self.step)

    def test_the_order_of_decision(self):
        rules = ('1. **A flag.**', '2. **A recorded sizing**', '3. **What exists**',
                 '4. **Judgement**')
        at = [self.step.index(rule) for rule in rules]
        self.assertEqual(at, sorted(at))
        for phrase in ('the first rule that matches wins',
                       '`--head=full` → `architectural`, `--head=lean` → `bounded`, '
                       '`--head=bug` → `bug`',
                       "The person's flag is the only thing that lowers a size.",
                       'a run interrupted inside a head resumes on the same head',
                       'A PRD or a plan, at either scope',
                       'Else `diagnosis.md` → `bug`.',
                       'Else a tasklist with open tasks, or a `vision.md` → `bounded`.',
                       'An open task is an unticked `- [ ]` box',
                       'Read that one document', 'explore nothing else'):
            self.assertIn(phrase, self.step)

    def test_doubt_goes_heavier_and_bug_wins(self):
        self.assertIn('In doubt between two sizes take the heavier: `spike` < `bounded` < '
                      '`architectural`.', self.step)
        self.assertIn('`bug` wins any doubt it is part of', self.step)

    def test_the_record_has_exactly_the_contract_s_keys(self):
        import json
        block = between(self.raw, '\n    {\n', '\n    }\n') + '\n    }'
        example = json.loads(block)
        self.assertEqual(list(example), self.KEYS)
        self.assertEqual((example['size'], example['head']), ('bounded', 'lean'))
        self.assertIsNone(example['raised_from'])
        self.assertIs(example['answered'], False)
        for phrase in ('write `.artel/run/<TICKET_ID>/sizing.json` — in `--step` runs too: it is '
                       'not run state',
                       'When rule 2 found the file, leave it as it is',
                       '`head` is `none`, `bug`, `lean` or `full`',
                       '`decided_by` is `flag` (rule 1), `existing` (rule 3), `judgement` '
                       '(rule 4) or `raised` (the ratchet)',
                       '`decided_at` is UTC ISO-8601'):
            self.assertIn(phrase, self.step)

    def test_the_line_said_aloud(self):
        for phrase in ('Size: <size> — <head> head. Reasons: <reason>; <reason>. To change: say '
                       'so now, or re-run with --head=<full|lean|bug>.',
                       '`Size: spike — no head; the researcher answers the question.`',
                       'It is an announcement, not a question: go straight on.',
                       'honour that as the flag',
                       'the run-start journal entry repeats the size either way (step 5)'):
            self.assertIn(phrase, self.step)

    def test_the_ratchet_raises_and_stops_at_arming(self):
        for phrase in ('**The ratchet.** A head may raise the size, never lower it',
                       "the lean head when `generate-tasklist` returns the writer's `RAISE`",
                       'the bug head when the diagnosis is `DIAGNOSED_STRUCTURAL`',
                       '`decided_by` `raised`, `raised_from` the earlier size',
                       'Size raised: <from> → architectural — full head. Reason: <reason>.',
                       'The ratchet stops at arming'):
            self.assertIn(phrase, self.step)

    def test_the_spike_outcome(self):
        for phrase in ('**The spike outcome.** A `spike` reads no head file.',
                       '`Skill: researcher` with `$0 --question`, plus `--local` when this run '
                       'was invoked with it',
                       '`<specs.dir>/<TICKET_ID>/spike.md`',
                       '`Spike answered: <one-line answer> — <path>`',
                       'set `answered: true` in `sizing.json`, and stop',
                       'Nothing is armed, journaled or committed'):
            self.assertIn(phrase, self.step)

    def test_an_answered_spike_is_not_a_recorded_sizing(self):
        for phrase in ('a spike already answered (`size` `spike`, `answered: true`) is not a '
                       'recorded sizing — go on to rule 3',
                       'A later run on the same ticket is sized afresh from rule 3.',
                       'do not dispatch the researcher a second time',
                       'Already answered: <path to spike.md>. To build on it, re-run with '
                       '--head=lean or --head=full.'):
            self.assertIn(phrase, self.step)

    def test_an_armed_run_resumes_past_the_head(self):
        opening = between(self.skill, '## Workflow', '### 0. Config gate')
        for phrase in ('Steps 0–1.5 run on every invocation.',
                       '**An armed run resumes past the head.**',
                       '`run_active: true` with `gates_confirmed` holding `TASKLIST_READY`',
                       'Skip steps 2–4',
                       'Do not size, do not read a head file, and do not present an approval '
                       'again.',
                       '`--head ignored: the run is past its head.`',
                       'A run armed before 0.25.0 has no `sizing.json` and needs none.',
                       'Every other run — new, or interrupted inside a head — goes on to step 2.'):
            self.assertIn(phrase, opening)


class TestLeanHead(unittest.TestCase):
    """Plan 2, Task 4: the lean head is dev's input ladder without its third branch; the
    tasklist-writer writes every new work list, and may raise the size (spec §5)."""

    def setUp(self):
        self.raw = raw(FD + 'heads/lean.md')
        self.head = flat(FD + 'heads/lean.md')
        self.ladder = between(self.head, '## The ladder', '## Routes at the confirmation')

    def test_headings_in_order(self):
        self.assertEqual([line for line in self.raw.split('\n') if line.startswith('#')],
                         ['# The lean head', '## The ladder', '## Routes at the confirmation',
                          '## Raised by the writer'])

    def test_it_is_the_unarmed_head_of_a_bounded_size(self):
        opening = between(self.head, '# The lean head', '## The ladder')
        for phrase in ('The head for a `bounded` size',
                       '`feature-development` step 4 '
                       '(`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`)',
                       'No PRD, vision or plan is written here and none is required; one that '
                       'exists is used.',
                       'every question here is a plain `AskUserQuestion` with no `pause_reason`',
                       'first invoke `Skill: sync-phases` with `$0`'):
            self.assertIn(phrase, opening)

    def test_a_finished_work_list_stops_the_head(self):
        # Follow-up work on a finished ticket: generate-tasklist skips a tasklist that exists,
        # so without this rule the run would arm with nothing to do and drop the request.
        opening = between(self.head, '# The lean head', '## The ladder')
        for phrase in ('**A finished work list stops the head.**',
                       'none of its boxes is open',
                       'Do not invoke `generate-tasklist`',
                       'do not go on to `SKILL.md` step 5',
                       "This ticket's work list is complete: nothing was planned and the run is "
                       'not armed.',
                       '/artel:tasks add <TICKET_ID> "<title>" --iteration <N>',
                       'or open a new ticket'):
            self.assertIn(phrase, opening)

    def test_two_branches_and_no_third(self):
        self.assertIn('1. **Tasklist with incomplete `- [ ]` tasks exists**', self.ladder)
        self.assertIn('2. **Otherwise** → `Skill: generate-tasklist` with `$0`, plus `--local` '
                      'when this run was invoked with it.', self.ladder)
        self.assertIn('Its questions+approval round IS the mini-interview and the one pause — do '
                      'not add another.', self.ladder)
        for gone in ('3. **Else**', 'max two rounds', 'old checkbox format', 'ranch 3',
                     '`idea.md` + `vision.md` exist', 'artifact_put'):
            self.assertNotIn(gone, self.head)

    def test_branch_one_checks_then_confirms_once(self):
        for phrase in ('First run the plan check on it',
                       'No PRD there (kartoteka path: `spec_store.py exists <prd-path>` exits 3) '
                       '→ `<requirements>` is `absent`',
                       'tasklist_tasks.py --tasklist <tasklist-path> --ticket-key <TICKET_ID> '
                       '--check --requirements <requirements>',
                       '**Confirm** / **Adjust** (feedback via "Other")',
                       "This is the run's one pause.",
                       'There is no automatic fix round here',
                       '`plan check: not run (<error.kind>)`'):
            self.assertIn(phrase, self.ladder)

    def test_an_old_format_tasklist_is_still_the_work_list(self):
        self.assertIn('An old-format tasklist (`data.format` `legacy`) is still the work list: '
                      'it has no findings to show and no routes.', self.ladder)
        self.assertIn('`plan check: skipped (old-format tasklist)`', self.ladder)
        self.assertIn('An old-format tasklist shows no routes.',
                      between(self.head, '## Routes at the confirmation',
                              '## Raised by the writer'))

    def test_the_confirmation_says_what_it_authorises(self):
        for phrase in ('The confirmed work list is the deviation anchor.',
                       "confirming authorizes the run's checkpoint commits & pushes to `origin`",
                       'procedure: `tail.md` `## Checkpoint commits & pushes`',
                       'the run ends with a pull request: in `plan-gate` a question before it is '
                       'opened, in `yolo` opened without asking',
                       'On branch 2, say the same in one line before invoking the skill'):
            self.assertIn(phrase, self.ladder)

    def test_yolo_keeps_the_finding_guardrail(self):
        yolo = self.ladder[self.ladder.index('**`yolo` only:**'):]
        self.assertIn('present nothing — the derived work list stands', yolo)
        self.assertIn('a Critical or Important plan-check finding still presents the '
                      'confirmation, findings first', yolo)
        self.assertIn('a guardrail, not a pause preference', yolo)

    def test_the_check_s_outcome_reaches_the_run_start_entry(self):
        for phrase in ("The check's outcome goes into the run-start journal entry (`SKILL.md` "
                       'step 5)',
                       '`plan check: <c> Critical, <i> Important, <m> Minor`',
                       '`plan check: run by generate-tasklist`'):
            self.assertIn(phrase, self.ladder)

    def test_routes_at_the_confirmation(self):
        routes = between(self.head, '## Routes at the confirmation', '## Raised by the writer')
        for phrase in ('(`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §16.1)',
                       "the tail's re-mirror command, without `task_create`",
                       '`<light|full> — set at approval`',
                       'the run-start journal entry (`SKILL.md` step 5), naming any floor it '
                       'lowered',
                       'a route change asked there reaches `tasklist-writer` like any other '
                       'change',
                       '`SKILL.md` step 5 announces the routes either way'):
            self.assertIn(phrase, routes)

    def test_a_raise_is_a_raise_not_a_stop(self):
        raised = self.head[self.head.index('## Raised by the writer'):]
        for phrase in ('`RAISE: <reason>; <reason>`',
                       '`Next: /artel:feature-development <TICKET_ID> --head=full`',
                       'it stops before its Phase 1b',
                       'raises in two cases only',
                       'Inside this run that is a raise, not a stop.',
                       '`SKILL.md` step 3, "The ratchet"',
                       '`Size raised: bounded → architectural — full head. Reason: <reason>.`',
                       '`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md`',
                       'Do not act on the `Next:` line',
                       'do not invoke `generate-tasklist` again'):
            self.assertIn(phrase, raised)

    def test_no_name_of_the_old_skill_is_left(self):
        for stale in ('`dev`', "dev's", "step 4's", '(step 3)', 'step 3 announces', '.dart',
                      '`feature-development` `## Checkpoint'):
            self.assertNotIn(stale, self.head)


class TestBugHead(unittest.TestCase):
    """Plan 2, Task 5: the bug head diagnoses before anything is planned, then takes the path
    the diagnosis' status names (spec §6)."""

    def setUp(self):
        self.raw = raw(FD + 'heads/bug.md')
        self.head = flat(FD + 'heads/bug.md')
        self.status = self.head[self.head.index("## By the diagnosis' status"):]

    def row(self, status):
        return between(self.raw, '\n| `' + status + '` |', '\n')

    def test_headings_in_order(self):
        self.assertEqual([line for line in self.raw.split('\n') if line.startswith('#')],
                         ['# The bug head', '## Gates', "## By the diagnosis' status"])

    def test_two_gates(self):
        gates = between(self.raw, '\n## Gates\n', '\n## ')
        self.assertEqual(re.findall(r'(?m)^\| (B\d) \|', gates), ['B1', 'B2'])
        for phrase in ('| B1 | `DIAGNOSED` — `diagnosis.md` has status `DIAGNOSED` or '
                       '`DIAGNOSED_STRUCTURAL`',
                       '`Skill: debugging` with `$0 --diagnose`, plus `--local` when this run '
                       'was invoked with it',
                       '| B2 | the work list — `tasklist.md` exists |'):
            self.assertIn(phrase, gates)

    def test_diagnose_mode_finds_the_cause_and_leaves_the_tree_alone(self):
        gate = between(self.head, '**Gate B1.**', "## By the diagnosis' status")
        for phrase in ('`${CLAUDE_PLUGIN_ROOT}/docs/debugging.md` §2.1–§2.3',
                       'stops before the fix',
                       'It leaves no fix, no failing test and no probe in the tree',
                       '`<specs.dir>/<TICKET_ID>/diagnosis.md`',
                       '`Diagnosis: <status> — <path>`',
                       'without the document entering this context'):
            self.assertIn(phrase, gate)

    def test_each_status_has_one_row(self):
        for status in ('DIAGNOSED', 'DIAGNOSED_STRUCTURAL', 'NOT_REPRODUCED'):
            self.assertEqual(self.raw.count('\n| `' + status + '` |'), 1, status)

    def test_diagnosed_writes_the_work_list_from_the_diagnosis(self):
        row = self.row('DIAGNOSED')
        for phrase in ('Gate B2: `Skill: generate-tasklist` with `$0`, plus `--local` when this '
                       'run was invoked with it',
                       'its first task writes the failing test for the reproduction and names it '
                       'in `Test:`',
                       'the fix goes at the fix origin and nowhere else',
                       "The skill's approval round is the run's one pause — add none."):
            self.assertIn(phrase, row)

    def test_a_structural_diagnosis_raises_the_size(self):
        row = self.row('DIAGNOSED_STRUCTURAL')
        for phrase in ('A raise (`SKILL.md` step 3, "The ratchet")',
                       '`Size raised: bug → architectural — full head. Reason: <reason>.`',
                       '`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/full.md`',
                       'Its PRD interview starts from the diagnosis.'):
            self.assertIn(phrase, row)

    def test_a_fault_that_did_not_reproduce_asks_and_stops_headless(self):
        row = self.row('NOT_REPRODUCED')
        for phrase in ('Ask (plain `AskUserQuestion`)', '**Give more detail**',
                       '**Treat it as a bounded change**', '**Stop**',
                       '(`size` `bounded`, `head` `lean`, `decided_by` `flag`)',
                       '`${CLAUDE_PLUGIN_ROOT}/skills/feature-development/heads/lean.md`',
                       "Headless: print the diagnosis' path and stop."):
            self.assertIn(phrase, row)

    def test_the_approval_says_what_it_authorises(self):
        for phrase in ("**Before gate B2's skill runs**, say in one line what its approval will "
                       'authorise',
                       '(`tail.md` `## Checkpoint commits & pushes`)',
                       'asked first in `plan-gate`, opened without asking in `yolo`',
                       'The approved work list is the deviation anchor.'):
            self.assertIn(phrase, self.status)

    def test_a_raise_from_the_writer_and_a_resume(self):
        for phrase in ("**When gate B2's skill returns `RAISE: <reason>; <reason>`**",
                       'that is a raise too, from `bug`',
                       'Do not act on its `Next:` line',
                       '**Resume.** `diagnosis.md` exists → gate B1 is done',
                       '`tasklist.md` exists → gate B2 is done',
                       'go to `SKILL.md` step 5',
                       '`plan check: run by generate-tasklist`'):
            self.assertIn(phrase, self.status)

    def test_it_is_unarmed_and_writes_no_spec_of_its_own(self):
        opening = between(self.head, '# The bug head', '## Gates')
        for phrase in ('The head for a `bug` size',
                       'find the cause before anything is planned',
                       'every question here is a plain `AskUserQuestion` with no `pause_reason`',
                       'No PRD, vision or plan is written here.'):
            self.assertIn(phrase, opening)
        self.assertNotIn('`dev`', self.head)


class TestTailRules(unittest.TestCase):
    """Plan 2, Task 6: four rules of the tail are keyed on what exists for the ticket, never on
    the head that ran (spec §7)."""

    def setUp(self):
        self.raw = raw(FD + 'tail.md')
        self.gate = between(flat(FD + 'tail.md'), '## Completion gate',
                            '## PR description and the PR gate')

    def fact(self, name):
        return between(self.raw, '\n| `' + name + '`', '\n')

    def test_the_docs_stage_is_keyed_on_a_prd(self):
        row = between(self.raw, '\n| 10 | `DOCS_UPDATED` |', '\n')
        for phrase in ('Runs only when a PRD exists for the ticket, at either scope',
                       '`spec_store.py exists`',
                       'journal `DOCS_UPDATED: skipped (no PRD)` and go on',
                       'With one: `Skill: docs-update` with `<TICKET_ID>` (ticket-wide)',
                       'once per ticket, on the last phase only, before its 10.7 checkpoint',
                       '`DOCS_UPDATED: deferred to the final phase`'):
            self.assertIn(phrase, row)
        self.assertNotIn('head', row)

    def test_the_completion_facts_are_keyed_on_what_exists(self):
        self.assertIn('no plan exists for the ticket → `skipped (no plan)`',
                      self.fact('PLAN_APPROVED'))
        self.assertIn('`gates_confirmed` holding `TASKLIST_READY` is the approval',
                      self.fact('PLAN_APPROVED'))
        self.assertIn('no PRD exists for the ticket → `skipped (no PRD)`',
                      self.fact('DOCS_UPDATED'))
        self.assertIn('A fact that reads `skipped` is green.', self.gate)

    def test_a_tasklist_without_a_status_is_read_from_gates_confirmed(self):
        row = self.fact('TASKLIST_READY')
        for phrase in ("the tasklist's status (`spec_store.py status`)",
                       'a tasklist that declares none',
                       'written by `generate-tasklist` before 0.25.0',
                       'is read from `gates_confirmed` in `run-state.json`'):
            self.assertIn(phrase, row)

    def test_every_fix_section_parent_is_closed(self):
        row = self.fact('IMPLEMENT_STEP_OK')
        self.assertIn('on the queue path, every fix-section parent row is closed', row)
        self.assertIn('`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6', row)
        self.assertIn('reports no unchecked box in any iteration or fix section', row)

    def test_there_are_still_eight_facts(self):
        self.assertIn('Confirm the eight facts below yourself', self.gate)
        self.assertEqual(len(re.findall(r'\| `[A-Z_]+`(?: — the final gate)? \| ', self.gate)), 8)


class TestDebugHere(unittest.TestCase):
    """Plan 2, Task 6: a run halted on a red gate in app code can be debugged where it stands —
    on three halts, once per halt (spec §10)."""

    LABEL = '**Debug it here first**'

    def setUp(self):
        self.raw = raw(FD + 'tail.md')
        self.tail = flat(FD + 'tail.md')
        self.section = between(self.raw, '\n## Debug it here first\n', '\n## ')

    def test_the_section_sits_between_the_gates_and_the_completion_gate(self):
        at = [self.raw.index('\n' + heading + '\n')
              for heading in ('## Gates', '## Debug it here first', '## Completion gate')]
        self.assertEqual(at, sorted(at))

    def test_three_halts_and_no_other(self):
        bullets = [line for line in self.section.split('\n') if line.startswith('- ')]
        self.assertEqual(len(bullets), 3)
        flat_section = re.sub(r'\s+', ' ', self.section)
        for phrase in ('- gate 5 — a task aborted when `MAX_VERIFY_ITERATIONS` ran out;',
                       "- gate 8 — the runtime gate's second RED from a runtime error in app "
                       'code;',
                       "- the checkpoint procedure's step 3 — still red after "
                       '`MAX_CHECKPOINT_VERIFY_ROUNDS`.',
                       'Nowhere else: not the review cap (findings, answered by guidance), not an '
                       'environment error, not `MAX_TOTAL_CORRECTION_ROUNDS`, not the wall clock.',
                       'A headless run journals and stops as before'):
            self.assertIn(phrase, flat_section)

    def test_each_of_the_three_halts_offers_it_and_the_review_cap_does_not(self):
        self.assertEqual(self.raw.count(self.LABEL), 4)  # the section and the three halts
        self.assertIn(self.LABEL, between(self.raw, '\n| 5 | `IMPLEMENT_STEP_OK`', '\n'))
        self.assertIn(self.LABEL, between(self.raw, '\n| 8 | `RUNTIME_OK` |', '\n'))
        self.assertIn(self.LABEL, between(self.raw, '3. **Quality gate (phase-end only).**',
                                          '4. **Stage explicitly.**'))
        self.assertNotIn(self.LABEL, between(self.raw, '\n| 7 | `REVIEW_OK` |', '\n'))
        self.assertNotIn(self.LABEL, between(self.raw, '**Budget:**', '\n## '))

    def test_what_the_option_runs(self):
        flat_section = re.sub(r'\s+', ' ', self.section)
        for phrase in ('`Skill: debugging` with the red evidence as its argument: the failing '
                       "stage, its first error lines, the path of the task's report",
                       '`pause_reason` stays `"cap-escalation"` throughout',
                       'The skill never commits and never writes run state or the journal'):
            self.assertIn(phrase, flat_section)

    def test_the_three_outcomes(self):
        flat_section = re.sub(r'\s+', ' ', self.section)
        for phrase in ('| fixed | Journal `debugged here: <root cause> — <files>`',
                       'add those files to `run-state.json` `deviation_files`',
                       're-run the gate that was red',
                       'reset that loop\'s counter as a resume with guidance does',
                       'by dispatching the implementer on it again',
                       'The next checkpoint commits the fix.',
                       'Ask the escalation question again, without this option: '
                       '`MAX_DEBUG_HERE_ATTEMPTS = 1`, one attempt per halt.',
                       '| structural (`${CLAUDE_PLUGIN_ROOT}/docs/debugging.md` §4) |',
                       "The skill's own ticket question runs"):
            self.assertIn(phrase, flat_section)

    def test_it_adds_no_step_up_round(self):
        self.assertEqual(self.tail.count('--model fable'), 2)


class TestAlias(unittest.TestCase):
    """Plan 2, Task 7: /artel:dev is an alias for one release — one printed line, one skill
    call, nothing else (spec §9)."""

    LINE = '/artel:dev is now /artel:feature-development --head=lean and goes away in 0.26.0.'
    HINT = ('[ticket-id] or [ticket-id]-[phase] [description-file] '
            '[--mode=yolo|plan-gate|full-gates]')

    def setUp(self):
        self.raw = raw('skills/dev/SKILL.md')
        self.body = self.raw.split('---', 2)[2]

    def test_the_frontmatter_keeps_the_name_and_the_hint(self):
        head = self.raw.split('---')[1]
        self.assertIn('\nname: dev\n', head)
        self.assertIn('\nargument-hint: "' + self.HINT + '"\n', head)
        self.assertRegex(head, r'(?m)^description: "Alias of /artel:feature-development '
                               r'--head=lean')
        self.assertNotRegex(head, r'(?m)^model:')

    def test_the_body_prints_one_line_then_hands_the_arguments_on(self):
        self.assertEqual(self.body.count(self.LINE), 1)
        call = '`Skill: feature-development` with `$ARGUMENTS --head=lean`'
        self.assertEqual(self.body.count(call), 1)
        self.assertLess(self.body.index(self.LINE), self.body.index(call))

    def test_nothing_else(self):
        lines = [line for line in self.body.split('\n') if line.strip()]
        self.assertLessEqual(len(lines), 5)
        for gone in ('#', 'run-state.json', 'spec_store.py', 'tasklist_tasks.py',
                     'AskUserQuestion', '--local', '--decided-by', '--author',
                     '${CLAUDE_PLUGIN_ROOT}'):
            self.assertNotIn(gone, self.body)

    def test_the_alias_never_resizes_an_armed_run(self):
        # The alias adds --head=lean on every call, a resume included; the shared start has to
        # ignore it once the run is armed, or every resume would present the work list again.
        opening = between(flat(FD + 'SKILL.md'), '## Workflow', '### 0. Config gate')
        for phrase in ('Skip steps 2–4', 'do not present an approval again',
                       'A `--head` flag on such a run changes nothing',
                       '`--head ignored: the run is past its head.`'):
            self.assertIn(phrase, opening)


class TestRouter(unittest.TestCase):
    """Plan 2, Task 8: the router has one entry point for ticket work and names /artel:dev only
    as its alias (spec §12)."""

    ROUTER = 'skills/using-artel/SKILL.md'
    CAP = 10240

    def setUp(self):
        self.raw = raw(self.ROUTER)
        self.router = flat(self.ROUTER)
        self.entry = between(self.raw, '**Entry points**', '**Pipeline stages')

    def test_one_entry_point_for_ticket_work(self):
        self.assertEqual(set(re.findall(r'/artel:([a-z-]+)', self.entry)),
                         {'feature-development', 'dev', 'setup'})
        rows = [line for line in self.entry.split('\n')
                if line.startswith('| ') and '/artel:' in line]
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertIn(re.search(r'/artel:([a-z-]+)', row).group(1),
                          ('feature-development', 'setup'), row)

    def test_the_head_flag_is_the_override(self):
        self.assertIn('| a ticket — feature, small change, bug or question — taken to a pull '
                      'request; it sizes the work and picks the head, one approval pause | '
                      '`/artel:feature-development <ticket> [description-file] '
                      '[--head=full\\|lean\\|bug] [--mode=…] [--dry-run] [--local]` |', self.raw)

    def test_dev_is_named_once_and_only_as_the_alias(self):
        self.assertEqual(self.raw.count('/artel:dev'), 1)
        line = [ln for ln in self.raw.split('\n') if '/artel:dev' in ln][0]
        self.assertIn('`--head=lean` forces it (`/artel:dev` is that, as an alias until 0.26.0)',
                      line)
        self.assertNotIn('`dev`', self.raw)

    def test_small_work_and_the_next_phase_go_to_the_one_entry_point(self):
        for row in ('| a small change implemented, reviewed and runtime-checked | '
                    '`/artel:feature-development <ticket>` — small work is sized lean;',
                    '| the next phase of a phased ticket | '
                    '`/artel:feature-development <ticket>-<N>` |',
                    '| "This change is small, I\'ll just implement it" | Small is a size: '
                    '`/artel:feature-development` picks the lean head — gates included. |'):
            self.assertIn(row, self.raw)
        self.assertNotIn('no PRD/docs', self.raw)

    def test_precedence_describes_one_sized_entry_point(self):
        precedence = between(self.router, '## Precedence', '## Red flags')
        for phrase in ('The entry point **is** the process: `feature-development` sizes the '
                       'work, runs the head that fits — interview and plan, a work list, or a '
                       'diagnosis — and pauses once for approval.',
                       'Do not run brainstorming or plan-writing skills in front of it — that is '
                       'the interview twice.'):
            self.assertIn(phrase, precedence)
        self.assertNotIn('confirms a work list', precedence)

    def test_it_fits_the_injection_budget(self):
        self.assertLessEqual(len(self.raw.encode('utf-8')), self.CAP)


class TestRunContract(unittest.TestCase):
    """Plan 2, Task 9: docs/autonomous-run.md is the contract of one orchestrator — the run
    tree, the armed run, the debug option, and §17 on sizing and heads (spec §12)."""

    RUN = 'docs/autonomous-run.md'
    LINES = ('Size: <size> — <head> head. Reasons: <reason>; <reason>. To change: say so now, or '
             're-run with --head=<full|lean|bug>.',
             'Size: spike — no head; the researcher answers the question.',
             'Size raised: <from> → architectural — full head. Reason: <reason>.',
             'Already answered: <path to spike.md>. To build on it, re-run with --head=lean or '
             '--head=full.',
             '--head ignored: the run is past its head.',
             '- size: <size> (<head> head; decided by <decided_by>)',
             'Spike answered: <one-line answer> — <path>')
    KEYS = ['size', 'head', 'reasons', 'decided_by', 'raised_from', 'answered', 'decided_at']
    CAPS = ['MAX_VERIFY_ITERATIONS = 4', 'MAX_TASK_REVIEW_ROUNDS = 1', 'ROUTE_FULL_FILES = 5',
            'MAX_PLAN_REVIEW_ROUNDS = 2', 'MAX_REVIEW_ROUNDS = 3', 'MAX_RUNTIME_RETRIES = 1',
            'MAX_CHECKPOINT_VERIFY_ROUNDS = 2', 'MAX_DEBUG_HERE_ATTEMPTS = 1',
            'MAX_TOTAL_CORRECTION_ROUNDS = 8', 'WALL_CLOCK_HOURS = 3']

    def setUp(self):
        self.raw = raw(self.RUN)
        self.run = flat(self.RUN)
        self.sizing = self.run[self.run.index('## 17. Sizing and heads'):]

    def test_the_run_tree_lists_sizing_json(self):
        tree = self.raw.split('## 1. Principles')[0]
        self.assertRegex(tree, r'(?m)^    ├── sizing\.json +# .*\(§17\.3\)$')
        self.assertLess(tree.index('sizing.json'), tree.index('run-state.json'))

    def test_section_17_and_its_five_parts(self):
        headings = [line for line in self.raw.split('\n') if line.startswith('#')]
        self.assertEqual(headings[-6:], ['## 17. Sizing and heads', '### 17.1 The four sizes',
                                         '### 17.2 Order of decision', '### 17.3 sizing.json',
                                         '### 17.4 The ratchet', '### 17.5 The spike outcome'])
        self.assertIn('(`../skills/feature-development/SKILL.md`, step 3)', self.sizing)
        self.assertIn('never a pause, and no agent is dispatched', self.sizing)

    def test_the_four_sizes(self):
        sizes = between(self.sizing, '### 17.1', '### 17.2')
        for row in ('| `spike` | `none` (§17.5) |', '| `bug` | `bug` — `heads/bug.md` |',
                    '| `bounded` | `lean` — `heads/lean.md` |',
                    '| `architectural` | `full` — `heads/full.md` |'):
            self.assertEqual(sizes.count(row), 1, row)

    def test_the_order_of_decision_and_the_doubt_rule(self):
        order = between(self.sizing, '### 17.2', '### 17.3')
        rules = ('1. **A flag.**', '2. **A recorded sizing**', '3. **What exists.**',
                 '4. **Judgement**')
        at = [order.index(rule) for rule in rules]
        self.assertEqual(at, sorted(at))
        for phrase in ('`--head=full` → `architectural`, `--head=lean` → `bounded`, '
                       '`--head=bug` → `bug`',
                       "The person's flag is the only thing that lowers a size.",
                       'unless it is a spike already answered (§17.5)',
                       'A PRD or a plan, at either scope → `architectural`.',
                       '`spike` < `bounded` < `architectural`',
                       '`bug` wins any doubt it is part of'):
            self.assertIn(phrase, order)

    def test_the_record(self):
        import json
        record = between(self.raw, '### 17.3 sizing.json', '### 17.4')
        example = json.loads(between(record, '```json\n', '```')[len('```json\n'):])
        self.assertEqual(list(example), self.KEYS)
        self.assertEqual(re.findall(r'(?m)^\| `([a-z_]+)` \|', record), self.KEYS)
        for phrase in ('`.artel/run/<TICKET_ID>/sizing.json`, written by the orchestrator at '
                       'sizing, before any head',
                       'It is written in `--step` runs too: it is not run state.',
                       '| `decided_by` | `flag`, `existing` (rule 3), `judgement` or `raised` |'):
            self.assertIn(phrase, re.sub(r'\s+', ' ', record))

    def test_the_contract_and_the_skill_print_the_same_lines(self):
        skill = flat(FD + 'SKILL.md')
        for line in self.LINES:
            self.assertIn(line, self.sizing, line)
            self.assertIn(line, skill, line)

    def test_the_ratchet_and_the_spike_outcome(self):
        ratchet = between(self.sizing, '### 17.4', '### 17.5')
        for phrase in ('A head may raise the size, never lower it',
                       'the bug head when the diagnosis is `DIAGNOSED_STRUCTURAL`',
                       'The ratchet stops at arming',
                       'One move is not a raise: on a `NOT_REPRODUCED` diagnosis'):
            self.assertIn(phrase, ratchet)
        spike = self.sizing[self.sizing.index('### 17.5'):]
        for phrase in ('A `spike` runs no head.', '`<specs.dir>/<TICKET_ID>/spike.md`',
                       'sets `answered: true` in `sizing.json` and stops',
                       'Nothing is armed, journaled or committed.',
                       'an answered spike is not a recorded sizing'):
            self.assertIn(phrase, spike)

    def test_an_armed_run_is_defined_and_skips_the_head(self):
        state = between(self.run, '## 2. `run-state.json`', '## 3.')
        self.assertIn('An **armed run** is one whose file has `run_active: true` and '
                      '`gates_confirmed` holding `TASKLIST_READY`.', state)
        self.assertIn('skips the import, sizing and every head (§17.2)', state)
        self.assertIn('An armed run (§2) is not sized.', self.sizing)
        self.assertIn('A run armed before 0.25.0 has no `sizing.json` and resumes the same way, '
                      'through either command.', self.sizing)

    def test_the_debug_option_is_the_one_new_cap(self):
        caps = between(self.run, '## 5. Capped loops', '## 6.')
        table = between(self.raw, '| Loop | Cap (default) | Counter location |', '\n\n')
        self.assertEqual(re.findall(r'`([A-Z_]+ = \d+)`', table), self.CAPS)
        for phrase in ('| debug it here first (per halt, below) | `MAX_DEBUG_HERE_ATTEMPTS = 1` |',
                       '**Debug it here first.** On three cap escalations',
                       'a task aborted when `MAX_VERIFY_ITERATIONS` ran out, a checkpoint still '
                       'red after `MAX_CHECKPOINT_VERIFY_ROUNDS`, and the runtime gate\'s second '
                       'red from an app-code error',
                       'Not the review cap, not an environment error',
                       '`debugged here: <root cause> — <files>`',
                       'one attempt per halt',
                       '(`../skills/feature-development/tail.md`, `## Debug it here first`)'):
            self.assertIn(phrase, caps)

    def test_the_completion_gate_reads_what_exists(self):
        gate = between(self.run, '## 7. Completion gate', '## 8.')
        for phrase in ('The orchestrator confirms eight facts itself',
                       '`PLAN_APPROVED` (`skipped (no plan)` when the ticket has no plan',
                       'or `gates_confirmed` for a tasklist that declares none',
                       'and every fix-section parent closed',
                       '`skipped (no PRD)` when the ticket has no PRD',
                       '(`../skills/feature-development/tail.md`, `## Completion gate`)',
                       'A fact that reads `skipped` is green.',
                       'never on the head that ran'):
            self.assertIn(phrase, gate)

    def test_the_pause_the_checkpoints_and_headless_name_one_orchestrator(self):
        for phrase in ('(`/artel:dev` is its alias until 0.26.0)',
                       '**One approval pause.** The full head: plan+tasklist approval. The lean '
                       'head and the bug head: the work-list approval (§17).',
                       "(the full head's pause on approve, the lean head's confirmation, the bug "
                       "head's work-list approval)",
                       'gate 0.5 of the full head (`heads/full.md`)',
                       'The orchestrator commits and pushes at fixed checkpoints',
                       '(`../skills/feature-development/tail.md`, `## Checkpoint commits & '
                       'pushes`)',
                       "`feature-development`'s tail runs the parser and `task_create`s its rows",
                       "in the orchestrator's implementation loop (gate 5, `tail.md`)",
                       'the phase review (gate 7, `tail.md`)',
                       "(the full head's pause, the lean head's confirmation)",
                       'Sizing (§17) never pauses, so it needs no answer',
                       'run start (with the §10 mode resolution and the §17 size)'):
            self.assertIn(phrase, self.run)

    def test_no_second_orchestrator_is_left(self):
        self.assertEqual(self.raw.count('/artel:dev'), 1)
        for gone in ('`dev`', 'dev §2', 'oth orchestrators', 'both entry-point',
                     'feature-development §', '`feature-development` step'):
            self.assertNotIn(gone, self.run)


if __name__ == '__main__':
    unittest.main()
