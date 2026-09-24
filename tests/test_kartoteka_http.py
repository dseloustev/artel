import hashlib
import json
import os
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import kartoteka_http as kh  # noqa: E402
import knowledge_mirror as km  # noqa: E402
from fake_kartoteka import FakeKartoteka  # noqa: E402


class TestOneDefinition(unittest.TestCase):
    def test_the_mirror_hook_uses_the_shared_definitions(self):
        # One addressing rule and one credential path, not two copies.
        for name in ('artifact_identity', 'knowledge_target', 'bearer_token', 'redacted',
                     'plaintext_off_loopback', 'post_artifact'):
            self.assertIs(getattr(km, name), getattr(kh, name), name)
        self.assertIs(km.MIRRORED, kh.MIRRORED)


class TestStorableProjectKey(unittest.TestCase):
    def test_keys_inside_kartotekas_ticket_key_grammar(self):
        for key in ('AW', 'A1', 'aw', 'PROJ'):
            self.assertTrue(kh.storable_project_key(key), key)

    def test_keys_outside_it(self):
        for key in ('X', 'MY_PROJ', 'MY-PROJ', '1A', ''):
            self.assertFalse(kh.storable_project_key(key), key)


IMAGE_CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


class TestImageNames(unittest.TestCase):
    def test_the_five_extensions_in_any_case(self):
        for name in ('a.png', 'a.PNG', 'a.jpg', 'a.JPEG', 'a.gif', 'a.webp', 'design/a.Png'):
            self.assertTrue(kh.is_image_name(name), name)

    def test_everything_else(self):
        for name in ('a.svg', 'a.md', 'a.png.txt', 'png', '.png', 'a.', 'a.tiff'):
            self.assertFalse(kh.is_image_name(name), name)

    def test_the_types_kartoteka_sniffs(self):
        self.assertEqual(kh.IMAGE_TYPES, {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.gif': 'image/gif', '.webp': 'image/webp'})


class TestImagePathGrammar(unittest.TestCase):
    def test_inside(self):
        for path in ('a.png', 'design/desktop-pending.png', 'phase-2/runtime/x.png',
                     'a/b/c/d.jpg', 'v1.2/shot_01.webp', 'a' * 251 + '.png'):
            self.assertTrue(kh.image_path_ok(path), path)

    def test_outside(self):
        for path in ('', '/a.png', 'a//b.png', 'a/', './a.png', 'a/../b.png', '..',
                     'a/b/c/d/e.png', 'Screen Shot.png', 'a+b.png', 'ä.png', 'a.png\n',
                     'design/a.md', 'a' * 252 + '.png', None):
            self.assertFalse(kh.image_path_ok(path), path)

    def test_the_limits(self):
        self.assertEqual((kh.MAX_IMAGE_PATH, kh.MAX_IMAGE_SEGMENTS), (255, 4))
        self.assertTrue(kh.IMAGE_SEGMENT.match('a-b_c.d'))
        self.assertFalse(kh.IMAGE_SEGMENT.match('a b'))


class TestImageIdentity(unittest.TestCase):
    def test_the_addressing_table(self):
        # docs/superpowers/specs/2026-09-23-kartoteka-spec-images-design.md §3
        for rel, expected in (
                ('specs/.current/AW-3270/design/desktop-pending.png',
                 ('AW-3270', 'design/desktop-pending.png')),
                ('specs/.current/AW-3270/runtime/macos-on-ramp-success.png',
                 ('AW-3270', 'runtime/macos-on-ramp-success.png')),
                ('specs/.current/AW-3270/phase-2/runtime/x.png',
                 ('AW-3270', 'phase-2/runtime/x.png')),
                ('specs/.current/AW-3270/design/Screen Shot.png', None)):
            self.assertEqual(kh.image_identity(rel, IMAGE_CONFIG), expected, rel)

    def test_the_ticket_directory_reads_as_artifact_identity_reads_it(self):
        for ticket_dir in ('AW-12', 'aw-12', 'AW-12-2', '12'):
            rel = 'specs/.current/{}/'.format(ticket_dir)
            self.assertEqual(kh.image_identity(rel + 'shot.png', IMAGE_CONFIG),
                             ('AW-12', 'shot.png'), ticket_dir)
            self.assertEqual(kh.artifact_identity(rel + 'prd.md', IMAGE_CONFIG)[0], 'AW-12')

    def test_not_an_image_of_a_ticket(self):
        for rel in ('specs/.current/AW-12/prd.md',             # a document
                    'specs/.current/shot.png',                 # no ticket directory
                    'specs/.current/scratch/shot.png',         # not a ticket directory
                    'design/AW-12/shot.png',                   # not under specs.dir
                    'specs/.current/AW-12/a/b/c/d/e.png',      # five segments
                    'specs/.current/AW-12/../AW-13/shot.png'):  # a '..'
            self.assertIsNone(kh.image_identity(rel, IMAGE_CONFIG), rel)

    def test_a_configured_specs_dir(self):
        config = dict(IMAGE_CONFIG, specs={'dir': 'docs/trail'})
        self.assertEqual(kh.image_identity('docs/trail/AW-9/design/a.gif', config),
                         ('AW-9', 'design/a.gif'))
        self.assertIsNone(kh.image_identity('specs/.current/AW-9/design/a.gif', config))


class TestCall(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)

    def test_get_returns_status_and_parsed_json(self):
        self.fake.seed('p', 'AW-1', 'prd', 'prd.md', 'body')
        status, body = kh.call(self.fake.base_url, 'GET', '/api/artifacts/AW-1/prd/prd.md',
                               query={'project': 'p'})
        self.assertEqual((status, body['content']), (200, 'body'))

    def test_an_error_status_is_returned_not_raised(self):
        status, body = kh.call(self.fake.base_url, 'GET', '/api/artifacts/AW-1/prd/prd.md',
                               query={'project': 'p'})
        self.assertEqual((status, body), (404, {'error': 'no such artifact'}))

    def test_post_and_patch_send_a_json_body_and_the_bearer_token(self):
        kh.call(self.fake.base_url, 'POST', '/api/artifacts', token='ktk_secret',
                body={'project': 'p', 'ticket_key': 'AW-1', 'stage': 'prd', 'name': 'prd.md',
                      'content': 'a'})
        status, body = kh.call(self.fake.base_url, 'PATCH', '/api/artifacts/AW-1/prd/prd.md',
                               body={'project': 'p', 'edits': [{'append': 'b'}]})
        self.assertEqual((status, body['content'], body['version']), (200, 'ab', 2))
        self.assertEqual(self.fake.requests[0][4], 'Bearer ktk_secret')

    def test_path_segments_are_quoted(self):
        self.assertEqual(kh.quote('phase-2.tasks.md'), 'phase-2.tasks.md')
        self.assertEqual(kh.quote('a/b c'), 'a%2Fb%20c')

    def test_a_configured_proxy_is_ignored(self):
        # The trust boundary: documents go to baseUrl and nowhere else.
        self.fake.seed('p', 'AW-1', 'prd', 'prd.md', 'body')
        saved = {k: os.environ.get(k) for k in ('http_proxy', 'HTTP_PROXY')}
        os.environ['http_proxy'] = os.environ['HTTP_PROXY'] = 'http://127.0.0.1:9'
        try:
            status, _ = kh.call(self.fake.base_url, 'GET', '/api/artifacts', query={'project': 'p'})
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(status, 200)


class TestTheFakeAnswersLikeKartoteka(unittest.TestCase):
    """The fake is worth what it shares with kartoteka's workspace.py and web.py."""

    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)

    def post(self, **fields):
        body = {'project': 'p', 'ticket_key': 'AW-1', 'stage': 'prd', 'name': 'prd.md',
                'content': 'a'}
        body.update(fields)
        return kh.call(self.fake.base_url, 'POST', '/api/artifacts', body=body)

    def patch(self, **fields):
        body = {'project': 'p', 'edits': [{'append': 'x'}]}
        body.update(fields)
        return kh.call(self.fake.base_url, 'PATCH', '/api/artifacts/AW-1/prd/prd.md', body=body)

    def test_a_redacted_version_carries_the_markers_hash(self):
        # redact_artifact recomputes content_hash from the marker, so putting
        # the original text back is a new version, not an idempotent no-op.
        row = self.fake.seed('p', 'AW-1', 'prd', 'prd.md', 'secret', redacted=True)
        self.assertEqual((row['content'], row['content_hash']),
                         ('[redacted]', hashlib.sha256(b'[redacted]').hexdigest()))
        status, body = self.post(content='secret')
        self.assertEqual((status, body['version'], body['content']), (200, 2, 'secret'))

    def test_a_ticket_key_outside_the_grammar_is_refused_before_registration(self):
        # put_artifact checks TICKET_KEY before require_registered.
        self.fake.mode = 'unregistered'
        for key in ('MY_PROJ-7', 'X-12', 'R-2026.10', 'aw-1'):
            status, body = self.post(ticket_key=key)
            self.assertEqual(status, 400, key)
            self.assertTrue(body['error'].startswith(
                "ticket_key {!r} does not match ^[A-Z][A-Z0-9]+-\\d+\\Z".format(key)), body)
        status, body = self.post()
        self.assertEqual(status, 400)
        self.assertIn('kartoteka project add', body['error'])
        self.assertEqual(self.fake.artifacts, {})

    def test_patch_checks_existence_before_expected_version(self):
        status, body = self.patch(expected_version=0)
        self.assertEqual(status, 404)
        self.assertIn('no such artifact', body['error'])
        self.fake.seed('p', 'AW-1', 'prd', 'prd.md', 'a')
        status, body = self.patch(expected_version=0)
        self.assertEqual((status, body['current_version']), (409, 1))
        self.assertIn('error', body)
        self.assertEqual(len(self.fake.artifacts[('p', 'AW-1', 'prd', 'prd.md')]), 1)
        status, body = self.patch(expected_version=1)
        self.assertEqual((status, body['version'], body['content']), (200, 2, 'ax'))

    def test_a_forced_answer_replaces_every_request_of_its_method(self):
        self.fake.forced['PATCH'] = (405, {'detail': 'Method Not Allowed'})
        self.fake.forced['GET'] = (500, 'Internal Server Error')
        self.assertEqual(self.patch(), (405, {'detail': 'Method Not Allowed'}))
        self.assertEqual(kh.call(self.fake.base_url, 'GET', '/api/artifacts'), (500, None))


PNG = b'\x89PNG\r\n\x1a\n'  # the magic kartoteka sniffs a PNG by
ROUTE = '/api/attachments/AW-1/design/a.png'


def png(tag):
    return PNG + tag.encode('utf-8')


def sha_of(data):
    return hashlib.sha256(data).hexdigest()


class TestCallBytes(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)

    def test_put_sends_the_raw_bytes_and_the_bearer_token(self):
        status, raw, _ = kh.call_bytes(self.fake.base_url, 'PUT', ROUTE, token='ktk_secret',
                                       query={'project': 'p', 'author_agent': 'artel:dev'},
                                       data=png('a'), content_type='image/png')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw.decode('utf-8'))['content_hash'], sha_of(png('a')))
        method, path, query, body, authorization = self.fake.requests[-1]
        self.assertEqual((method, path, body, authorization),
                         ('PUT', ROUTE, png('a'), 'Bearer ktk_secret'))
        self.assertEqual(query['author_agent'], 'artel:dev')

    def test_get_answers_raw_bytes_and_lower_cased_headers(self):
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a'))
        status, raw, headers = kh.call_bytes(self.fake.base_url, 'GET', ROUTE,
                                             query={'project': 'p'})
        self.assertEqual((status, raw), (200, png('a')))
        self.assertEqual(headers['etag'], '"{}"'.format(sha_of(png('a'))))
        self.assertEqual(headers['x-kartoteka-version'], '1')
        self.assertEqual(headers['content-type'], 'image/png')
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        self.assertEqual(headers['content-security-policy'], "default-src 'none'; sandbox")
        self.assertEqual(headers['content-disposition'], 'inline; filename="a.png"')
        self.assertEqual(headers['cache-control'], 'private, no-cache')

    def test_a_304_is_an_answer_not_an_exception(self):
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a'))
        etag = '"{}"'.format(sha_of(png('a')))
        status, raw, headers = kh.call_bytes(self.fake.base_url, 'GET', ROUTE,
                                             query={'project': 'p'},
                                             headers={'If-None-Match': etag})
        self.assertEqual((status, raw, headers['etag']), (304, b'', etag))

    def test_an_error_status_is_returned_not_raised(self):
        status, raw, headers = kh.call_bytes(self.fake.base_url, 'GET', ROUTE,
                                             query={'project': 'p'})
        self.assertEqual(status, 404)
        self.assertIn('error', json.loads(raw.decode('utf-8')))
        self.assertEqual(headers['content-type'], 'application/json')

    def test_a_configured_proxy_is_ignored(self):
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a'))
        saved = {k: os.environ.get(k) for k in ('http_proxy', 'HTTP_PROXY')}
        os.environ['http_proxy'] = os.environ['HTTP_PROXY'] = 'http://127.0.0.1:9'
        try:
            status, _, _ = kh.call_bytes(self.fake.base_url, 'GET', ROUTE, query={'project': 'p'})
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(status, 200)


