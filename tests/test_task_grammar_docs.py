"""The task grammar (sub-project 2a, 0.23.0): `docs/task-grammar.md` states the grammar that
`scripts/task_grammar.py` enforces, and the writers, the plan review and the execution side
cite it. Plan 1 pins the contract against the code; plans 2 and 3 add a class each for the
documents they change."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import task_grammar  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return re.sub(r'\s+', ' ', text)


def section(doc, heading):
    """The text of one `## ` or `### ` section, up to the next heading of its level."""
    level = heading.split(' ', 1)[0]
    start = doc.index(heading + '\n')
    rest = doc[start + len(heading) + 1:]
    match = re.search(r'(?m)^{} '.format(re.escape(level)), rest)
    return rest[:match.start()] if match else rest


def table_rules(text):
    """{rule: first other cell} for every `| \\`rule\\` | … |` row of a table."""
    rows = {}
    for match in re.finditer(r'(?m)^\| `([a-z-]+)` \| ([^|]+)\|', text):
        rows[match.group(1)] = match.group(2).strip()
    return rows


class TestGrammarContract(unittest.TestCase):
    def setUp(self):
        self.doc = read('docs/task-grammar.md')

    def test_sections(self):
        for heading in ('## 1. The task block', '## 2. Field rules',
                        '## 3. What stays and what goes',
                        '## 4. Format detection and the old format',
                        '## 5. IDs stay out of the product',
                        '## 6. Validation and the plan check',
                        '## 7. Rows, waves, readiness and route floors'):
            self.assertIn(heading + '\n', self.doc)
        for field in ('### 2.1 `Files:` (required)', '### 2.2 `Depends on:` (required)',
                      '### 2.3 `Route:` (required)', '### 2.4 `Test:` (required)',
                      '### 2.5 `Produces:` (optional)',
                      '### 2.6 `Implements:` (required when the PRD has requirements)'):
            self.assertIn(field + '\n', self.doc)

    def test_the_example_uses_every_field_and_parses_without_warnings(self):
        # The first `markdown` fence of the document is §1's example. (section() would
        # stop at the example's own `## Iteration` line, which sits inside the fence.)
        example = self.doc.split('```markdown\n', 1)[1].split('\n```', 1)[0]
        iterations, _, warnings = task_grammar.parse(example)
        self.assertEqual(warnings, [])
        task = iterations[0]['tasks'][0]
        self.assertEqual(set(task['fields']), set(task_grammar.KNOWN_FIELDS))
        self.assertEqual(task['hitl'], 'copy needs product sign-off')

    def test_the_grammar_rule_table_is_the_code_s(self):
        rows = table_rules(section(self.doc, '### 6.1 Grammar problems'))
        self.assertEqual(set(rows), set(task_grammar.GRAMMAR_RULES))

    def test_the_check_rule_table_is_the_code_s_with_its_severities(self):
        rows = table_rules(section(self.doc, '### 6.2 The plan check (`--check`)'))
        self.assertEqual(rows, task_grammar.CHECK_RULES)

    def test_the_approval_reason_is_the_code_s(self):
        self.assertIn('`<light|full> — {}`'.format(task_grammar.APPROVAL_REASON), self.doc)

    def test_the_floor_threshold_is_the_code_s(self):
        self.assertIn('`ROUTE_FULL_FILES = {}`'.format(task_grammar.ROUTE_FULL_FILES), self.doc)
        self.assertIn('more than {} files (<n>)'.format(task_grammar.ROUTE_FULL_FILES), self.doc)

    def test_the_invocations_match_the_cli(self):
        doc = flat(self.doc)
        for phrase in ('tasklist_tasks.py requirements --prd <prd-path>',
                       '--check --requirements <ids>',
                       'spec_store.py get <prd-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd -',
                       '`absent` when there is no PRD or `data.present` is false'):
            self.assertIn(phrase, doc)

    def test_rows_and_readiness(self):
        doc = flat(section(self.doc, '## 7. Rows, waves, readiness and route floors'))
        for phrase in ('`I<N> · <N.M> · <task title>`', 'the idempotency key, capped at 500',
                       'the steps without ticks', '`data.waves`', '`data.ready_now`',
                       'in the first iteration that has a task not done',
                       'is known only at runtime and is the orchestrator\'s',
                       'a route whose reason is `set at approval` is final over these three'
                       ' floors'):
            self.assertIn(phrase, doc)

    def test_old_format_and_ids(self):
        doc = flat(self.doc)
        self.assertIn('The old format stays until sub-project 2b ships', doc)
        self.assertIn('never appear in code, tests, identifiers, comments or commit subjects',
                      doc)
        self.assertIn('The fix sections are outside this grammar.', doc)


# ---------------------------------------------------------------------------
# Plan 2 of sub-project 2a: the analyst's requirements, the writers, the
# reviewer's plan mode, gate 4.2, the dev-path check, sync-phases and
# /artel:tasks add. Prompts have no code path, so a phrase dropped here fails a
# test instead of a run nobody is watching. Helpers carry a `_w_` prefix so they
# never shadow a helper of plan 1's class above.
# ---------------------------------------------------------------------------
import re as _w_re
import unittest
from pathlib import Path as _W_Path

_W_ROOT = _W_Path(__file__).resolve().parent.parent


def _w_raw(rel):
    return (_W_ROOT / rel).read_text(encoding='utf-8')


def _w_flat(rel):
    """`rel` with whitespace runs collapsed, so re-wrapping a paragraph never breaks a pin."""
    return _w_re.sub(r'\s+', ' ', _w_raw(rel))


def _w_between(text, start, end=None):
    """The text after the first `start`, up to the first `end` after it (the rest when None)."""
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end else body


class TestAnalystRequirements(unittest.TestCase):
    """Spec §2: the PRD's `## Requirements` section, required for PRD_READY."""

    def setUp(self):
        self.analyst = _w_flat('agents/analyst.md')
        self.section = _w_between(self.analyst, '## Requirements in the PRD', '## Rules')

    def test_the_section_is_listed_and_required_for_prd_ready(self):
        output = _w_between(self.analyst, '## Output', '## Requirements in the PRD')
        self.assertIn('`## Requirements` — the numbered list every task traces to', output)
        self.assertIn('once open questions are empty and `## Requirements` is written', output)
        self.assertIn('`PRD_READY` requires the section', self.section)

    def test_an_entry_is_an_id_a_statement_and_an_accepts_when_line(self):
        for phrase in ('- **R1** — A completed checkout shows a receipt with the order total.',
                       '*Accepts when:* checking out with the test card shows the receipt'
                       ' with the total.',
                       'is one line, never wrapped'):
            self.assertIn(phrase, self.section)

    def test_entries_come_from_what_the_prd_already_says(self):
        for phrase in ('the user stories, the scenarios, the success criteria',
                       'every Resolved Question that binds scope'):
            self.assertIn(phrase, self.section)

    def test_ids_are_stable_and_the_markers_are_spelled_once(self):
        for phrase in ('never renumbered and never reused', 'takes the next free number',
                       '`(withdrawn — <reason>)` at the end of its first line',
                       '`(already met — <evidence>)` there instead',
                       "The plan review's coverage skips both"):
            self.assertIn(phrase, self.section)

    def test_a_phase_prd_owns_its_ids(self):
        self.assertIn('numbers its own requirements from `R1`', self.section)
        self.assertIn('`phase-<PHASE_NUM>/prd.md`', self.section)

    def test_ids_stay_out_of_the_product(self):
        self.assertIn('never in code, tests, identifiers, comments or commit subjects',
                      self.section)
        self.assertIn('docs/task-grammar.md` §5', self.section)

    def test_the_only_amendment_after_prd_ready_is_the_already_met_marker(self):
        after = _w_between(self.section, '**After `PRD_READY`.**')
        self.assertIn("to the end of that requirement's first line and change nothing else",
                      after)
        self.assertIn('the status stays `PRD_READY`', after)

    def test_rewriting_a_prd_keeps_its_ids(self):
        rules = _w_between(self.analyst, '## Rules')
        self.assertIn('**Requirement IDs carry over.**', rules)

    def test_the_analysis_skill_asks_for_the_section(self):
        finalize = _w_between(_w_flat('skills/analysis/SKILL.md'),
                              '### Phase 3: Finalize the PRD', '### Completion')
        for phrase in ('metrics, requirements, risks', 'Write `## Requirements`',
                       '`PRD_READY` requires it'):
            self.assertIn(phrase, finalize)


