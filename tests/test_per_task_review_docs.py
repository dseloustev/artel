"""The per-task review and the report-file contract are described identically everywhere.

Not a behaviour test: these are prompts, and there is no code path to exercise.
Same guard as tests/test_task_queue_docs.py, for the same reason -- a gate that
three prose files describe drifts in one of them, and the drift reads as
perfectly sensible in review.

Three things it holds in place:

**The config key exists in every place a key must.** `review.perTask` in the
default block, the key reference and the filled example of config.md, and in
the setup interview -- a key the interview cannot write is a key nobody sets.

**The gate is one procedure with one cap.** `autonomous-run.md` §16 is the
only full description; both orchestrators cite it and the cap by name; the
script, the skill flag and the reviewer's task mode are the three things §16
relies on, and each has to exist.

**The completion is a contract, not a payload.** The implementer agent writes
a report file and returns `Report:`; it no longer returns the diff; and both
worker agents carry the no-subagent rule.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONTRACT = 'docs/autonomous-run.md'
CONFIG = 'docs/config.md'
KEY = 'review.perTask'
CAP = 'MAX_TASK_REVIEW_ROUNDS = 1'

ORCHESTRATORS = ('skills/dev/SKILL.md', 'skills/feature-development/SKILL.md')
WORKER_AGENTS = ('agents/implementer.md', 'agents/reviewer.md')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def section(text, heading, next_heading_re=r'^## '):
    """Body of `heading` up to the next same-level heading."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    match = re.search(next_heading_re, rest, flags=re.MULTILINE)
    return rest[:match.start()] if match else rest


class TestConfigKey(unittest.TestCase):
    def test_default_block_carries_the_key_off(self):
        default = section(read(CONFIG), '## The default config')
        self.assertIn('"review": {\n    "perTask": false\n  }', default)

    def test_key_reference_has_a_review_section(self):
        text = read(CONFIG)
        self.assertIn('### `review` — the review gates', text)
        body = section(text, '### `review` — the review gates', r'^### ')
        self.assertIn('`review.perTask`', body)
        self.assertIn('`false`', body)
        self.assertIn('§16', body)

    def test_filled_example_shows_the_key_on(self):
        example = section(read(CONFIG), '## A filled example')
        self.assertIn('"review": {\n    "perTask": true\n  }', example)

    def test_setup_interview_offers_and_validates_the_key(self):
        text = read('skills/setup/SKILL.md')
        self.assertIn('`review.perTask`', section(text, '## 2. Interview'))
        self.assertIn('`review.perTask`', section(text, '## 3. Validate'))


class TestGateContract(unittest.TestCase):
    def test_contract_has_section_16_with_the_cap_and_the_script(self):
        text = read(CONTRACT)
        self.assertIn('## 16. Per-task review', text)
        body = section(text, '## 16. Per-task review')
        self.assertIn(CAP, body)
        self.assertIn('scripts/review_package.py snapshot', body)
        self.assertIn('scripts/review_package.py diff', body)
        self.assertIn('run-reviewer', body)
        self.assertIn('## Code Review Fixes', body)

    def test_cap_table_has_the_row(self):
        table = section(read(CONTRACT), '## 5. Capped loops')
        self.assertIn('`' + CAP + '`', table)
        self.assertIn('§16', table)

    def test_run_dir_tree_lists_the_reports(self):
        tree = read(CONTRACT).split('## 1. Principles')[0]
        for entry in ('reports/', 'NNN-<slug>.md', 'NNN-<slug>.diff', 'NNN-<slug>-review.md'):
            self.assertIn(entry, tree, 'run-dir tree lacks ' + entry)

    def test_both_orchestrators_cite_the_section_the_key_and_the_cap(self):
        for rel in ORCHESTRATORS:
            text = read(rel)
            self.assertIn('§16', text, rel + ' does not cite autonomous-run.md §16')
            self.assertIn(KEY, text, rel + ' does not name ' + KEY)
            self.assertIn(CAP, text, rel + ' does not name the cap')
            self.assertIn('review_package.py snapshot', text, rel + ' never snapshots')

    def test_script_exists_and_has_both_commands(self):
        path = ROOT / 'scripts' / 'review_package.py'
        self.assertTrue(path.is_file())
        text = path.read_text(encoding='utf-8')
        self.assertIn("add_parser('snapshot'", text)
        self.assertIn("add_parser('diff'", text)

    def test_run_reviewer_has_the_task_flags_and_no_fallback(self):
        text = read('skills/run-reviewer/SKILL.md')
        for flag in ('--task', '--report', '--package'):
            self.assertIn(flag, text)
        self.assertIn('Mode: task', text)
        self.assertIn('never fall back to the\nticket review', text)

    def test_reviewer_agent_task_mode_leaves_the_phase_review_artifacts_alone(self):
        text = read('agents/reviewer.md')
        self.assertIn('## Task mode', text)
        body = section(text, '## Task mode')
        self.assertIn('NNN-<slug>-review.md', body)
        self.assertIn('## Code Review Fixes', body)
        self.assertIn('does not write `review.md`', body)
        self.assertIn('does not write `review/findings.json`', body)
        self.assertIn('does not touch `**Review round:**`', body)