class TestTheFakeAttachmentStore(unittest.TestCase):
    """The fake is worth what it shares with kartoteka 0.44.0's attachment routes."""

    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)

    def put(self, data, route=ROUTE, **query):
        query.setdefault('project', 'p')
        status, raw, _ = kh.call_bytes(self.fake.base_url, 'PUT', route, query=query, data=data)
        return status, json.loads(raw.decode('utf-8'))

    def listing(self, **query):
        query.setdefault('project', 'p')
        query.setdefault('ticket_key', 'AW-1')
        status, body = kh.call(self.fake.base_url, 'GET', '/api/attachments', query=query)
        return status, body

    def test_the_same_bytes_again_are_unchanged_and_make_no_version(self):
        self.assertEqual(self.put(png('a'))[1]['unchanged'], False)
        status, receipt = self.put(png('a'))
        self.assertEqual((status, receipt['version'], receipt['unchanged']), (200, 1, True))
        self.assertEqual(set(receipt), {'project', 'ticket_key', 'path', 'version',
                                        'content_hash', 'byte_size', 'content_type', 'unchanged'})

    def test_bytes_after_a_redacted_newest_version_make_a_version(self):
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a'), redacted=True)
        status, receipt = self.put(png('a'))
        self.assertEqual((status, receipt['version'], receipt['unchanged']), (200, 2, False))

    def test_expected_version_is_checked_before_the_unchanged_rule(self):
        self.put(png('a'))
        status, body = self.put(png('a'), expected_version=0)
        self.assertEqual((status, body['current_version']), (409, 1))
        self.assertIn('error', body)

    def test_the_sniffed_type_must_agree_with_the_extension(self):
        for route, data in (('/api/attachments/AW-1/design/a.jpg', png('a')),
                            ('/api/attachments/AW-1/design/a.png', b'<svg/>'),
                            ('/api/attachments/AW-1/design/a.png', b'')):
            status, body = self.put(data, route=route)
            self.assertEqual(status, 400, route)
            self.assertIn('error', body)
        self.assertEqual(self.fake.attachments, {})

    def test_over_the_cap_is_413(self):
        self.fake.max_attachment_bytes = 8
        status, body = self.put(png('more than eight bytes'))
        self.assertEqual(status, 413)
        self.assertIn('error', body)

    def test_the_path_grammar(self):
        for route in ('/api/attachments/AW-1/a/b/c/d/e.png', '/api/attachments/AW-1/a%20b.png',
                      '/api/attachments/AW-1/design/a.md', '/api/attachments/aw-1/a.png'):
            self.assertEqual(self.put(png('a'), route=route)[0], 400, route)

    def test_the_listing_newest_per_path_and_every_version_of_one(self):
        self.fake.seed_image('p', 'AW-1', 'runtime/b.png', png('b'))
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a1'), author_agent='artel:x')
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a2'), redacted=True)
        self.fake.seed_image('p', 'AW-2', 'design/a.png', png('other ticket'))
        self.fake.seed_image('q', 'AW-1', 'design/a.png', png('other project'))
        status, body = self.listing()
        self.assertEqual(status, 200)
        self.assertEqual([(r['path'], r['version']) for r in body['attachments']],
                         [('design/a.png', 2), ('runtime/b.png', 1)])
        self.assertEqual(set(body['attachments'][0]), {
            'ticket_key', 'path', 'version', 'content_type', 'byte_size', 'content_hash',
            'author_agent', 'created_at', 'redacted_at'})
        self.assertIsNone(body['attachments'][0]['content_hash'])
        _, body = self.listing(path='design/a.png')
        self.assertEqual([r['version'] for r in body['attachments']], [2, 1])
        self.assertEqual(self.listing(ticket_key='')[0], 400)

    def test_a_redacted_version_answers_410(self):
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('a'))
        self.fake.seed_image('p', 'AW-1', 'design/a.png', png('b'), redacted=True)
        status, raw, _ = kh.call_bytes(self.fake.base_url, 'GET', ROUTE, query={'project': 'p'})
        self.assertEqual(status, 410)
        self.assertIn('error', json.loads(raw.decode('utf-8')))
        status, raw, _ = kh.call_bytes(self.fake.base_url, 'GET', ROUTE,
                                       query={'project': 'p', 'version': 1})
        self.assertEqual((status, raw), (200, png('a')))

    def test_a_daemon_without_attachments_answers_a_plain_404(self):
        for mode in ('old', 'workspace_off', 'pre_attachments'):
            self.fake.mode = mode
            for method in ('GET', 'PUT'):
                status, raw, headers = kh.call_bytes(
                    self.fake.base_url, method, ROUTE, query={'project': 'p'},
                    data=png('a') if method == 'PUT' else None)
                self.assertEqual((status, raw, headers['content-type']),
                                 (404, b'Not Found', 'text/plain'), (mode, method))
        self.fake.mode = 'pre_attachments'  # the artifact routes still answer like 0.43
        status, body = kh.call(self.fake.base_url, 'PATCH', '/api/artifacts/AW-1/prd/prd.md',
                               body={'project': 'p', 'edits': [{'append': 'x'}]})
        self.assertEqual(status, 404)
        self.assertIn('no such artifact', body['error'])


class TestUnreachable(unittest.TestCase):
    def test_no_answer_raises_unreachable_without_the_token(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]  # closed again before the call
        with self.assertRaises(kh.Unreachable) as info:
            kh.call('http://127.0.0.1:{}'.format(port), 'GET', '/api/artifacts',
                    token='ktk_secret', timeout=2)
        self.assertNotIn('ktk_secret', str(info.exception))

    def test_call_bytes_raises_unreachable_without_the_token(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        with self.assertRaises(kh.Unreachable) as info:
            kh.call_bytes('http://127.0.0.1:{}'.format(port), 'PUT', ROUTE, token='ktk_secret',
                          data=png('a'), content_type='image/png', timeout=2)
        self.assertNotIn('ktk_secret', str(info.exception))
