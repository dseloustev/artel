"""Doc contract for the `models` config section: the OpenCode dispatch models.

Spec: docs/superpowers/specs/2026-10-07-opencode-model-selection-design.md (gitignored).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFIG = 'docs/config.md'
SETUP = 'skills/setup/SKILL.md'
SECTION = '### `models` — the OpenCode dispatch models'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def section(text, heading, next_heading_re=r'^## '):
    """Body of `heading` up to the next same-level heading."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    match = re.search(next_heading_re, rest, flags=re.MULTILINE)
    return rest[:match.start()] if match else rest


class TestConfigKey(unittest.TestCase):
    def test_default_block_carries_the_block_off(self):
        default = section(read(CONFIG), '## The default config')
        self.assertIn('"models": {\n    "opencode": {}\n  }', default)

    def test_key_reference_has_a_models_section(self):
        text = read(CONFIG)
        self.assertIn(SECTION, text)
        body = section(text, SECTION, r'^### ')
        for key in ('`models.opencode`', '`models.opencode.implementer.light`',
                    '`models.opencode.implementer.stepUp`', '`models.opencode.reviewer.task`',
                    '`models.opencode.reviewer.reReview`',
                    '`models.opencode.reviewer.deepReview`',
                    '`models.opencode.reviewForecaster`', '`models.opencode.agents.<name>`'):
            self.assertIn(key, body)
        self.assertIn('`null`', body)
        self.assertIn('scripts/models.py', body)
        self.assertIn('provider/model', body)
        self.assertIn('ignored', body)

    def test_models_section_pins_the_call_time_read_and_the_fallbacks(self):
        body = section(read(CONFIG), SECTION, r'^### ')
        self.assertIn('the resolver reads this block at call time', body)
        self.assertIn('leaves the first-round rule (`implementer.fix`) in effect', body)
        self.assertIn("leaves ticket mode's `reviewer.phase` in effect", body)

    def test_filled_example_shows_the_block_on(self):
        example = section(read(CONFIG), '## A filled example')
        self.assertIn('"models": {\n    "opencode": {\n      "implementer": {', example)

    def test_setup_interview_offers_and_validates_the_key(self):
        text = read(SETUP)
        self.assertIn('`models.opencode`', section(text, '## 2. Interview'))
        self.assertIn('`models.opencode`', section(text, '## 3. Validate'))

    def test_the_resolver_exists(self):
        self.assertTrue((ROOT / 'scripts' / 'models.py').is_file())


if __name__ == '__main__':
    unittest.main()
