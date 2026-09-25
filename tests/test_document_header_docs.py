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


class TestReferencesContract(unittest.TestCase):
    def test_spec_storage_defines_the_reference_and_its_rules(self):
        cite = flat(section(read(STORAGE), '### 3.1 Citing a document'))
        for phrase in ('workspace:<TICKET_KEY>/<stage>/<name>[@v<N>]', '- ref:',
                       'Copy it, never compose it', '**Inputs:**', 'named in prose'):
            self.assertIn(phrase, cite)

    def test_path_conventions_bans_run_state_and_allows_references(self):
        text = flat(read('docs/path-conventions.md'))
        self.assertIn('`.artel/…`', text)
        self.assertIn('workspace:<TICKET_KEY>/<stage>/<name>[@v<N>]', text)
        self.assertIn('spec-storage.md) §3.1', text)
        self.assertIn(r'\.artel/', read('docs/path-conventions.md'))


PROMPTS = sorted([*(ROOT / 'agents').glob('*.md'), *(ROOT / 'skills').glob('*/SKILL.md'),
                  *(p for p in (ROOT / 'docs').glob('*.md'))])


class TestGates(unittest.TestCase):
    def test_no_prompt_or_doc_greps_for_a_status_line(self):
        for path in PROMPTS:
            with self.subTest(path.name):
                self.assertNotIn("grep -m1 'Status:'", path.read_text(encoding='utf-8'))

    def test_the_gates_read_status_through_the_verb(self):
        for rel in ('skills/feature-development/SKILL.md', 'skills/figma-analysis/SKILL.md'):
            with self.subTest(rel):
                self.assertIn('spec_store.py status', read(rel))

    def test_no_gate_names_a_capitalised_status_line(self):
        for rel in ('skills/feature-development/SKILL.md', 'skills/figma-analysis/SKILL.md',
                    'agents/validator.md', 'docs/autonomous-run.md'):
            with self.subTest(rel):
                self.assertIsNone(re.search(r'`Status: [A-Z_]+`', read(rel)))


if __name__ == '__main__':
    unittest.main()
