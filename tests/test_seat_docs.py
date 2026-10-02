#!/usr/bin/env python3
"""Doc-contract pins for the orchestrator seat (spec §11)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FD = 'skills/feature-development/'


def raw(path):
    return (ROOT / path).read_text(encoding='utf-8')


def flat(path):
    return re.sub(r'\s+', ' ', raw(path))


def between(text, start, end):
    i, j = text.index(start), text.index(end, text.index(start) + len(start))
    return text[i:j]


class TestSeatDocs(unittest.TestCase):

    def test_step6_branches_on_seat_enabled(self):
        step = between(flat(FD + 'SKILL.md'), '### 6. The tail', '## Important')
        for phrase in ('`seat.enabled` absent or', 'Agent(artel:seat',
                       'seat: unavailable — running the tail inline',
                       'autonomous-run.md §18.2',
                       'In a `--step` run'):
            self.assertIn(phrase, step)

    def test_the_seat_body(self):
        body = flat('agents/seat.md')
        for phrase in ('model: sonnet', 'never prompt the person directly',
                       'pause-request.json', 'PAUSED: <reason>', 'STOPPED: <reason>',
                       'auto-resolved', 'SendMessage'):
            self.assertIn(phrase, body)

    def test_autonomous_run_18(self):
        doc = flat('docs/autonomous-run.md')
        for phrase in ('## 18. The orchestrator seat', '### 18.2 The pause relay',
                       'pause-request.json',
                       'seat: unavailable — running the tail inline',
                       'never in a `--step` or `--dry-run` run'):
            self.assertIn(phrase, doc)
        s2 = between(doc, '## 2. `run-state.json`', '## 3.')
        self.assertIn('pause-request.json', s2)

    def test_config_key(self):
        self.assertIn('`seat.enabled`', flat('docs/config.md'))

    def test_opencode_softening(self):
        self.assertIn('`seat.enabled` is inert', flat('docs/opencode.md'))

    def test_agents_md(self):
        doc = flat('docs/agents.md')
        self.assertIn('seat (the orchestrator', doc)
        self.assertIn('`seat`', between(doc, '## Models', '## Why this file'))

    def test_the_generator_excludes_the_seat(self):
        self.assertIn("'seat'", raw('scripts/build_opencode.py'))


if __name__ == '__main__':
    unittest.main()
