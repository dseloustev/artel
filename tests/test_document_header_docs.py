"""The document header and references reach every contract, gate and writer
(design 2026-09-24, §4 and §7). Spellings only, not prose."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORAGE = 'docs/spec-storage.md'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


def section(text, heading):
    start = text.index(heading)
    ends = [i for i in (text.find('\n### ', start + len(heading)),
                        text.find('\n## ', start + len(heading))) if i != -1]
    return text[start:min(ends) if ends else None]


class TestHeaderContract(unittest.TestCase):
    def setUp(self):
        self.text = read(STORAGE)

    def test_the_header_section_gives_the_field_order(self):
        header = section(self.text, '### 3.2 The document header')
        order = [m.group(1) for m in re.finditer(r'^([a-z_]+): ', header, re.M)]
        self.assertEqual(order[:8], ['type', 'ticket', 'version', 'title', 'status', 'summary',
                                     'schema', 'produced_by'])

    def test_every_write_carries_the_version_and_its_guard(self):
        ops = flat(section(self.text, '### 4.1 Agents'))
        for phrase in ('`version: 1`', 'expected_version=0', '`version: <N+1>`',
                       'ticket: <T>\\nversion: <N>\\n', 'the version bump first'):
            self.assertIn(phrase, ops)
        self.assertNotIn('no `expected_version` unless', ops)

    def test_scripts_read_status_and_post_bodies_through_the_verbs(self):
        scripts = section(self.text, '### 4.2 Scripts')
        self.assertIn('spec_store.py status', scripts)
        self.assertIn('spec_store.py body', scripts)

    def test_the_review_reset_carries_a_header(self):
        self.assertIn('type: review', section(self.text, '### 4.4 The review-round reset'))

    def test_the_verb_table_has_status_and_body(self):
        verbs = section(self.text, '## 8. spec_store.py')
        self.assertIn('| `status` (stdin) |', verbs)
        self.assertIn('| `body` (stdin) |', verbs)


if __name__ == '__main__':
    unittest.main()
