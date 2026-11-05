"""The testing-PR selection, prompt and body."""
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka_config as cfg  # noqa: E402
import bratishka_testing as testing  # noqa: E402

CONFIG = dict(cfg.DEFAULTS)

SOURCE_100 = """**Summary**

Wrap the title.

**Notes for QA**
- Set the app language to Spanish.
- Check a larger font.

**Platforms to test**
- [x] Android
- [x] iOS
- [ ] Windows
- [x] macOS
"""

SOURCE_124 = """**Summary**

No QA sections here.
"""


class TestParsers(unittest.TestCase):
    def test_notes(self):
        self.assertEqual(testing.parse_notes(SOURCE_100),
                         '- Set the app language to Spanish.\n- Check a larger font.')

    def test_missing_notes_is_empty(self):
        self.assertEqual(testing.parse_notes(SOURCE_124), '')

    def test_platforms_are_the_checked_boxes(self):
        self.assertEqual(testing.parse_platforms(SOURCE_100), 'Android, iOS, macOS')

    def test_any_platform_is_kept_verbatim(self):
        body = '**Platforms to test**\n- [x] Any platform\n'
        self.assertEqual(testing.parse_platforms(body), 'Any platform')

    def test_missing_platforms(self):
        self.assertEqual(testing.parse_platforms(SOURCE_124),
                         'Not specified in the source PR')

    def test_ticket(self):
        self.assertEqual(testing.ticket_of('AW-3320: wrap the title'), 'AW-3320')
        self.assertEqual(testing.ticket_of('no key'), '—')


class TestBody(unittest.TestCase):
    def task(self, number, title, head, body):
        return {'number': number, 'title': title, 'head': head, 'body': body,
                'url': 'https://github.com/o/r/pull/{}'.format(number)}

    def test_body_has_the_eight_parts(self):
        tasks = [self.task(100, 'AW-3320: wrap the backup title', 'agent/x', SOURCE_100)]
        body = testing.build_body(
            tasks, CONFIG, date(2026, 11, 3), 'master', 'c284403',
            {100: [{'name': 'build', 'bucket': 'pass'}]},
            ['All merges were clean.'], ['flutter analyze — clean'])
        for heading in ('## Summary', '## Tasks', '## Notes for QA',
                        '## CI status of source PRs', '## Merge notes',
                        '## Local verification'):
            self.assertIn(heading, body)
        self.assertIn('- **AW-3320** — [#100 AW-3320: wrap the backup title]'
                      '(https://github.com/o/r/pull/100)', body)
        self.assertIn('### AW-3320 — PR #100', body)
        self.assertIn('**Platforms to test:** Android, iOS, macOS', body)
        self.assertIn('Single testing PR for 2026-11-03', body)

    def test_missing_notes_are_named(self):
        tasks = [self.task(124, 'AW-3321: fix the fee label', 'agent/y', SOURCE_124)]
        body = testing.build_body(tasks, CONFIG, date(2026, 11, 3), 'master', 'abc',
                                  {}, [], [])
        self.assertIn('does not declare QA notes or platforms', body)


class TestPrompt(unittest.TestCase):
    def test_prompt_lists_the_branches_and_the_marker(self):
        tasks = [{'number': 100, 'title': 'AW-3320: x', 'head': 'agent/x',
                  'body': SOURCE_100, 'url': 'u'}]
        prompt = testing.build_prompt(tasks, CONFIG, date(2026, 11, 3), 'master',
                                      'artel-123')
        self.assertIn('PR #100, head branch agent/x', prompt)
        self.assertIn('feature/testing-03-11-26', prompt)
        self.assertIn('Testing 03-11-26', prompt)
        self.assertIn('Do not merge the testing PR', prompt)
        self.assertIn('Run id: artel-123', prompt)
