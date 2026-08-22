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
    """Docs must not credit a skill with a mirror step it does not have.

    docs/autonomous-run.md once claimed feature-development re-mirrors on entry
    to implementation; it has no mirror step at all, and the claim contradicted
    docs/task-queue.md §2, which it cited.
    """

    def test_only_the_three_mirroring_skills_invoke_the_parser(self):
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
        })

    def test_autonomous_run_does_not_credit_feature_development_with_a_remirror(self):
        text = (ROOT / 'docs/autonomous-run.md').read_text(encoding='utf-8')
        bullet = text.split('**Task-queue mirror**')[1].split('\n- ')[0]
        self.assertNotIn('`dev` and `feature-development` re-mirror', bullet)


if __name__ == '__main__':
    unittest.main()
