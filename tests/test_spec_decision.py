import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import spec_decision as sd  # noqa: E402

CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


class InRepo(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def touch(self, rel, text='x'):
        path = Path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


class TestCanonicalTicket(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(sd.canonical_ticket('AW-12', CONFIG), 'AW-12')
        self.assertEqual(sd.canonical_ticket('aw-12-3', CONFIG), 'AW-12')
        self.assertEqual(sd.canonical_ticket('12', CONFIG), 'AW-12')
        self.assertIsNone(sd.canonical_ticket('nonsense', CONFIG))


class TestFreshness(unittest.TestCase):
    def test_within_three_hours_is_fresh(self):
        now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
        decision = {'decided_at': '2026-09-22T10:00:00Z'}
        self.assertTrue(sd.is_fresh(decision, now))
        self.assertFalse(sd.is_fresh(decision, now + timedelta(hours=2)))

    def test_missing_or_garbled_stamp_is_stale(self):
        self.assertFalse(sd.is_fresh({}))
        self.assertFalse(sd.is_fresh({'decided_at': 'yesterday'}))
        self.assertFalse(sd.is_fresh(None))

    def test_a_non_string_stamp_is_stale_not_an_error(self):
        for stamp in (1758535200, ['2026-09-22T10:00:00Z'], {'at': 'now'}):
            self.assertFalse(sd.is_fresh({'decided_at': stamp}), stamp)

    def test_a_naive_stamp_is_stale_not_an_error(self):
        # Subtracting a naive datetime from an aware one raises TypeError, and
        # using_artel.py would then drop the whole injected context.
        now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
        self.assertFalse(sd.is_fresh({'decided_at': '2026-09-22T11:00:00'}, now))


class TestReadWrite(InRepo):
    def test_round_trip_and_location(self):
        decision = sd.new_decision('kartoteka', None, 'feature-development', {'prd.md': 3})
        sd.write('AW-12', decision)
        self.assertEqual(sd.path_for('AW-12'), Path('.artel/run/AW-12/spec-store.json'))
        loaded = sd.load('AW-12')
        self.assertEqual(loaded['store'], 'kartoteka')
        self.assertEqual(loaded['versions'], {'prd.md': 3})
        self.assertEqual(loaded['pending'], [])
        self.assertTrue(sd.is_fresh(loaded))

    def test_load_of_nothing_or_garbage_is_none(self):
        self.assertIsNone(sd.load('AW-12'))
        self.touch('.artel/run/AW-12/spec-store.json', 'not json')
        self.assertIsNone(sd.load('AW-12'))


class TestAdmitsLocalWrite(InRepo):
    REL = 'specs/.current/AW-12/plan.md'

    def test_no_decision_admits_nothing(self):
        self.assertFalse(sd.admits_local_write(self.REL, 'AW-12'))

    def test_a_fresh_files_decision_admits(self):
        sd.write('AW-12', sd.new_decision('files', 'local-only run requested', 'dev'))
        self.assertTrue(sd.admits_local_write(self.REL, 'AW-12'))

    def test_a_stale_files_decision_does_not(self):
        decision = sd.new_decision('files', 'x', 'dev')
        decision['decided_at'] = '2020-01-01T00:00:00Z'
        sd.write('AW-12', decision)
        self.assertFalse(sd.admits_local_write(self.REL, 'AW-12'))

    def test_pending_paths_compare_normalised(self):
        sd.write('AW-12', sd.new_decision('kartoteka', None, 'dev', pending=[
            {'path': './specs/.current/AW-12/plan.md', 'base_version': 2}]))
        self.assertTrue(sd.admits_local_write(self.REL, 'AW-12'))
        self.assertTrue(sd.admits_local_write('specs/.current/AW-12/../AW-12/plan.md', 'AW-12'))

    def test_a_kartoteka_decision_admits_only_pending_paths(self):
        sd.write('AW-12', sd.new_decision(
            'kartoteka', None, 'feature-development',
            pending=[{'path': self.REL, 'base_version': 2}]))
        self.assertTrue(sd.admits_local_write(self.REL, 'AW-12'))
        self.assertFalse(sd.admits_local_write('specs/.current/AW-12/prd.md', 'AW-12'))


class TestLocalTrail(InRepo):
    def test_finds_mirrored_documents_in_both_places_and_nothing_else(self):
        self.touch('specs/.current/AW-12/prd.md')
        self.touch('specs/.current/AW-12/phase-2/tasks.md')
        self.touch('specs/.current/AW-12/runtime/observation.md')  # evidence
        self.touch('specs/.current/AW-12/pr-pending.md')           # not mirrored
        self.touch('specs/.current/.active_ticket')
        self.touch('.artel/context/tickets/AW-12/spec-trail/plan.md')
        self.touch('specs/.current/AW-13/prd.md')                  # another ticket
        self.assertEqual(sd.local_trail('AW-12', CONFIG), [
            'specs/.current/AW-12/phase-2/tasks.md',
            'specs/.current/AW-12/prd.md',
            '.artel/context/tickets/AW-12/spec-trail/plan.md',
        ])


class TestImageFiles(InRepo):
    def test_every_regular_image_file_at_any_depth_sorted(self):
        self.touch('specs/.current/AW-12/design/b.PNG')
        self.touch('specs/.current/AW-12/design/a.png')
        self.touch('specs/.current/AW-12/phase-2/runtime/x.webp')
        self.touch('specs/.current/AW-12/shot.jpeg')
        self.touch('specs/.current/AW-12/design-analysis.md')    # a document
        self.touch('specs/.current/AW-12/runtime/observation.md')
        self.touch('specs/.current/AW-12/diagram.svg')           # not an image kartoteka takes
        self.assertEqual(sd.image_files(Path('specs/.current/AW-12')), [
            Path('specs/.current/AW-12/design/a.png'),
            Path('specs/.current/AW-12/design/b.PNG'),
            Path('specs/.current/AW-12/phase-2/runtime/x.webp'),
            Path('specs/.current/AW-12/shot.jpeg'),
        ])

    def test_links_are_never_listed_or_entered(self):
        self.touch('outside/secret.png')
        self.touch('specs/.current/AW-12/design/real.png')
        os.symlink(os.path.abspath('outside/secret.png'), 'specs/.current/AW-12/design/link.png')
        os.symlink(os.path.abspath('outside'), 'specs/.current/AW-12/linked-dir')
        self.assertEqual(sd.image_files(Path('specs/.current/AW-12')),
                         [Path('specs/.current/AW-12/design/real.png')])

    def test_a_missing_root_is_empty(self):
        self.assertEqual(sd.image_files(Path('specs/.current/AW-99')), [])
