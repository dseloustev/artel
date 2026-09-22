"""An in-memory stand-in for kartoteka's artifact HTTP surface, for artel's tests.

Implements the routes artel's hooks and scripts call, answering the way
kartoteka 0.43.0 answers: GET/POST /api/artifacts, GET .../{name},
GET .../{name}/versions, PATCH .../{name}. `mode` switches the whole server into
one failure: 'old' (PATCH answers 404 with a plain-text "Not Found" -- what a
daemon before artifact_patch really answers, verified against 0.42.0: Starlette's
router-level not-found, not a 405), 'workspace_off' (the same plain 404 on every
artifact route), 'unregistered' (400
naming `kartoteka project add` on writes; reads answer silent zeros, as the
real daemon does), 'unauthorized' (401 everywhere). `forced` goes finer: a method
mapped to (status, payload) gets that one answer for every request -- a dict as
JSON, a str as plain text, the way Starlette answers a 405 or an unhandled 500.
`on_request`, a callable (method, path, query, body), runs just before each
request is handled: a test forces a race with it -- seed a version so the next
put conflicts, edit the body it is about to store, or touch a local file while
the caller is mid-run.

The write checks run in workspace.py's order: POST refuses a ticket_key outside
TICKET_KEY before it asks whether the project is registered; PATCH looks the
artifact up (404) before it compares expected_version (409). A redacted version
holds the marker and the marker's hash, because redact_artifact recomputes it.
"""
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TICKET_KEY = re.compile(r'^[A-Z][A-Z0-9]+-\d+\Z')  # kartoteka models.TICKET_KEY
REDACTION_MARKER = '[redacted]'                     # kartoteka workspace.REDACTION_MARKER


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class FakeKartoteka:
    def __init__(self):
        self.mode = 'ok'
        self.forced = {}     # method -> (status, JSON dict or plain-text str), every request
        self.on_request = None  # callable(method, path, query, body), before each request
        self.artifacts = {}  # (project, ticket, stage, name) -> [version dict], oldest first
        self.requests = []   # (method, path, query, body, authorization)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _handler_for(self))
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._stopped = False

    @property
    def base_url(self):
        return 'http://127.0.0.1:{}'.format(self.server.server_port)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        # A test that stops the fake itself (test_unreachable) leaves addCleanup's
        # stop() to run again; ThreadingHTTPServer.shutdown()/server_close() are
        # not safe to call twice, so the second call is a no-op.
        if self._stopped:
            return
        self._stopped = True
        self.server.shutdown()
        self.server.server_close()

    def seed(self, project, ticket, stage, name, content, author_agent=None, redacted=False):
        versions = self.artifacts.setdefault((project, ticket, stage, name), [])
        stored = REDACTION_MARKER if redacted else content
        versions.append({
            'version': len(versions) + 1,
            'content': stored,
            'content_hash': _sha256(stored),
            'author_agent': author_agent,
            'created_at': '2026-09-22T10:00:{:02d}+00:00'.format(len(versions)),
            'redacted_at': '2026-09-22T11:00:00+00:00' if redacted else None,
        })
        return versions[-1]

    def newest(self, project, ticket, stage, name):
        versions = self.artifacts.get((project, ticket, stage, name)) or []
        return versions[-1] if versions else None


def _row(key, version):
    project, ticket, stage, name = key
    return dict(version, project=project, ticket_key=ticket, stage=stage, name=name)


