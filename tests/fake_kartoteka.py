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

The attachment store (kartoteka 0.44.0) is here too: PUT, GET and the listing
under /api/attachments, raw bytes in and out. A PUT runs its checks in this
order: the ticket key, registration, the path grammar, the size cap
(`max_attachment_bytes`, 413), an empty body, the sniffed type against the
extension (400), expected_version (409), and only then the unchanged rule --
so a put naming a version the store has moved past is refused even when its
bytes match. A byte answer carries kartoteka's headers, and If-None-Match equal
to its ETag answers 304. A redacted version has no bytes and no hash; asking
for it answers 410. Mode 'pre_attachments' is a 0.43 daemon: the artifact
routes work, and every /api/attachments route answers a plain-text 404 -- as
it also does under 'old' and 'workspace_off'.
"""
import hashlib
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TICKET_KEY = re.compile(r'^[A-Z][A-Z0-9]+-\d+\Z')  # kartoteka models.TICKET_KEY
REDACTION_MARKER = '[redacted]'                     # kartoteka workspace.REDACTION_MARKER

ATTACHMENT_SEGMENT = re.compile(r'[A-Za-z0-9._-]+\Z')  # kartoteka's attachment path grammar
ATTACHMENT_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.gif': 'image/gif', '.webp': 'image/webp'}


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _sniff(data):
    """The image type magic bytes say, as kartoteka sniffs it, or None."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _extension_type(path):
    return ATTACHMENT_TYPES.get(os.path.splitext(path)[1].lower())


def _attachment_path_error(path):
    segments = path.split('/')
    if not path or len(path) > 255 or len(segments) > 4 or any(
            s in ('.', '..') or not ATTACHMENT_SEGMENT.match(s) for s in segments):
        return 'path {!r} is outside the attachment grammar'.format(path)
    if _extension_type(path) is None:
        return 'path {!r} does not end in .png, .jpg, .jpeg, .gif or .webp'.format(path)
    return None


def _header_fields(content):
    """kartoteka 0.45.0's read_header for flat blocks: {key: raw value}, or None
    when the content opens with no block. Its own parser, never doc_header's."""
    if not content.startswith('---\n'):
        return None
    end = content.find('\n---\n', 4)
    if end == -1:
        return None
    meta = {}
    for line in content[4:end].split('\n'):
        key, sep, value = line.partition(':')
        if sep and key and not key.startswith((' ', '\t', '#')):
            meta[key] = value.strip()
    return meta or None


def _header_refusal(content, stage, ticket, next_version):
    """The 400 text kartoteka answers for a header that disagrees with the row it
    would become, or None. Only the fields the block carries are checked."""
    meta = _header_fields(content)
    if meta is None:
        return None
    if 'version' in meta and meta['version'] != str(next_version):
        current = next_version - 1
        where = 'the store is at v{}'.format(current) if current else 'no version is stored yet'
        advice = 're-read, re-apply, then ' if current else ''
        return ('header version {} is not the version this write creates ({}): {}set '
                '`version: {}` and pass expected_version={}').format(
                    meta['version'], where, advice, next_version, current)
    if 'type' in meta and meta['type'] != stage:
        return 'header type does not match the stage {!r}'.format(stage)
    if 'ticket' in meta and meta['ticket'] != ticket:
        return 'header ticket does not match the ticket_key {!r}'.format(ticket)
    return None


class FakeKartoteka:
    def __init__(self):
        self.mode = 'ok'
        self.forced = {}     # method -> (status, JSON dict or plain-text str), every request
        self.on_request = None  # callable(method, path, query, body), before each request
        self.artifacts = {}  # (project, ticket, stage, name) -> [version dict], oldest first
        self.attachments = {}  # (project, ticket, path) -> [version dict], oldest first
        self.max_attachment_bytes = 5242880  # kartoteka's [workspace] default
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

    def seed_image(self, project, ticket, path, data, author_agent=None, redacted=False,
                   created_at=None):
        versions = self.attachments.setdefault((project, ticket, path), [])
        versions.append({
            'version': len(versions) + 1,
            'content_hash': None if redacted else hashlib.sha256(data).hexdigest(),
            'content_type': _sniff(data) or _extension_type(path) or 'application/octet-stream',
            'byte_size': len(data),
            'author_agent': author_agent,
            # A clearly past default: today's date would make a version's
            # created_at race the wall clock -- "in the future" before this
            # time of day, and a caller comparing it against a file's mtime
            # would get a different answer depending on when the suite runs.
            'created_at': created_at or '2026-01-01T00:00:{:02d}+00:00'.format(len(versions)),
            'redacted_at': '2026-09-23T11:00:00+00:00' if redacted else None,
            'bytes': None if redacted else bytes(data),
        })
        return versions[-1]

    def newest_image(self, project, ticket, path):
        versions = self.attachments.get((project, ticket, path)) or []
        return versions[-1] if versions else None


def _row(key, version):
    project, ticket, stage, name = key
    return dict(version, project=project, ticket_key=ticket, stage=stage, name=name)


def _image_row(key, version):
    fields = ('version', 'content_type', 'byte_size', 'content_hash', 'author_agent',
              'created_at', 'redacted_at')
    return dict({k: version[k] for k in fields}, ticket_key=key[1], path=key[2])