class TestTaskPlannerWritesTheGrammar(unittest.TestCase):
    """Spec §7: task-planner writes task blocks; per-task acceptance criteria become
    `Test:` and `Implements:`; the phase-scoped output uses the same blocks."""

    def setUp(self):
        self.agent = _w_flat('agents/task-planner.md')
        self.output = _w_between(self.agent, '## Output', '## Rules')
        self.rules = _w_between(self.agent, '## Rules', '## Fix rounds and fold-backs')

    def test_both_outputs_are_written_in_the_grammar(self):
        for phrase in ('`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md`',
                       'a `### Task <N>.<m>: <title>` heading, its field bullets, then its steps',
                       'one `## Iteration <N>: <title>` section per phase / iteration',
                       'one `## Iteration <PHASE_NUM>: <title>` section',
                       'numbered `<PHASE_NUM>.1`, `<PHASE_NUM>.2`, …',
                       'no `## Final Verification` section'):
            self.assertIn(phrase, self.output)

    def test_the_example_carries_every_field(self):
        for field in ('- **Files:** `src/checkout/receipt_view.py` (new)',
                      '- **Depends on:** none', '- **Depends on:** 2.1', '- **Route:** light',
                      '- **Route:** full — payment path',
                      '- **Test:** `tests/checkout/test_receipt_view.py`',
                      '- **Produces:** `ReceiptView.render(order)` — class method',
                      '- **Implements:** R1',
                      '### Task 2.2: Show the receipt after checkout [HITL: '):
            self.assertIn(field, self.output)

    def test_the_shapeless_output_is_gone(self):
        for retired in ('a list of tasks with checkboxes', 'acceptance criteria for each task',
                        '**Acceptance criteria** — For each task', 'note the dependency',
                        '`- [ ] [HITL: <reason>] <task text>`'):
            self.assertNotIn(retired, self.agent)

    def test_rules_carry_the_fields_the_trace_and_the_order(self):
        for phrase in ('**Every task carries the required fields**',
                       '**`Test:` is the acceptance criterion.**',
                       '**`Implements:` traces the PRD.**', 'no task carries `Implements:`',
                       '**Dependencies are declared, never implied.**',
                       'Never a task of another iteration',
                       '**Steps state intent, not code.**', '**No gate tasks.**',
                       '`### Task <N>.<m>: <title> [HITL: <reason>]`'):
            self.assertIn(phrase, self.rules)

    def test_already_met_is_an_open_question(self):
        for phrase in ('**Already met is a question, not a gap.**',
                       '`Is R<n> already met by the current code?`', '`from: tasklist`'):
            self.assertIn(phrase, self.rules)

    def test_fix_rounds_and_fold_backs_edit_the_fields(self):
        section = _w_between(self.agent, '## Fix rounds and fold-backs')
        for phrase in ('.artel/run/<TICKET_ID>/plan-review.md',
                       'keep the grammar, the header and `status: TASKLIST_READY`',
                       'the fields as well as the prose', 'task numbers stay contiguous'):
            self.assertIn(phrase, section)

    def test_the_tasklist_skill_asks_for_task_blocks(self):
        self.assertIn('write every task as a task block in the task grammar',
                      _w_flat('skills/tasklist/SKILL.md'))

    def test_a_task_grammar_tasklist_is_not_mirrored_before_approval(self):
        # Spec §4 as amended while planning: gate 4.2 and the approval fold-back can
        # renumber or retitle tasks, and create-only rows would outlive them (the
        # AW-3342 D4 class), so the first mirror of a task-grammar tasklist is the
        # orchestrator's re-mirror after approval.
        skill = _w_flat('skills/tasklist/SKILL.md')
        for phrase in ('Skipped too for a task-grammar tasklist',
                       'whose plan is not yet `PLAN_APPROVED`',
                       "the queue's rows are create-only",
                       "the orchestrator's re-mirror before the first implementer dispatch"
                       ' creates them once the plan is approved',
                       'or an old-format tasklist, mirrors here as before'):
            self.assertIn(phrase, skill)


class TestTasklistWriterWritesTheGrammar(unittest.TestCase):
    """Spec §7: tasklist-writer drops the file headings and `### After changes`, writes task
    blocks, reads the PRD only for its IDs, and drafts into run state for the plan check."""

    def setUp(self):
        self.agent = _w_flat('agents/tasklist-writer.md')

    def test_the_template_is_task_blocks(self):
        template = _w_between(self.agent, '### Required structure',
                              '### Rules for the Progress Report table')
        for phrase in ('### Task 1.1: {imperative title}', '- **Depends on:** none',
                       '- **Depends on:** 1.1', '- **Route:** light',
                       '- **Route:** full — {why this task needs its own review}',
                       '- **Test:** none — {why no test can pin it}',
                       '- **Implements:** {requirement IDs — only when the PRD has'
                       ' `## Requirements`}',
                       '**Test:** {how the developer verifies this iteration end-to-end'):
            self.assertIn(phrase, template)
        for retired in ('### `{repo-relative file path}`', '### After changes', '(new file)'):
            self.assertNotIn(retired, template)

    def test_file_grouping_and_after_changes_are_gone(self):
        for retired in ('**Group tasks by file.**', 'Include the "After changes" checklist',
                        'Call this out in the "After changes" checklist', '(new file)',
                        'subheading must be a path', '`- [ ] [HITL: <reason>] <task text>`'):
            self.assertNotIn(retired, self.agent)
        self.assertIn('no `### After changes` checklist', self.agent)

    def test_every_task_carries_the_fields_and_implements_follows_the_prd(self):
        for phrase in ('**Tasks are blocks, not file groups.**',
                       '**Every task carries the required fields**', 'and on none otherwise',
                       '**Every task ends green.**',
                       '`### Task <N>.<m>: <title> [HITL: <reason>]`'):
            self.assertIn(phrase, self.agent)

    def test_it_reads_the_prd_for_its_ids_only(self):
        inputs = _w_between(self.agent, '## Input', '## Output')
        self.assertIn("read its `## Requirements` section for the IDs your tasks'"
                      " `Implements:` lines cite", inputs)
        self.assertIn("the PRD's path or `none`", inputs)

    def test_the_draft_lives_in_run_state_and_takes_fix_rounds(self):
        workflow = _w_between(self.agent, '## Per-run workflow', '## KISS rules')
        for phrase in ('.artel/run/<TICKET_ID>/tasklist-draft.md',
                       'Never write `<specs.dir>/<TICKET_ID>/tasklist.md` in this step',
                       '### Step 1b — Fix the draft', 'This happens at most twice',
                       'the fields as well as the prose'):
            self.assertIn(phrase, workflow)
        self.assertNotIn('Draft the tasklist in memory', self.agent)


