#!/usr/bin/env python3
"""spec-store: artel's scripted access to kartoteka's artifact store.

Where a spec document goes to a program rather than to a model, it goes through
this script by pipe -- `spec_store.py get <path> | tasklist_tasks.py --tasklist -`
-- so the document never passes through an orchestrator's context
(docs/spec-storage.md §4.2). Agents use kartoteka's MCP tools instead.

Every document verb takes a logical path, `<specs.dir>/<TICKET_ID>/plan.md` or
`<specs.dir>/<TICKET_ID>/phase-2/tasks.md`, and addresses the artifact exactly
as the mirror hook always has (kartoteka_http.artifact_identity).

Exit codes: 0 ok · 2 error (JSON envelope on stderr) · 3 absent ·
4 version conflict · 5 kartoteka unavailable (decide only).
Contract: docs/spec-storage.md
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common as h  # noqa: E402
import kartoteka_http as kh  # noqa: E402
import spec_decision as sd  # noqa: E402

VERB = 'spec-store'
OK, ERROR, ABSENT, CONFLICT, UNAVAILABLE = 0, 2, 3, 4, 5


class Failure(Exception):
    def __init__(self, kind, message, code=ERROR):
        super().__init__(message)
        self.kind = kind
        self.code = code


def load_config():
    if not h.CONFIG_PATH.exists():
        raise Failure('no_config', '.artel/config.json not found; run /artel:setup')
    try:
        return json.loads(h.CONFIG_PATH.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise Failure('no_config', '.artel/config.json is not valid JSON: {}'.format(exc))


def address(path, config):
    identity = kh.artifact_identity(path, config)
    if identity is None:
        raise Failure('not_a_spec_document', (
            '{} is not a spec-trail document: a mirrored filename directly under '
            '<specs.dir>/<TICKET_ID>/ or its phase-<N>/ (docs/spec-storage.md §3)').format(path))
    return identity


def artifact_path(ticket_key, stage, name):
    return '/api/artifacts/{}/{}/{}'.format(kh.quote(ticket_key), kh.quote(stage), kh.quote(name))


def unauthorized_message(token_env, token):
    if token:
        return ('kartoteka refused the token in {} (HTTP 401): revoked, expired, or minted for '
                'another daemon -- check `kartoteka token list` on the daemon host').format(token_env)
    if token_env:
        return ('the daemon requires a bearer token (HTTP 401) but {} (knowledge.tokenEnv) is '
                'not set in this environment').format(token_env)
    return ('the daemon requires a bearer token (HTTP 401) but knowledge.tokenEnv is empty; '
            'name the variable that holds a token from `kartoteka token add`')


class Store:
    """One configured kartoteka: target, project and credential, resolved once."""

    def __init__(self, config):
        base, project, error = kh.knowledge_target(config)
        if base is None and error is None:
            raise Failure('adapter_off',
                          'knowledge.adapter is not "kartoteka": this project keeps its spec '
                          'trail as files')
        if error:
            raise Failure('misconfigured', error)
        token, token_env, token_error = kh.bearer_token(config)
        if token_error:
            raise Failure('misconfigured', token_error)
        if token and kh.plaintext_off_loopback(base):
            raise Failure('misconfigured', (
                'knowledge.baseUrl {} is plaintext http:// off loopback and a bearer token would '
                'cross the network in the clear; use the daemon\'s https:// origin').format(base))
        self.base, self.project = base, project
        self._token, self._token_env = token, token_env

    def request(self, method, path, query=None, body=None):
        try:
            status, payload = kh.call(self.base, method, path, token=self._token, query=query,
                                      body=body)
        except kh.Unreachable as exc:
            raise Failure('unreachable', 'kartoteka at {} is unreachable: {}'.format(self.base, exc))
        if status == 401:
            raise Failure('unauthorized', unauthorized_message(self._token_env, self._token))
        if status == 404 and not (isinstance(payload, dict) and 'error' in payload):
            raise Failure('store_off', "kartoteka's artifact store is off on that daemon "
                                       '([workspace] enabled = false)')
        return status, payload

    def _expect_ok(self, status, payload):
        if status >= 400:
            detail = payload.get('error') if isinstance(payload, dict) else payload
            raise Failure('rejected', 'kartoteka answered HTTP {}: {}'.format(status, detail))

    def get(self, ticket_key, stage, name, version=None):
        query = {'project': self.project}
        if version is not None:
            query['version'] = version
        status, payload = self.request('GET', artifact_path(ticket_key, stage, name), query=query)
        if status == 404:
            return None
        self._expect_ok(status, payload)
        return payload

    def versions(self, ticket_key, stage, name):
        status, payload = self.request('GET', artifact_path(ticket_key, stage, name) + '/versions',
                                       query={'project': self.project})
        self._expect_ok(status, payload)
        return payload.get('versions') or []

    def listing(self, ticket_key):
        status, payload = self.request('GET', '/api/artifacts',
                                       query={'project': self.project, 'ticket_key': ticket_key})
        self._expect_ok(status, payload)
        return payload.get('artifacts') or []

    def put(self, ticket_key, stage, name, content, expected_version=None, author=None):
        body = {'project': self.project, 'ticket_key': ticket_key, 'stage': stage, 'name': name,
                'content': content, 'author_agent': author}
        if expected_version is not None:
            body['expected_version'] = expected_version
        status, payload = self.request('POST', '/api/artifacts', body=body)
        if status != 409:
            self._expect_ok(status, payload)
        return status, payload


def _redacted(row):
    return row.get('redacted_at') is not None


def cmd_get(args, config):
    ticket_key, stage, name = address(args.path, config)
    found = Store(config).get(ticket_key, stage, name, args.version)
    if found is None:
        return ABSENT
    sys.stdout.write(found['content'])
    return OK


def cmd_exists(args, config):
    ticket_key, stage, name = address(args.path, config)
    return OK if Store(config).versions(ticket_key, stage, name) else ABSENT


def cmd_list(args, config):
    ticket = sd.canonical_ticket(args.ticket, config)
    if ticket is None:
        raise Failure('invalid_argument', 'not a ticket id: {}'.format(args.ticket))
    rows = Store(config).listing(ticket)
    print(json.dumps([{'name': r['name'], 'stage': r['stage'], 'version': r['version'],
                       'created_at': r['created_at'], 'redacted': _redacted(r)} for r in rows]))
    return OK


def cmd_versions(args, config):
    ticket_key, stage, name = address(args.path, config)
    rows = Store(config).versions(ticket_key, stage, name)
    print(json.dumps([{'version': r['version'], 'content_hash': r['content_hash'],
                       'author_agent': r.get('author_agent'), 'created_at': r['created_at'],
                       'redacted': _redacted(r)} for r in rows]))
    return OK if rows else ABSENT


def cmd_put(args, config):
    ticket_key, stage, name = address(args.path, config)
    content = sys.stdin.buffer.read().decode('utf-8')
    if len(content.encode('utf-8')) > kh.MAX_BYTES:
        raise Failure('too_large', '{} is over {} bytes'.format(args.path, kh.MAX_BYTES))
    status, payload = Store(config).put(ticket_key, stage, name, content,
                                        args.expected_version, args.author)
    if status == 409:
        print(json.dumps({'current_version': payload.get('current_version')}))
        raise Failure('conflict', 'kartoteka holds {} at version {}, not {}'.format(
            name, payload.get('current_version'), args.expected_version), CONFLICT)
    print(json.dumps({'version': payload['version'], 'content_hash': payload['content_hash']}))
    return OK


def build_parser():
    parser = argparse.ArgumentParser(prog='spec_store.py', description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest='verb', required=True)
    get = verbs.add_parser('get')
    get.add_argument('path')
    get.add_argument('--version', type=int)
    get.set_defaults(run=cmd_get)
    exists = verbs.add_parser('exists')
    exists.add_argument('path')
    exists.set_defaults(run=cmd_exists)
    listing = verbs.add_parser('list')
    listing.add_argument('ticket')
    listing.set_defaults(run=cmd_list)
    versions = verbs.add_parser('versions')
    versions.add_argument('path')
    versions.set_defaults(run=cmd_versions)
    put = verbs.add_parser('put')
    put.add_argument('path')
    put.add_argument('--expected-version', type=int)
    put.add_argument('--author')
    put.set_defaults(run=cmd_put)
    return parser


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = build_parser().parse_args(argv)
    try:
        return args.run(args, load_config())
    except Failure as exc:
        print(json.dumps({'ok': False, 'verb': VERB,
                          'error': {'kind': exc.kind, 'message': str(exc)}}), file=sys.stderr)
        return exc.code


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # never a traceback: callers parse stderr
        print(json.dumps({'ok': False, 'verb': VERB,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}),
              file=sys.stderr)
        sys.exit(ERROR)
