import hashlib
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


class TestUnreachable(unittest.TestCase):
    def test_no_answer_raises_unreachable_without_the_token(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]  # closed again before the call
        with self.assertRaises(kh.Unreachable) as info:
            kh.call('http://127.0.0.1:{}'.format(port), 'GET', '/api/artifacts',
                    token='ktk_secret', timeout=2)
        self.assertNotIn('ktk_secret', str(info.exception))
