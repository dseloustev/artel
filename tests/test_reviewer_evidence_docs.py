"""The review-evidence contract: anchors, scope, triage.

Four mechanics ported from AdGuard's `code-review` plugin (see
docs/comparisons/adguard-code-review-vs-deep-review.md) land in prose, not code: the
reviewer names its evidence, validates anchors mechanically, triages large diffs, and
routes pre-existing defects instead of dressing them as this change's findings. These
pins hold the contract across the reviewer, the forecast contract and the forecaster --
the same guard tests/test_deep_review_docs.py runs for the deep-review file itself.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REVIEWER = 'agents/reviewer.md'
CONTRACT = 'docs/review-forecast.md'
FORECASTER = 'agents/review-forecaster.md'
SCRIPT = 'scripts/validate_findings.py'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class TestReviewerEvidence(unittest.TestCase):

    def setUp(self):
        self.text = read(REVIEWER)

    def test_every_finding_names_its_evidence(self):
        self.assertIn('observed failure or the violated contract', self.text)

    def test_reading_only_correctness_claims_say_so(self):
        self.assertIn('rests on reading, not a run', self.text)

    def test_anchors_are_validated_mechanically(self):
        self.assertIn('scripts/validate_findings.py', self.text)
        self.assertIn('--range', self.text)

    def test_pre_existing_findings_are_routed_not_dropped(self):
        self.assertIn('Pre-existing issues (out of diff)', self.text)
        self.assertIn('"scope": "repository"', self.text)

    def test_large_diffs_are_triaged(self):
        for phrase in ('400 changed lines or 30 changed files',
                       'Classify files first',
                       'Read risk-first',
                       'Consolidate repeated patterns',
                       'Staged passes',
                       'left unreviewed'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.text)


class TestForecastRoutesPreExisting(unittest.TestCase):

    def test_the_contract_routes_repository_scope_away_from_the_tables(self):
        text = read(CONTRACT)
        self.assertIn('"scope": "repository"', text)
        self.assertIn('attaches to no unit', text)
        self.assertIn('pre-existing issue', text)

    def test_the_contract_says_anchors_are_validated(self):
        self.assertIn('scripts/validate_findings.py', read(CONTRACT))

    def test_the_forecaster_template_carries_the_pre_existing_section(self):
        self.assertIn('Pre-existing issues (out of diff)', read(FORECASTER))


class TestValidatorScript(unittest.TestCase):

    def test_the_script_ships_with_both_diff_sources_and_the_check(self):
        self.assertTrue((ROOT / SCRIPT).is_file())
        text = read(SCRIPT)
        for phrase in ("'--diff'", "'--range'", 'def parse_diff', 'def check_findings'):
            with self.subTest(phrase):
                self.assertIn(phrase, text)


if __name__ == '__main__':
    unittest.main()
