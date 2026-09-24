"""Store mode reaches every agent, skill and contract that touches a spec document.

Prompts, not code: this pins the spellings docs/spec-storage.md depends on -- the
dispatch field, the decision read, the pipe form of every script call on a spec
document, the pause reason, the review reset -- the way test_task_queue_docs.py
pins the queue's. Prose is not asserted on beyond those spellings.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Agents outside the spec trail read no spec document and write none, so store mode
# never reaches them. Guarded for the absence, not the presence.
OUTSIDE_TRAIL = ('issue-scout',)
AGENTS = sorted(p.stem for p in (ROOT / 'agents').glob('*.md') if p.stem not in OUTSIDE_TRAIL)
DISPATCHERS = ('analysis', 'researcher', 'planner', 'tasklist', 'generate-tasklist',
               'generate-vision', 'implementer', 'run-reviewer', 'qa', 'validate',
               'docs-update', 'pr-description', 'figma-analysis', 'deep-review')
INLINE = ('generate-idea', 'sync-phases', 'tasks', 'change-digest', 'address-pr-comment',
          'pr-create', 'knowledge')
ORCHESTRATORS = ('feature-development', 'dev')
CONTRACT = 'docs/spec-storage.md'
FILE_FORM = re.compile(r'(tasklist_tasks\.py --tasklist|plan_check\.py --plan) <')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def skill(name):
    return read('skills/{}/SKILL.md'.format(name))


class TestAgents(unittest.TestCase):
    def test_agents_outside_the_trail_carry_no_spec_store_section(self):
        for name in OUTSIDE_TRAIL:
            with self.subTest(name):
                self.assertNotIn('\n## Spec store\n', read('agents/{}.md'.format(name)))

    def test_every_agent_has_the_spec_store_section(self):
        for name in AGENTS:
            with self.subTest(name):
                text = read('agents/{}.md'.format(name))
                self.assertIn('\n## Spec store\n', text)
                self.assertIn('{}` §4.1'.format(CONTRACT), text)
                self.assertIn('**Spec store:**', text)
                self.assertIn('STORE_UNAVAILABLE', text)
                self.assertIn('spec_store.py decision <TICKET_ID>', text)

    def test_the_implementer_ticks_with_one_patch(self):
        text = read('agents/implementer.md')
        self.assertIn('artifact_patch', text)
        self.assertIn('§4.3', text)

    def test_the_reviewer_knows_the_reset_and_the_batch_patch(self):
        text = read('agents/reviewer.md')
        self.assertIn('§4.4', text)
        self.assertIn('§4.3', text)
        self.assertIn('review-claude.md', text)

    def test_qa_finds_previous_reports_in_the_store(self):
        self.assertIn('artifact_list(project=<project>)', read('agents/qa.md'))


class TestDispatchers(unittest.TestCase):
    def test_every_dispatcher_reads_the_decision_and_passes_the_field(self):
        for name in DISPATCHERS:
            with self.subTest(name):
                text = skill(name)
                self.assertIn('spec_store.py decision <TICKET_ID>', text)
                self.assertIn('**Spec store:** kartoteka', text)
                self.assertIn('**Spec store:** files (<reason>)', text)
                self.assertIn('STORE_UNAVAILABLE', text)

    def test_the_implementer_skill_lists_the_field_beside_task_queue(self):
        text = skill('implementer')
        self.assertIn('- **Spec store:**', text)
        self.assertLess(text.index('- **Task queue:**'), text.index('- **Spec store:**'))

    def test_release_scope_is_always_files(self):
        for name in ('qa', 'validate'):
            with self.subTest(name):
                self.assertIn('A release id (`R-…`) is always `files`', skill(name))


class TestInlineSkills(unittest.TestCase):
    def test_every_inline_writer_resolves_the_store(self):
        for name in INLINE:
            with self.subTest(name):
                text = skill(name)
                self.assertIn(CONTRACT, text)
                self.assertIn('spec_store.py decision <TICKET_ID>', text)

    def test_restore_context_never_restores_spec_documents_under_the_store(self):
        text = skill('restore-context')
        self.assertIn('--exclude={idea,vision,prd,research,plan,tasklist,tasks,'
                      'implementation-notes,review,deep-review,qa,adr,summary,design-analysis,'
                      'pr-description,post_feedback}.md', text)
        self.assertIn('/artel:migrate-specs', text)

    def test_save_context_names_the_store(self):
        self.assertIn(CONTRACT, skill('save-context'))

    def test_pr_create_pipes_the_body(self):
        text = skill('pr-create')
        self.assertIn('spec_store.py get <specs.dir>/<TICKET_ID>/pr-description.md) && printf', text)
        self.assertIn('"$doc" | gh pr create', text)
        self.assertIn('--body-file -', text)

    def test_deep_review_checks_existence_in_the_store(self):
        self.assertIn('spec_store.py exists <specs.dir>/<TICKET_ID>/deep-review.md',
                      skill('deep-review'))

    def test_sync_phases_finds_phase_files_by_name(self):
        self.assertIn('phase-<N>.tasks.md', skill('sync-phases'))


class TestScriptPipes(unittest.TestCase):
    LIVE = [p for p in sorted((ROOT / 'skills').glob('*/SKILL.md'))] + [
        ROOT / 'docs' / 'task-queue.md', ROOT / 'docs' / 'workflow-guide.md']

    def test_every_file_form_has_its_pipe_form_beside_it(self):
        for path in self.LIVE:
            text = path.read_text(encoding='utf-8')
            if FILE_FORM.search(text) is None:
                continue
            with self.subTest(str(path.relative_to(ROOT))):
                if 'tasklist_tasks.py --tasklist <' in text:
                    self.assertIn('tasklist_tasks.py --tasklist -', text)
                if 'plan_check.py --plan <' in text:
                    self.assertIn('plan_check.py --plan -', text)


class TestOrchestrators(unittest.TestCase):
    def test_both_orchestrators_carry_the_storage_step(self):
        for name in ORCHESTRATORS:
            with self.subTest(name):
                text = skill(name)
                self.assertIn('\n### 1.5 Spec store\n', text)
                self.assertIn('spec_store.py decide <TICKET_ID> --decided-by {}'.format(name), text)
                self.assertIn('"store-unavailable"', text)
                self.assertIn('Skill: migrate-specs', text)
                self.assertIn('--pending-only', text)
                self.assertIn('spec_store.py pending add', text)
                self.assertIn('§4.4', text)
                self.assertIn('only `.active_ticket` changed', text)

    def test_feature_development_passes_local_to_decide(self):
        self.assertIn('--decided-by feature-development` (plus `--local`',
                      skill('feature-development'))


class TestContracts(unittest.TestCase):
    def test_autonomous_run(self):
        text = read('docs/autonomous-run.md')
        self.assertIn('spec-store.json', text)
        self.assertIn('"store-unavailable"', text)
        self.assertIn('§4.4', text)
        self.assertIn('specs.onUnavailable', text)

    def test_ticket_parsing_calls_the_paths_logical(self):
        text = read('docs/ticket-parsing.md')
        self.assertIn('**These paths are logical addresses.**', text)
        self.assertIn('starts `phase-`', text)

    def test_knowledge_consultation_has_both_paths(self):
        self.assertIn('On the kartoteka path', read('docs/knowledge-consultation.md'))

    def test_task_queue_reads_the_stored_tasklist(self):
        text = read('docs/task-queue.md')
        self.assertIn('{}` §4.3'.format(CONTRACT), text)

    def test_the_small_contracts_point_at_the_store(self):
        for rel in ('docs/path-conventions.md', 'docs/deviation-protocol.md',
                    'docs/orchestrator-common.md'):
            with self.subTest(rel):
                self.assertIn('spec-storage.md', read(rel))

    def test_config_says_the_adapter_makes_kartoteka_the_store(self):
        self.assertIn("kartoteka's artifact store is this project's spec store",
                      read('docs/config.md'))


class TestReleaseDocs(unittest.TestCase):
    def test_changelog_announces_store_mode(self):
        # Everything since 0.15.0: [Unreleased] before the release is cut, [0.16.0] after.
        since = read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [0.15.0]', 1)[0]
        for phrase in ('kartoteka is the spec store', '/artel:migrate-specs', 'artifact_patch',
                       'kartoteka 0.43.0', 'specs.onUnavailable'):
            self.assertIn(phrase, since)

    def test_design_records_the_decisions(self):
        self.assertIn('2026-09-22 — kartoteka is the spec store', read('docs/design.md'))
