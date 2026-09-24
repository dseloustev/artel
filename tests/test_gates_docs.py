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


if __name__ == '__main__':
    unittest.main()
