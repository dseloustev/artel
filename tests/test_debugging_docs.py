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


if __name__ == '__main__':
    unittest.main()
