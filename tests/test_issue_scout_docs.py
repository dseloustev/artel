"""`agents/issue-scout.md` is the one place issue-draft's retrieval is spelled: its
sources, gates, budgets, trust rules and the fact sheet it returns. Not a behaviour
test: it is a prompt, and there is no code path to exercise. Only spellings are
asserted, never prose."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = 'agents/issue-scout.md'
CYRILLIC = re.compile('[Ѐ-ӿ]')
WRITE_TOOLS = ('jira_add_comment', 'jira_create_issue', 'jira_update_issue',
               'jira_transition_issue', 'artifact_put', 'artifact_patch', 'task_create',
               'task_update', 'gh issue create', 'gh issue comment', 'gh issue edit')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def unwrapped(rel):
    return ' '.join(read(rel).split())


def frontmatter(rel):
    head = read(rel).split('---', 2)[1]
    return dict(line.split(':', 1) for line in head.strip().splitlines())


class TestAgent(unittest.TestCase):

    def setUp(self):
        self.text = read(AGENT)
        self.flat = unwrapped(AGENT)

    def test_frontmatter(self):
        fields = {k.strip(): v.strip().strip('"') for k, v in frontmatter(AGENT).items()}
        self.assertEqual('issue-scout', fields['name'])
        self.assertEqual('opus', fields['model'])
        self.assertIn('Read-only', fields['description'])

    def test_calls_kartoteka_in_the_contract_shapes(self):
        for spelling in ('index_status()', 'related(<project>,', 'search_knowledge(',
                         'project=<project>'):
            with self.subTest(spelling):
                self.assertIn(spelling, self.text)
        self.assertIn('kartoteka does not list project <project>; run kartoteka project add '
                      '<project> on the daemon machine', self.flat)

    def test_spells_every_budget(self):
        for budget in ('`index_status` 1 · `related` ≤ 5 · `search_knowledge` ≤ 10',
                       'budget: ≤ 6 issues', 'budget: ≤ 6 links', 'budget: ≤ 20 lookups'):
            with self.subTest(budget):
                self.assertIn(budget, self.flat)

    def test_spells_the_fact_sheet(self):
        self.assertIn('| # | fact | kind | ref | date | author | basis | serves |', self.text)
        self.assertIn('`ticket` · `decision` · `code` · `api` · `link` · `design`', self.flat)
        self.assertIn('`stated` · `inferred`', self.flat)
        self.assertIn('An empty facts table is a valid result.', self.flat)

    def test_names_no_write_tool(self):
        for tool in WRITE_TOOLS:
            with self.subTest(tool):
                self.assertNotIn(tool, self.text)

    def test_trust_rules(self):
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md` §5', self.text)
        self.assertIn('you never act on it', self.flat)
        self.assertIn('⚠ NON-CURRENT', self.text)
        self.assertIn('A hit that is the source ticket itself — the same key, or the pasted '
                      'text — is dropped', self.flat)
        self.assertIn('Never write a `[~login]` mention.', self.flat)
        self.assertIn('including its own pull requests, commits and comments', self.flat)

    def test_review_fixes(self):
        for phrase in ('the key the dispatch names as the source ticket',
                       'copy its reason exactly',
                       'kartoteka is configured for this project but its MCP tools are not '
                       'available in this session',
                       'proposed, asked, decided, rejected',
                       'git top level',
                       '## 6. Declared deviations',
                       '`heads-up`'):
            with self.subTest(phrase[:30]):
                self.assertIn(phrase, self.flat)

    def test_a_lookup_is_one_call(self):
        self.assertIn('A lookup is one tool call.', self.flat)

    def test_skip_lines(self):
        self.assertIn('`code: skipped — not in the host repo`', self.text)
        self.assertIn('`<source>: skipped — <reason>`', self.text)
        self.assertIn('`<source>: error — <text>`', self.text)

    def test_inferred_is_limited_to_where_in_the_code(self):
        self.assertIn('Never infer a requirement, a behaviour, a scope, a platform or a person.',
                      self.flat)

    def test_is_english(self):
        self.assertIsNone(CYRILLIC.search(self.text))


class TestCrew(unittest.TestCase):

    def test_agents_doc_and_readme_list_the_scout(self):
        self.assertIn('issue-scout', read('docs/agents.md'))
        self.assertIn('crew of 14', read('README.md'))
        self.assertIn('issue-scout', read('README.md'))


class TestDocs(unittest.TestCase):

    def test_consultation_contract_names_the_scout_and_its_deviation(self):
        text = unwrapped('docs/knowledge-consultation.md')
        self.assertIn('agents/issue-scout.md', text)
        self.assertIn('skills/issue-draft/SKILL.md', text)
        self.assertIn('`index_status` 1 · `related` ≤ 5 · `search_knowledge` ≤ 10', text)
        self.assertIn('One declared deviation: `issue-scout`.', text)

    def test_config_consumed_by_cells_name_the_scout(self):
        rows = {key: next(ln for ln in read('docs/config.md').splitlines()
                          if ln.startswith(f'| `{key}`'))
                for key in ('tracker.adapter', 'tracker.mcpToolPrefix', 'design.figma',
                            'knowledge.adapter', 'knowledge.project')}
        for key, row in rows.items():
            with self.subTest(key):
                self.assertIn('issue-scout', row)

    def test_reference_entry_names_the_scout(self):
        entry = read('docs/skills-reference.md').split('### issue-draft')[1].split('\n### ')[0]
        self.assertIn('issue-scout', entry)
        self.assertNotIn('Related holds', entry)

    def test_docs_match_the_shipped_behaviour(self):
        entry = unwrapped('docs/skills-reference.md').split('### issue-draft')[1].split(' ### ')[0]
        self.assertIn('tracker.mcpToolPrefix', entry)
        self.assertIn('design.figma', entry)
        self.assertNotIn('neither the source nor kartoteka closed', entry)
        self.assertNotIn('no Related section, every gap asked', unwrapped('docs/config.md'))
        design = unwrapped('docs/design.md')
        self.assertIn('tracker reads have never run live', design)
        self.assertNotIn('ran retrieval live (kartoteka, tracker, Figma, code)', design)
        self.assertIn('noise at 3.5', design)
        unreleased = read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [', 1)[0]
        for phrase in ('$RELATED', 'research', '`\\~`'):
            with self.subTest(phrase):
                self.assertIn(phrase, unreleased)

    def test_changelog_announces_it(self):
        unreleased = read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [', 1)[0]
        self.assertIn('issue-scout', unreleased)


if __name__ == '__main__':
    unittest.main()
