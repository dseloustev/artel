import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fake_kartoteka import FakeKartoteka

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import spec_store  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'spec_store.py'
PROJECT = 'adguard-wallet'
TRAIL = 'specs/.current/AW-12'
PNG = b'\x89PNG\r\n\x1a\n'  # the magic kartoteka sniffs a PNG by
CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


def png(tag):
    """Distinct bytes kartoteka takes as a PNG: the magic, then a payload."""
    return PNG + tag.encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ImageCase(unittest.TestCase):
    """A git host repo whose config points at a fresh FakeKartoteka."""

    knowledge_extra = {}

    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / '.artel').mkdir()
        knowledge = {'adapter': 'kartoteka', 'baseUrl': self.fake.base_url, 'project': PROJECT}
        knowledge.update(self.knowledge_extra)
        self.config = dict(CONFIG, version=1, knowledge=knowledge)
        (self.repo / '.artel' / 'config.json').write_text(json.dumps(self.config),
                                                          encoding='utf-8')
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True,
                              text=True)

    def image(self, rel, data):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_cli(self, *args, env=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo,
                              capture_output=True, text=True, env=full_env)

    def error_of(self, proc):
        return json.loads(proc.stderr)['error']


class TestImageHelpers(unittest.TestCase):
    def test_the_route_quotes_each_segment_and_keeps_the_slashes(self):
        self.assertEqual(spec_store.attachment_route('AW-12', 'phase-2/runtime/x.png'),
                         '/api/attachments/AW-12/phase-2/runtime/x.png')

    def test_the_address_and_its_refusal(self):
        self.assertEqual(spec_store.image_address(TRAIL + '/design/a.png', CONFIG),
                         ('AW-12', 'design/a.png'))
        with self.assertRaises(spec_store.Failure) as info:
            spec_store.image_address(TRAIL + '/design/Screen Shot.png', CONFIG)
        self.assertEqual(info.exception.kind, 'not_an_image')

    def test_the_unavailable_record(self):
        self.assertEqual(spec_store.ATTACHMENTS_MISSING,
                         'the kartoteka daemon predates attachments (0.44.0); upgrade it')


class TestImagePut(ImageCase):
    def test_stores_the_bytes_and_prints_the_receipt(self):
        self.image(TRAIL + '/design/a.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png',
                            '--author', 'artel:figma-analysis')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertEqual((receipt['ticket_key'], receipt['path'], receipt['version'],
                          receipt['content_hash'], receipt['unchanged']),
                         ('AW-12', 'design/a.png', 1, sha(png('a')), False))
        stored = self.fake.newest_image(PROJECT, 'AW-12', 'design/a.png')
        self.assertEqual((stored['bytes'], stored['author_agent']),
                         (png('a'), 'artel:figma-analysis'))
        self.assertNotIn('PNG', proc.stdout + proc.stderr)  # bytes are never printed

    def test_file_reads_the_bytes_from_another_path(self):
        source = self.image('elsewhere/capture.png', png('b'))
        proc = self.run_cli('image', 'put', TRAIL + '/runtime/x.png', '--file', str(source))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.fake.newest_image(PROJECT, 'AW-12', 'runtime/x.png')['bytes'],
                         png('b'))

    def test_the_same_bytes_again_are_unchanged(self):
        self.image(TRAIL + '/design/a.png', png('a'))
        self.run_cli('image', 'put', TRAIL + '/design/a.png')
        receipt = json.loads(self.run_cli('image', 'put', TRAIL + '/design/a.png').stdout)
        self.assertEqual((receipt['version'], receipt['unchanged']), (1, True))

    def test_a_stale_expected_version_exits_4_with_the_current_version(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('stored'))
        self.image(TRAIL + '/design/a.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png', '--expected-version', '0')
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(json.loads(proc.stdout), {'current_version': 1})
        self.assertEqual(self.error_of(proc)['kind'], 'conflict')

    def test_not_an_image_address_exits_2_and_sends_nothing(self):
        self.image(TRAIL + '/design/Screen Shot.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/Screen Shot.png')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'not_an_image'))
        self.assertEqual(self.fake.requests, [])

    def test_a_missing_file_exits_2(self):
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'unreadable'))
        self.assertEqual(self.fake.requests, [])

    def test_a_refused_upload_is_the_answered_record(self):
        self.image(TRAIL + '/design/a.png', b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png')
        error = self.error_of(proc)
        self.assertEqual((proc.returncode, error['kind']), (2, 'rejected'))
        self.assertTrue(error['message'].startswith('kartoteka answered HTTP 400: '), error)

    def test_a_daemon_without_attachments_is_named(self):
        self.fake.mode = 'pre_attachments'
        self.image(TRAIL + '/design/a.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png')
        self.assertEqual((proc.returncode, self.error_of(proc)),
                         (2, {'kind': 'no_attachments',
                              'message': spec_store.ATTACHMENTS_MISSING}))

    def test_a_405_is_a_missing_route_too(self):
        self.fake.forced['PUT'] = (405, 'Method Not Allowed')
        self.image(TRAIL + '/design/a.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png')
        self.assertEqual((proc.returncode, self.error_of(proc)),
                         (2, {'kind': 'no_attachments',
                              'message': spec_store.ATTACHMENTS_MISSING}))


class TestPutCreatedVersionDefaults(ImageCase):
    def test_the_default_created_at_is_clearly_in_the_past(self):
        # fake_kartoteka.py's default for a PUT-created version must not be
        # today's date: a caller comparing it against a file's mtime would
        # get a different answer depending on the time of day the suite runs.
        self.image(TRAIL + '/design/a.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stored = self.fake.newest_image(PROJECT, 'AW-12', 'design/a.png')
        when = datetime.fromisoformat(stored['created_at'])
        self.assertLess(when, datetime.now(timezone.utc))


class TestImageToken(ImageCase):
    knowledge_extra = {'tokenEnv': 'ARTEL_TEST_TOKEN'}

    def test_the_token_is_sent_and_never_printed(self):
        self.fake.mode = 'unauthorized'
        self.image(TRAIL + '/design/a.png', png('a'))
        proc = self.run_cli('image', 'put', TRAIL + '/design/a.png',
                            env={'ARTEL_TEST_TOKEN': 'ktk_secret'})
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'unauthorized'))
        self.assertNotIn('ktk_secret', proc.stdout + proc.stderr)
        self.assertEqual(self.fake.requests[-1][4], 'Bearer ktk_secret')


