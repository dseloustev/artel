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
        text = (ROOT / QUEUE_DOC).read_text(encoding='utf-8')
        for fragment in ('local-only run requested',
                         'its MCP tools are not available in this session',
                         'became unreachable mid-run'):
            self.assertIn(fragment, text, QUEUE_DOC + ' is missing: ' + fragment)

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

    def test_hitl_boundary_survives_the_queue(self):
        # The HITL rule predates the queue and must not be lost in the rewrite:
        # a claimed HITL task is set blocked and returned, never implemented.
        text = (ROOT / 'agents/implementer.md').read_text(encoding='utf-8')
        self.assertIn('HITL: <reason>', text)
        self.assertIn('blocked', text)

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
