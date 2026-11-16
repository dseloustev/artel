"""docs/tracker-status.md states the resolution the delegate skill obeys.

The doc is the one copy of the algorithm: the skill cites it, so a rule that
stops a guessed move has to be spelled here or nothing enforces it.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = 'docs/tracker-status.md'
CONFIG = 'docs/config.md'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class TestIntents(unittest.TestCase):
    def test_names_both_intents(self):
        text = read(DOC)
        self.assertIn('| `start` |', text)
        self.assertIn('| `handoff` |', text)

    def test_names_the_config_keys_that_override_them(self):
        text = read(DOC)
        for key in ('bratishka.delegate.startStatus', 'bratishka.delegate.handoffStatus'):
            self.assertIn(key, text, key)


class TestResolution(unittest.TestCase):
    def test_an_override_never_falls_back_to_the_aliases(self):
        text = ' '.join(read(DOC).split())
        self.assertIn('An override never falls back to the aliases', text)

    def test_never_uses_a_transition_identifier(self):
        text = ' '.join(read(DOC).split())
        self.assertIn('Never use a transition identifier', text)

    def test_an_ambiguous_match_stops_and_asks(self):
        text = ' '.join(read(DOC).split())
        self.assertIn('Never pick a candidate silently', text)

    def test_a_rollback_is_confirmed(self):
        text = ' '.join(read(DOC).split())
        self.assertIn('reads like a rollback', text)
        self.assertIn('Confirm before applying it', text)

    def test_done_categories_are_preferred_over_names(self):
        text = read(DOC)
        self.assertIn('statusCategory.key', text)
        self.assertIn('locale-independent', text)

    def test_a_finished_move_is_verified(self):
        text = read(DOC)
        self.assertIn('the status did not change', text)

    def test_the_aliases_carry_russian_spellings(self):
        text = read(DOC)
        self.assertIn('в работе', text)
        self.assertIn('на ревью', text)


class TestConfigAgrees(unittest.TestCase):
    def test_every_key_the_doc_names_is_documented_in_config(self):
        doc = read(DOC)
        reference = read(CONFIG)
        for key in ('bratishka.delegate.startStatus', 'bratishka.delegate.handoffStatus'):
            self.assertIn(key, doc)
            self.assertIn(key, reference, key + ' is not in config.md')


class TestSweeps(unittest.TestCase):
    """The doc enters the repo-wide scans; keep it out of the tasklist audit."""

    def test_it_reads_no_tasklist(self):
        text = read(DOC)
        for needle in ('tasklist.md', 'tasks.md', 'checkbox', '- [ ]', 'first incomplete'):
            self.assertNotIn(needle, text, needle)


if __name__ == '__main__':
    unittest.main()
