"""The PR title is always English (decision 2026-10-08): `pr-create` composes it from the
tracker summary translated to English, and `language.pr` never reaches it. Spellings only,
not prose."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


class TestPrTitleLanguage(unittest.TestCase):
    def test_pr_create_composes_the_title_in_english(self):
        text = flat(read('skills/pr-create/SKILL.md'))
        self.assertIn('**PR title.** Always English, whatever `language.pr` says', text)
        self.assertIn('<TICKET_ID>: <tracker summary translated to English>', text)
        self.assertEqual(text.count('tracker summary translated to English'), 4,
                         'the rule, both gh paths and bitbucket')  # rule + gh files + gh kartoteka + bitbucket
        self.assertNotIn('--title "<TICKET_ID>: <tracker summary>"', text)

    def test_pr_description_leaves_the_title_to_pr_create(self):
        text = flat(read('skills/pr-description/SKILL.md'))
        self.assertIn("**Title.** The PR title is not this document's: `pr-create` composes it",
                      text)
        self.assertIn('always in English — never `<language.pr>`', text)
        self.assertNotIn('translated to `<language.pr>` if it is in another language', text)
        self.assertIn('Body must be `<language.pr>`', text)  # the body keeps the config

    def test_config_scopes_language_pr_to_the_body(self):
        text = read('docs/config.md')
        row = next(line for line in text.splitlines() if line.startswith('| `language.pr`'))
        self.assertIn('PR body, tracker comments, drafted issues', row)
        self.assertNotIn('PR title', row)
        self.assertIn('The PR **title** is always English — `language.pr` never applies to it.',
                      flat(text))

    def test_no_contract_still_quotes_the_old_scope(self):
        for rel in ('docs/config.md', 'skills/issue-draft/SKILL.md'):
            with self.subTest(rel):
                self.assertNotIn('PR title and body', read(rel))

    def test_the_reference_states_the_english_title(self):
        entry = read('docs/skills-reference.md').split('### pr-create')[1].split('\n### ')[0]
        self.assertIn('tracker summary translated to English', entry)
        self.assertIn('always English', entry)


if __name__ == '__main__':
    unittest.main()