class TestReviewerPlanMode(unittest.TestCase):
    """Spec §6.2 and §2: the reviewer's plan mode, `run-reviewer --plan`, the acceptance
    table per requirement, and an ID in code reported as Minor."""

    def setUp(self):
        self.agent = _w_flat('agents/reviewer.md')
        self.plan = _w_between(self.agent, '## Plan mode', '## Review focus')
        self.skill = _w_flat('skills/run-reviewer/SKILL.md')

    def test_the_role_names_four_modes(self):
        role = _w_between(self.agent, '## Role', '## Phase support')
        for phrase in ('Four modes:', '- **plan** — the tasklist before the approval pause',
                       'Task mode and plan mode are never assumed'):
            self.assertIn(phrase, role)

    def test_plan_mode_sits_between_task_mode_and_the_review_focus(self):
        raw = _w_raw('agents/reviewer.md')
        self.assertLess(raw.index('\n## Task mode\n'), raw.index('\n## Plan mode\n'))
        self.assertLess(raw.index('\n## Plan mode\n'), raw.index('\n## Review focus'))

    def test_the_eight_points(self):
        for point in ('1. **Delivery.**', '2. **Clarity.**', '3. **Size and route.**',
                      '4. **Dependencies.**', '5. **Tests.**', '6. **Intent, not code.**',
                      '7. **Decisions honoured.**', '8. **Scope.**'):
            self.assertIn(point, self.plan)

    def test_the_parser_s_rules_are_not_re_reported(self):
        self.assertIn('so never re-report those', self.plan)
        self.assertIn('docs/task-grammar.md` §6', self.plan)

    def test_findings_carry_severity_where_what_why_and_the_smallest_fix(self):
        for phrase in ('- **Critical** —', '- **Important** —', '- **Minor** —',
                       'where (the task number, or the iteration, or the whole tasklist)',
                       'the smallest fix that resolves it', 'marked `(repeat)`'):
            self.assertIn(phrase, self.plan)

    def test_the_evidence_file_and_its_round(self):
        for phrase in ('.artel/run/<TICKET_ID>/plan-review.md', 'no document header',
                       '**Tasklist:** <the tasklist path you reviewed>',
                       '**Plan-review round:** <k>', "`k` is the previous file's round plus one",
                       'or when its `**Tasklist:**` line names another tasklist',
                       'Plan review round <k>: <c> Critical, <i> Important, <m> Minor — '
                       '.artel/run/<TICKET_ID>/plan-review.md'):
            self.assertIn(phrase, self.plan)

    def test_plan_mode_writes_nothing_else(self):
        self.assertIn('plan mode edits no spec document, appends to no fix section', self.plan)

    def test_the_acceptance_table_is_one_row_per_requirement(self):
        output = _w_between(self.agent, '### Output', '### Deviation check')
        for phrase in ("one row per requirement of the PRD's `## Requirements` section",
                       'keeps one row per acceptance criterion of the work list',
                       'or reads `none`'):
            self.assertIn(phrase, output)
        self.assertNotIn("one row per criterion of the PRD's acceptance section", self.agent)

    def test_an_id_in_code_is_a_minor_finding(self):
        focus = _w_between(self.agent, '## Review focus', '## Review lenses')
        for phrase in ('**IDs stay out of the product**', '**Minor** convention finding',
                       'never a fix task', 'docs/task-grammar.md` §5'):
            self.assertIn(phrase, focus)

    def test_run_reviewer_dispatches_plan_mode(self):
        for phrase in ('--package <path> | --plan] [--local]', '### Plan mode (`--plan`)',
                       '**Mode: plan**', 'skip `## Record the fix tasks` below',
                       "In plan mode relay the agent's one line verbatim"):
            self.assertIn(phrase, self.skill)


class TestGateFourTwoPlanReviewed(unittest.TestCase):
    """Spec §6.1, §6.3, §6.4: gate 4.2 in feature-development and the pause around it."""

    def setUp(self):
        self.raw = _w_raw('skills/feature-development/SKILL.md')
        self.fd = _w_flat('skills/feature-development/SKILL.md')
        self.gate = _w_between(self.fd, '#### Gate 4.2 — the plan review', '### 3. THE ONE PAUSE')
        self.pause = _w_between(self.fd, '### 3. THE ONE PAUSE', '### 4. Arm the run')

    def test_the_row_sits_between_the_tasklist_and_phase_extraction(self):
        self.assertLess(self.raw.index('| 4 | `TASKLIST_READY`'),
                        self.raw.index('| 4.2 | `PLAN_REVIEWED`'))
        self.assertLess(self.raw.index('| 4.2 | `PLAN_REVIEWED`'), self.raw.index('| 4.5 |'))

    def test_it_runs_on_the_grammar_before_approval_in_the_chatty_head(self):
        for phrase in ("while the plan's status is not `PLAN_APPROVED`",
                       'A resume after approval never re-runs it',
                       '`PLAN_REVIEWED: skipped (old-format tasklist)`',
                       'never count toward `counters.correction_rounds`'):
            self.assertIn(phrase, self.gate)

    def test_the_requirements_read_feeds_the_mechanical_check(self):
        for phrase in ('tasklist_tasks.py requirements --prd <prd-path>',
                       'tasklist_tasks.py requirements --prd -',
                       '`present: false` → `absent`',
                       '`present: true` with an empty `ids` → `none`',
                       'tasklist_tasks.py --tasklist <tasklist-path> --ticket-key <TICKET_ID>'
                       ' --check --requirements <requirements>',
                       'tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>'
                       ' --check --requirements <requirements>'):
            self.assertIn(phrase, self.gate)

    def test_the_agent_half_and_the_capped_rounds(self):
        for phrase in ('`Skill: run-reviewer --plan` with `$0`',
                       '`MAX_PLAN_REVIEW_ROUNDS = 2`', 'the `task-planner` agent',
                       'Minor findings never start a round'):
            self.assertIn(phrase, self.gate)

    def test_an_already_met_question_waits_for_the_pause(self):
        self.assertIn('`Is R<n> already met by the current code?`', self.gate)
        self.assertIn('the pause answers it', self.gate)

    def test_a_stale_or_resumed_review_keeps_its_count_honest(self):
        for phrase in ('whose `**Tasklist:**` line names another tasklist is stale',
                       'has used its rounds', 'the reviewer continues the round count'):
            self.assertIn(phrase, self.gate)

    def test_the_pause_shows_what_is_open_and_the_old_prd_line(self):
        for phrase in ("the plan review's open findings as their own section",
                       'no requirement coverage — the PRD predates requirement IDs'):
            self.assertIn(phrase, self.pause)

    def test_the_fold_back_reaches_the_analyst_and_rechecks(self):
        approve = _w_between(self.pause, '- **Approve** →', '- **Request changes** →')
        for phrase in ('fields as well as its prose', 'goes to the `analyst` agent instead',
                       '`(already met — <evidence>)`',
                       "re-run gate 4.2's requirements read and mechanical check"):
            self.assertIn(phrase, approve)

    def test_request_changes_restarts_the_review(self):
        request = _w_between(self.pause, '- **Request changes** →', '- **`yolo` only:**')
        self.assertIn('delete `.artel/run/<TICKET_ID>/plan-review.md`', request)

    def test_yolo_still_stops_on_a_surviving_finding(self):
        yolo = _w_between(self.pause, '- **`yolo` only:**')
        self.assertIn('a Critical or Important plan-review finding still open', yolo)
        self.assertIn('guardrail, not a pause preference', yolo)

    def test_the_run_start_entry_records_the_outcome(self):
        arm = _w_between(self.fd, '### 4. Arm the run', 'Then run the **planning checkpoint**')
        self.assertIn("the plan review's outcome", arm)


