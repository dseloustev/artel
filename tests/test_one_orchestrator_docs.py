"""Doc-contract tests for sub-project 2b: one orchestrator.

Prompts have no code path, so a phrase dropped from one fails a test here instead of a run
nobody is watching. Phrases are matched with whitespace collapsed.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FD = 'skills/feature-development/'


def raw(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(rel):
    """`rel` with whitespace runs collapsed, so re-wrapping a paragraph never breaks a pin."""
    return re.sub(r'\s+', ' ', raw(rel))


def between(text, start, end):
    """The slice of `text` from `start` up to the next `end`, or to the end of the text."""
    i = text.index(start)
    j = text.find(end, i + len(start))
    return text[i:] if j == -1 else text[i:j]


class TestFloorTwoDocs(unittest.TestCase):
    """Route floor 2 reads the ticket's spec trail; both contracts say so in one sentence."""

    RULE = ('the task carries a `[HITL: …]` tag and at least one of its `Files:` lies outside '
            'the ticket\'s spec trail (`<specs.dir>/<TICKET_ID>/`)')

    def test_the_grammar_states_the_rule(self):
        floors = between(flat('docs/task-grammar.md'), '- **Route floors.**', '`route_effective`')
        self.assertIn(self.RULE + ' — `HITL tag`', floors)
        self.assertIn('keeps its declared route', floors)

    def test_the_run_contract_states_the_same_rule(self):
        which = between(flat('docs/autonomous-run.md'), '### 16.1', '### 16.2')
        self.assertIn('2. ' + self.RULE + ';', which)

    def test_the_old_wording_is_gone(self):
        self.assertNotIn('the task carries a `[HITL: …]` tag — `HITL tag`',
                         flat('docs/task-grammar.md'))
        self.assertNotIn('the task carries a `[HITL: …]` tag;', flat('docs/autonomous-run.md'))


if __name__ == '__main__':
    unittest.main()
