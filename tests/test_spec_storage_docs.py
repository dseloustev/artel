import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'hooks'))
import kartoteka_http as kh  # noqa: E402

DOC = ROOT / 'docs' / 'spec-storage.md'
ROW = re.compile(r'^\| `(<specs\.dir>/[^`]+)` \| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|$')


def section(text, heading):
    start = text.index(heading)
    nxt = text.find('\n## ', start + len(heading))
    return text[start:nxt if nxt != -1 else None]


class TestContract(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding='utf-8')

    def test_the_addressing_table_matches_the_code(self):
        rows = [ROW.match(line) for line in section(self.text, '## 3. Addressing').splitlines()]
        rows = [r for r in rows if r]
        self.assertGreaterEqual(len(rows), 4)
        config = {'ticket': {'projectKey': 'PROJ'}, 'specs': {'dir': 'specs/.current'}}
        for match in rows:
            logical = match.group(1).replace('<specs.dir>', 'specs/.current')
            self.assertEqual(kh.artifact_identity(logical, config), match.groups()[1:], logical)

    def test_the_mirrored_set_is_listed_in_full(self):
        scope = section(self.text, '## 1. What moves')
        for name in sorted(kh.MIRRORED):
            self.assertIn(name, scope)

    def test_the_numbered_sections_exist(self):
        for heading in ('## 1. What moves', '## 2. The storage decision', '### 2.1 Resolution',
                        '### 2.2 The decision file', '### 2.3 The dispatch field',
                        '## 3. Addressing', '## 4. Operations', '### 4.1 Agents',
                        '### 4.2 Scripts', '### 4.3 The tasklist', '### 4.4 The review-round reset',
                        '### 4.5 When kartoteka fails', '## 5. Unavailability', '### 5.1 At the start',
                        '### 5.2 Mid-run', '### 5.3 Resume', '### 5.4 Headless',
                        '### 5.5 Completion', '## 6. The guard', '## 7. Local trails and migration',
                        '## 8. spec_store.py'):
            self.assertIn(heading, self.text)

    def test_every_exit_code_and_verb_is_documented(self):
        ref = section(self.text, '## 8. spec_store.py')
        for verb in ('get', 'exists', 'list', 'versions', 'put', 'decide', 'decision', 'pending add'):
            self.assertIn('`{}'.format(verb), ref)
        for code in ('`0`', '`2`', '`3`', '`4`', '`5`'):
            self.assertIn(code, ref)


class TestConfig(unittest.TestCase):
    def test_on_unavailable_is_documented_with_its_default(self):
        text = (ROOT / 'docs' / 'config.md').read_text(encoding='utf-8')
        self.assertIn('| `specs.onUnavailable` | string | `"abort"` |', text)
        start = text.index('```json', text.index('## The default config')) + len('```json')
        defaults = json.loads(text[start:text.index('```', start)])
        self.assertEqual(defaults['specs']['onUnavailable'], 'abort')
