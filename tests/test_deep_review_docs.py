"""deep-review's single-file contract is spelled identically everywhere it appears.

Not a behaviour test: these are prompts, and there is no code path to exercise.
It guards the strings that three files and the mirror hook must agree on -- the
output filename, the forecast-mode lines, the config keys, the lookup budget --
and the formula's single home. Prose is deliberately not asserted on.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONTRACT = 'docs/review-forecast.md'
SKILL = 'skills/deep-review/SKILL.md'
AGENT = 'agents/review-forecaster.md'
CONFIG = 'docs/config.md'
HOOK = 'hooks/kartoteka_http.py'  # MIRRORED's home; knowledge_mirror.py imports it
CONSULTATION = 'docs/knowledge-consultation.md'
REFERENCE = 'docs/skills-reference.md'

OUTPUT_FILE = 'deep-review.md'
REPORT_PATH = '.artel/run/<TICKET_ID>/reports/deep-review-findings.md'

OFF_MODES = (
    'off: local-only run requested',
    'off: knowledge.adapter is not kartoteka for this project',
    'off: kartoteka is configured for this project but its MCP tools are not '
    'available in this session',
    'off: kartoteka is configured for this project but knowledge.project is not set',
)
# The three mode reasons that knowledge-consultation.md §1 already spells; the
# contract inherits them byte for byte rather than paraphrasing.
INHERITED = (
    'local-only run requested',
    'kartoteka is configured for this project but its MCP tools are not '
    'available in this session',
    'kartoteka is configured for this project but knowledge.project is not set',
)
BUDGET = '`search_knowledge` at most 16; `related` at most 4; at most two calls per unit'
FORMULA = '(F + 1) / (N + 2)'
KEYS = ('review.forecast.threshold', 'review.forecast.reviewers')
SECTIONS = (
    '## 1. Gate', '## 2. Change units', '## 3. Lookup', '## 4. Classification',
    '## 5. The number', '## 6. Proposed fixes', '## 7. The record',
)


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class TestContract(unittest.TestCase):

    def setUp(self):
        self.text = read(CONTRACT)

    def test_has_the_seven_numbered_sections(self):
        for heading in SECTIONS:
            with self.subTest(heading):
                self.assertIn(heading, self.text)

    def test_spells_every_forecast_mode(self):
        for mode in OFF_MODES:
            with self.subTest(mode):
                self.assertIn(mode, self.text)

    def test_inherited_reasons_match_the_consultation_contract(self):
        consultation = read(CONSULTATION)
        for reason in INHERITED:
            with self.subTest(reason):
                self.assertIn(reason, consultation)
                self.assertIn(reason, self.text)

    def test_states_the_budget_and_the_formula(self):
        self.assertIn(BUDGET, self.text)
        self.assertIn(FORMULA, self.text)

    def test_names_both_config_keys_and_the_default_threshold(self):
        for key in KEYS:
            with self.subTest(key):
                self.assertIn(key, self.text)
        self.assertIn('default `70`', self.text)


class TestAgent(unittest.TestCase):

    def setUp(self):
        self.text = read(AGENT)

    def test_frontmatter_names_the_agent(self):
        head = self.text.split('---')[1]
        self.assertIn('name: review-forecaster', head)
        self.assertIn('model:', head)

    def test_cites_the_contract_and_never_restates_the_formula(self):
        self.assertIn('docs/review-forecast.md', self.text)
        self.assertIn('§5', self.text)
        self.assertNotIn(FORMULA, self.text)

    def test_budget_is_spelled_as_the_contract_spells_it(self):
        self.assertIn(BUDGET, self.text)

    def test_names_the_output_file_the_report_path_and_both_keys(self):
        self.assertIn(OUTPUT_FILE, self.text)
        self.assertIn(REPORT_PATH, self.text)
        for key in KEYS:
            with self.subTest(key):
                self.assertIn(key, self.text)

    def test_carries_the_no_subagent_rule(self):
        self.assertIn('No subagents', self.text)


class TestSkill(unittest.TestCase):

    def setUp(self):
        self.text = read(SKILL)

    def test_argument_hint_carries_local(self):
        hints = [ln for ln in self.text.splitlines() if ln.startswith('argument-hint:')]
        self.assertEqual(1, len(hints))
        self.assertIn('--local', hints[0])

    def test_spells_every_forecast_mode_and_never_the_formula(self):
        for mode in OFF_MODES:
            with self.subTest(mode):
                self.assertIn(mode, self.text)
        self.assertNotIn(FORMULA, self.text)

    def test_dispatches_both_agents_to_the_right_paths(self):
        self.assertIn('subagent_type: "reviewer"', self.text)
        self.assertIn('subagent_type: "review-forecaster"', self.text)
        self.assertIn(REPORT_PATH, self.text)
        self.assertIn('<specs.dir>/<TICKET_ID>/' + OUTPUT_FILE, self.text)

    def test_hands_off_through_the_review_fix_contract(self):
        self.assertIn('AskUserQuestion', self.text)
        self.assertIn('## Code Review Fixes', self.text)
        self.assertIn('Skill: implementer', self.text)
        self.assertNotIn('EnterPlanMode', self.text)

    def test_no_retired_file_is_named(self):
        for name in ('review-claude.md', 'review-second.md', 'review-summary.md'):
            with self.subTest(name):
                self.assertNotIn(name, self.text)


class TestMirrorHook(unittest.TestCase):

    def test_mirrored_set_names_the_new_file_and_not_the_old(self):
        text = read(HOOK)
        self.assertIn("'deep-review.md'", text)
        self.assertNotIn("'review-summary.md'", text)


class TestConfigDoc(unittest.TestCase):

    def setUp(self):
        self.text = read(CONFIG)

    def test_documents_both_keys_with_the_default_threshold(self):
        for key in KEYS:
            with self.subTest(key):
                self.assertIn('`' + key + '`', self.text)
        row = [ln for ln in self.text.splitlines() if ln.startswith('| `review.forecast.threshold`')]
        self.assertEqual(1, len(row))
        self.assertIn('`70`', row[0])

    def test_knowledge_adapter_names_deep_review_as_a_consumer(self):
        row = [ln for ln in self.text.splitlines() if ln.startswith('| `knowledge.adapter`')]
        self.assertEqual(1, len(row))
        self.assertIn('deep-review', row[0])
        self.assertIn('docs/review-forecast.md', self.text)


RETIRED = ('review-second.md', 'review-summary.md', '<TICKET_ID>/review-claude.md')

# docs/design.md is the decision log and records the retirement itself, so it
# is history, like CHANGELOG.md, and not scanned.
LIVE_FILES = tuple(sorted(
    p.relative_to(ROOT).as_posix()
    for pattern in ('docs/*.md', 'skills/*/SKILL.md', 'agents/*.md', 'hooks/*.py')
    for p in ROOT.glob(pattern)
    if p.name != 'design.md'
))


class TestRetiredNames(unittest.TestCase):

    def test_no_live_file_names_a_retired_review_file(self):
        for rel in LIVE_FILES:
            text = read(rel)
            for name in RETIRED:
                with self.subTest(rel=rel, name=name):
                    self.assertNotIn(name, text)

    def test_reference_docs_name_the_new_file(self):
        for rel in ('docs/ticket-parsing.md', REFERENCE):
            with self.subTest(rel):
                self.assertIn(OUTPUT_FILE, read(rel))

    def test_crew_doc_and_router_know_the_new_agent_and_skill(self):
        self.assertIn('review-forecaster', read('docs/agents.md'))
        self.assertIn('/artel:deep-review <ticket> [branch] [pr-link] [--local]',
                      read('skills/using-artel/SKILL.md'))

    def test_reviewer_no_longer_calls_it_a_dual_pass(self):
        self.assertNotIn('dual pass', read('agents/reviewer.md'))


class TestApplyRecordsTheFixes(unittest.TestCase):
    """Step 6 records the fixes it appends; it used to skip the mirror.

    The fix tasks deep-review applies are the longest-running work of a review
    cycle, and they were invisible in the task queue until they were done.
    """

    def setUp(self):
        self.step = read(SKILL).split('## Step 6: Apply')[1].split('## Rules')[0]

    def test_the_mirror_runs_before_the_first_implementer(self):
        self.assertNotIn('Do **not** run the tasklist mirror', self.step)
        mirror = self.step.index('python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py')
        self.assertLess(mirror, self.step.index('Skill: implementer'))
        self.assertIn('data.sections', self.step)

    def test_the_batch_opens_with_a_dated_source_heading(self):
        self.assertIn('### deep-review-<YYYY-MM-DD>', self.step)
        self.assertIn('date +%F', self.step)
        self.assertIn('not their\n   `### Tasks` heading', self.step)

    def test_the_forecast_contract_says_who_adds_the_heading(self):
        self.assertIn('### deep-review-<YYYY-MM-DD>', read(CONTRACT))


if __name__ == '__main__':
    unittest.main()
