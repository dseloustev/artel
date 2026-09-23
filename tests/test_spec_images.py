import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