def _handler_for(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload):
            raw = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_text(self, status, text):
            raw = text.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_not_found(self):
            self._send_text(404, 'Not Found')

        def _forced(self):
            answer = fake.forced.get(self.command)
            if answer is None:
                return False
            status, payload = answer
            if isinstance(payload, str):
                self._send_text(status, payload)
            else:
                self._send(status, payload)
            return True

        def _read(self):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else None
            fake.requests.append(
                (self.command, parts.path, query, body, self.headers.get('Authorization')))
            if fake.on_request is not None:
                fake.on_request(self.command, parts.path, query, body)
            return [unquote(s) for s in parts.path.split('/') if s], query, body

        def _gated(self, segments, write, ticket_key=None):
            if fake.mode == 'unauthorized':
                return 401, {'detail': 'missing or invalid bearer token'}
            if fake.mode == 'workspace_off' and segments[:2] == ['api', 'artifacts']:
                return 404, None  # plain-text Not Found, like an unmounted route
            if ticket_key is not None and not TICKET_KEY.match(ticket_key):
                # put_artifact's first check, before require_registered.
                return 400, {'error': (
                    'ticket_key {!r} does not match {} \u2014 e.g. AW-1200. A key that does not '
                    'match opens a trail nothing joins to.').format(ticket_key, TICKET_KEY.pattern)}
            if fake.mode == 'unregistered' and write:
                return 400, {'error': "unknown project; run: kartoteka project add <name>"}
            return None

        def do_GET(self):
            segments, query, _ = self._read()
            if self._forced():
                return
            gated = self._gated(segments, write=False)
            if gated:
                return self._send_not_found() if gated[1] is None else self._send(*gated)
            project = query.get('project')
            if segments == ['api', 'artifacts']:
                rows = []
                for key, versions in sorted(fake.artifacts.items()):
                    if project and key[0] != project:
                        continue
                    if query.get('ticket_key') and key[1] != query['ticket_key']:
                        continue
                    rows.append(_row(key, {k: v for k, v in versions[-1].items() if k != 'content'}))
                return self._send(200, {'artifacts': rows})
            if len(segments) == 5 and segments[:2] == ['api', 'artifacts']:
                key = (project,) + tuple(segments[2:5])
                versions = fake.artifacts.get(key) or []
                if 'version' in query:
                    versions = [v for v in versions if v['version'] == int(query['version'])]
                if not versions:
                    return self._send(404, {'error': 'no such artifact'})
                return self._send(200, _row(key, versions[-1]))
            if len(segments) == 6 and segments[:2] == ['api', 'artifacts'] and segments[5] == 'versions':
                key = (project,) + tuple(segments[2:5])
                fields = ('version', 'content_hash', 'author_agent', 'created_at', 'redacted_at')
                return self._send(200, {'versions': [
                    {k: v[k] for k in fields} for v in reversed(fake.artifacts.get(key) or [])]})
            return self._send(404, {'detail': 'Not Found'})

        def do_POST(self):
            segments, _, body = self._read()
            if self._forced():
                return
            route = segments == ['api', 'artifacts']
            gated = self._gated(segments, write=True,
                                ticket_key=body.get('ticket_key') if route else None)
            if gated:
                return self._send_not_found() if gated[1] is None else self._send(*gated)
            if not route:
                return self._send(404, {'detail': 'Not Found'})
            key = (body['project'], body['ticket_key'], body['stage'], body['name'])
            newest = fake.newest(*key)
            digest = _sha256(body['content'])
            if newest is not None and newest['content_hash'] == digest:
                return self._send(200, _row(key, newest))
            current = newest['version'] if newest else 0
            expected = body.get('expected_version')
            if expected is not None and expected != current:
                return self._send(409, {'error': 'artifact is at version {}'.format(current),
                                        'current_version': current})
            stored = fake.seed(*key, body['content'], author_agent=body.get('author_agent'))
            return self._send(200, _row(key, stored))

        def do_PATCH(self):
            segments, _, body = self._read()
            if self._forced():
                return
            if fake.mode == 'old':
                return self._send_not_found()
            gated = self._gated(segments, write=True)
            if gated:
                return self._send_not_found() if gated[1] is None else self._send(*gated)
            key = (body['project'],) + tuple(segments[2:5])
            newest = fake.newest(*key)
            if newest is None:
                return self._send(404, {'error': 'no such artifact {}/{}/{}; create it with '
                                                 'artifact_put'.format(*segments[2:5])})
            expected = body.get('expected_version')
            if expected is not None and expected != newest['version']:
                # After the lookup, as patch_artifact does: a missing artifact is 404 first.
                return self._send(409, {'error': 'artifact is at version {}'.format(
                    newest['version']), 'current_version': newest['version']})
            text = newest['content']
            for edit in body['edits']:
                if 'append' in edit:
                    text += edit['append']
                else:
                    text = text.replace(edit['old_string'], edit['new_string'], 1)
            stored = fake.seed(*key, text, author_agent=body.get('author_agent'))
            return self._send(200, _row(key, stored))

    return Handler