class TestDevPathPlanCheck(unittest.TestCase):
    """Spec §6.1: the mechanical half on the dev path — generate-tasklist's draft and ladder
    branch 1's existing tasklist; branch 3 stays in the old format."""

    def setUp(self):
        self.gen = _w_flat('skills/generate-tasklist/SKILL.md')
        self.check = _w_between(self.gen, '### Phase 1b: Check the draft', '### Phase 2:')
        self.ladder = _w_between(_w_flat('skills/dev/SKILL.md'), '### 2. Input ladder',
                                 '### 3. Arm the run')

    def test_the_draft_is_checked_before_the_approval_round(self):
        raw = _w_raw('skills/generate-tasklist/SKILL.md')
        self.assertLess(raw.index('### Phase 1b: Check the draft'),
                        raw.index('### Phase 2: Ask User Questions'))
        for phrase in ('tasklist_tasks.py --tasklist .artel/run/<TICKET_ID>/tasklist-draft.md'
                       ' --ticket-key <TICKET_ID> --check --requirements <requirements>',
                       'tasklist_tasks.py requirements --prd <specs.dir>/<TICKET_ID>/prd.md',
                       '`present: false` → `absent`'):
            self.assertIn(phrase, self.check)

    def test_no_prd_means_absent_without_a_read(self):
        self.assertIn('No PRD (the **PRD.** check in Phase 1) → `<requirements>` is `absent`',
                      self.check)
        self.assertIn('`spec_store.py exists <specs.dir>/<TICKET_ID>/prd.md`', self.gen)
        self.assertIn('No PRD there', self.ladder)

    def test_at_most_two_fix_rounds_to_the_same_writer(self):
        self.assertIn('`MAX_PLAN_REVIEW_ROUNDS = 2`', self.check)
        self.assertIn('Minor findings never start a round', self.check)
        rules = _w_between(self.gen, '## Important Rules')
        self.assertIn('at most `MAX_PLAN_REVIEW_ROUNDS` fix-round `SendMessage`s in Phase 1b',
                      rules)

    def test_the_writer_is_asked_for_task_blocks_and_given_the_prd(self):
        for phrase in ('**PRD (input, requirement IDs only):**', 'task blocks in the task grammar',
                       'Write the draft to .artel/run/<TICKET_ID>/tasklist-draft.md'):
            self.assertIn(phrase, self.gen)
        for retired in ('file-grouped checkbox tasks', 'checkbox tasks grouped by file'):
            self.assertNotIn(retired, self.gen)

    def test_the_written_tasklist_is_checked_once_more(self):
        phase3 = _w_between(self.gen, '### Phase 3:', '### Phase 4:')
        self.assertIn('the answers folded in can break what the draft check passed', phase3)
        self.assertIn('tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>'
                      ' --check --requirements <requirements>', phase3)

    def test_branch_one_checks_and_shows_without_a_round(self):
        for phrase in ('tasklist_tasks.py --tasklist <tasklist-path> --ticket-key <TICKET_ID>'
                       ' --check --requirements <requirements>',
                       'There is no automatic fix round here',
                       '`plan check: not run (<error.kind>)`'):
            self.assertIn(phrase, self.ladder)

    def test_branch_three_stays_in_the_old_format(self):
        self.assertIn('This work list stays in the old checkbox format', self.ladder)

    def test_yolo_still_presents_a_critical_or_important_finding(self):
        self.assertIn('a Critical or Important plan-check finding still presents the'
                      ' confirmation', self.ladder)

    def test_the_approval_round_shows_routes_and_a_change_is_set_at_approval(self):
        # Spec §5.3 on the dev path's branch 2: generate-tasklist's approval round is the
        # pause, so it shows the routes, and the writer records a change.
        phase2 = _w_between(self.gen, '### Phase 2:', '### Phase 3:')
        for phrase in ("lists every task's effective route with its reasons",
                       'tasklist_tasks.py --tasklist .artel/run/<TICKET_ID>/tasklist-draft.md'
                       ' --ticket-key <TICKET_ID>',
                       'down as well as up', '`Route: <light|full> — set at approval`'):
            self.assertIn(phrase, phase2)
        self.assertIn('A route the person changed is written'
                      ' `Route: <light|full> — set at approval`',
                      _w_flat('agents/tasklist-writer.md'))


class TestSyncPhasesCopiesTheIteration(unittest.TestCase):
    """Spec §7: a task-grammar phase file is the iteration, verbatim; write-back syncs step
    ticks; old-format tasklists keep today's extraction."""

    def setUp(self):
        self.skill = _w_flat('skills/sync-phases/SKILL.md')
        self.extract = _w_between(self.skill, '### Step 6', '### Step 7')
        self.writeback = _w_between(self.skill, '### Step 4', '### Step 5')

    def test_format_is_detected_per_tasklist(self):
        step2 = _w_between(self.skill, '### Step 2', '### Step 3')
        self.assertIn('any `### Task <N>.<m>:` heading → the task grammar', step2)
        self.assertIn('docs/task-grammar.md` §4', step2)

    def test_a_grammar_phase_file_is_the_iteration_verbatim(self):
        for phrase in ('**Task grammar.**', 'copied, never re-derived',
                       "<the tasklist's `## Iteration N: Title` section, verbatim>",
                       "its `## Iteration N:` heading (the parser finds the phase's tasks"
                       " under it)",
                       'the extraction copies none from the tasklist',
                       'title: "Phase N: Title"', 'produced_by: artel:sync-phases',
                       'name="phase-<N>.tasks.md"'):
            self.assertIn(phrase, self.extract)

    def test_the_old_format_keeps_its_derived_extraction(self):
        old = _w_between(self.extract, '**Old format.**')
        for phrase in ('## Context', '## Technical Details', 'Phase N-1 complete'):
            self.assertIn(phrase, old)

    def test_write_back_syncs_step_ticks_by_task_number(self):
        for phrase in ('matched by task number and step text, never by position',
                       'a step with no twin is reported, never guessed',
                       'The Progress Report counts tasks',
                       'fix-section boxes stay in the file their writer put them in'):
            self.assertIn(phrase, self.writeback)


