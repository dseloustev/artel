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


class TestUnreachable(unittest.TestCase):
    def test_no_answer_raises_unreachable_without_the_token(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]  # closed again before the call
        with self.assertRaises(kh.Unreachable) as info:
            kh.call('http://127.0.0.1:{}'.format(port), 'GET', '/api/artifacts',
                    token='ktk_secret', timeout=2)
        self.assertNotIn('ktk_secret', str(info.exception))