class TestCompletionContract(unittest.TestCase):
    def test_implementer_writes_a_report_and_returns_its_path(self):
        text = read('agents/implementer.md')
        step6 = section(text, '### Step 6 — Report', r'^(### |---)')
        self.assertIn('.artel/run/<TICKET_ID>/reports/NNN-<slug>.md', step6)
        self.assertIn('`Report: <path>`', step6)
        self.assertIn('**paths only**', step6)
        self.assertNotIn('(with the actual diff)', step6)

    def test_implementer_skill_relays_without_opening_the_report(self):
        text = read('skills/implementer/SKILL.md')
        self.assertIn('`Report: <path>`', text)
        self.assertIn('Do not open\nthe report file', text)

    def test_principle_is_stated_once_and_cited_by_the_orchestrators(self):
        self.assertIn('**Bulk stays in files.**', section(read(CONTRACT), '## 1. Principles'))
        for rel in ORCHESTRATORS:
            self.assertIn('Bulk stays in files', read(rel), rel + ' does not cite the principle')
            self.assertIn('`Report:`', read(rel), rel + ' does not journal the report path')

    def test_worker_agents_carry_the_no_subagent_rule(self):
        for rel in WORKER_AGENTS:
            self.assertIn('**No subagents**', read(rel), rel + ' lacks the no-subagent rule')


class TestReviewFixesAreRecorded(unittest.TestCase):
    """The reviewer writes the fix batch; run-reviewer records it in the queue.

    The agent never writes to kartoteka. The skill that dispatched it mirrors
    the batch before any implementer is dispatched, and honours --local, which
    feature-development passes on (docs/task-queue.md §2, §6).
    """

    def test_ticket_mode_opens_its_batch_with_the_round(self):
        ticket = section(read('agents/reviewer.md'), '## Ticket mode')
        self.assertIn('### review-r<R>', ticket)
        self.assertIn('### review-p<PHASE_NUM>-r<R>', ticket)

    def test_task_mode_opens_its_batch_with_the_report_number(self):
        self.assertIn('### task-gate-<NNN>',
                      section(read('agents/reviewer.md'), '## Task mode'))

    def test_the_agent_never_writes_rows(self):
        text = read('agents/reviewer.md')
        for name in ('task_create', 'task_update'):
            self.assertNotIn(name, text)

    def test_run_reviewer_mirrors_the_batch_and_takes_local(self):
        text = read('skills/run-reviewer/SKILL.md')
        hint = [ln for ln in text.splitlines() if ln.startswith('argument-hint:')][0]
        self.assertIn('--local', hint)
        self.assertIn('## Record the fix tasks', text)
        self.assertIn('python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py', text)
        self.assertIn('data.sections', text)

    def test_feature_development_passes_local_to_both_review_calls(self):
        text = read('skills/feature-development/SKILL.md')
        gate7 = text.split('| 7 | `REVIEW_OK` |')[1].split('\n')[0]
        self.assertIn('--local', gate7)
        gate5 = text.split('| 5 | `IMPLEMENT_STEP_OK`')[1].split('\n')[0]
        self.assertIn('`Skill: run-reviewer --task …` (plus `--local` when this run was'
                      ' invoked with it)', gate5)

    def test_section_16_names_the_task_gate_source(self):
        self.assertIn('### task-gate-<NNN>', section(read(CONTRACT), '## 16.'))


if __name__ == '__main__':
    unittest.main()