class TestTasksAddWritesATaskBlock(unittest.TestCase):
    """Spec §7 and §8: `/artel:tasks add` writes in the tasklist's own format; the check runs
    before the row; `done` ticks a whole task block; `add --fix` is unchanged."""

    FLAGS = ('--iteration N [--files <paths>] [--depends <tasks>] [--route <route>]'
             ' [--test <paths>] [--section <name>]')

    def setUp(self):
        self.skill = _w_flat('skills/tasks/SKILL.md')
        self.add = _w_between(self.skill, '### `add <ticket> "<title>" --iteration N',
                              '### `add <ticket> "<title>" --fix')

    def test_the_hint_and_the_heading_name_the_field_flags(self):
        hint = [ln for ln in _w_raw('skills/tasks/SKILL.md').splitlines()
                if ln.startswith('argument-hint:')][0]
        self.assertIn(self.FLAGS, hint)
        self.assertIn('### `add <ticket> "<title>" ' + self.FLAGS, self.skill)

    def test_the_format_decides_the_branch_and_refuses_the_other_flags(self):
        for phrase in ('docs/task-grammar.md` §4',
                       '`--section` places a checkbox in an old-format tasklist',
                       'those flags write a task block'):
            self.assertIn(phrase, self.add)

    def test_a_task_block_with_its_title_as_its_one_step(self):
        for phrase in ('### Task <N>.<m>: <title>',
                       "The title is both the heading and the task's one step",
                       'Ask for every required field still missing in one `AskUserQuestion`',
                       'Never invent a value'):
            self.assertIn(phrase, self.add)

    def test_no_prd_means_absent(self):
        self.assertIn('`<specs.dir>/<TICKET_ID>/prd.md` absent', self.add)
        self.assertIn('`<requirements>` is `absent`', self.add)

    def test_the_check_runs_before_the_row_and_a_failure_restores_the_file(self):
        check = _w_between(self.add, '**Check before the row.**', '3. **Mirror**')
        for phrase in ('--check --requirements <requirements>',
                       'take the block back out of every file you wrote it to',
                       'no row is created'):
            self.assertIn(phrase, check)

    def test_a_grammar_row_s_status_follows_ready_now(self):
        self.assertIn('`ready` when the new task is in `data.ready_now`', self.add)
        self.assertIn('`I<N> · <N>.<m> · <title>` in the task grammar', self.add)

    def test_done_ticks_every_step_of_a_task_block(self):
        done = _w_between(self.skill, '### `done <task-id>`', '### `block')
        self.assertIn('tick every step of the `### Task <N>.<m>:` block', done)

    def test_add_fix_is_unchanged(self):
        fix = _w_between(self.skill, '### `add <ticket> "<title>" --fix', '### `done')
        for flag in ('--files', '--depends', '--route', '--test'):
            self.assertNotIn(flag, fix)


# --- Plan 3 of sub-project 2a: execution, promotion, routes, readers, release docs ----------
# Reuses plan 1's module helpers `read(rel)` and `flat(text)`. The names below carry a leading
# underscore so they can never shadow a helper another plan's classes rely on.

import re as _re
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent


def _between(text, start, end=None):
    """The text after the first `start`, up to the first `end` after it (to the end when None)."""
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end else body


class TestOneTaskPerDispatch(unittest.TestCase):
    """Plan 3, Task 1: one dispatch works one whole task block (spec §4)."""

    def setUp(self):
        self.agent = flat(read('agents/implementer.md'))

    def test_step_one_takes_a_task_block(self):
        step = _between(self.agent, '### Step 1', '### Step 2')
        for phrase in ('**A task-format tasklist**', '`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §4',
                       '`I<N> · <N.M> · <title>`', 'take the first task of `data.ready_now`',
                       'never the first unticked box', '`tasklist_malformed`',
                       'is a `DEVIATION` halt',
                       "the `Produces:` line of every task its `Depends on:` names",
                       'keeps one checkbox as one task'):
            self.assertIn(phrase, step)

    def test_hitl_covers_the_whole_task(self):
        step = _between(self.agent, '### Step 1', '### Step 2')
        sentence = _between(step, 'If the task carries a `[HITL: …]` tag')
        self.assertIn("sits on the task's heading and covers every step", sentence)
        self.assertIn('(a tag on a task block\'s heading covers all its steps)',
                      _between(self.agent, '- **HITL boundary**', ' - **'))

    def test_step_three_works_the_block_and_names_the_dependency_deviation(self):
        step = _between(self.agent, '### Step 3', '### Step 4')
        for phrase in ('**A task block is worked whole.**', 'the report lists it (Step 6)',
                       '**A missing or wrong dependency is a deviation.**',
                       '**Major** under `${CLAUDE_PLUGIN_ROOT}/docs/deviation-protocol.md` §2',
                       'Halt before writing a stand-in'):
            self.assertIn(phrase, step)

    def test_step_four_gate_runs_the_test_files(self):
        step = _between(self.agent, '### Step 4', '### Step 5')
        self.assertIn("add every file the task's `Test:` field lists to the paths, touched or not",
                      step)
        self.assertIn('`data.missing`', step)

    def test_step_five_ticks_once_and_promotes_by_dependency(self):
        step = _between(self.agent, '### Step 5', '### Step 6')
        for phrase in ('tick every step of the block and update the Progress Report in the same write',
                       'promotion follows dependencies instead',
                       '`data.ready_now` task whose row',
                       'is still `backlog`',
                       'every `I<N+1> · ` child from `backlog` to `ready`'):
            self.assertIn(phrase, step)

    def test_step_six_reports_outside_files_and_deviation_files(self):
        step = _between(self.agent, '### Step 6', '## Rules')
        for phrase in ('`**Outside Files:**`', 'generated files excepted',
                       '`Task <N.M>: <title>`', '`D1 (minor: <path>, …)`',
                       'each with the files it changed'):
            self.assertIn(phrase, step)

    def test_rules(self):
        rules = _between(self.agent, '## Rules')
        for phrase in ('**IDs stay out of the product**',
                       '`${CLAUDE_PLUGIN_ROOT}/docs/task-grammar.md` §5',
                       'the task is the whole `### Task <N.M>:` block',
                       '`D1 (minor: <path>, …), …`',
                       '§16 on a task whose route is `full`'):
            self.assertIn(phrase, rules)

    def test_the_skill_dispatch_carries_the_same_rules(self):
        skill = flat(read('skills/implementer/SKILL.md'))
        for phrase in ("the first task of the parser's `data.ready_now`",
                       "on the task's `Test:` files, touched or not",
                       'every step of a task block, in one write',
                       'that its `Depends on:` does not list is Major',
                       "names the files each deviation changed (protocol §5)"):
            self.assertIn(phrase, skill)

    def test_inner_loop_and_gates_add_the_test_files(self):
        loop = _between(flat(read('skills/inner-loop/SKILL.md')), '## Inputs', '## Exit-code contract')
        self.assertIn("every file the task's `Test:` field lists, touched or not", loop)
        gates = flat(read('docs/gates.md'))
        self.assertIn("the paths also carry the task's `Test:` files, touched or not",
                      _between(gates, '| **task** |', '| **checkpoint** |'))
        self.assertIn("**A task's `Test:` files join its task gate.**",
                      _between(gates, '## 1. The schedule', '## 2.'))

    def test_the_deviations_line_names_files(self):
        protocol = flat(read('docs/deviation-protocol.md'))
        contract = _between(protocol, '## 5. Completion contract', '## 6.')
        for phrase in ('`Deviations: D1 (minor: lib/wallet/wallet_repository.dart), D2 (major: '
                       'lib/wallet/wallet_bloc.dart, test/wallet/wallet_bloc_test.dart)`',
                       '`D<n> (<severity>[: <path>, …])`',
                       'keeps the bare `D<n> (<severity>)`',
                       '`deviation_files`'):
            self.assertIn(phrase, contract)
        self.assertIn('leave the task checkbox unchecked (every step of a task block)', protocol)

    def test_the_stored_tick_is_one_patch(self):
        tick = _between(flat(read('docs/spec-storage.md')), '- **Tick a task**',
                        '- **Append a fix batch**')
        self.assertIn('one replace edit per step instead of the one checkbox, in the same patch',
                      tick)