class TestImageList(ImageCase):
    def test_the_newest_version_of_each_path_with_its_logical_path(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'runtime/b.png', png('b'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('a1'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('a2'))
        self.fake.seed_image(PROJECT, 'AW-13', 'design/a.png', png('other ticket'))
        self.fake.seed_image('vpn', 'AW-12', 'design/a.png', png('other project'))
        proc = self.run_cli('image', 'list', 'AW-12-2')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = json.loads(proc.stdout)
        self.assertEqual([(r['path'], r['logical'], r['version']) for r in rows], [
            ('design/a.png', TRAIL + '/design/a.png', 2),
            ('runtime/b.png', TRAIL + '/runtime/b.png', 1)])
        self.assertEqual(set(rows[0]), {'path', 'logical', 'version', 'content_type',
                                        'byte_size', 'content_hash', 'created_at'})
        self.assertEqual((rows[0]['content_type'], rows[0]['content_hash']),
                         ('image/png', sha(png('a2'))))

    def test_nothing_stored_is_an_empty_list(self):
        proc = self.run_cli('image', 'list', 'AW-12')
        self.assertEqual((proc.returncode, json.loads(proc.stdout)), (0, []))

    def test_not_a_ticket_exits_2(self):
        proc = self.run_cli('image', 'list', 'nonsense')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'invalid_argument'))


class TestRequestBytesTimeout(ImageCase):
    """Store.request_bytes' timeout is call_bytes' 60s unless a caller that
    sends no body -- a JSON listing -- asks for call()'s shorter 15s
    (spec-images contract, Store.request_bytes)."""

    def timeouts_seen(self, act):
        seen = []
        real_call_bytes = spec_store.kh.call_bytes

        def spy(*args, **kwargs):
            seen.append(kwargs.get('timeout'))
            return real_call_bytes(*args, **kwargs)

        with mock.patch.object(spec_store.kh, 'call_bytes', side_effect=spy):
            act()
        return seen

    def test_request_bytes_defaults_to_sixty_seconds(self):
        store = spec_store.Store(self.config)
        seen = self.timeouts_seen(lambda: store.request_bytes(
            'GET', '/api/attachments', query={'project': PROJECT, 'ticket_key': 'AW-12'}))
        self.assertEqual(seen, [60])

    def test_image_listing_asks_for_fifteen_seconds(self):
        store = spec_store.Store(self.config)
        seen = self.timeouts_seen(lambda: store.image_listing('AW-12'))
        self.assertEqual(seen, [15])


