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
HOOK = 'hooks/knowledge_mirror.py'
CONSULTATION = 'docs/knowledge-consultation.md'
REFERENCE = 'docs/skills-reference.md'

OUTPUT_FILE = 'deep-review.md'
REPORT_PATH = '.artel/run/<TICKET_ID>/reports/deep-review-findings.md'

OFF_MODES = (
    'off: local-only run requested',
    'off: knowledge.adapter is not kartoteka for this project',
    'off: kartoteka is configured for this project but its MCP tools are not '
    'available in this session',
)
# The two mode reasons that knowledge-consultation.md §1 already spells; the
# contract inherits them byte for byte rather than paraphrasing.
INHERITED = (
    'local-only run requested',
    'kartoteka is configured for this project but its MCP tools are not '
    'available in this session',
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


if __name__ == '__main__':
    unittest.main()