class TestPromotionByDependency(unittest.TestCase):
    """Plan 3, Task 2: rows per task, statuses at creation, promotion and repair (spec §4)."""

    def setUp(self):
        self.doc = flat(read('docs/task-queue.md'))

    def test_the_mirror_creates_one_row_per_task(self):
        mirror = _between(self.doc, '## 2. Mirroring the tasklist', '## 3.')
        for phrase in ('**A task-format tasklist mirrors one row per task**',
                       '`I<N> · <N.M> · <task title>`',
                       '`ready` when the task is in `data.ready_now`',
                       'never claimed ahead of its dependencies',
                       'An old-format tasklist keeps one row per checkbox, mirrored at gate 4'):
            self.assertIn(phrase, mirror)

    def test_a_task_format_tasklist_is_first_mirrored_after_approval(self):
        mirror = _between(self.doc, '## 2. Mirroring the tasklist', '## 3.')
        for phrase in ('`tasklist` only once the plan is approved when the tasklist is task-format',
                       '**A task-format tasklist is first mirrored after approval.**',
                       'mirrors nothing while the tasklist is task-format and the plan is not yet '
                       '`PLAN_APPROVED`',
                       "the orchestrator's re-mirror before the first implementer dispatch",
                       '`generate-tasklist` mirrors after its own approval round, as before'):
            self.assertIn(phrase, mirror)
        bullet = _between(flat(read('docs/autonomous-run.md')), '**Task-queue mirror**', '## 10.')
        self.assertIn('For a task-format tasklist it is also the first mirror', bullet)
        remirror = _between(flat(read('skills/feature-development/tail.md')),
                            '**Re-mirror first.**', '| 5 |')
        self.assertIn('For a task-format tasklist this step is also the first mirror', remirror)

    def test_the_claim_loop_promotes_by_dependency(self):
        claim = _between(self.doc, '## 3. Claiming, reporting and promoting', '## 4.')
        for phrase in ('promote (task format — rows titled "I<N> · <N.M> · …")',
                       'every data.ready_now task whose row is backlog → task_update(row, ready)',
                       "(task format: the claimed block's heading carries \"[HITL:\"",
                       'work every step of the block, then tick them all',
                       '**Promotion follows dependencies on a task-format tasklist.**',
                       '`phase-<N>/tasks.md` on a phase-scoped run, where the ticks land first',
                       'every "I<N+1> · " child: backlog → ready'):
            self.assertIn(phrase, claim)

    def test_the_fallback_takes_ready_now(self):
        fallback = _between(self.doc, '## 4. The fallback path', '## 5.')
        self.assertIn('take the first task of `data.ready_now`', fallback)
        self.assertIn('flipping the checkbox on completion', fallback)

    def test_the_repair_uses_ready_now(self):
        empty = _between(self.doc, '## 5. When the queue is empty', '## 6.')
        self.assertIn('On a task-format tasklist the repair is the §3 promotion itself', empty)
        self.assertIn('nothing is promotable', empty)
        self.assertIn('promote every `I<N> · ` child of the lowest-numbered iteration', empty)


