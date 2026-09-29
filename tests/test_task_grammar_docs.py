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


if __name__ == '__main__':
    unittest.main()
