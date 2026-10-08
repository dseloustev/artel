"""deep-review's single-file contract is spelled identically everywhere it appears.

Not a behaviour test: these are prompts, and there is no code path to exercise.
It guards the strings the skill, the reviewer agent, the mirror hook and the
reference docs must agree on -- the output filename, the single reviewer
dispatch, the fix-task handoff -- and the absence of the retired forecast
machinery. Prose is deliberately not asserted on.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILL = 'skills/deep-review/SKILL.md'
REVIEWER = 'agents/reviewer.md'
CONFIG = 'docs/config.md'
HOOK = 'hooks/kartoteka_http.py'  # MIRRORED's home; knowledge_mirror.py imports it
REFERENCE = 'docs/skills-reference.md'

OUTPUT_FILE = 'deep-review.md'
RETURN_LINE = 'Deep review: <c> Critical, <w> Warning, <s> Suggestion'
TASK_BLOCKS = ('### Tasks (Critical)', '### Tasks (Warning)')
RETIRED_FORECAST = ('review-forecaster', 'review.forecast.', 'reviewForecaster',
                    'docs/review-forecast.md', 'pass%', 'Forecast:')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class TestSkill(unittest.TestCase):

    def setUp(self):
        self.text = read(SKILL)

    def test_argument_hint_carries_local(self):
        hints = [ln for ln in self.text.splitlines() if ln.startswith('argument-hint:')]
        self.assertEqual(1, len(hints))
        self.assertIn('--local', hints[0])

    def test_dispatches_one_reviewer_to_the_ticket_document(self):
        self.assertIn('subagent_type: "reviewer"', self.text)
        self.assertNotIn('subagent_type: "review-forecaster"', self.text)
        self.assertIn('<specs.dir>/<TICKET_ID>/' + OUTPUT_FILE, self.text)

    def test_has_no_forecast_machinery(self):
        for phrase in RETIRED_FORECAST:
            with self.subTest(phrase):
                self.assertNotIn(phrase, self.text)

    def test_reads_the_counts_from_the_return_line_not_the_file(self):
        self.assertIn(RETURN_LINE, self.text)
        self.assertIn('Do not open the file to recount', self.text)

    def test_offers_the_two_task_blocks(self):
        for block in TASK_BLOCKS:
            with self.subTest(block):
                self.assertIn(block, self.text)

    def test_hands_off_through_the_review_fix_contract(self):
        self.assertIn('AskUserQuestion', self.text)
        self.assertIn('## Code Review Fixes', self.text)
        self.assertIn('Skill: implementer', self.text)
        self.assertNotIn('EnterPlanMode', self.text)

    def test_no_retired_file_is_named(self):
        for name in ('review-claude.md', 'review-second.md', 'review-summary.md'):
            with self.subTest(name):
                self.assertNotIn(name, self.text)


class TestReviewer(unittest.TestCase):

    def setUp(self):
        self.text = read(REVIEWER)

    def test_standalone_deep_review_output_names_the_header(self):
        self.assertIn('type: deep-review', self.text)
        self.assertIn('produced_by: artel:reviewer', self.text)

    def test_the_output_carries_the_two_task_blocks_and_the_return_line(self):
        for phrase in (*TASK_BLOCKS, RETURN_LINE):
            with self.subTest(phrase):
                self.assertIn(phrase, self.text)

    def test_the_skill_adds_the_source_heading(self):
        self.assertIn('### deep-review-<YYYY-MM-DD>', self.text)
        self.assertIn('docs/task-queue.md', self.text)

    def test_never_names_the_forecaster(self):
        for phrase in ('review-forecaster', 'review.forecast.', 'reviewForecaster'):
            with self.subTest(phrase):
                self.assertNotIn(phrase, self.text)


class TestMirrorHook(unittest.TestCase):

    def test_mirrored_set_names_the_file_and_not_the_old(self):
        text = read(HOOK)
        self.assertIn("'deep-review.md'", text)
        self.assertNotIn("'review-summary.md'", text)


class TestConfigDoc(unittest.TestCase):

    def setUp(self):
        self.text = read(CONFIG)

    def test_the_forecast_keys_and_site_are_gone(self):
        for phrase in ('review.forecast.threshold', 'review.forecast.reviewers',
                       'reviewForecaster', 'review-forecaster'):
            with self.subTest(phrase):
                self.assertNotIn(phrase, self.text)

    def test_the_deep_review_site_stays(self):
        self.assertIn('`models.opencode.reviewer.deepReview`', self.text)


RETIRED = ('review-second.md', 'review-summary.md', '<TICKET_ID>/review-claude.md')

# docs/design.md is the decision log and records the retirement itself, so it
# is history, like CHANGELOG.md, and not scanned.
LIVE_FILES = tuple(sorted(
    p.relative_to(ROOT).as_posix()
    for pattern in ('docs/*.md', 'skills/*/SKILL.md', 'skills/*/heads/*.md', 'skills/*/tail.md',
                    'agents/*.md', 'hooks/*.py')
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

    def test_no_live_file_names_the_retired_forecast(self):
        for rel in LIVE_FILES:
            text = read(rel)
            for phrase in RETIRED_FORECAST:
                with self.subTest(rel=rel, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_reference_docs_name_the_new_file(self):
        for rel in ('docs/ticket-parsing.md', REFERENCE):
            with self.subTest(rel):
                self.assertIn(OUTPUT_FILE, read(rel))

    def test_crew_doc_and_router_know_the_skill(self):
        self.assertNotIn('review-forecaster', read('docs/agents.md'))
        self.assertIn('/artel:deep-review <ticket> [branch] [pr-link] [--local]',
                      read('skills/using-artel/SKILL.md'))

    def test_reviewer_no_longer_calls_it_a_dual_pass(self):
        self.assertNotIn('dual pass', read(REVIEWER))


class TestApplyRecordsTheFixes(unittest.TestCase):
    """Step 5 records the fixes it appends; it used to skip the mirror.

    The fix tasks deep-review applies are the longest-running work of a review
    cycle, and they were invisible in the task queue until they were done.
    """

    def setUp(self):
        self.step = read(SKILL).split('## Step 5: Apply')[1].split('## Rules')[0]

    def test_the_mirror_runs_before_the_first_implementer(self):
        self.assertNotIn('Do **not** run the tasklist mirror', self.step)
        mirror = self.step.index('python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py')
        self.assertLess(mirror, self.step.index('Skill: implementer'))
        self.assertIn('data.sections', self.step)

    def test_the_batch_opens_with_a_dated_source_heading(self):
        self.assertIn('### deep-review-<YYYY-MM-DD>', self.step)
        self.assertIn('date +%F', self.step)
        self.assertIn('not their\n   `### Tasks` heading', self.step)

    def test_the_reviewer_contract_says_who_adds_the_heading(self):
        self.assertIn('### deep-review-<YYYY-MM-DD>', read(REVIEWER))


if __name__ == '__main__':
    unittest.main()
