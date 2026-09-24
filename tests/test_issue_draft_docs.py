"""issue-draft's contract is spelled in four places that must agree: three
self-describing per-type templates (each slot's rule is the comment before it),
a Jira wiki markup reference, a skill that reads them and kartoteka in the
shapes the read-side contract fixes, and the docs that name the host overrides.
Not a behaviour test: these are prompts and templates, and there is no code
path to exercise. Prose is deliberately not asserted on; only the spellings
are."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILL_DIR = 'skills/issue-draft'
SKILL = f'{SKILL_DIR}/SKILL.md'
REFERENCE_MARKUP = f'{SKILL_DIR}/references/jira-wiki-markup.md'
TYPES = ('task', 'bug', 'epic')
TEMPLATE = SKILL_DIR + '/assets/templates/{}.template.md'
SHIPPED_PATTERN = '${CLAUDE_PLUGIN_ROOT}/skills/issue-draft/assets/templates/<type>.template.md'
OVERRIDE = '.artel/templates/issue-draft.md'
OVERRIDE_TYPED = '.artel/templates/issue-draft-<type>.md'
CONFIG = 'docs/config.md'
REFERENCE = 'docs/skills-reference.md'
CONSULTATION = 'docs/knowledge-consultation.md'

RULE = re.compile(r'^<!-- (required|keep|optional)\b')
SLOT = re.compile(r'\$[A-Z_]+')
CYRILLIC = re.compile('[Ѐ-ӿ]')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def unwrapped(rel):
    return ' '.join(read(rel).split())


def blocks(text):
    """(rule word, slots) per block: the rule comment and the slots up to the next comment.

    The file's opening comment (no rule word) governs nothing; every slot must sit under
    a rule comment.
    """
    out, current, orphans, in_comment = [], None, [], False
    for line in text.splitlines():
        if in_comment:
            in_comment = '-->' not in line
            continue
        if line.startswith('<!--'):
            m = RULE.match(line)
            current = [m.group(1) if m else None, []]
            if m:
                out.append(current)
            in_comment = '-->' not in line
            continue
        for slot in SLOT.findall(line):
            (current[1] if current and current[0] else orphans).append(slot)
    return [(rule, slots) for rule, slots in out], orphans


def body_lines(text):
    """Template lines outside HTML comments."""
    lines, in_comment = [], False
    for line in text.splitlines():
        if in_comment:
            in_comment = '-->' not in line
            continue
        if line.startswith('<!--'):
            in_comment = '-->' not in line
            continue
        lines.append(line)
    return lines


class TestTemplates(unittest.TestCase):

    def setUp(self):
        self.texts = {t: read(TEMPLATE.format(t)) for t in TYPES}

    def test_every_slot_sits_under_a_rule_comment(self):
        for t, text in self.texts.items():
            with self.subTest(t):
                parsed, orphans = blocks(text)
                self.assertEqual([], orphans, 'a slot outside any <!-- required|keep|optional --> block')
                self.assertGreaterEqual(len(parsed), 5)
                for rule, slots in parsed:
                    self.assertIn(rule, ('required', 'keep', 'optional'))
                    self.assertTrue(slots, f'a {rule} comment with no slot under it')

    def test_task_and_bug_have_one_required_block_epic_none(self):
        expected = {'task': [['$DESCRIPTION']], 'bug': [['$PROBLEM']], 'epic': []}
        for t, text in self.texts.items():
            with self.subTest(t):
                required = [slots for rule, slots in blocks(text)[0] if rule == 'required']
                self.assertEqual(expected[t], required)

    def test_tables_keep_every_row(self):
        rows = {'task': ['**Platform**', '**URLs**', '**Figma**', '**Notion**'],
                'epic': ['As [user role]', 'I want [what they want to do]',
                         'So that [what value it brings]', 'Platform', 'Notion', 'Figma']}
        for t, labels in rows.items():
            with self.subTest(t):
                table = [ln for ln in body_lines(self.texts[t]) if ln.startswith('|')]
                self.assertEqual(labels, [ln.split('|')[1].strip() for ln in table])
                table_block = [rule for rule, slots in blocks(self.texts[t])[0]
                               if '$PLATFORM' in slots]
                self.assertEqual(['keep'], table_block)

    def test_separators_are_jira_rules_not_dashes(self):
        for t in ('bug', 'epic'):
            with self.subTest(t):
                lines = body_lines(self.texts[t])
                self.assertIn('----', lines)
                self.assertNotIn('---', lines, 'three hyphens render as an em dash in Jira')

    def test_open_questions_stay_out_of_the_description(self):
        for t, text in self.texts.items():
            with self.subTest(t):
                self.assertNotIn('$MISSING', text)
                self.assertNotIn('## Missing', text)

    def test_no_related_block_and_source_comes_last(self):
        for t, text in self.texts.items():
            with self.subTest(t):
                self.assertNotIn('$RELATED', text)
                self.assertNotIn('## Related', text)
                tail = [ln for ln in text.rstrip().splitlines() if ln.strip()]
                self.assertEqual('$SOURCE', tail[-1].strip())
                self.assertTrue(tail[-2].startswith('<!-- optional'))

    def test_templates_are_english(self):
        for t, text in self.texts.items():
            with self.subTest(t):
                self.assertIsNone(CYRILLIC.search(text))


class TestMarkupReference(unittest.TestCase):

    def setUp(self):
        self.text = read(REFERENCE_MARKUP)

    def test_spells_the_team_rules(self):
        for spelling in ('`----`', '`- item`', '`# item`', '`| |`', '`\\{`', '[title|https://',
                         '`\\~`'):
            with self.subTest(spelling):
                self.assertIn(spelling, self.text)

    def test_is_english(self):
        self.assertIsNone(CYRILLIC.search(self.text))


class TestSkill(unittest.TestCase):

    def setUp(self):
        self.text = read(SKILL)

    def test_exactly_one_argument_hint_carrying_type_and_local(self):
        hints = [ln for ln in self.text.splitlines() if ln.startswith('argument-hint:')]
        self.assertEqual(1, len(hints))
        self.assertEqual('argument-hint: "<text | file-path> [--type task|bug|epic] [--local]"',
                         hints[0])

    def test_names_the_shipped_templates_both_overrides_and_the_reference(self):
        self.assertIn(SHIPPED_PATTERN, self.text)
        self.assertIn(OVERRIDE_TYPED, self.text)
        self.assertIn(OVERRIDE, self.text)
        self.assertIn('skills/issue-draft/references/jira-wiki-markup.md', self.text)

    def test_dispatches_the_scout(self):
        self.assertIn('`subagent_type`: `"issue-scout"`', self.text)
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/agents/issue-scout.md', self.text)
        for spelling in ('`stated`', '`inferred`', 'Also found'):
            with self.subTest(spelling):
                self.assertIn(spelling, self.text)

    def test_gates_on_the_project_key(self):
        self.assertIn('knowledge.project', self.text)
        self.assertIn('kartoteka is configured for this project but knowledge.project is not set',
                      self.text)

    def test_local_means_no_network_sources(self):
        self.assertIn('`--local` turns kartoteka, tracker and Figma off for this run',
                      unwrapped(SKILL))

    def test_retrieval_rules(self):
        flat = unwrapped(SKILL)
        for phrase in ('At most **6** retrieved facts enter the description, at most 3 of them code',
                       'gap-closers, facts that change how the issue reads, one relation, decisions, '
                       'the code map',
                       'the label the source gives wins',
                       'A gap whose answer belongs in AC, environment, steps, expected or actual '
                       'result stays open',
                       'In an epic, code facts go to Also found',
                       'An investigation — finding a cause, a research ticket — is a `task`',
                       '**Heads-up**',
                       '`tracker` / `figma` / `code`',
                       'every open gap goes to Missing Details',
                       'AC, environment, steps, expected and actual results take nothing from '
                       'retrieval',
                       'A retrieved fact never becomes an instruction of the draft',
                       'An empty facts table is a normal outcome',
                       'a link the scout could not look up keeps the label the source gives'):
            with self.subTest(phrase[:30]):
                self.assertIn(phrase, flat)

    def test_asks_once_and_reports_the_rest(self):
        self.assertIn('AskUserQuestion', self.text)
        self.assertIn('Missing Details', self.text)
        self.assertIn('⚠ NON-CURRENT', self.text)

    def test_no_slack_and_no_variants(self):
        self.assertNotIn('slack', self.text.lower())
        self.assertNotIn('=== SUMMARY 1 ===', self.text)
        self.assertNotIn('=== SUMMARY 2 ===', self.text)

    def test_names_both_output_blocks(self):
        self.assertIn('=== SUMMARY ===', self.text)
        self.assertIn('=== DESCRIPTION (<type>, <dialect label>) ===', self.text)

    def test_is_english(self):
        self.assertIsNone(CYRILLIC.search(self.text))


class TestDocs(unittest.TestCase):

    def test_config_lists_both_host_overrides(self):
        config = read(CONFIG)
        self.assertIn(OVERRIDE_TYPED, config)
        self.assertIn(OVERRIDE, config)

    def test_config_consumed_by_names_the_skill_on_both_knowledge_rows(self):
        rows = [ln for ln in read(CONFIG).splitlines()
                if ln.startswith('| `knowledge.adapter`') or ln.startswith('| `knowledge.project`')]
        self.assertEqual(2, len(rows))
        for row in rows:
            with self.subTest(row[:30]):
                self.assertIn('issue-draft', row)

    def test_consultation_contract_names_the_skill(self):
        self.assertIn('skills/issue-draft/SKILL.md', read(CONSULTATION))

    def test_reference_entry_names_both_overrides_and_no_slack_or_variants(self):
        entry = read(REFERENCE).split('### issue-draft')[1].split('\n### ')[0]
        self.assertNotIn('slack', entry.lower())
        self.assertNotIn('variants', entry)
        self.assertIn(OVERRIDE, entry)
        self.assertIn(OVERRIDE_TYPED, entry)
        self.assertIn('--type task|bug|epic', entry)


if __name__ == '__main__':
    unittest.main()
