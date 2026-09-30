"""The debugging discipline: one contract (docs/debugging.md) and the places that follow it --
the inner loop, the implementer, the fix-row writers, the reviewer and the /artel:debugging
skill -- plus the third-party notice for the text the contract adapts.

Not a behaviour test: these are prompts, and there is no code path to exercise. Same guard as
tests/test_gates_docs.py -- a section renamed or a citation dropped fails here instead of in a
run nobody is watching. Phrases are matched with whitespace collapsed, so re-wrapping a
paragraph never breaks a pin.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONTRACT = 'docs/debugging.md'
NOTICES = 'THIRD_PARTY_NOTICES.md'
INNER_LOOP = 'skills/inner-loop/SKILL.md'
IMPLEMENTER = 'agents/implementer.md'
REVIEWER = 'agents/reviewer.md'
FORECASTER = 'agents/review-forecaster.md'
DEEP_REVIEW = 'skills/deep-review/SKILL.md'
TASKS = 'skills/tasks/SKILL.md'
SKILL = 'skills/debugging/SKILL.md'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return re.sub(r'\s+', ' ', text)


class TestContract(unittest.TestCase):
    def setUp(self):
        self.doc = flat(read(CONTRACT))

    def section(self, start, end=None):
        body = self.doc.split(start, 1)[1]
        return body.split(end, 1)[0] if end else body

    def test_sections(self):
        for heading in ('## 1. The rule', '## 2. The four phases', '### 2.1 Investigate',
                        '### 2.2 Compare', '### 2.3 Hypothesise', '### 2.4 Fix',
                        '## 3. Evidence', '## 4. When the fix is structural',
                        '## 5. No root cause found', '## 6. Special cases',
                        '## 7. Where this applies in artel'):
            self.assertIn(heading, self.doc)

    def test_provenance_names_superpowers_and_mit(self):
        head = self.doc.split('## 1. The rule', 1)[0]
        for phrase in ('superpowers', '`systematic-debugging`', 'MIT', 'THIRD_PARTY_NOTICES.md'):
            self.assertIn(phrase, head)

    def test_expected_red_is_not_a_bug(self):
        self.assertIn('It is not for an expected red', self.section('## 1. The rule', '## 2.'))

    def test_failing_test_comes_first(self):
        fix = self.section('### 2.4 Fix', '## 3. Evidence')
        self.assertIn('**before** any fix is applied', fix)
        self.assertIn('Never weaken the failing test', fix)
        self.assertIn('Three failed fixes are structural', fix)

    def test_repro_id_grammar(self):
        evidence = self.section('## 3. Evidence', '## 4. When')
        for phrase in ('repro-<code>-<source>-<N>', '(`tasklist` when there is none)',
                       '1-based position', 'repro-crf-review-r2-3', '`.json`', '`.txt`',
                       'line 2 `exit <code>` (non-zero)', '.artel/run/repro/',
                       '## Verify iterations', 'A `skipped` stage is never red evidence'):
            self.assertIn(phrase, evidence)

    def test_structural_maps_to_the_deviation_protocol(self):
        structural = self.section('## 4. When the fix is structural', '## 5.')
        for phrase in ('an interface change', 'a design reversal', 'scope growth',
                       'Three failed fixes are structural too', '§2 **Major**',
                       '[deviation-protocol.md](deviation-protocol.md) §4',
                       '/artel:issue-draft'):
            self.assertIn(phrase, structural)

    def test_secrets_rule_names_kartoteka_documents(self):
        special = self.section('## 6. Special cases', '## 7.')
        self.assertIn('**Secrets.**', special)
        self.assertIn('a kartoteka document', special)

    def test_where_it_applies(self):
        table = self.section('## 7. Where this applies in artel')
        for reader in ('skills/inner-loop', 'agents/implementer.md', 'agents/reviewer.md',
                       '/artel:debugging'):
            self.assertIn(reader, table)

    def test_no_placeholders(self):
        self.assertNotRegex(self.doc, r'\b(TBD|TODO)\b')


class TestNotices(unittest.TestCase):
    def test_carries_the_superpowers_notice(self):
        text = flat(read(NOTICES))
        for phrase in ('https://github.com/obra/superpowers', 'docs/debugging.md',
                       'Copyright (c) 2025 Jesse Vincent',
                       'Permission is hereby granted, free of charge',
                       'The above copyright notice and this permission notice shall be included'):
            self.assertIn(phrase, text)


class TestInnerLoop(unittest.TestCase):
    def setUp(self):
        self.doc = flat(read(INNER_LOOP))

    def test_budget_is_unchanged(self):
        self.assertIn('MAX_VERIFY_ITERATIONS = 4', self.doc)

    def test_fast_red_is_fixed_per_finding(self):
        self.assertIn("red stage fast → fix minimally, targeting each finding's file:line", self.doc)

    def test_test_red_is_debugged(self):
        for phrase in ('red stage test → read its keys and tail in full', 'ONE hypothesis',
                       '**A red `test` stage is debugged, not patched**',
                       '${CLAUDE_PLUGIN_ROOT}/docs/debugging.md'):
            self.assertIn(phrase, self.doc)

    def test_structural_cause_stops_at_once(self):
        self.assertIn('a structural cause (§4) → STOP as a Major deviation', self.doc)

    def test_never_weaken_a_failing_test(self):
        self.assertIn('**Never weaken a failing test.**', self.doc)


class TestImplementer(unittest.TestCase):
    def setUp(self):
        self.doc = flat(read(IMPLEMENTER))

    def test_behavioural_rows_are_defined(self):
        for phrase in ('**A behavioural fix-section row is reproduced first.**',
                       'a `## Code Review Fixes` row whose checkbox text carries `behavior`',
                       'every `## Runtime Fixes` row',
                       'a `## Verify Fixes` row whose finding is a failing test'):
            self.assertIn(phrase, self.doc)

    def test_reproduction_evidence(self):
        for phrase in ('verify/repro-<code>-<source>-<N>.json',
                       'verify/repro-<code>-<source>-<N>.txt',
                       'a `skipped` stage is never red evidence', '.artel/run/repro/',
                       '${CLAUDE_PLUGIN_ROOT}/docs/debugging.md'):
            self.assertIn(phrase, self.doc)

    def test_red_evidence_needs_a_red_test_stage(self):
        for phrase in ('The new test file must pass `fast` first',
                       'A `test` stage `skipped` for any reason'):
            self.assertIn(phrase, self.doc)

    def test_task_gate_red_test_stage(self):
        self.assertIn('A red `test` stage is debugged, not patched', self.doc)

    def test_report_lines(self):
        for phrase in ('the `## Verify iterations` table', '`**Root cause:**`',
                       '`**Reproduction:**`'):
            self.assertIn(phrase, self.doc)

    def test_never_weaken_a_failing_test(self):
        self.assertIn('**Never weaken a failing test**', self.doc)

class TestFixWriters(unittest.TestCase):
    def test_reviewer_marks_behavior_in_both_modes(self):
        doc = flat(read(REVIEWER))
        for phrase in ('`**Task N (Blocking, behavior): …**`', 'carry no marker',
                       'The `behavior` marker applies as in ticket mode'):
            self.assertIn(phrase, doc)

    def test_forecaster_marks_behavior(self):
        doc = flat(read(FORECASTER))
        self.assertIn('`**Task N (behavior): …**`', doc)
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/docs/debugging.md', doc)

    def test_deep_review_keeps_the_marker_on_renumbering(self):
        self.assertIn('a `(behavior)` marker stays in place', flat(read(DEEP_REVIEW)))

    def test_tasks_add_fix_documents_the_marker(self):
        doc = flat(read(TASKS))
        self.assertIn('`(behavior)`', doc)
        self.assertIn('docs/debugging.md', doc)


class TestReviewer(unittest.TestCase):
    def setUp(self):
        self.doc = flat(read(REVIEWER))

    def test_behavioural_fix_check(self):
        for phrase in ('### Behavioural-fix check', 'repro-crf-<source>-<N>',
                       '`"ok": false`', 'a non-zero `exit`', 'an **Important** finding',
                       'The evidence is the file, not the report', 'docs/debugging.md'):
            self.assertIn(phrase, self.doc)

    def test_only_marked_rows_are_checked(self):
        # The implementer's own grammar: priority rows, forecaster rows and manual rows alike.
        self.assertIn('whose checkbox text carries `behavior` in its parenthetical', self.doc)
        self.assertIn('this check does not read them', self.doc)

    def test_every_round_checks_and_flags_a_row_once(self):
        self.assertNotIn('2 or higher', self.doc)
        self.assertIn('A row already flagged in an earlier round is not flagged again', self.doc)

    def test_a_skipped_envelope_is_not_evidence(self):
        self.assertIn('one whose `test` stage is not red (green or `skipped`)', self.doc)

class TestSkill(unittest.TestCase):
    def setUp(self):
        self.raw = read(SKILL)
        self.doc = flat(self.raw)

    def test_frontmatter(self):
        self.assertTrue(self.raw.startswith('---\nname: debugging\n'))
        frontmatter = self.raw.split('---', 2)[1]
        description = re.search(r'(?m)^description: (.*)$', frontmatter).group(1)
        self.assertIn('artel-configured repo', description)
        self.assertLessEqual(len(description), 1024)
        self.assertNotRegex(frontmatter, r'(?m)^model:')
        self.assertNotRegex(frontmatter, r'(?m)^disable-model-invocation:')

    def test_reads_the_contract_first(self):
        self.assertIn('**The discipline is `${CLAUDE_PLUGIN_ROOT}/docs/debugging.md`', self.doc)

    def test_reproduces_through_the_gate(self):
        for phrase in ('python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py task --files',
                       'No config →', 'a `skipped` stage is never red evidence'):
            self.assertIn(phrase, self.doc)

    def test_knowledge_search_is_conditional(self):
        for phrase in ('With `knowledge.adapter: "kartoteka"`', '`Skill: knowledge`',
                       'With the adapter off or absent, skip this step and say nothing about it'):
            self.assertIn(phrase, self.doc)

    def test_structural_hands_off_to_issue_draft(self):
        for phrase in ('/artel:issue-draft <the report> --type bug',
                       '/artel:issue-draft <the report> --type task', '`AskUserQuestion`'):
            self.assertIn(phrase, self.doc)

    def test_footprint(self):
        self.assertIn('**Never commits, pushes or opens a PR.**', self.doc)
        self.assertIn('**Never touches the spec trail**', self.doc)
        self.assertNotIn('subagent_type', self.doc)

class TestReleaseDocs(unittest.TestCase):
    def test_readme_lists_the_skill(self):
        self.assertIn('`debugging` (root cause before a fix)', read('README.md'))

    def test_design_records_the_decisions(self):
        doc = flat(read('docs/design.md'))
        for phrase in ('**2026-09-29 — Debugging is a contract, a pipeline rule and an inline skill.**',
                       '**2026-09-29 — No whiteboard.**',
                       "**2026-09-29 — SDD v2's sizing, `Implements` line and plan-review rubric wait for sub-project 2**",
                       "**kartoteka's dashboard shows Mermaid as source.**",
                       '**2026-09-29 — Artel is validated by use on real projects, not by separate smoke tests.**'):
            self.assertIn(phrase, doc)

    def test_skills_reference_has_an_entry(self):
        doc = flat(read('docs/skills-reference.md'))
        self.assertIn('### debugging', doc)
        entry = doc.split('### debugging', 1)[1].split('### ', 1)[0]
        for field in ('**Purpose:**',
                      '**Invocation:** `/artel:debugging [symptom | failing test | error text]'
                      ' or <ticket-id> --diagnose [--local]`',
                      '**Reads:**', '**Writes:**', '**Pauses:**', '**Notes:**'):
            self.assertIn(field, entry)

    def test_changelog_is_accurate_about_tickets_in_flight(self):
        since = flat(read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [0.21.0]', 1)[0])
        self.assertIn('open `## Runtime Fixes` and failing-test `## Verify Fixes` rows are reproduced '
                      'first from this release on', since)

    def test_changelog_announces_it(self):
        # Everything since 0.21.0: [Unreleased] before the release is cut, [0.22.0] after.
        since = flat(read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [0.21.0]', 1)[0])
        for phrase in ('/artel:debugging', 'docs/debugging.md', 'THIRD_PARTY_NOTICES.md',
                       '`behavior`', 'verify/repro-<code>-<source>-<N>.json', 'repro-<id>.txt',
                       '**Owed from 0.18.0**', 'baseline: absent'):
            self.assertIn(phrase, since)

if __name__ == '__main__':
    unittest.main()