class TestRoutes(unittest.TestCase):
    """Plan 3, Task 3: autonomous-run.md §16 and both orchestrators (spec §5)."""

    def setUp(self):
        self.run = flat(read('docs/autonomous-run.md'))
        self.routes = _between(self.run, '## 16. Routes')

    def test_section_16_is_routes(self):
        self.assertNotIn('## 16. Per-task review', self.run)
        for heading in ('### 16.1 Which route a task takes', '### 16.2 What a route runs'):
            self.assertIn(heading, self.routes)

    def test_the_four_floors(self):
        which = _between(self.routes, '### 16.1', '### 16.2')
        for phrase in ('a `Files:` path matches a sensitive-paths category',
                       'the task carries a `[HITL: …]` tag',
                       'more than `ROUTE_FULL_FILES = 5` paths',
                       'an earlier deviation in this run changed one of its files',
                       '`route_floor`, `route_reasons` and `route_effective`',
                       '`run-state.json` `deviation_files` (§2) as it stood when the task was dispatched'):
            self.assertIn(phrase, which)

    def test_override_per_task_and_old_format(self):
        which = _between(self.routes, '### 16.1', '### 16.2')
        for phrase in ('`— set at approval`', 'down as well as up',
                       'A route set at approval is final over floors 1–3; floor 4 still applies',
                       '**`review.perTask: true`** ([config.md](config.md)) raises every task to `full`',
                       'On an old-format tasklist there are no routes',
                       'exactly as before 0.23.0',
                       'snapshots before **every** iteration-task dispatch'):
            self.assertIn(phrase, which)

    def test_the_journal_line(self):
        for phrase in ('task 2.3: route full (declared full; floor: sensitive path (payments): '
                       'lib/ramps/ramps_bloc.dart)',
                       '`task <N.M>: route <effective> (declared <route>[; floor: <reason>[, '
                       '<reason>…]])`',
                       '`earlier deviation: <path>`'):
            self.assertIn(phrase, self.routes)

    def test_what_a_route_runs(self):
        runs = _between(self.routes, '### 16.2')
        for phrase in ('**`light`** — nothing more', 'The `full` wrapper, per iteration-task dispatch:',
                       'Fix-list tasks (`## Code Review Fixes`, `## Runtime Fixes`, `## Verify Fixes`) '
                       'are never wrapped',
                       "`<task title>` is the task heading's text after `### `"):
            self.assertIn(phrase, runs)

    def test_run_state_caps_and_hitl(self):
        state = _between(self.run, '## 2. `run-state.json`', '## 3.')
        self.assertIn('"deviation_files": []', state)
        self.assertIn('`deviation_files` is **carried, not re-derived**', state)
        self.assertIn('read it as `[]`', state)
        caps = _between(self.run, '## 5. Capped loops', '## 6.')
        for phrase in ('`ROUTE_FULL_FILES = 5`', '`MAX_PLAN_REVIEW_ROUNDS = 2`',
                       '`**Plan-review round:** k`', 'per task on the `full` route, §16'):
            self.assertIn(phrase, caps)
        self.assertIn('`### Task 2.3: <title> [HITL: <reason>]`',
                      _between(self.run, '## 4. AFK / HITL task tags', '## 5.'))

    def test_feature_development_routes(self):
        fd = flat(read('skills/feature-development/SKILL.md'))
        gate = _between(flat(read('skills/feature-development/tail.md')),
                        '| 5 | `IMPLEMENT_STEP_OK`', '| 6 |')
        for phrase in ('add every path its `Deviations:` line names to `run-state.json` `deviation_files`',
                       '**Routes** (autonomous-run.md §16)',
                       'before every iteration-task dispatch',
                       'a `Route:` ending `— set at approval` is final over floors 1–3',
                       '`task <N.M>: route <effective> (declared <route>[; floor: <reason>[, <reason>…]])`',
                       'a `light` task gets none',
                       'On an old-format tasklist there are no routes'):
            self.assertIn(phrase, gate)
        pause = _between(fd, '**Routes at the pause**', '### 4. Arm the run')
        for phrase in ('`<light|full> — set at approval`', 'naming any floor it lowered',
                       'In `yolo` the routes stand as declared and floored'):
            self.assertIn(phrase, pause)
        arm = _between(fd, '### 4. Arm the run', '### 5.')
        self.assertIn('`deviation_files: []` (§2)', arm)
        self.assertIn('carry `requested_local` and `deviation_files` forward unchanged', arm)

    def test_dev_routes(self):
        dev = flat(read('skills/dev/SKILL.md'))
        confirm = _between(dev, '**Routes at the confirmation**', 'The confirmed work list')
        for phrase in ('`<light|full> — set at approval`', 'naming any floor it lowered',
                       "Branch 3's work list has no routes"):
            self.assertIn(phrase, confirm)
        arm = _between(dev, '### 3. Arm the run', '### 4.')
        self.assertIn("every task's effective route (§16.1)", arm)
        self.assertIn('carry `deviation_files` forward unchanged', arm)
        step = _between(dev, '### 4. Implement (autonomous)', '### 5.')
        for phrase in ('add every path the `Deviations:` line names to `run-state.json` `deviation_files`',
                       '**Routes** (autonomous-run.md §16)',
                       'a `light` task gets none',
                       'On an old-format tasklist'):
            self.assertIn(phrase, step)
        self.assertIn("a task's `Route:` line changed at the step-2 confirmation",
                      _between(dev, '## Important'))

    def test_config_setup_and_run_reviewer(self):
        row = _between(flat(read('docs/config.md')), '| `review.perTask` |',
                       '| `review.forecast.threshold` |')
        for phrase in ('`true` raises every iteration task to the `full` route',
                       'so `full` tasks are reviewed either way',
                       'A tasklist written before 0.23.0 has no routes'):
            self.assertIn(phrase, row)
        self.assertIn('every task on the `full` route', flat(read('skills/setup/SKILL.md')))
        reviewer = flat(read('skills/run-reviewer/SKILL.md'))
        self.assertIn('whose task runs on the `full` route', reviewer)
        self.assertIn("its heading's text after `### `", reviewer)


class TestReadersAudit(unittest.TestCase):
    """Plan 3, Task 4: every reader of a tasklist treats a task block as the unit (spec §7)."""

    # `grep -rlE 'tasklist\.md|tasks\.md|checkbox|- \[ \]|first incomplete' agents skills/*/SKILL.md
    # docs/*.md README.md`, each hit read and ruled on in plan 3, Task 4.
    AUDITED = {
        'README.md', 'agents/implementer.md', 'agents/planner.md', 'agents/qa.md',
        'agents/researcher.md', 'agents/review-forecaster.md', 'agents/reviewer.md',
        'agents/task-planner.md', 'agents/tasklist-writer.md', 'agents/tech-writer.md',
        'agents/validator.md', 'docs/autonomous-run.md', 'docs/config.md', 'docs/design.md',
        'docs/deviation-protocol.md', 'docs/kartoteka-requirements.md',
        'docs/orchestrator-common.md', 'docs/porting-plan.md', 'docs/review-forecast.md',
        'docs/skills-reference.md', 'docs/source-inventory-workflow.md', 'docs/spec-storage.md',
        'docs/task-grammar.md', 'docs/task-queue.md', 'docs/ticket-parsing.md',
        'docs/workflow-guide.md', 'skills/address-pr-comment/SKILL.md',
        'skills/change-digest/SKILL.md', 'skills/deep-review/SKILL.md', 'skills/dev/SKILL.md',
        'skills/feature-development/SKILL.md', 'skills/generate-tasklist/SKILL.md',
        'skills/implementer/SKILL.md', 'skills/issue-draft/SKILL.md', 'skills/planner/SKILL.md',
        'skills/pr-description/SKILL.md', 'skills/researcher/SKILL.md',
        'skills/run-reviewer/SKILL.md', 'skills/sync-phases/SKILL.md', 'skills/tasklist/SKILL.md',
        'skills/tasks/SKILL.md', 'skills/using-artel/SKILL.md',
    }

    def test_every_tasklist_reader_was_audited(self):
        pattern = _re.compile(r'tasklist\.md|tasks\.md|checkbox|- \[ \]|first incomplete')
        live = (sorted(_ROOT.glob('agents/*.md')) + sorted(_ROOT.glob('skills/*/SKILL.md'))
                + sorted(_ROOT.glob('docs/*.md')) + [_ROOT / 'README.md'])
        readers = {str(p.relative_to(_ROOT)) for p in live
                   if pattern.search(p.read_text(encoding='utf-8'))}
        self.assertEqual(set(), readers - self.AUDITED,
                         'a new tasklist reader: read it, fix any one-checkbox-per-task '
                         'assumption, then add it to AUDITED')

    def test_reviewer_task_mode_scopes_the_task_block(self):
        task_mode = _between(flat(read('agents/reviewer.md')), '## Task mode', '## Review focus')
        for phrase in ('the task is its whole `### Task <N.M>:` block, not one checkbox line',
                       'every step, its `Files:`, its `Test:` and its `Implements:`',
                       'with the `*Accepts when:*` line as the check',
                       '`**Outside Files:**`',
                       'a requirement another task also implements may be only partly met here'):
            self.assertIn(phrase, task_mode)

    def test_fix_task_numbers_ignore_task_headings(self):
        self.assertIn("its `### Task N.M:` headings number iteration tasks and never count",
                      flat(read('skills/deep-review/SKILL.md')))

    def test_change_digest_reads_task_blocks(self):
        digest = flat(read('skills/change-digest/SKILL.md'))
        self.assertIn('each `### Task N.M:` block is one unit of work', digest)
        self.assertIn("(each task's `Files:` field; an older tasklist groups its boxes under file "
                      "headings, `` ### `path` ``)", digest)

    def test_product_text_carries_no_ids(self):
        pr = _between(flat(read('skills/pr-description/SKILL.md')), '5. **Cite no trail document.**',
                      '6. **Reader contract.**')
        self.assertIn('no task number (`2.3`) or requirement ID (`R1`) either', pr)
        self.assertIn('never a task number (`2.3`) or a requirement ID (`R1`)',
                      flat(read('agents/tech-writer.md')))

    def test_qa_and_validator(self):
        self.assertIn('every active requirement gets at least one scenario',
                      flat(read('agents/qa.md')))
        self.assertIn('every step of every `### Task N.M:` block',
                      flat(read('agents/validator.md')))

    def test_tasks_done_and_list(self):
        tasks = flat(read('skills/tasks/SKILL.md'))
        done = _between(tasks, '### `done <task-id>`', '### `block')
        self.assertIn('names a task block, not a box', done)
        self.assertIn('tick every unticked step under its `### Task <N.M>:` heading', done)
        listing = _between(tasks, '### `list', '### `add')
        self.assertIn('holds its dependents in `backlog` by design', listing)

    def test_description_file_sync(self):
        self.assertIn('every step of the matching `### Task N.M:` block is ticked',
                      flat(read('docs/orchestrator-common.md')))


