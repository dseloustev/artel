import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'hooks'))
sys.path.insert(0, str(ROOT / 'scripts'))
import kartoteka_http as kh  # noqa: E402
import spec_store  # noqa: E402

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

    def test_the_resolution_records_are_the_ones_the_code_writes(self):
        scope = section(self.text, '### 2.1 Resolution')
        for record in (
                spec_store.UNSTORABLE_KEY_REASON.format('<KEY>'),
                spec_store.answered('<status>', {'error': '<error text>'}),
                spec_store.unauthorized_message('<VAR>', 'token'),
                spec_store.unauthorized_message('<VAR>', None),
                spec_store.unauthorized_message('', None)):
            self.assertIn(record, scope)

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


class TestMigrateSpecsSkill(unittest.TestCase):
    SKILL = ROOT / 'skills' / 'migrate-specs' / 'SKILL.md'

    def setUp(self):
        self.text = self.SKILL.read_text(encoding='utf-8')

    def test_runs_the_three_verbs_in_order(self):
        plan, apply_, delete = (self.text.index('migrate ' + v) for v in ('plan', 'apply', 'delete'))
        self.assertLess(plan, apply_)
        self.assertLess(apply_, delete)

    def test_confirms_deletion_once_with_three_choices(self):
        for choice in ('Delete and commit', 'Delete, leave staged', 'Keep local copies'):
            self.assertIn(choice, self.text)

    def test_conflicts_offer_keep_local_keep_stored_skip(self):
        for choice in ('keep-local', 'keep-stored', 'skip'):
            self.assertIn(choice, self.text)

    def test_pending_only_never_asks_to_delete(self):
        self.assertIn('--pending-only', self.text)
        self.assertIn('without asking', self.text)

    def test_names_what_is_never_deleted(self):
        for kept in ('.active_ticket', 'evidence', 'release'):
            self.assertIn(kept, self.text)

    def test_routed_and_referenced(self):
        self.assertIn('/artel:migrate-specs', (ROOT / 'skills/using-artel/SKILL.md').read_text())
        self.assertIn('\n### migrate-specs\n', (ROOT / 'docs/skills-reference.md').read_text())

    def test_no_prompt_runs_apply_headless_and_only_deletes_pending(self):
        self.assertNotIn('stops after step 3', self.text)
        start = self.text.index('`--no-prompt` (headless)')
        end = self.text.index('\n\n', start)
        bullet = self.text[start:end]
        self.assertIn('apply', bullet)


class TestMigrateSpecsImages(unittest.TestCase):
    """spec-images §8 and §10.3: image items in the migrate-specs report and its
    conflict prompt, spelled as scripts/spec_store.py prints them."""
    SKILL = ROOT / 'skills' / 'migrate-specs' / 'SKILL.md'

    def setUp(self):
        self.text = self.SKILL.read_text(encoding='utf-8')

    def test_names_which_images_the_trail_holds(self):
        for phrase in ('git tracks under `<specs.dir>/<TICKET_ID>/`',
                       '`.artel/context/tickets/<TICKET_ID>/spec-trail/`',
                       '`spec_store.py image sync`', '`kind: "image"`'):
            self.assertIn(phrase, self.text)

    def test_counts_images_apart_and_names_the_old_daemon(self):
        self.assertIn('`image_summary`', self.text)
        self.assertIn(spec_store.ATTACHMENTS_MISSING, self.text)

    def test_an_image_conflict_shows_sizes_hashes_and_the_cached_stored_copy(self):
        start = self.text.index('### Image conflicts')
        block = self.text[start:self.text.index('\n## ', start)]
        for phrase in ('never has a diff', '`byte_size`', '`sha256`', '`stored.cache_path`',
                       '`stored.redacted: true`', 'image fetch --version <N>',
                       'keep-local@<N>', '`expected_version`', '`keep-stored`', '`skip`'):
            self.assertIn(phrase, block)

    def test_an_image_kartoteka_cannot_address_is_named_for_renaming(self):
        self.assertTrue(spec_store.OUTSIDE_THE_GRAMMAR.startswith(
            "outside kartoteka's image path grammar"))
        self.assertIn("`outside kartoteka's image path grammar`", self.text)
        self.assertIn('rename it', self.text)

    def test_images_are_no_longer_listed_as_evidence(self):
        start = self.text.index('**Never deleted')
        never = self.text[start:self.text.index('\n\n', start)]
        self.assertNotIn('`design/`', never)
        self.assertIn('`runtime/*.md`', never)
        self.assertIn('untracked working-tree images', never)