def _receipt(key, version, unchanged):
    project, ticket, path = key
    return {'project': project, 'ticket_key': ticket, 'path': path,
            'version': version['version'], 'content_hash': version['content_hash'],
            'byte_size': version['byte_size'], 'content_type': version['content_type'],
            'unchanged': unchanged}


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

        def _send_bytes(self, status, data, headers):
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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

        def _read(self, raw=False):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            length = int(self.headers.get('Content-Length') or 0)
            data = self.rfile.read(length) if length else b''
            if raw:
                body = data  # an attachment PUT: the image, as sent
            else:
                body = json.loads(data.decode('utf-8')) if length else None
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
            if fake.mode in ('old', 'workspace_off', 'pre_attachments') \
                    and segments[:2] == ['api', 'attachments']:
                return 404, None  # a daemon before 0.44.0 has no attachment routes
            if ticket_key is not None and not TICKET_KEY.match(ticket_key):
                # put_artifact's first check, before require_registered.
                return 400, {'error': (
                    'ticket_key {!r} does not match {} \u2014 e.g. AW-1200. A key that does not '
                    'match opens a trail nothing joins to.').format(ticket_key, TICKET_KEY.pattern)}
            if fake.mode == 'unregistered' and write:
                return 400, {'error': "unknown project; run: kartoteka project add <name>"}
            return None

        def _attachment_address(self, segments, query):
            """(key, None) for /api/attachments/{ticket_key}/{path}, or (None, a 400)."""
            ticket, path = segments[2], '/'.join(segments[3:])
            if not TICKET_KEY.match(ticket):
                return None, {'error': 'ticket_key {!r} does not match {}'.format(
                    ticket, TICKET_KEY.pattern)}
            error = _attachment_path_error(path)
            if error:
                return None, {'error': error}
            return (query.get('project'), ticket, path), None

        def _get_attachments(self, segments, query):
            if segments == ['api', 'attachments']:
                ticket = query.get('ticket_key')
                if not ticket:
                    return self._send(400, {'error': 'ticket_key is required'})
                if 'path' in query:
                    key = (query.get('project'), ticket, query['path'])
                    return self._send(200, {'attachments': [
                        _image_row(key, v) for v in reversed(fake.attachments.get(key) or [])]})
                return self._send(200, {'attachments': [
                    _image_row(key, versions[-1])
                    for key, versions in sorted(fake.attachments.items())
                    if key[0] == query.get('project') and key[1] == ticket]})
            if len(segments) < 4:
                return self._send(404, {'detail': 'Not Found'})
            key, refused = self._attachment_address(segments, query)
            if refused:
                return self._send(400, refused)
            versions = fake.attachments.get(key) or []
            if 'version' in query:
                versions = [v for v in versions if v['version'] == int(query['version'])]
            if not versions:
                return self._send(404, {'error': 'no such attachment {}/{}'.format(*key[1:])})
            stored = versions[-1]
            if stored['redacted_at'] is not None:
                return self._send(410, {'error': 'attachment {}/{} v{} is redacted'.format(
                    key[1], key[2], stored['version'])})
            etag = '"{}"'.format(stored['content_hash'])
            if self.headers.get('If-None-Match') == etag:
                return self._send_bytes(304, b'', [('ETag', etag)])
            return self._send_bytes(200, stored['bytes'], [
                ('Content-Type', stored['content_type']),
                ('ETag', etag),
                ('X-Kartoteka-Version', str(stored['version'])),
                ('X-Content-Type-Options', 'nosniff'),
                ('Content-Security-Policy', "default-src 'none'; sandbox"),
                ('Content-Disposition', 'inline; filename="{}"'.format(key[2].split('/')[-1])),
                ('Cache-Control', 'private, no-cache'),
            ])

        def do_PUT(self):
            segments, query, data = self._read(raw=True)
            if self._forced():
                return
            route = segments[:2] == ['api', 'attachments'] and len(segments) >= 4
            gated = self._gated(segments, write=True, ticket_key=segments[2] if route else None)
            if gated:
                return self._send_not_found() if gated[1] is None else self._send(*gated)
            if not route:
                return self._send(404, {'detail': 'Not Found'})
            key, refused = self._attachment_address(segments, query)
            if refused:
                return self._send(400, refused)
            if len(data) > fake.max_attachment_bytes:
                return self._send(413, {'error': 'attachment is over max_attachment_bytes '
                                                 '({})'.format(fake.max_attachment_bytes)})
            if not data:
                return self._send(400, {'error': 'empty body'})
            sniffed = _sniff(data)
            if sniffed is None or sniffed != _extension_type(key[2]):
                return self._send(400, {'error': 'the bytes are {}, which {} does not name'.format(
                    sniffed or 'not a png, jpeg, gif or webp image', key[2])})
            newest = fake.newest_image(*key)
            current = newest['version'] if newest else 0
            if 'expected_version' in query and int(query['expected_version']) != current:
                return self._send(409, {'error': 'attachment is at version {}'.format(current),
                                        'current_version': current})
            digest = hashlib.sha256(data).hexdigest()
            if newest is not None and newest['content_hash'] == digest:
                return self._send(200, _receipt(key, newest, True))
            stored = fake.seed_image(*key, data, author_agent=query.get('author_agent'))
            return self._send(200, _receipt(key, stored, False))

        def do_GET(self):
            segments, query, _ = self._read()
            if self._forced():
                return
            gated = self._gated(segments, write=False)
            if gated:
                return self._send_not_found() if gated[1] is None else self._send(*gated)
            if segments[:2] == ['api', 'attachments']:
                return self._get_attachments(segments, query)
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
            refusal = _header_refusal(body['content'], key[2], key[1], current + 1)
            if refusal:
                return self._send(400, {'error': refusal})
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
            if _sha256(text) == newest['content_hash']:
                return self._send(200, _row(key, newest))
            refusal = _header_refusal(text, key[2], key[1], newest['version'] + 1)
            if refusal:
                return self._send(400, {'error': refusal})
            stored = fake.seed(*key, text, author_agent=body.get('author_agent'))
            return self._send(200, _row(key, stored))

    return Handler
