"""The task-queue tool names are spelled identically everywhere they appear.

Not a behaviour test: these are prompts, and there is no code path to exercise.
Same guard as tests/test_knowledge_consultation_docs.py, for the same reason --
a tool name that drifts in one prompt fails at call time in a session nobody is
watching, and prose review does not catch a single changed underscore.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

QUEUE_DOC = 'docs/task-queue.md'

# The four kartoteka MCP tools this integration calls. Exact spellings.
TOOL_NAMES = ('task_create', 'task_update', 'task_list', 'task_ready')

# Plausible drifts. Each is a real name somebody would reach for.
NEAR_MISSES = ('task_claim', 'task_next', 'tasks_ready', 'task_get',
               'task_ready_claim', 'tasks_create')

MIRROR_FILES = (
    'skills/generate-tasklist/SKILL.md',
    'skills/tasklist/SKILL.md',
    'skills/dev/SKILL.md',
    'skills/feature-development/SKILL.md',
)

CLAIM_FILES = (
    'agents/implementer.md',
    'skills/implementer/SKILL.md',
)

# Files that describe the loop and must agree with the reference doc.
LOOP_FILES = (QUEUE_DOC,) + MIRROR_FILES + CLAIM_FILES

# The four sections the queue records but never offers, with their title codes.
FIX_SECTION_CODES = (('Code Review Fixes', 'CRF'), ('Runtime Fixes', 'RTF'),
                     ('Verify Fixes', 'VF'), ('Final Verification', 'FV'))


class TestQueueDoc(unittest.TestCase):
    def test_reference_doc_exists(self):
        self.assertTrue((ROOT / QUEUE_DOC).is_file(), QUEUE_DOC + ' is missing')

    def test_reference_doc_spells_every_tool(self):
        text = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')
        for name in TOOL_NAMES:
            self.assertIn(name, text, QUEUE_DOC + ' never mentions ' + name)

    def test_no_file_uses_a_near_miss_spelling(self):
        for rel in LOOP_FILES:
            text = (ROOT / rel).read_text(encoding='utf-8')
            for wrong in NEAR_MISSES:
                self.assertNotIn(wrong, text,
                                 '{} uses {}, not a real kartoteka tool'.format(rel, wrong))

    def test_gating_table_covers_all_four_rows(self):
        # Was three fragments matched against the whole file, one of which
        # belongs to the fifth case rather than to any row, and rows 2 and 3
        # were never checked at all. Each row's behaviour is asserted here, in
        # the row itself, so a dropped or reordered row fails.
        section = (ROOT / QUEUE_DOC).read_text(encoding='utf-8').split(
            '## 1. Whether to use the queue at all')[1].split('## 2.')[0]
        rows = [ln for ln in section.splitlines()
                if ln.startswith('|') and not ln.startswith('|---')][1:]
        self.assertEqual(4, len(rows), 'the gating table must carry exactly four rows')
        self.assertIn('local-only run requested', rows[0])
        self.assertIn('Fallback path', rows[0])
        self.assertIn('Record nothing', rows[1])
        self.assertIn('Fallback path', rows[1])
        self.assertIn('Queue path', rows[2])
        self.assertNotIn('Fallback path', rows[2])
        self.assertIn('its MCP tools are not available in this session', rows[3])
        self.assertIn('Fallback path', rows[3])

    def test_the_fifth_case_is_documented_outside_the_table(self):
        # The daemon dying mid-ticket has no row: it is not a starting state.
        section = (ROOT / QUEUE_DOC).read_text(encoding='utf-8').split(
            '## 1. Whether to use the queue at all')[1].split('## 2.')[0]
        self.assertIn('became unreachable mid-run', section)

    def test_local_flag_is_spelled_the_one_way(self):
        text = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')
        self.assertIn('`--local`', text)
        for wrong in ('--no-kartoteka', '--local-only', '--offline', '--no-knowledge'):
            self.assertNotIn(wrong, text)

    def test_doc_says_where_the_local_flag_actually_exists(self):
        # dev carries no --local by a deliberate decision that
        # test_knowledge_consultation_docs.py pins. Gating dev's re-mirror on a
        # flag it cannot receive would make row 1 unreachable while implying it
        # applied, so the doc has to say which orchestrators hold the flag.
        text = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')
        self.assertIn('test_dev_does_not_carry_the_flag', text)
        self.assertIn('rows 2-4', text)

    def test_dev_skill_still_carries_no_local_flag(self):
        self.assertNotIn('--local',
                         (ROOT / 'skills/dev/SKILL.md').read_text(encoding='utf-8'))


class TestMirrorStep(unittest.TestCase):
    def test_every_producer_runs_the_mirror_step(self):
        for rel in MIRROR_FILES:
            text = (ROOT / rel).read_text(encoding='utf-8')
            self.assertIn('scripts/tasklist_tasks.py', text,
                          rel + ' never invokes the parser')
            self.assertIn('docs/task-queue.md', text,
                          rel + ' does not point at the reference doc')

    def test_mirror_step_names_the_script_the_one_way(self):
        for rel in MIRROR_FILES:
            text = (ROOT / rel).read_text(encoding='utf-8')
            self.assertIn(
                'python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py', text,
                rel + ' does not invoke the script the plugin-root way')


class TestClaimLoop(unittest.TestCase):
    def test_implementer_claims_reports_and_promotes(self):
        text = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        for name in ('task_ready', 'task_update', 'task_list'):
            self.assertIn(name, text, 'agents/implementer.md never calls ' + name)

    def test_both_claim_files_point_at_the_reference_doc(self):
        for rel in CLAIM_FILES:
            text = (ROOT / rel).read_text(encoding='utf-8')
            self.assertIn('docs/task-queue.md', text,
                          rel + ' does not point at the reference doc')

    def test_actor_is_spelled_the_one_way(self):
        text = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        self.assertIn('artel@<hostname>', text)

    def test_actor_hostname_is_a_command_not_a_guess(self):
        # Scoped to the prose around the placeholder: `hostname -s` must sit
        # beside `artel@<hostname>` in both documents. Handed the placeholder
        # alone, the implementer composed a name -- one ticket's queue carried
        # rows held by three machines, two of them fictitious (2026-09-02).
        for rel in ('agents/implementer.md', QUEUE_DOC):
            text = (ROOT / rel).read_text(encoding='utf-8')
            at = text.rfind('artel@<hostname>')
            self.assertNotEqual(at, -1, rel + ' lost the actor placeholder')
            self.assertIn('`hostname -s`', text[at:at + 400],
                          rel + ' does not tell the implementer to run hostname -s')

    def test_hitl_boundary_survives_the_queue(self):
        # The HITL rule predates the queue and must not be lost in the rewrite:
        # a claimed HITL task is set blocked and returned, never implemented.
        # Scoped to Step 1's HITL sentence: `blocked` appears half a dozen times
        # in this file now, so an unscoped assertIn passed with the whole clause
        # deleted -- it pinned the word, not the rule.
        text = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        step_one = text.split('### Step 1')[1].split('### Step 2')[0]
        sentence = step_one.split('If the task carries a `[HITL: …]` tag')[1]
        self.assertIn('do not implement', sentence)
        self.assertIn('status="blocked"', sentence)
        self.assertIn('HITL: <reason>', sentence)

    def test_fallback_path_is_still_described(self):
        text = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        self.assertIn('first incomplete `- [ ]`', text)


class TestMirrorAttributionIsAccurate(unittest.TestCase):
    """Docs must credit exactly the skills that carry a mirror step.

    docs/autonomous-run.md once claimed feature-development re-mirrors on entry
    to implementation while it had no mirror step at all. It has one now, so the
    claim is checked against the skill file rather than against a phrasing.
    `implementer` is the one that must stay out: it claims, it never mirrors.
    """

    def test_exactly_the_mirroring_skills_invoke_the_parser(self):
        carriers = {rel for rel in (
            'skills/generate-tasklist/SKILL.md',
            'skills/tasklist/SKILL.md',
            'skills/dev/SKILL.md',
            'skills/feature-development/SKILL.md',
            'skills/implementer/SKILL.md',
        ) if 'tasklist_tasks.py' in (ROOT / rel).read_text(encoding='utf-8')}
        self.assertEqual(carriers, {
            'skills/generate-tasklist/SKILL.md',
            'skills/tasklist/SKILL.md',
            'skills/dev/SKILL.md',
            'skills/feature-development/SKILL.md',
        })

    def test_autonomous_run_credits_both_orchestrators_with_the_remirror(self):
        # Was an assertNotIn on one former phrasing, which the next rewording
        # would have satisfied by accident. The claim is positive now, and it is
        # checked against the file it is a claim about.
        text = (ROOT / 'docs/autonomous-run.md').read_text(encoding='utf-8')
        bullet = text.split('**Task-queue mirror**')[1].split('\n- ')[0]
        self.assertIn('`dev` and `feature-development` alike run the parser', bullet)
        skill = (ROOT / 'skills/feature-development/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('scripts/tasklist_tasks.py', skill,
                      'the bullet credits a re-mirror feature-development does not have')


class TestGateWorkIsFileScanOnBothPaths(unittest.TestCase):
    """The queue holds `## Iteration N:` work and nothing else.

    "Queue before file" was written unqualified. None of the four sections below
    is ever mirrored, so on the queue path the three fix loops claimed nothing
    and applied no fix -- each gate re-ran to its cap and escalated -- and
    `## Final Verification` never came up at all, which is the state every
    successful ticket ends in.
    """

    GATE_SECTIONS = ('## Code Review Fixes', '## Runtime Fixes', '## Verify Fixes',
                     '## Final Verification')

    def setUp(self):
        self.agent = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        self.doc = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')

    def test_the_rule_is_scoped_to_iteration_work(self):
        rules = self.agent.split('## Rules')[1]
        self.assertIn('**Queue before file, for iteration work only**', rules)

    def test_step_one_sends_a_fix_list_dispatch_to_the_file(self):
        step_one = self.agent.split('### Step 1')[1].split('### Step 2')[0]
        paragraph = step_one.split('**A fix-list dispatch is file-scan work')[1].split(
            '**Fallback path.**')[0]
        self.assertIn('do not call `task_ready` at all', paragraph)
        for section in self.GATE_SECTIONS[:3]:
            self.assertIn(section, paragraph)
        self.assertIn('Final Verification', paragraph)

    def test_the_doc_lists_every_section_it_records_but_never_offers(self):
        section = self.doc.split('## 6. What the queue records but never offers')[1]
        for heading, code in FIX_SECTION_CODES:
            rows = [ln for ln in section.splitlines()
                    if ln.startswith('| `## ' + heading + '` |')]
            self.assertEqual(1, len(rows), heading + ' needs exactly one table row')
            self.assertIn('`{}: {}`'.format(code, heading), rows[0])
            self.assertIn('`{} · <source> · <checkbox text>`'.format(code), rows[0])
            self.assertIn('file scan, on either path', rows[0])

    def test_a_drained_queue_is_a_documented_branch_not_a_stall(self):
        empty = self.doc.split('## 5. When the queue is empty')[1].split('## 6.')[0]
        self.assertIn('- every **iteration child** row `done` — the iteration work is complete.',
                      empty)
        self.assertIn('`queue drained: iteration work complete`', empty)
        step_one = self.agent.split('### Step 1')[1].split('### Step 2')[0]
        self.assertIn('`queue drained: iteration work complete`', step_one)

    def test_the_drained_branch_tolerates_a_parent_left_in_backlog(self):
        # build_rows mirrors every `I<N>: …` parent `backlog`, and only a child
        # completing promotes one to `done`. An iteration already fully `- [x]`
        # at mirror time therefore leaves its parent `backlog` for good. Read on
        # rows rather than children, bullet 1 was false for such a ticket while
        # bullet 2 was true, so the repair ran, found no iteration with an
        # unfinished child, and had no terminating case -- C2's non-termination
        # again, on exactly the resumed runs bullet 2 was written for.
        empty = self.doc.split('## 5. When the queue is empty')[1].split('## 6.')[0]
        self.assertIn('An `I<N>: …` parent', empty)
        self.assertIn('still `backlog` because its iteration was already complete', empty)
        self.assertIn('is not a stall: mark it `done` and treat the queue as drained.',
                      empty)
        self.assertIn('there is nothing left to promote — take the', empty)


class TestFixRowsAreRecordedNeverOffered(unittest.TestCase):
    """The four fix sections become rows that task_ready never hands out.

    On a host run on 2026-09-18 a deep review appended 21 fix tasks and the
    implementer worked nine of them over several hours while `/artel:tasks list`
    read "queue drained". The rows make that work visible; keeping them out of
    `ready` keeps task_ready -- which claims with no notion of section -- from
    handing one to an iteration dispatch or across a phase boundary.
    """

    def setUp(self):
        self.doc = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')
        self.mirror = self.doc.split('## 2. Mirroring the tasklist')[1].split('## 3.')[0]
        self.claim = self.doc.split(
            '## 3. Claiming, reporting and promoting')[1].split('## 4.')[0]
        self.empty = self.doc.split('## 5. When the queue is empty')[1].split('## 6.')[0]
        self.records = self.doc.split('## 6. What the queue records but never offers')[1]
        self.fix_block = self.claim.split('**Fix-section rows are recorded, not claimed.**')[1]

    def test_the_old_title_and_claim_are_gone(self):
        self.assertNotIn('What the queue does not hold', self.doc)
        self.assertNotIn('never become rows', self.doc)

    def test_the_mirror_creates_sections_after_iterations(self):
        self.assertIn('Then each entry of `data.sections`', self.mirror)
        self.assertIn('fix task #<id> is done in the queue but open in the file: <title>',
                      self.mirror)

    def test_a_fix_writer_creates_sections_only_from_the_file_it_wrote(self):
        self.assertIn('**A fix-section writer creates `data.sections` only.**', self.mirror)
        self.assertIn('`phase-<N>/tasks.md` on a phase-scoped run', self.mirror)

    def test_the_fix_row_protocol_moves_rows_by_task_update_alone(self):
        for status in ('in_progress', 'done', 'blocked'):
            self.assertIn('task_update(task_id, status="{}")'.format(status), self.fix_block)
        self.assertIn('row not found; file only', self.fix_block)
        self.assertNotIn('status="ready"', self.fix_block)
        self.assertNotIn('task_ready(', self.fix_block)

    def test_holder_absence_and_hitl_are_stated(self):
        self.assertIn('**No holder.**', self.fix_block)
        self.assertIn('**A HITL fix task is never claimable,**', self.fix_block)

    def test_the_empty_queue_reads_iteration_children_only(self):
        self.assertIn('iteration children in `backlog` with none `ready`', self.empty)
        self.assertIn('- fix-section rows open, `in_progress` or `blocked`', self.empty)
        self.assertIn('never repair, promote or release one', self.empty)

    def test_the_source_heading_rule_names_every_writer(self):
        for source in ('### review-r<R>', '### review-p<PHASE_NUM>-r<R>',
                       '### task-gate-<NNN>', '### deep-review-<YYYY-MM-DD>',
                       '### runtime-r<n>', '### checkpoint-r<k>',
                       '### manual-<YYYY-MM-DD>'):
            self.assertIn(source, self.records)
        self.assertIn('the source is `tasklist`', self.records)

    def test_the_parent_id_follow_up_is_recorded(self):
        self.assertIn('parent_id', self.records)
        self.assertIn('design.md', self.records)


class TestLocalOnlyReachesTheImplementer(unittest.TestCase):
    """`--local` has to survive the hop from orchestrator to agent.

    docs/task-queue.md §1 row 1 is the whole opt-out, and it was unreachable:
    feature-development passed `--local` to `analysis` and `researcher` only, and
    neither the implementer skill nor its agent had a field to receive it. The
    field is the carrier -- the agent cannot see the invocation's arguments.
    """

    def test_the_dispatch_prompt_carries_the_task_queue_field(self):
        text = (ROOT / 'skills/implementer/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('- **Task queue:** <"local-only (--local was passed)" | "enabled">',
                      text)

    def test_the_skill_accepts_the_flag_it_forwards(self):
        hints = [ln for ln in
                 (ROOT / 'skills/implementer/SKILL.md').read_text(encoding='utf-8').splitlines()
                 if ln.startswith('argument-hint:')]
        self.assertEqual(1, len(hints), 'exactly one argument-hint line')
        self.assertIn('--local', hints[0])

    def test_the_agent_reads_the_field_rather_than_a_flag(self):
        step_one = (ROOT / 'agents/implementer.md').read_text(
            encoding='utf-8').split('### Step 1')[1].split('### Step 2')[0]
        self.assertIn('A dispatch carrying **Task queue:**', step_one)

    def test_feature_development_passes_it_to_the_implementer_dispatch(self):
        gate = (ROOT / 'skills/feature-development/SKILL.md').read_text(
            encoding='utf-8').split('| 5 | `IMPLEMENT_STEP_OK`')[1].split('\n')[0]
        self.assertIn('--local', gate)
        self.assertIn('**Task queue:**', gate)


class TestAbortedClaimIsReleased(unittest.TestCase):
    """A red gate must not leave a task held forever.

    task_ready claims `ready` rows only, and §3's promotion needs every sibling
    done, so a task left `in_progress` by an abort is never re-claimed and its
    iteration never advances -- with §5 reporting neither, because it knew only
    `backlog` and `blocked`.
    """

    def setUp(self):
        self.doc = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')
        self.agent = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')

    def test_agent_sets_an_aborted_task_blocked(self):
        # Scoped to the abort paragraph on purpose. The pre-existing HITL guard
        # also contains status="blocked", so an unscoped assertIn passes even
        # with this whole paragraph deleted -- it pinned nothing.
        paragraph = self.agent.split('**Release the claim on the way out.**')[1]
        self.assertIn('status="blocked"', paragraph)

    def test_agent_no_longer_leaves_an_aborted_task_in_progress(self):
        self.assertNotIn('leave the task `in_progress`', self.agent)

    def test_empty_queue_section_covers_the_in_progress_state(self):
        section = self.doc.split('## 5. When the queue is empty')[1]
        self.assertIn('in_progress', section)

    def test_path_record_names_a_destination(self):
        section = self.doc.split('## 4. The fallback path')[1].split('## 5.')[0]
        self.assertIn('implementation-notes.md', section)

    def test_doc_states_the_iteration_to_phase_mapping(self):
        self.assertIn('Iteration N is phase N', self.doc)


class TestCountClaimsAreNotStale(unittest.TestCase):
    """A document that miscounts its own contents misleads the next reader."""

    def test_workflow_guide_counts_three_scripts(self):
        text = (ROOT / 'docs/workflow-guide.md').read_text(encoding='utf-8')
        self.assertNotIn('Two Python scripts', text)
        self.assertIn('Three Python scripts', text)

    def test_generate_tasklist_counts_four_phases(self):
        text = (ROOT / 'skills/generate-tasklist/SKILL.md').read_text(encoding='utf-8')
        self.assertNotIn('Three-phase model', text)
        self.assertIn('Four-phase model', text)

    def test_config_counts_three_things_gated_by_the_adapter(self):
        # It gated two until the queue landed, and the section it points at
        # listed the three read tools and none of the four task tools -- so a
        # reader configuring `kartoteka` could not tell what else switched on.
        text = (ROOT / 'docs/config.md').read_text(encoding='utf-8')
        self.assertNotIn('gates two things', text)
        self.assertIn('gates three things', text)
        for name in TOOL_NAMES:
            self.assertIn(name, text, 'docs/config.md never mentions ' + name)


class TestEveryNonCompletionExitReleasesTheClaim(unittest.TestCase):
    """One uncovered exit wedges the queue exactly as the red-gate one did.

    agents/implementer.md halts with a DEVIATION report from three places, not
    one, and deviation-protocol.md's Abort task outcome returns control with no
    queue update at all.
    """

    def setUp(self):
        self.agent = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        self.doc = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')

    def test_rules_carry_the_release_as_a_standing_rule(self):
        rules = self.agent.split('## Rules')[1]
        self.assertIn('Release the claim on any exit that is not a completion', rules)

    def test_step_one_anchor_halt_points_at_the_release_rule(self):
        step_one = self.agent.split('### Step 1')[1].split('### Step 2')[0]
        self.assertIn('release the claim first', step_one)

    def test_queue_doc_abort_line_names_more_than_the_red_gate(self):
        self.assertIn('any DEVIATION halt, or Abort task', self.doc)


class TestStalledClaimIsNotClearedUnilaterally(unittest.TestCase):
    """actor + updated_at cannot tell a slow verify loop from a dead holder."""

    def test_empty_queue_section_forbids_clearing_another_agents_claim(self):
        section = (ROOT / QUEUE_DOC).read_text(encoding='utf-8').split(
            '## 5. When the queue is empty')[1]
        self.assertIn('do not clear another agent', section)
        self.assertNotIn('status="ready"', section)


if __name__ == '__main__':
    unittest.main()
