"""The bratishka duty contract and its config keys are documented.

Not a behaviour test: the skills and the engine are prompts plus a CLI, and the
contract lives in Markdown. Same guard as tests/test_using_artel_docs.py.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def default_config():
    text = read('docs/config.md')
    start = text.index('## The default config')
    return json.loads(text[start:].split('```json', 1)[1].split('```', 1)[0])


class TestConfigDocs(unittest.TestCase):
    def test_default_config_carries_the_bratishka_block(self):
        block = default_config()['bratishka']
        self.assertEqual(block['dashboardUrl'], '')
        self.assertEqual(block['repo'], '')
        self.assertEqual(block['botLogin'], 'bratishka-bot')
        self.assertEqual(block['teams'], {'codeReview': '', 'qa': ''})
        self.assertEqual(block['qaLogins'], [])
        self.assertEqual(block['excludeTitlePatterns'], [])
        self.assertEqual(block['fixCommand'], '/agent')
        self.assertEqual(block['spawn']['adapter'], 'manual')
        self.assertEqual(block['testingPr']['titleTemplate'], 'Testing {dd}-{mm}-{yy}')
        self.assertEqual(block['testingPr']['branchTemplate'],
                         'feature/testing-{dd}-{mm}-{yy}')
        self.assertFalse(block['testingPr']['deleteBranchOnCancel'])

    def test_key_reference_names_every_key(self):
        text = read('docs/config.md')
        for key in (
            'bratishka.dashboardUrl', 'bratishka.repo', 'bratishka.botLogin',
            'bratishka.baseRef', 'bratishka.teams.codeReview', 'bratishka.teams.qa',
            'bratishka.qaLogins', 'bratishka.excludeTitlePatterns',
            'bratishka.fixCommand', 'bratishka.spawn.adapter',
            'bratishka.spawn.tokenEnv', 'bratishka.spawn.mcpToolPrefix',
            'bratishka.spawn.promptFile', 'bratishka.testingPr.titleTemplate',
            'bratishka.testingPr.branchTemplate',
            'bratishka.testingPr.deleteBranchOnCancel',
            'bratishka.testingPr.bodyFile',
        ):
            self.assertIn(key, text, key)


class TestContractDoc(unittest.TestCase):
    def test_names_every_phase_one_skill(self):
        text = read('docs/bratishka-duty.md')
        for name in ('status', 'cancel-testing-pr', 'merge-approved',
                     'request-fix', 'testing-pr'):
            self.assertIn('/artel-bratishka-' + name, text, name)

    def test_carries_the_testing_pr_body_sections(self):
        text = read('docs/bratishka-duty.md')
        for heading in ('## Summary', '## Tasks', '## Notes for QA',
                        '## CI status of source PRs', '## Merge notes',
                        '## Local verification'):
            self.assertIn(heading, text, heading)

    def test_carries_the_approval_and_naming_rules(self):
        text = read('docs/bratishka-duty.md')
        for phrase in ('CODEOWNERS', 'DISMISSED', 'mergeStateStatus',
                       'Testing {dd}-{mm}-{yy}', 'feature/testing-{dd}-{mm}-{yy}',
                       'Run id:', 'update-branch', '--squash'):
            self.assertIn(phrase, text, phrase)
