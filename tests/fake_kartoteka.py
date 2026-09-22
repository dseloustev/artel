"""An in-memory stand-in for kartoteka's artifact HTTP surface, for artel's tests.

Implements the routes artel's hooks and scripts call, answering the way
kartoteka 0.43.0 answers: GET/POST /api/artifacts, GET .../{name},
GET .../{name}/versions, PATCH .../{name}. `mode` switches the whole server into
one failure: 'old' (PATCH answers 404 with a plain-text "Not Found" -- what a
daemon before artifact_patch really answers, verified against 0.42.0: Starlette's
router-level not-found, not a 405), 'workspace_off' (the same plain 404 on every
artifact route), 'unregistered' (400
naming `kartoteka project add` on writes; reads answer silent zeros, as the
real daemon does), 'unauthorized' (401 everywhere).
"""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


class FakeKartoteka:
    def __init__(self):
        self.mode = 'ok'
        self.artifacts = {}  # (project, ticket, stage, name) -> [version dict], oldest first
        self.requests = []   # (method, path, query, body, authorization)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _handler_for(self))
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return 'http://127.0.0.1:{}'.format(self.server.server_port)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def seed(self, project, ticket, stage, name, content, author_agent=None, redacted=False):
        versions = self.artifacts.setdefault((project, ticket, stage, name), [])
        versions.append({
            'version': len(versions) + 1,
            'content': '[redacted]' if redacted else content,
            'content_hash': hashlib.sha256(content.encode('utf-8')).hexdigest(),
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

        def _send_not_found(self):
            raw = b'Not Found'
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read(self):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length).decode('utf-8')) if length else None
            fake.requests.append(
                (self.command, parts.path, query, body, self.headers.get('Authorization')))
            return [unquote(s) for s in parts.path.split('/') if s], query, body

        def _gated(self, segments, write):
            if fake.mode == 'unauthorized':
                return 401, {'detail': 'missing or invalid bearer token'}
            if fake.mode == 'workspace_off' and segments[:2] == ['api', 'artifacts']:
                return 404, None  # plain-text Not Found, like an unmounted route
            if fake.mode == 'unregistered' and write:
                return 400, {'error': "unknown project; run: kartoteka project add <name>"}
            return None

        def do_GET(self):
            segments, query, _ = self._read()
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
            gated = self._gated(segments, write=True)
            if gated:
                return self._send_not_found() if gated[1] is None else self._send(*gated)
            if segments != ['api', 'artifacts']:
                return self._send(404, {'detail': 'Not Found'})
            key = (body['project'], body['ticket_key'], body['stage'], body['name'])
            newest = fake.newest(*key)
            digest = hashlib.sha256(body['content'].encode('utf-8')).hexdigest()
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
            text = newest['content']
            for edit in body['edits']:
                if 'append' in edit:
                    text += edit['append']
                else:
                    text = text.replace(edit['old_string'], edit['new_string'], 1)
            stored = fake.seed(*key, text, author_agent=body.get('author_agent'))
            return self._send(200, _row(key, stored))

    return Handler