class TestImageCachePath(unittest.TestCase):
    def test_the_cache_path(self):
        self.assertEqual(spec_store.image_cache_path('AW-12', 'design/a.png'),
                         Path('.artel/run/AW-12/images/design/a.png'))
        self.assertEqual(spec_store.image_cache_path('AW-12', 'design/a.png', 3),
                         Path('.artel/run/AW-12/images/@v3/design/a.png'))

    def test_the_hash(self):
        self.assertEqual(spec_store.sha256_bytes(b'x'), hashlib.sha256(b'x').hexdigest())


class TestImageFetch(ImageCase):
    LOGICAL = TRAIL + '/design/a.png'
    CACHE = '.artel/run/AW-12/images/design/a.png'

    def fetch(self, *extra):
        proc = self.run_cli('image', 'fetch', self.LOGICAL, *extra)
        return proc, (Path(proc.stdout.strip()) if proc.stdout.strip() else None)

    def assertPrinted(self, printed, rel):
        self.assertTrue(printed.is_absolute(), printed)
        self.assertEqual(printed.resolve(), (self.repo / rel).resolve())

    def test_a_file_not_yet_swept_is_printed_without_a_request(self):
        self.image(self.LOGICAL, png('local'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('stored'))
        proc, printed = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertPrinted(printed, self.LOGICAL)
        self.assertEqual(self.fake.requests, [])

    def test_a_stored_image_is_written_to_the_cache_and_printed(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('stored'))
        proc, printed = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertPrinted(printed, self.CACHE)
        self.assertEqual(printed.read_bytes(), png('stored'))
        self.assertNotIn('PNG', proc.stdout + proc.stderr)

    def test_a_cached_copy_kartoteka_still_holds_is_revalidated_not_rewritten(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('stored'))
        cached = self.image(self.CACHE, png('stored'))
        os.utime(cached, (1, 1))
        proc, printed = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertPrinted(printed, self.CACHE)
        self.assertEqual(os.stat(cached).st_mtime, 1)  # a 304: not written again

    def test_a_cached_copy_kartoteka_moved_past_is_replaced(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('old'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('new'))
        self.image(self.CACHE, png('old'))
        proc, printed = self.fetch()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(printed.read_bytes(), png('new'))
        self.assertEqual(sorted(p.name for p in printed.parent.iterdir()), ['a.png'])

    def test_a_named_version_has_its_own_cache_and_ignores_the_local_file(self):
        self.image(self.LOGICAL, png('local'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('v1'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('v2'))
        proc, printed = self.fetch('--version', '1')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertPrinted(printed, '.artel/run/AW-12/images/@v1/design/a.png')
        self.assertEqual(printed.read_bytes(), png('v1'))

    def test_absent_exits_3_and_removes_a_stale_cached_copy(self):
        cached = self.image(self.CACHE, png('stale'))
        proc, printed = self.fetch()
        self.assertEqual((proc.returncode, printed), (3, None))
        self.assertFalse(cached.exists())

    def test_redacted_exits_2_and_drops_the_cached_copy(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('secret'))
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('secret'), redacted=True)
        cached = self.image(self.CACHE, png('secret'))
        proc, printed = self.fetch()
        self.assertEqual((proc.returncode, printed, self.error_of(proc)['kind']),
                         (2, None, 'redacted'))
        self.assertFalse(cached.exists())

    def test_an_unreachable_store_is_an_error_never_the_cache(self):
        cached = self.image(self.CACHE, png('cached'))
        self.fake.stop()
        proc, printed = self.fetch()
        self.assertEqual((proc.returncode, printed, self.error_of(proc)['kind']),
                         (2, None, 'unreachable'))
        self.assertTrue(cached.exists())

    def test_a_daemon_without_attachments_is_an_error(self):
        self.fake.mode = 'pre_attachments'
        proc, printed = self.fetch()
        self.assertEqual((proc.returncode, printed, self.error_of(proc)['kind']),
                         (2, None, 'no_attachments'))

    def test_a_linked_file_at_the_logical_path_is_not_printed(self):
        outside = self.image('elsewhere/secret.png', png('secret'))
        (self.repo / TRAIL / 'design').mkdir(parents=True)
        os.symlink(str(outside), str(self.repo / self.LOGICAL))
        proc, printed = self.fetch()
        self.assertEqual((proc.returncode, printed), (3, None))

    def test_a_name_outside_the_grammar_but_present_locally_is_printed_with_no_request(self):
        # Spec §5 rule 1: a regular file at the logical path is printed with
        # no grammar exception -- a screenshot tool's "Screen Shot.png" is
        # still the newest copy of itself even though its name (the space)
        # would be refused by kartoteka's attachment path grammar.
        outside_grammar = TRAIL + '/design/Screen Shot.png'
        self.image(outside_grammar, png('local'))
        proc = self.run_cli('image', 'fetch', outside_grammar)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        printed = Path(proc.stdout.strip())
        self.assertPrinted(printed, outside_grammar)
        self.assertEqual(self.fake.requests, [])

    def test_a_missing_file_with_a_name_outside_the_grammar_still_exits_2_not_an_image(self):
        proc = self.run_cli('image', 'fetch', TRAIL + '/design/Screen Shot.png')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'not_an_image'))
        self.assertEqual(self.fake.requests, [])

    def test_a_path_that_walks_back_out_of_the_trail_with_dot_dot_is_not_printed(self):
        # Spec §11: fetch must never read or print outside a ticket's trail.
        # kh._trail_address matches only the ticket-directory segment, so a
        # '..' walking back out below it still matches and named the ticket
        # -- the shortcut must not stop at that alone.
        (self.repo / TRAIL).mkdir(parents=True)
        outside = self.image('outside/evil.png', png('secret'))
        escaping = TRAIL + '/../../../' + str(outside.relative_to(self.repo))
        proc = self.run_cli('image', 'fetch', escaping)
        self.assertEqual(proc.stdout, '')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'not_an_image'))
        self.assertEqual(self.fake.requests, [])

    def test_a_path_with_a_dot_segment_is_not_printed_for_free(self):
        # Path() silently drops a '.' component, so kh._trail_address alone
        # cannot tell this spelling from the canonical one; the shortcut must
        # refuse it by the raw string instead of taking Path()'s word for it.
        self.image(self.LOGICAL, png('local'))
        dotted = TRAIL + '/./design/a.png'
        proc = self.run_cli('image', 'fetch', dotted)
        self.assertEqual(proc.stdout, '')
        self.assertNotEqual(proc.returncode, 0)

    def test_a_leading_dot_slash_still_takes_the_free_shortcut(self):
        # M-2: unlike a '.' segment INSIDE the path (the test above), a
        # leading './' is just a spelling of the same path -- './specs/…'.
        # split('/') used to trip the very same '.'-segment guard and fall
        # through to the network, answering absent for an unswept file that
        # exists.
        self.image(self.LOGICAL, png('local'))
        proc = self.run_cli('image', 'fetch', './' + self.LOGICAL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        printed = Path(proc.stdout.strip())
        self.assertPrinted(printed, self.LOGICAL)
        self.assertEqual(self.fake.requests, [])


class TestImageSync(ImageCase):
    AUTHOR = 'artel:feature-development'

    def sync(self):
        proc = self.run_cli('image', 'sync', 'AW-12', '--author', self.AUTHOR)
        return proc, (json.loads(proc.stdout) if proc.returncode == 0 else None)

    def cached(self, path):
        return self.repo / '.artel/run/AW-12/images' / path

    def test_uploads_verifies_moves_and_prunes(self):
        self.image(TRAIL + '/design/a.png', png('a'))
        self.image(TRAIL + '/phase-2/runtime/x.png', png('x'))
        (self.repo / TRAIL / 'runtime').mkdir()
        (self.repo / TRAIL / 'runtime' / 'observation.md').write_text('RUNTIME_OK',
                                                                     encoding='utf-8')
        proc, out = self.sync()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out, {'uploaded': [TRAIL + '/design/a.png',
                                            TRAIL + '/phase-2/runtime/x.png'],
                               'unchanged': [], 'skipped': [], 'failed': [], 'tracked': []})
        stored = self.fake.newest_image(PROJECT, 'AW-12', 'phase-2/runtime/x.png')
        self.assertEqual((stored['bytes'], stored['author_agent']), (png('x'), self.AUTHOR))
        self.assertEqual(self.cached('design/a.png').read_bytes(), png('a'))
        self.assertEqual(self.cached('phase-2/runtime/x.png').read_bytes(), png('x'))
        # Emptied folders go; the ticket directory and anything still holding files stay.
        self.assertFalse((self.repo / TRAIL / 'design').exists())
        self.assertFalse((self.repo / TRAIL / 'phase-2').exists())
        self.assertTrue((self.repo / TRAIL / 'runtime' / 'observation.md').exists())
        self.assertNotIn('PNG', proc.stdout + proc.stderr)

    def test_the_ticket_directory_itself_is_never_pruned(self):
        self.image(TRAIL + '/shot.png', png('s'))
        proc, out = self.sync()
        self.assertEqual(out['uploaded'], [TRAIL + '/shot.png'])
        self.assertTrue((self.repo / TRAIL).is_dir())

    def test_bytes_kartoteka_already_holds_are_unchanged_and_still_moved(self):
        self.fake.seed_image(PROJECT, 'AW-12', 'design/a.png', png('a'))
        self.image(TRAIL + '/design/a.png', png('a'))
        proc, out = self.sync()
        self.assertEqual((out['uploaded'], out['unchanged']), ([], [TRAIL + '/design/a.png']))
        self.assertFalse((self.repo / TRAIL / 'design/a.png').exists())
        self.assertEqual(len(self.fake.attachments[(PROJECT, 'AW-12', 'design/a.png')]), 1)

    def test_tracked_images_are_left_to_migration(self):
        self.image(TRAIL + '/design/a.png', png('a'))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'seed')
        proc, out = self.sync()
        self.assertEqual(out['tracked'], [TRAIL + '/design/a.png'])
        self.assertTrue((self.repo / TRAIL / 'design/a.png').exists())
        self.assertEqual([r for r in self.fake.requests if r[0] == 'PUT'], [])

    def test_an_untracked_glob_lookalike_of_a_tracked_name_is_not_reported_tracked(self):
        # git ls-files reads a bare pathspec as a glob: 'design/a*.png' would
        # otherwise match the tracked 'design/abc.png' and be reported
        # tracked itself, though the file at that exact name is untracked.
        self.image(TRAIL + '/design/abc.png', png('tracked'))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'seed')
        self.image(TRAIL + '/design/a*.png', png('untracked'))
        proc, out = self.sync()
        self.assertEqual(out['tracked'], [TRAIL + '/design/abc.png'])
        self.assertEqual([e['path'] for e in out['skipped']], [TRAIL + '/design/a*.png'])
        self.assertEqual(out['skipped'][0]['reason'], spec_store.OUTSIDE_THE_GRAMMAR)
        self.assertTrue((self.repo / TRAIL / 'design/abc.png').exists())
        self.assertTrue((self.repo / TRAIL / 'design/a*.png').exists())
        self.assertEqual(self.fake.requests, [])

    def test_a_name_outside_the_grammar_is_skipped_and_kept(self):
        self.image(TRAIL + '/design/Screen Shot.png', png('s'))
        proc, out = self.sync()
        self.assertEqual([e['path'] for e in out['skipped']], [TRAIL + '/design/Screen Shot.png'])
        self.assertEqual(out['skipped'][0]['reason'], spec_store.OUTSIDE_THE_GRAMMAR)
        self.assertTrue(spec_store.OUTSIDE_THE_GRAMMAR.startswith(
            "outside kartoteka's image path grammar"))  # plan 2's migrate-specs prose names it
        self.assertTrue((self.repo / TRAIL / 'design/Screen Shot.png').exists())
        self.assertEqual(self.fake.requests, [])

    def test_a_link_is_reported_skipped_and_never_read_or_moved(self):
        outside = self.image('elsewhere/secret.png', png('secret'))
        (self.repo / TRAIL / 'design').mkdir(parents=True)
        os.symlink(str(outside), str(self.repo / TRAIL / 'design/link.png'))
        os.symlink('nowhere.png', str(self.repo / TRAIL / 'design/dangling.png'))
        proc, out = self.sync()
        self.assertEqual(out, {'uploaded': [], 'unchanged': [], 'failed': [], 'tracked': [],
                               'skipped': [
                                   {'path': TRAIL + '/design/dangling.png',
                                    'reason': spec_store.OUTSIDE_THE_TRAIL},
                                   {'path': TRAIL + '/design/link.png',
                                    'reason': spec_store.OUTSIDE_THE_TRAIL}]})
        self.assertTrue((self.repo / TRAIL / 'design/link.png').is_symlink())
        self.assertEqual(outside.read_bytes(), png('secret'))
        self.assertEqual(self.fake.attachments, {})

    def test_an_image_over_the_default_cap_is_skipped_unsent_and_kept(self):
        big = self.image(TRAIL + '/design/big.png',
                         png('big') + b'\0' * spec_store.IMAGE_CAP_BYTES)
        proc, out = self.sync()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['skipped'], [{'path': TRAIL + '/design/big.png',
                                           'reason': spec_store.OVER_THE_CAP}])
        self.assertTrue(big.exists())
        self.assertEqual(self.fake.requests, [])

    def test_a_linked_ticket_directory_is_skipped(self):
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        outside = Path(elsewhere.name)
        (outside / 'a.png').write_bytes(png('not this trail'))
        (self.repo / 'specs/.current').mkdir(parents=True)
        os.symlink(str(outside), str(self.repo / TRAIL))
        proc, out = self.sync()
        self.assertEqual(out['skipped'], [{'path': TRAIL + '/a.png', 'reason': (
            'a symbolic link or a path outside the trail; not read')}])
        self.assertTrue((outside / 'a.png').exists())
        self.assertEqual(self.fake.attachments, {})

    def test_a_refused_upload_is_failed_and_the_rest_still_move(self):
        self.fake.max_attachment_bytes = 16
        self.image(TRAIL + '/design/big.png', png('more than sixteen bytes'))
        self.image(TRAIL + '/design/small.png', png('s'))
        proc, out = self.sync()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out['uploaded'], [TRAIL + '/design/small.png'])
        self.assertEqual([e['path'] for e in out['failed']], [TRAIL + '/design/big.png'])
        self.assertTrue(out['failed'][0]['reason'].startswith('kartoteka answered HTTP 413: '))
        self.assertTrue((self.repo / TRAIL / 'design/big.png').exists())

    def test_a_receipt_for_other_bytes_is_failed_and_the_file_stays(self):
        self.fake.forced['PUT'] = (200, {'project': PROJECT, 'ticket_key': 'AW-12',
                                         'path': 'design/a.png', 'version': 1,
                                         'content_hash': '0' * 64, 'byte_size': 1,
                                         'content_type': 'image/png', 'unchanged': False})
        self.image(TRAIL + '/design/a.png', png('a'))
        proc, out = self.sync()
        self.assertEqual(out['failed'], [{'path': TRAIL + '/design/a.png', 'reason': (
            'kartoteka did not confirm the bytes that were sent; the file is kept')}])
        self.assertTrue((self.repo / TRAIL / 'design/a.png').exists())
        self.assertFalse(self.cached('design/a.png').exists())

    def test_a_file_changed_during_the_upload_is_failed_and_kept(self):
        local = self.image(TRAIL + '/design/a.png', png('a'))

        def rewrite(method, path, query, body):
            if method == 'PUT':
                local.write_bytes(png('rewritten'))
        self.fake.on_request = rewrite
        proc, out = self.sync()
        self.assertEqual(out['failed'], [{'path': TRAIL + '/design/a.png', 'reason': (
            'it changed while it was being stored; the file is kept for the next sweep')}])
        self.assertEqual(local.read_bytes(), png('rewritten'))

    def test_a_write_landing_right_before_the_move_into_the_cache_is_still_caught(self):
        # A write between kartoteka's confirmation and the move into the
        # cache must not move unverified bytes into the cache: the sweep
        # hashes the file it actually moved, not the one it read earlier.
        local = self.image(TRAIL + '/design/a.png', png('a'))
        raced = []
        real_replace = spec_store.os.replace

        def replace_then_race(src, dst):
            if not raced:  # only the first call -- the move into the cache
                raced.append(True)
                Path(src).write_bytes(png('raced'))
            return real_replace(src, dst)

        cwd = os.getcwd()
        os.chdir(str(self.repo))
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(spec_store.os, 'replace', side_effect=replace_then_race):
            out = spec_store.sync_images(spec_store.Store(self.config), self.config, 'AW-12',
                                         self.AUTHOR)
        self.assertEqual(out['failed'], [{'path': TRAIL + '/design/a.png', 'reason': (
            'it changed while it was being stored; the file is kept for the next sweep')}])
        self.assertEqual(local.read_bytes(), png('raced'))  # moved back, not left in the cache
        self.assertFalse(self.cached('design/a.png').exists())

    def test_a_move_back_failure_falls_back_to_copying_the_file_back(self):
        # A cross-device os.replace (or a permissions error) can fail the
        # move-back even though a plain copy would succeed; the sweep must
        # not lose the file just because the cheaper move failed.
        local = self.image(TRAIL + '/design/a.png', png('a'))
        raced = []
        real_replace = spec_store.os.replace

        def race_then_fail_the_move_back(src, dst):
            if not raced:  # the move into the cache: let it happen, then race it
                raced.append(True)
                Path(src).write_bytes(png('raced'))
                return real_replace(src, dst)
            raise OSError(errno.EXDEV, 'Invalid cross-device link')  # the move back

        cwd = os.getcwd()
        os.chdir(str(self.repo))
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(spec_store.os, 'replace', side_effect=race_then_fail_the_move_back):
            out = spec_store.sync_images(spec_store.Store(self.config), self.config, 'AW-12',
                                         self.AUTHOR)
        self.assertEqual(out['failed'], [{'path': TRAIL + '/design/a.png', 'reason': (
            'it changed while it was being stored; the file is kept for the next sweep')}])
        self.assertEqual(local.read_bytes(), png('raced'))  # copied back, not lost
        self.assertFalse(self.cached('design/a.png').exists())  # the cache copy is cleaned up

    def test_a_move_back_and_copy_back_failure_stops_the_sweep_naming_both_paths(self):
        # When even the copy-back cannot put the bytes at the logical path,
        # silently recording `failed` and moving on would strand kartoteka's
        # unverified bytes in the cache with nothing left in the trail -- the
        # sweep must stop loudly instead, exit 2 (never 5: kartoteka itself
        # is not the problem here), and name both paths so an operator can
        # restore the file by hand.
        self.image(TRAIL + '/design/a.png', png('a'))
        raced = []
        real_replace = spec_store.os.replace

        def race_then_fail_the_move_back(src, dst):
            if not raced:
                raced.append(True)
                Path(src).write_bytes(png('raced'))
                return real_replace(src, dst)
            raise OSError(errno.EXDEV, 'Invalid cross-device link')

        cwd = os.getcwd()
        os.chdir(str(self.repo))
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(spec_store.os, 'replace', side_effect=race_then_fail_the_move_back), \
             mock.patch.object(spec_store.shutil, 'copy2',
                               side_effect=OSError(errno.EACCES, 'Permission denied')):
            with self.assertRaises(spec_store.Failure) as ctx:
                spec_store.sync_images(spec_store.Store(self.config), self.config, 'AW-12',
                                       self.AUTHOR)
        self.assertEqual(ctx.exception.code, spec_store.ERROR)  # exit 2, never 5
        message = str(ctx.exception)
        self.assertIn(str(spec_store.image_cache_path('AW-12', 'design/a.png')), message)
        self.assertIn(TRAIL + '/design/a.png', message)

    def test_a_double_failure_strands_the_raced_bytes_at_the_unverified_path(self):
        # I-1: the cache path (`target`) is also image_cache_path's UNVERSIONED
        # fetch cache -- leaving the raced, unverified bytes there would let
        # the next `image fetch <logical>` (or the next sweep of this path)
        # overwrite them. Renaming the stranded file aside first, under a name
        # no other verb reads or writes, keeps it recoverable and out of the
        # way of `image fetch`, which must still answer with kartoteka's
        # verified bytes.
        local = self.image(TRAIL + '/design/a.png', png('a'))
        calls = []
        real_replace = spec_store.os.replace

        def replace_race_and_fail_only_the_move_back(src, dst):
            calls.append((src, dst))
            if len(calls) == 1:  # the move into the cache: race it, then let it through
                Path(src).write_bytes(png('raced'))
                return real_replace(src, dst)
            if len(calls) == 2:  # the move back
                raise OSError(errno.EXDEV, 'Invalid cross-device link')
            return real_replace(src, dst)  # the aside rename: let it through

        cwd = os.getcwd()
        os.chdir(str(self.repo))
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(spec_store.os, 'replace',
                               side_effect=replace_race_and_fail_only_the_move_back), \
             mock.patch.object(spec_store.shutil, 'copy2',
                               side_effect=OSError(errno.EACCES, 'Permission denied')):
            with self.assertRaises(spec_store.Failure) as ctx:
                spec_store.sync_images(spec_store.Store(self.config), self.config, 'AW-12',
                                       self.AUTHOR)
        unverified = self.cached('design/a.png.unverified')
        # The message names the path the way image_cache_path spells it (relative,
        # since the sweep runs from the repo root) -- not necessarily this test's
        # own absolute spelling of the same file.
        relative = spec_store.image_cache_path('AW-12', 'design/a.png').with_name(
            'a.png.unverified')
        self.assertIn(str(relative), str(ctx.exception))
        self.assertFalse(self.cached('design/a.png').exists())
        self.assertTrue(unverified.exists())
        self.assertEqual(unverified.read_bytes(), png('raced'))
        self.assertFalse(local.exists())  # never made it back to the trail

        proc = self.run_cli('image', 'fetch', TRAIL + '/design/a.png')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fetched = Path(proc.stdout.strip())
        self.assertEqual(fetched.read_bytes(), png('a'))  # kartoteka's verified bytes
        self.assertEqual(unverified.read_bytes(), png('raced'))  # untouched by the fetch

    def test_when_only_removing_the_leftover_cache_copy_fails_the_file_is_kept_not_lost(self):
        # I-1: the copy-back landed the bytes back in the trail; only the
        # orphan cache copy under it could not be removed. That copy is
        # harmless -- the next fetch revalidates it against kartoteka -- so
        # this is an ordinary `failed` entry (retried at the next sweep), not
        # the raised `unrecoverable` a real double failure is.
        local = self.image(TRAIL + '/design/a.png', png('a'))
        raced = []
        real_replace = spec_store.os.replace

        def race_then_fail_the_move_back(src, dst):
            if not raced:
                raced.append(True)
                Path(src).write_bytes(png('raced'))
                return real_replace(src, dst)
            raise OSError(errno.EXDEV, 'Invalid cross-device link')

        cwd = os.getcwd()
        os.chdir(str(self.repo))
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(spec_store.os, 'replace', side_effect=race_then_fail_the_move_back), \
             mock.patch.object(spec_store.os, 'unlink',
                               side_effect=OSError(errno.EACCES, 'Permission denied')):
            out = spec_store.sync_images(spec_store.Store(self.config), self.config, 'AW-12',
                                         self.AUTHOR)
        self.assertEqual(out['failed'], [{'path': TRAIL + '/design/a.png', 'reason': (
            'it changed while it was being stored; the file is kept for the next sweep')}])
        self.assertEqual(local.read_bytes(), png('raced'))  # copied back into the trail
        self.assertTrue(self.cached('design/a.png').exists())  # the orphan cache copy is left

    def test_an_outage_midway_exits_5_and_the_rest_stay(self):
        self.image(TRAIL + '/design/a.png', png('a'))
        self.image(TRAIL + '/design/b.png', png('b'))
        puts = []

        def go_down_on_the_second_put(method, path, query, body):
            if method == 'PUT':
                puts.append(path)
                if len(puts) == 2:
                    self.fake.mode = 'unauthorized'
        self.fake.on_request = go_down_on_the_second_put
        proc, out = self.sync()
        self.assertEqual((proc.returncode, proc.stdout), (5, ''))
        error = self.error_of(proc)
        self.assertEqual(error['kind'], 'unavailable')
        self.assertIn('HTTP 401', error['message'])
        self.assertFalse((self.repo / TRAIL / 'design/a.png').exists())
        self.assertTrue((self.repo / TRAIL / 'design/b.png').exists())

    def test_unreachable_exits_5(self):
        self.image(TRAIL + '/design/a.png', png('a'))
        self.fake.stop()
        proc, _ = self.sync()
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (5, 'unavailable'))
        self.assertTrue((self.repo / TRAIL / 'design/a.png').exists())

    def test_a_daemon_without_attachments_exits_5_naming_the_upgrade(self):
        self.fake.mode = 'pre_attachments'
        self.image(TRAIL + '/design/a.png', png('a'))
        proc, _ = self.sync()
        self.assertEqual((proc.returncode, self.error_of(proc)),
                         (5, {'kind': 'unavailable', 'message': spec_store.ATTACHMENTS_MISSING}))
        self.assertTrue((self.repo / TRAIL / 'design/a.png').exists())

    def test_nothing_to_sweep_is_empty_and_sends_nothing(self):
        proc, out = self.sync()
        self.assertEqual((proc.returncode, out),
                         (0, {'uploaded': [], 'unchanged': [], 'skipped': [], 'failed': [],
                              'tracked': []}))
        self.assertEqual(self.fake.requests, [])

    def test_sync_images_is_callable_in_process(self):
        # The migration and conversion plans call it by this signature.
        self.image(TRAIL + '/design/a.png', png('a'))
        cwd = os.getcwd()
        os.chdir(str(self.repo))
        self.addCleanup(os.chdir, cwd)
        result = spec_store.sync_images(spec_store.Store(self.config), self.config, 'AW-12',
                                        self.AUTHOR)
        self.assertEqual(result['uploaded'], [TRAIL + '/design/a.png'])
