"""The task grammar (sub-project 2a, 0.23.0): `docs/task-grammar.md` states the grammar that
`scripts/task_grammar.py` enforces, and the writers, the plan review and the execution side
cite it. Plan 1 pins the contract against the code; plans 2 and 3 add a class each for the
documents they change."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import task_grammar  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return re.sub(r'\s+', ' ', text)


def section(doc, heading):
    """The text of one `## ` or `### ` section, up to the next heading of its level."""
    level = heading.split(' ', 1)[0]
    start = doc.index(heading + '\n')
    rest = doc[start + len(heading) + 1:]
    match = re.search(r'(?m)^{} '.format(re.escape(level)), rest)
    return rest[:match.start()] if match else rest


def table_rules(text):
    """{rule: first other cell} for every `| \\`rule\\` | … |` row of a table."""
    rows = {}
    for match in re.finditer(r'(?m)^\| `([a-z-]+)` \| ([^|]+)\|', text):
        rows[match.group(1)] = match.group(2).strip()
    return rows


class TestGrammarContract(unittest.TestCase):
    def setUp(self):
        self.doc = read('docs/task-grammar.md')

    def test_sections(self):
        for heading in ('## 1. The task block', '## 2. Field rules',
                        '## 3. What stays and what goes',
                        '## 4. Format detection and the old format',
                        '## 5. IDs stay out of the product',
                        '## 6. Validation and the plan check',
                        '## 7. Rows, waves, readiness and route floors'):
            self.assertIn(heading + '\n', self.doc)
        for field in ('### 2.1 `Files:` (required)', '### 2.2 `Depends on:` (required)',
                      '### 2.3 `Route:` (required)', '### 2.4 `Test:` (required)',
                      '### 2.5 `Produces:` (optional)',
                      '### 2.6 `Implements:` (required when the PRD has requirements)'):
            self.assertIn(field + '\n', self.doc)

    def test_the_example_uses_every_field_and_parses_without_warnings(self):
        # The first `markdown` fence of the document is §1's example. (section() would
        # stop at the example's own `## Iteration` line, which sits inside the fence.)
        example = self.doc.split('```markdown\n', 1)[1].split('\n```', 1)[0]
        iterations, _, warnings = task_grammar.parse(example)
        self.assertEqual(warnings, [])
        task = iterations[0]['tasks'][0]
        self.assertEqual(set(task['fields']), set(task_grammar.KNOWN_FIELDS))
        self.assertEqual(task['hitl'], 'copy needs product sign-off')

    def test_the_grammar_rule_table_is_the_code_s(self):
        rows = table_rules(section(self.doc, '### 6.1 Grammar problems'))
        self.assertEqual(set(rows), set(task_grammar.GRAMMAR_RULES))

    def test_the_check_rule_table_is_the_code_s_with_its_severities(self):
        rows = table_rules(section(self.doc, '### 6.2 The plan check (`--check`)'))
        self.assertEqual(rows, task_grammar.CHECK_RULES)

    def test_the_approval_reason_is_the_code_s(self):
        self.assertIn('`<light|full> — {}`'.format(task_grammar.APPROVAL_REASON), self.doc)

    def test_the_floor_threshold_is_the_code_s(self):
        self.assertIn('`ROUTE_FULL_FILES = {}`'.format(task_grammar.ROUTE_FULL_FILES), self.doc)
        self.assertIn('more than {} files (<n>)'.format(task_grammar.ROUTE_FULL_FILES), self.doc)

    def test_the_invocations_match_the_cli(self):
        doc = flat(self.doc)
        for phrase in ('tasklist_tasks.py requirements --prd <prd-path>',
                       '--check --requirements <ids>',
                       'spec_store.py get <prd-path> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py requirements --prd -',
                       '`absent` when there is no PRD or `data.present` is false'):
            self.assertIn(phrase, doc)

    def test_rows_and_readiness(self):
        doc = flat(section(self.doc, '## 7. Rows, waves, readiness and route floors'))
        for phrase in ('`I<N> · <N.M> · <task title>`', 'the idempotency key, capped at 500',
                       'the steps without ticks', '`data.waves`', '`data.ready_now`',
                       'in the first iteration that has a task not done',
                       'is known only at runtime and is the orchestrator\'s',
                       'a route whose reason is `set at approval` is final over these three'
                       ' floors'):
            self.assertIn(phrase, doc)

    def test_old_format_and_ids(self):
        doc = flat(self.doc)
        self.assertIn('The old format stays until sub-project 2b ships', doc)
        self.assertIn('never appear in code, tests, identifiers, comments or commit subjects',
                      doc)
        self.assertIn('The fix sections are outside this grammar.', doc)


if __name__ == '__main__':
    unittest.main()
