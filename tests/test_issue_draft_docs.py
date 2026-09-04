"""issue-draft's contract is spelled in three places that must agree: a
self-describing template (each section's first comment word is its rule), a
skill that reads that template and kartoteka in the shapes the read-side
contract fixes, and the docs that name the host override. Not a behaviour
test: these are prompts and a template, and there is no code path to
exercise. Prose is deliberately not asserted on; only the spellings are."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILL = 'skills/issue-draft/SKILL.md'
TEMPLATE = 'skills/issue-draft/assets/templates/description.template.md'
OVERRIDE = '.artel/templates/issue-draft.md'
CONFIG = 'docs/config.md'
REFERENCE = 'docs/skills-reference.md'
CONSULTATION = 'docs/knowledge-consultation.md'

HEADING = re.compile(r'^## (.+)$')
RULE = re.compile(r'^<!-- (required|optional)\b')
PLACEHOLDER = re.compile(r'^\$[A-Z_]+$')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def sections(text):
    """(heading, rule word, placeholder) per `## ` section, in order.

    The rule is the first non-blank line after the heading; the placeholder is
    the first non-blank line after the rule comment closes.
    """
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        rule = RULE.match(lines[j]) if j < len(lines) else None
        while j < len(lines) and '-->' not in lines[j]:
            j += 1
        j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        slot = lines[j].strip() if j < len(lines) else ''
        out.append((m.group(1), rule.group(1) if rule else None, slot))
    return out


class TestTemplate(unittest.TestCase):

    def setUp(self):
        self.text = read(TEMPLATE)
        self.sections = sections(self.text)

    def test_has_sections(self):
        self.assertGreaterEqual(len(self.sections), 5)

    def test_every_section_opens_with_a_rule_comment(self):
        for heading, rule, _ in self.sections:
            with self.subTest(heading):
                self.assertIn(rule, ('required', 'optional'),
                              'first non-blank line after the heading is '
                              '<!-- required … --> or <!-- optional … -->')

    def test_exactly_one_required_section_and_it_is_description(self):
        required = [h for h, rule, _ in self.sections if rule == 'required']
        self.assertEqual(['Description'], required)

    def test_every_section_has_one_placeholder(self):
        for heading, _, slot in self.sections:
            with self.subTest(heading):
                self.assertRegex(slot, PLACEHOLDER)

    def test_source_placeholder_comes_last_with_its_own_rule(self):
        tail = self.text.rstrip().splitlines()
        self.assertEqual('$SOURCE', tail[-2].strip())
        self.assertTrue(tail[-1].startswith('<!-- optional'))

    def test_related_section_names_the_non_current_marker(self):
        block = self.text.split('## Related')[1].split('## ')[0]
        self.assertIn('⚠ NON-CURRENT', block)


class TestSkill(unittest.TestCase):

    def setUp(self):
        self.text = read(SKILL)

    def test_exactly_one_argument_hint_carrying_local(self):
        hints = [ln for ln in self.text.splitlines() if ln.startswith('argument-hint:')]
        self.assertEqual(1, len(hints))
        self.assertEqual('argument-hint: "<text | file-path> [--local]"', hints[0])

    def test_names_the_shipped_template_and_the_host_override(self):
        self.assertIn(TEMPLATE, self.text)
        self.assertIn(OVERRIDE, self.text)

    def test_calls_kartoteka_in_the_contract_shapes(self):
        self.assertIn('index_status()', self.text)
        self.assertIn('related(<project>,', self.text)
        self.assertIn('search_knowledge(', self.text)
        self.assertIn('project=<project>', self.text)

    def test_gates_on_the_project_key_and_spells_both_record_lines(self):
        self.assertIn('knowledge.project', self.text)
        self.assertIn('kartoteka is configured for this project but knowledge.project is not set',
                      self.text)
        self.assertIn('kartoteka does not list project <project>; run kartoteka project add '
                      '<project> on the daemon machine', self.text)

    def test_asks_once_and_lists_the_rest(self):
        self.assertIn('AskUserQuestion', self.text)
        self.assertIn('Missing Details', self.text)
        self.assertIn('⚠ NON-CURRENT', self.text)

    def test_no_slack_and_no_variants(self):
        self.assertNotIn('slack', self.text.lower())
        self.assertNotIn('=== SUMMARY 1 ===', self.text)
        self.assertNotIn('2–5', self.text)


class TestDocs(unittest.TestCase):

    def test_config_lists_the_host_override(self):
        self.assertIn(OVERRIDE, read(CONFIG))

    def test_config_consumed_by_names_the_skill_on_both_knowledge_rows(self):
        rows = [ln for ln in read(CONFIG).splitlines()
                if ln.startswith('| `knowledge.adapter`') or ln.startswith('| `knowledge.project`')]
        self.assertEqual(2, len(rows))
        for row in rows:
            with self.subTest(row[:30]):
                self.assertIn('issue-draft', row)

    def test_consultation_contract_names_the_skill(self):
        self.assertIn('skills/issue-draft/SKILL.md', read(CONSULTATION))

    def test_reference_entry_has_no_slack_and_no_variants(self):
        entry = read(REFERENCE).split('### issue-draft')[1].split('\n### ')[0]
        self.assertNotIn('slack', entry.lower())
        self.assertNotIn('variants', entry)
        self.assertIn(OVERRIDE, entry)


if __name__ == '__main__':
    unittest.main()
