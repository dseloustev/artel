"""`docs/gates.md` exists, has the shape the runner implements, and the surrounding docs
point at it. Plan 2 extends this file with the "no live skill restates the full gate" rule
once the conversion has happened; until then the gates are a runner nobody calls."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class TestGatesContract(unittest.TestCase):
    def setUp(self):
        self.doc = read('docs/gates.md')

    def test_sections(self):
        for heading in ('## 1. The schedule', '## 2. Invoking the runner', '## 3. The envelope',
                        '## 4. Rules', '## 5. What the baseline cannot see'):
            self.assertIn(heading, self.doc)

    def test_schedule_rows(self):
        for gate in ('task', 'checkpoint', 'final', 'baseline'):
            self.assertRegex(self.doc, r'(?m)^\| \*\*{}\*\* \|'.format(gate), gate)

    def test_invocations_match_the_runner(self):
        self.assertIn('verify.py task --files', self.doc)
        self.assertIn('verify.py checkpoint --record-baseline', self.doc)
        self.assertIn('.artel/run/<TICKET_ID>/verify-baseline.json', self.doc)

    def test_rules_are_stated(self):
        for phrase in ('never green', 'environment error', 'runs nowhere else',
                       'baseline_red', 'new_keys'):
            self.assertIn(phrase, self.doc)

    def test_no_placeholders(self):
        self.assertNotRegex(self.doc, r'\b(TBD|TODO)\b')


class TestSurroundingDocs(unittest.TestCase):
    def test_config_documents_the_three_keys(self):
        config = read('docs/config.md')
        for key in ('`verify.test`', '`verify.testSurface`', '`verify.baseline`'):
            self.assertIn(key, config)
        self.assertIn('"test": ""', config)
        self.assertIn('"baseline": true', config)

    def test_runner_docstring_cites_the_contract(self):
        head = read('scripts/verify.py').split('"""', 2)[1]
        self.assertIn('docs/gates.md', head)

    def test_changelog_announces_the_runner(self):
        unreleased = read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [', 1)[0]
        for phrase in ('docs/gates.md', 'verify.py task', 'verify.py checkpoint', 'verify.test'):
            self.assertIn(phrase, unreleased)


def section(text, start, end=None):
    """The text between two anchors (to the end when `end` is None)."""
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end else body


PROMPT_FILES = sorted(
    [str(p.relative_to(ROOT)) for p in (ROOT / 'agents').glob('*.md')]
    + [str(p.relative_to(ROOT)) for p in (ROOT / 'skills').glob('*/SKILL.md')]
)


class TestTaskLoop(unittest.TestCase):
    """Task 1 of the conversion: the task loop runs the task gate and nothing else."""

    def test_inner_loop_runs_the_task_gate_only(self):
        text = read('skills/inner-loop/SKILL.md')
        self.assertIn('verify.py task --files', text)
        self.assertIn('docs/gates.md', text)
        self.assertNotIn('iteration-<i>-full', text)
        self.assertNotIn('run the full gate', text)

    def test_implementer_closes_on_the_task_gate(self):
        text = read('agents/implementer.md')
        step_four = section(text, '### Step 4', '### Step 5')
        self.assertIn('docs/gates.md', step_four)
        self.assertIn('never yours', step_four)
        step_five = section(text, '### Step 5', '### Step 6')
        self.assertIn('last task gate is green or skipped', step_five)
        self.assertNotIn('unscoped verify', text)
        rule = section(text, '- **Gate before done**', '\n- ')
        self.assertIn('task gate', rule)

    def test_implementer_step_one_still_works_a_legacy_final_verification_section(self):
        # Review Focus 2: older tasklists carry the section; the file scan keeps it.
        step_one = section(read('agents/implementer.md'), '### Step 1', '### Step 2')
        self.assertIn('Final Verification', step_one)

    def test_implementer_skill_dispatch_names_the_task_gate(self):
        text = read('skills/implementer/SKILL.md')
        self.assertIn('task gate', text)
        self.assertIn('docs/gates.md', text)
        self.assertNotIn('unscoped verify green', text)

    def test_tasklist_writer_stops_emitting_final_verification(self):
        text = read('agents/tasklist-writer.md')
        self.assertNotIn('Required `## Final Verification` section', text)
        self.assertNotIn('Run every command in `verify.commands`', text)
        self.assertIn('### No `## Final Verification` section', text)
        self.assertIn('docs/gates.md', text)
        after = section(text, '### After changes', '**Test:**')
        self.assertIn('task gate', after)

    def test_task_planner_forbids_gate_tasks(self):
        text = read('agents/task-planner.md')
        self.assertIn('**No gate tasks.**', text)
        self.assertIn('no `## Final Verification` section', text)

    def test_no_prompt_file_runs_the_full_gate_per_task(self):
        for rel in PROMPT_FILES:
            with self.subTest(rel):
                text = read(rel)
                self.assertNotIn('Run every command in `verify.commands`', text)
                self.assertNotIn('iteration-<i>-full', text)


if __name__ == '__main__':
    unittest.main()