class TestTaskGrammarReleaseDocs(unittest.TestCase):
    """Plan 3, Task 5: the operator docs, the design log and the changelog (spec §10)."""

    def test_workflow_guide(self):
        guide = flat(read('docs/workflow-guide.md'))
        self.assertIn('One dispatch works one whole task', guide)
        self.assertIn('**Routes at the pause.**', guide)
        self.assertIn('The journal says which route each task took and why', guide)
        self.assertIn('the task taken is the first whose dependencies are done', guide)

    def test_skills_reference(self):
        ref = flat(read('docs/skills-reference.md'))
        implementer = _between(ref, '### implementer', '### inner-loop')
        self.assertIn('one whole `### Task N.M:` block per dispatch', implementer)
        self.assertIn("the first task of the parser's `ready_now`", implementer)
        self.assertIn("and on the task's `Test:` files", implementer)
        self.assertIn("Task mode is the `full` route's review",
                      _between(ref, '### run-reviewer', '### run-app'))
        for entry, end in (('### feature-development', '### dev'), ('### dev', '### setup')):
            with self.subTest(entry):
                self.assertIn("a task's own review runs when its route is `full`",
                              _between(ref, entry, end))

    def test_readme(self):
        self.assertIn('**Tasks with dependencies and routes**', flat(read('README.md')))

    def test_design_log(self):
        design = flat(read('docs/design.md'))
        for phrase in ('**2026-09-29 — Sub-project 2 is split into 2a, 2b and 2c, 2a first.**',
                       '**2026-09-29 — A task is a block, and the parser is its only reader.**',
                       'a task-format tasklist is first mirrored only after approval',
                       '**2026-09-29 — Routes replace the run-wide per-task review.**',
                       '**2026-09-29 — Requirement IDs live in the PRD, and the plan is reviewed '
                       'before the pause.**',
                       '**2026-09-29 — Old-format tasklists keep working until 2b.**',
                       '**Sub-project 2b: one orchestrator**', '**Sub-project 2c: parallel seats.**',
                       '**Old-format tasklists are still read** (0.23.0).'):
            self.assertIn(phrase, design)
        follow_ups = _between(design, '## Open follow-ups', '## Decision log')
        sdd = _between(follow_ups, '**SDD v2 inputs for sub-project 2**', '**Sub-project 2b')
        self.assertIn('Sub-project 2b still owes', sdd)
        self.assertNotIn('an `Implements` line per task', sdd)
        self.assertNotIn('a plan-review rubric', sdd)

    def test_changelog(self):
        # Everything since 0.22.0: [Unreleased] before the release is cut, [0.23.0] after.
        since = flat(_between(read('CHANGELOG.md'), '## [Unreleased]', '\n## [0.22.0]'))
        for phrase in ('docs/task-grammar.md', '## Requirements', 'gate 4.2', '`PLAN_REVIEWED`',
                       '**Routes.**', '**One dispatch works one whole task**',
                       '**Tasks run in dependency order.**',
                       'mirrored into the task queue after approval, not at gate 4',
                       '`deviation_files`',
                       '**`review.perTask: true` now raises every task to `full`**',
                       '### Upgrading', '**A ticket in flight keeps working.**',
                       '**New tickets get the grammar**', '**PRDs gain `## Requirements`.**'):
            self.assertIn(phrase, since)


class TestWholeBranchReviewFixes(unittest.TestCase):
    """The 0.23.0 whole-branch review's Important findings: an indented checkbox and a step-level
    HITL tag are grammar problems (docs/task-grammar.md §1, §6.1; autonomous-run.md §4), and a
    red post-write check on the dev path never arms silently (generate-tasklist Phase 3)."""

    def test_the_contract_names_both_problems(self):
        self.assertIn('an indented checkbox is not a step and is a grammar problem',
                      flat(read('docs/task-grammar.md')))
        self.assertIn('| `hitl-on-step` |', read('docs/task-grammar.md'))

    def test_autonomous_run_leads_with_the_heading_form(self):
        section = flat(read('docs/autonomous-run.md')).split(
            '## 4. AFK / HITL task tags')[1].split('## 5.')[0]
        self.assertLess(section.index('`### Task 2.3: <title> [HITL: <reason>]`'),
                        section.index('`- [ ] [HITL: <reason>] <task text>`'))
        self.assertIn('a tag on a step is a grammar problem (`hitl-on-step`)', section)
        self.assertIn('On an old-format tasklist the tag sits on the checkbox', section)

    def test_a_red_post_write_check_gets_one_round_then_the_person(self):
        gen = flat(read('skills/generate-tasklist/SKILL.md'))
        phase3 = gen.split('### Phase 3:')[1].split('### Phase 4:')[0]
        for phrase in ('one fix `SendMessage` to the same agent',
                       'still has a Critical or Important finding',
                       '**Fix it by hand**', '**Proceed anyway**', '**Abort**',
                       'even in `yolo`'):
            self.assertIn(phrase, phase3)
        self.assertIn('at most one post-write fix `SendMessage` in Phase 3', gen)


if __name__ == '__main__':
    unittest.main()
