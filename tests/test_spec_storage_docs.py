import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'hooks'))
sys.path.insert(0, str(ROOT / 'scripts'))
import kartoteka_http as kh  # noqa: E402
import spec_store  # noqa: E402
import spec_store_guard as guard  # noqa: E402

DOC = ROOT / 'docs' / 'spec-storage.md'
ROW = re.compile(r'^\| `(<specs\.dir>/[^`]+)` \| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|$')


def section(text, heading):
    start = text.index(heading)
    nxt = text.find('\n## ', start + len(heading))
    return text[start:nxt if nxt != -1 else None]


IMAGE_ROW = re.compile(r'^\| `(<specs\.dir>/[^`]+)` \| (?:`([^`]+)`|—) \| (?:`([^`]+)`|—) \|$')
CONFIG = {'ticket': {'projectKey': 'PROJ'}, 'specs': {'dir': 'specs/.current'}}


def subsection(text, heading):
    """`heading` up to the next `### ` or `## ` heading."""
    start = text.index(heading)
    ends = [i for i in (text.find('\n### ', start + len(heading)),
                        text.find('\n## ', start + len(heading))) if i != -1]
    return text[start:min(ends) if ends else None]


def flat(text):
    return ' '.join(text.split())


def excludes():
    return ["':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*{}'".format(ext)
            for ext in kh.IMAGE_TYPES]


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
                        '### 4.5 When kartoteka fails', '### 4.6 Images', '## 5. Unavailability',
                        '### 5.1 At the start',
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


class TestImagesMove(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding='utf-8')
        self.images = subsection(self.text, '### 4.6 Images')

    def test_images_move_and_evidence_text_stays(self):
        scope = section(self.text, '## 1. What moves')
        self.assertNotIn('design/*.png', scope)
        for ext in kh.IMAGE_TYPES:
            self.assertIn('`{}`'.format(ext), scope)
        for kept in ('.active_ticket', 'review/findings.json', 'runtime/observation.md',
                     'runtime/drive-observation.md', 'change-report.html', 'pr-pending.md'):
            self.assertIn(kept, scope)
        self.assertIn('§4.6', scope)
        self.assertIn('Images follow the documents', scope)

    def test_a_daemon_without_attachments_is_a_row_8_variant(self):
        scope = subsection(self.text, '### 2.1 Resolution')
        row = next(line for line in scope.splitlines() if line.startswith('| 8 |'))
        self.assertIn(spec_store.ATTACHMENTS_MISSING, row)
        self.assertIn('GET /api/attachments?project=<project>&ticket_key=<TICKET_ID>', flat(scope))

    def test_the_image_addressing_table_matches_the_code(self):
        rows = [IMAGE_ROW.match(line) for line in self.images.splitlines()]
        rows = [r for r in rows if r]
        self.assertGreaterEqual(len(rows), 4)
        self.assertTrue(any(r.group(2) is None for r in rows), 'a row with no address')
        for match in rows:
            logical = match.group(1).replace('<specs.dir>', 'specs/.current')
            expected = None if match.group(2) is None else (match.group(2), match.group(3))
            self.assertEqual(kh.image_identity(logical, CONFIG), expected, logical)
        self.assertIn("`hooks/kartoteka_http.py`'s `image_identity`", flat(self.images))

    def test_the_section_names_the_fetch_the_cache_the_sweep_and_its_points(self):
        text = flat(self.images)
        for phrase in ('spec_store.py image fetch <logical path>',
                       '.artel/run/<TICKET_ID>/images/<path>',
                       '.artel/run/<TICKET_ID>/images/@v<N>/<path>',
                       'spec_store.py image sync <TICKET_ID> --author artel:<skill>',
                       '`figma-analysis`', '`feature-development`', '`dev`', '`pr-create`',
                       '`restore-context`', 'redact'):
            self.assertIn(phrase, text)

    def test_the_documented_exclude_keeps_every_image_out_of_git(self):
        line = next(l.strip() for l in self.images.splitlines()
                    if l.strip().startswith('git add -- <paths> '))
        for exclude in excludes():
            self.assertIn(exclude, line)
        command = (line.replace('<paths>', "assets/icon.png '<specs.dir>/<TICKET_ID>' "
                                           "'<specs.dir>/.active_ticket'")
                   .replace('<specs.dir>', 'specs/.current').replace('<TICKET_ID>', 'PROJ-12'))
        images = ('design/a.png', 'design/B.PNG', 'design/c.Jpg', 'design/d.jpeg',
                  'design/e.GIF', 'design/f.webp', 'runtime/g.png', 'phase-2/runtime/h.WebP',
                  'top.png')
        text = ('runtime/observation.md', 'verify/iteration-1.json',
                'phase-2/runtime/observation.md')
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True, env=env)
            for rel in images + text:
                path = root / 'specs/.current/PROJ-12' / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            (root / 'specs/.current/.active_ticket').write_text('PROJ-12\n', encoding='utf-8')
            (root / 'assets').mkdir()
            (root / 'assets/icon.png').write_bytes(b'x')
            proc = subprocess.run(shlex.split(command), cwd=str(root), env=env,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            staged = subprocess.run(['git', 'ls-files'], cwd=str(root), env=env, check=True,
                                    capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(staged), sorted(
            ['assets/icon.png', 'specs/.current/.active_ticket'] +
            ['specs/.current/PROJ-12/' + rel for rel in text]))

    def test_ticket_parsing_stores_images_by_path(self):
        text = flat((ROOT / 'docs' / 'ticket-parsing.md').read_text(encoding='utf-8'))
        self.assertIn('is stored in kartoteka by path (spec-storage.md §4.6)', text)
        self.assertNotIn('`runtime/`, `design/`, `change-report.html`', text)


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
