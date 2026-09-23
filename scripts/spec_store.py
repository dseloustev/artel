#!/usr/bin/env python3
"""spec-store: artel's scripted access to kartoteka's artifact store.

Where a spec document goes to a program rather than to a model, it goes through
this script by pipe -- `set -o pipefail; spec_store.py get <path> |
tasklist_tasks.py --tasklist -` -- so the document never passes through an
orchestrator's context (docs/spec-storage.md §4.2). Agents use kartoteka's MCP
tools instead.

Every document verb takes a logical path, `<specs.dir>/<TICKET_ID>/plan.md` or
`<specs.dir>/<TICKET_ID>/phase-2/tasks.md`, and addresses the artifact exactly
as the mirror hook always has (kartoteka_http.artifact_identity).

The `image` verbs do the same for the trail's images, which kartoteka keeps in
its attachment store (kartoteka_http.image_identity). Their bytes move script
to HTTP and are never printed: an agent Reads the local path `image fetch`
prints.

Exit codes: 0 ok · 2 error (JSON envelope on stderr) · 3 absent ·
4 version conflict · 5 kartoteka unavailable (`decide`, the `migrate` verbs and
`image sync` -- wherever in a migration or a sweep the store goes down).
Contract: docs/spec-storage.md
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common as h  # noqa: E402
import kartoteka_http as kh  # noqa: E402
import spec_decision as sd  # noqa: E402

VERB = 'spec-store'
OK, ERROR, ABSENT, CONFLICT, UNAVAILABLE = 0, 2, 3, 4, 5


class Failure(Exception):
    """`key` names the config key a misconfiguration is about, when it is one
    that is unset or malformed (kartoteka_http.knowledge_target_detail)."""

    def __init__(self, kind, message, code=ERROR, key=None):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.key = key


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
        base, project, error, key = kh.knowledge_target_detail(config)
        if base is None and error is None:
            raise Failure('adapter_off',
                          'knowledge.adapter is not "kartoteka": this project keeps its spec '
                          'trail as files')
        if error:
            raise Failure('misconfigured', error, key=key)
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
            raise Failure('unreachable', 'kartoteka is unreachable at {}: {}'.format(self.base, exc))
        if status == 401:
            raise Failure('unauthorized', unauthorized_message(self._token_env, self._token))
        if status == 404 and not (isinstance(payload, dict) and 'error' in payload):
            raise Failure('store_off', "kartoteka's artifact store is off on that daemon "
                                       '([workspace] enabled = false)')
        return status, payload

    def _expect_ok(self, status, payload):
        if status >= 400:
            raise Failure('rejected', answered(status, payload))

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

    def request_bytes(self, method, path, query=None, data=None, content_type=None,
                      headers=None, timeout=60):
        """request()'s twin for the attachment routes: (status, raw bytes, headers).

        A 404 without kartoteka's JSON {"error"}, or a 405, is a route that is
        not there, and on /api/attachments that is a daemon before 0.44.0: its
        own kind, so that a sweep or a migration can say "upgrade it" rather
        than "off" (spec-images §7). `timeout` defaults to call_bytes' own 60s
        -- long enough for an image upload -- but a caller that sends no body
        (a JSON listing) can ask for call()'s shorter 15s instead, so it does
        not wait as long as an upload would for the same unreachable daemon.
        """
        try:
            status, raw, answer = kh.call_bytes(self.base, method, path, token=self._token,
                                                query=query, data=data,
                                                content_type=content_type, headers=headers,
                                                timeout=timeout)
        except kh.Unreachable as exc:
            raise Failure('unreachable', 'kartoteka is unreachable at {}: {}'.format(self.base, exc))
        if status == 401:
            raise Failure('unauthorized', unauthorized_message(self._token_env, self._token))
        if status == 405 or (status == 404 and 'error' not in (_json_object(raw) or {})):
            raise Failure('no_attachments', ATTACHMENTS_MISSING)
        return status, raw, answer

    def image_put(self, ticket_key, path, data, expected_version=None, author=None):
        """(status, receipt) -- or (409, {error, current_version}), returned for
        the caller to word. Any other refusal (400, 413) is Failure('rejected')."""
        query = {'project': self.project}
        if author:
            query['author_agent'] = author
        if expected_version is not None:
            query['expected_version'] = expected_version
        status, raw, _ = self.request_bytes(
            'PUT', attachment_route(ticket_key, path), query=query, data=data,
            content_type=kh.IMAGE_TYPES.get(os.path.splitext(path)[1].lower()))
        payload = _json_object(raw)
        if status != 409:
            self._expect_ok(status, payload)
        return status, payload

    def image_listing(self, ticket_key, path=None):
        """The newest version of each stored image of this ticket, by path; or,
        with `path`, every version of that one, newest first, redacted included."""
        query = {'project': self.project, 'ticket_key': ticket_key}
        if path is not None:
            query['path'] = path
        status, raw, _ = self.request_bytes('GET', '/api/attachments', query=query, timeout=15)
        payload = _json_object(raw)
        self._expect_ok(status, payload)
        return (payload or {}).get('attachments') or []

    def image_get(self, ticket_key, path, version=None, etag=None):
        """(status, bytes, headers) for 200, 304, 404 and 410: each means
        something different to `image fetch`. `etag` is the sha256 of a copy
        already held; kartoteka answers 304 when it still holds those bytes."""
        query = {'project': self.project}
        if version is not None:
            query['version'] = version
        headers = {'If-None-Match': '"{}"'.format(etag)} if etag else None
        status, raw, answer = self.request_bytes('GET', attachment_route(ticket_key, path),
                                                 query=query, headers=headers)
        if status not in (200, 304, 404, 410):
            self._expect_ok(status, _json_object(raw))
        return status, raw, answer


def _redacted(row):
    return row.get('redacted_at') is not None


def cmd_get(args, config):
    ticket_key, stage, name = address(args.path, config)
    found = Store(config).get(ticket_key, stage, name, args.version)
    if found is None:
        return ABSENT
    if _redacted(found):
        # The content is kartoteka's marker; printed, a pipe would read it as the document.
        raise Failure('redacted', '{} v{} is redacted: kartoteka keeps a marker in place of the '
                                  'document'.format(args.path, found.get('version')))
    try:
        sys.stdout.write(found['content'])
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader stopped early: `grep -q` exits on its first match. The
        # document was fetched, so this is success -- under `set -o pipefail`
        # (docs/spec-storage.md §4.2) the pipe answers with the reader's status.
        # stdout goes to devnull so the interpreter's exit flush cannot raise again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
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


PROBE_STAGE, PROBE_NAME = 'artel-probe', 'probe.md'
UNSTORABLE_KEY_REASON = ('kartoteka cannot store tickets keyed {}-…: its ticket-key grammar '
                         'needs a project key of two or more letters or digits, starting with '
                         'a letter')


def answered(status, payload):
    """The one record for an answer nothing more specific explains.

    The error text is kartoteka's JSON `error` (or FastAPI's `detail`), cut at
    kh.ERROR_BODY_LIMIT: a 422 echoes the request body, and that body can be a
    whole document -- which would then land in stderr and in a model's context.
    """
    text = None
    if isinstance(payload, dict):
        text = payload.get('error') or payload.get('detail')
    text = str(text) if text else 'no error text'
    if len(text) > kh.ERROR_BODY_LIMIT:
        text = text[:kh.ERROR_BODY_LIMIT] + '...(truncated)'
    return 'kartoteka answered HTTP {}: {}'.format(status, text)


def _route_missing(store, ticket):
    """The record for a PATCH route that is not there (a plain 404, or a 405).

    A daemon before 0.43.0 has no PATCH route but still serves the listing; a
    daemon with [workspace] off serves neither (verified against 0.42.0: an
    unknown route answers a plain-text 404). Only a served listing means "old".
    """
    try:
        status, payload = store.request('GET', '/api/artifacts',
                                        query={'project': store.project, 'ticket_key': ticket})
    except Failure as exc:
        return str(exc)  # store_off: the workspace is off; or the listing's own record
    if 200 <= status < 300:
        return 'the kartoteka daemon predates artifact_patch (0.43.0); upgrade it'
    return answered(status, payload)


def probe(store, ticket):
    """None when this daemon can hold the ticket's spec trail, else the §2.1 record.

    One PATCH to an address that never exists, with expected_version 0.
    kartoteka looks the artifact up before it compares versions, so a current,
    registered daemon answers 404 {"error"} -- or 409 if someone did create
    the address -- and writes nothing either way. Every other answer is one row
    of the resolution table. A GET cannot do this: scoped reads for an
    unregistered project answer silent zeros, and a daemon before 0.43.0
    serves them fine.
    """
    try:
        status, payload = store.request(
            'PATCH', artifact_path(ticket, PROBE_STAGE, PROBE_NAME),
            body={'project': store.project, 'edits': [{'append': 'probe'}],
                  'expected_version': 0})
    except Failure as exc:
        if exc.kind != 'store_off':
            return str(exc)  # unreachable, unauthorized: already the record
        return _route_missing(store, ticket)  # a 404 without kartoteka's {"error"}
    if status in (404, 409):
        return None
    if status == 405:
        return _route_missing(store, ticket)
    error = payload.get('error') if isinstance(payload, dict) else None
    if status == 400 and 'kartoteka project add' in str(error or ''):
        return ('kartoteka refused knowledge.project as unregistered; run kartoteka project add '
                '{}').format(store.project)
    return answered(status, payload)


def attachment_probe(store, ticket):
    """None when this daemon holds attachments (kartoteka 0.44.0), else the record.

    One scoped listing, GET /api/attachments?project=&ticket_key=<T>, asked only
    after the artifact probe and listing passed (spec-images §7). The artifact
    store is therefore on, and a plain 404 here can only mean the route is
    missing: a daemon before 0.44.0, a row-8 variant of docs/spec-storage.md
    §2.1. Store.image_listing names that 'no_attachments'; 'store_off' is the
    same plain 404 seen through the JSON client, and means the same. Any other
    failure is already its own record, as row 9's are."""
    try:
        store.image_listing(ticket)
    except Failure as exc:
        if exc.kind in ('no_attachments', 'store_off'):
            return ATTACHMENTS_MISSING
        return str(exc)
    return None


def _emit(decision, ticket, config):
    out = dict(decision, ticket=ticket, written=True, local_trail=[])
    if decision['store'] == 'kartoteka':
        pending = {p.get('path') for p in decision.get('pending') or []}
        out['local_trail'] = [p for p in sd.local_trail(ticket, config) if p not in pending]
    print(json.dumps(out))
    return OK


def _inherited_files_base(previous):
    """The files-era migration base a new decision inherits from the standing one, or None.

    A files decision froze `versions` when the run went local: the version each
    document on disk was edited from. Resume re-probes with a plain `decide`,
    which replaces that decision -- and with it the base -- while the copies are
    still on disk, so it is carried on as `files_base` (docs/spec-storage.md
    §2.2) until the ticket's local trail is gone. An existing `files_base` wins
    over the standing decision's `versions`: a files decision renewed AFTER a
    re-probe carries a listing taken long after those copies were made, and
    judging them against it would read another agent's newer version as their
    own base."""
    carried = previous.get('files_base')
    if isinstance(carried, dict) and carried:
        return carried
    versions = previous.get('versions')
    if previous.get('store') == 'files' and isinstance(versions, dict) and versions:
        return versions
    return None


def cmd_decide(args, config):
    ticket = sd.canonical_ticket(args.ticket, config)
    if ticket is None:
        raise Failure('invalid_argument', 'not a ticket id: {}'.format(args.ticket))
    previous = sd.load(ticket) or {}
    carried = {'versions': previous.get('versions') or {}, 'pending': previous.get('pending') or []}
    adapter = (config.get('knowledge') or {}).get('adapter') or 'none'
    if adapter == 'none':
        print(json.dumps({'store': 'files', 'reason': None, 'written': False}))
        return OK

    frozen = previous.get('files_base')
    frozen = frozen if isinstance(frozen, dict) and frozen else None

    def files(reason):
        decision = sd.new_decision('files', reason, args.decided_by, **carried)
        if frozen:  # a files decision keeps the base a previous local episode froze
            decision['files_base'] = frozen
        sd.write(ticket, decision)
        return _emit(decision, ticket, config)

    def unavailable(record):
        print(json.dumps({'store': None, 'reason': record, 'versions': carried['versions']}))
        return UNAVAILABLE

    if args.files is not None:
        return files(args.files)
    if args.local:
        return files('local-only run requested')
    project_key = (config.get('ticket') or {}).get('projectKey') or 'PROJ'
    if not kh.storable_project_key(project_key):
        return files(UNSTORABLE_KEY_REASON.format(project_key.upper()))
    try:
        store = Store(config)
    except Failure as exc:
        if exc.key:
            # The record line docs/knowledge-consultation.md and docs/task-queue.md
            # already spell for an empty or malformed key -- byte for byte, because
            # a paraphrase of a gate message is how two documents come to disagree.
            return unavailable(
                'kartoteka is configured for this project but {} is not set'.format(exc.key))
        return unavailable(str(exc))  # any other misconfiguration names itself
    record = probe(store, ticket)
    if record:
        return unavailable(record)
    try:
        rows = store.listing(ticket)
    except Failure as exc:
        return unavailable(str(exc))
    record = attachment_probe(store, ticket)
    if record:
        return unavailable(record)
    versions = {row['name']: row['version'] for row in rows}
    decision = sd.new_decision('kartoteka', None, args.decided_by, versions, carried['pending'])
    base = _inherited_files_base(previous)
    if base:
        decision['files_base'] = base
    sd.write(ticket, decision)
    return _emit(decision, ticket, config)


def cmd_decision(args, config):
    ticket = sd.canonical_ticket(args.ticket, config)
    decision = sd.load(ticket) if ticket else None
    if decision is None:
        print('{}')
        return ABSENT
    print(json.dumps(dict(decision, ticket=ticket, fresh=sd.is_fresh(decision))))
    return OK


def cmd_pending_add(args, config):
    path = os.path.normpath(args.path)  # the guard compares normalised paths
    ticket_key, _, _ = address(path, config)
    decision = sd.load(ticket_key)
    if decision is None:
        raise Failure('no_decision', 'no storage decision for {}; run decide first'.format(
            ticket_key))
    pending = [p for p in decision.get('pending') or []
               if not (isinstance(p, dict) and isinstance(p.get('path'), str)
                       and os.path.normpath(p['path']) == path)]
    pending.append({'path': path, 'base_version': args.base_version})
    decision['pending'] = pending
    sd.write(ticket_key, decision)
    print(json.dumps({'ticket': ticket_key, 'pending': pending}))
    return OK


# ---- Migration: docs/spec-storage.md §7, skills/migrate-specs/SKILL.md ----------

MIGRATION_AUTHOR = 'artel:migrate-specs'
DIFF_LINES = 200
STORE_DOWN = ('unreachable', 'unauthorized', 'store_off', 'no_attachments')


def migrating(run):
    """Wrap a migrate verb so that a store-level failure anywhere inside it --
    not only at the opening probe -- exits 5 with its record, exactly as an
    outage before the run began does. An orchestrator resuming a migration reads
    one answer for "kartoteka is down", wherever the run got to."""
    def verb(args, config):
        try:
            return run(args, config)
        except Failure as exc:
            if exc.kind in STORE_DOWN:
                raise Failure('unavailable', str(exc), UNAVAILABLE) from None
            raise
    return verb


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _diff(old, new, old_label, new_label):
    lines = list(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                      fromfile=old_label, tofile=new_label))
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + ['... ({} more diff lines)\n'.format(len(lines) - DIFF_LINES)]
    return ''.join(lines)


def migration_tickets(args, config):
    if args.all:
        specs_dir = Path((config.get('specs') or {}).get('dir') or 'specs/.current')
        names = set()
        for root in (specs_dir, sd.CONTEXT_TICKETS):
            if root.is_dir():
                names.update(p.name for p in root.iterdir() if p.is_dir())
        return sorted(n for n in names if sd.canonical_ticket(n, config) == n)
    tickets = set()
    for value in args.ticket:
        ticket = sd.canonical_ticket(value, config)
        if ticket is None:
            raise Failure('invalid_argument', 'not a ticket id: {}'.format(value))
        tickets.add(ticket)
    if not tickets:
        raise Failure('invalid_argument', 'name at least one ticket, or pass --all')
    return sorted(tickets)


def migration_store(config, tickets):
    """The store, proven able to take these trails -- documents and images -- or
    Failure(unavailable, exit 5). The attachment probe runs for a trail of
    documents too: kartoteka 0.44.0 is a hard floor (spec-images, decision 8),
    and failing here beats failing midway through classification."""
    store = Store(config)
    record = None
    if tickets:
        record = probe(store, tickets[0]) or attachment_probe(store, tickets[0])
    if record:
        raise Failure('unavailable', record, UNAVAILABLE)
    return store


OUTSIDE_THE_TRAIL = 'a symbolic link or a path outside the trail; not read'


def trail_roots(ticket, config):
    """This ticket's two trail roots, in sd.local_trail's order: the working
    tree's <specs.dir>/<TICKET_ID>, then save-context's context copy."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    return [str(Path(specs_dir) / ticket), str(sd.CONTEXT_TICKETS / ticket / 'spec-trail')]


def _trail_root_of(source, ticket, config):
    """The ticket trail root this path sits under, as written, or None."""
    source = os.path.normpath(source)
    for root in trail_roots(ticket, config):
        root = os.path.normpath(root)
        if source.startswith(root + os.sep):
            return root
    return None


def _outside_the_trail(source, ticket, config):
    """Whether this candidate must never be read or deleted.

    A symbolic link -- the file itself, its trail root, or any directory
    between them -- or a real path that lands outside the real trail root. A
    committed symlink under <specs.dir> can point anywhere, at a private key or
    at another repository, and reading it would upload that file as this
    ticket's document while deleting it would reach outside the trail. What sits
    ABOVE a trail root may well be a link: a worktree's .artel/context is a
    symlink to the main checkout's store (docs/worktrees.md)."""
    root = _trail_root_of(source, ticket, config)
    if root is None:
        return True
    walked = Path(root)
    if walked.is_symlink():
        return True
    for part in Path(os.path.normpath(source)).relative_to(root).parts:
        walked = walked / part
        if walked.is_symlink():
            return True
    real_root, real = os.path.realpath(root), os.path.realpath(source)
    return not real.startswith(real_root + os.sep)


def _created_at(version):
    """A stored version's created_at as an aware datetime, or None when it is not
    a timezone-aware ISO stamp."""
    stamp = version.get('created_at')
    if not isinstance(stamp, str):
        return None
    try:
        created = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    return created if created.tzinfo is not None else None


def _mtime(source):
    """This file's last write as an aware UTC datetime, or None when unreadable."""
    try:
        return datetime.fromtimestamp(os.stat(source).st_mtime, timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


def _older_than(source, version):
    """Whether this file was last written before kartoteka stored that version.

    An unreadable file time, or a created_at that is not a timezone-aware ISO
    stamp, answers False: the check only ever adds conflicts, and a stamp this
    cannot read says nothing."""
    created, written = _created_at(version), _mtime(source)
    return created is not None and written is not None and written < created


def _newer_than(source, version):
    """Whether this file was last written after kartoteka stored that version.

    The mirror of _older_than, and cautious in the other direction: an
    unreadable time answers False, because this check alone lets an image
    upload on its own (spec-images §8's successor)."""
    created, written = _created_at(version), _mtime(source)
    return created is not None and written is not None and written > created


def candidates(ticket, config, pending_only):
    """({logical path: [source paths]}, decision, {pending source: base_version})."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    decision = sd.load(ticket) or {}
    raw_pending = decision.get('pending')
    # A hand-corrupted decision file (e.g. "pending": 5) must not crash migration
    # (plan 2 final re-review): only a list of {path, base_version} dicts counts.
    raw_pending = raw_pending if isinstance(raw_pending, list) else []
    pending = {os.path.normpath(p['path']): p.get('base_version') for p in raw_pending
               if isinstance(p, dict) and isinstance(p.get('path'), str) and p.get('path')}
    context_prefix = str(sd.CONTEXT_TICKETS / ticket / 'spec-trail') + '/'
    grouped = {}
    for source in sd.local_trail(ticket, config, unmovable=True):
        logical = source
        if source.startswith(context_prefix):
            logical = str(Path(specs_dir) / ticket / source[len(context_prefix):])
        if pending_only and source not in pending:
            continue
        grouped.setdefault(logical, []).append(source)
    return grouped, decision, pending


def _known_base(sources, name, decision, pending):
    """The stored version this copy was made from, when the trail says; else None.

    A pending save records it exactly. Otherwise the frozen files-era base --
    `files_base` where a later `decide` carried it on, else a standing files
    decision's own `versions` (§2.2) -- and a document it lacks was new then (0).
    `files_base` is read FIRST for the reason _inherited_files_base gives: where
    both are there, it is the older, truer base. Nothing else records one: a
    legacy trail from the mirror era has none. Anything that is not a whole
    number in a dict is a hand-corrupted decision file, and reads as no base at
    all -- the conservative answer, since a wrong base can turn another agent's
    version into this copy's own.
    """
    for source in sources:
        if source in pending:
            base = pending[source]
            if isinstance(base, int) and not isinstance(base, bool):
                return base
    frozen = decision.get('files_base')
    if not (isinstance(frozen, dict) and frozen):
        frozen = decision.get('versions') if decision.get('store') == 'files' else None
    if not (isinstance(frozen, dict) and frozen):
        return None
    base = frozen.get(name, 0)
    return base if isinstance(base, int) and not isinstance(base, bool) else None


def _judge(item, text, versions, stored, decision, pending, working_copy):
    """Classify the one local copy of an address kartoteka does not hold:
    absent, successor, or conflict (docs/spec-storage.md §7).

    A redaction blocks every automatic upload of a copy that has no known base:
    a redacted version keeps the marker and the marker's hash, so the removed
    text can never read as current or stale, and a copy that still carries it
    would otherwise be uploaded as the newest version. Nothing about such an
    address is diffed -- a diff would print the removed text back out."""
    newest = versions[0] if versions else None
    if newest is None:
        item['class'] = 'absent'
        return
    redactions = [v for v in versions if _redacted(v)]

    def conflict(reason):
        item.update({'class': 'conflict', 'reason': reason, 'diff': None if redactions else _diff(
            stored().get('content', ''), text, 'kartoteka v{}'.format(newest['version']),
            item['source'])})

    if _redacted(newest):
        return conflict('the stored newest version (v{}) is redacted'.format(newest['version']))
    base = _known_base(item['sources'], item['name'], decision, pending)
    item['base_version'] = base
    if base is None and redactions:
        return conflict('kartoteka redacted {} of this document; this copy may carry the removed '
                        'content — review it before choosing'.format(
                            ', '.join('v{}'.format(v['version']) for v in redactions)))
    if base is not None:
        if newest['version'] == base:
            item.update({'class': 'successor', 'reason': 'made from v{}'.format(base)})
            return
        return conflict('kartoteka moved from v{} to v{} since this copy was made'.format(
            base, newest['version']))
    if all(v.get('author_agent') is None for v in versions):
        # Mirror-only history can only lag BEHIND the working tree, because the
        # mirror hook wrote it from that very file. A .artel/context copy is a
        # different thing: save-context snapshots, shared across worktrees and
        # merged newer-wins, which can be older than anything kartoteka holds.
        if working_copy is None:
            return conflict('a saved context copy with no known base; it may be older than what '
                            'kartoteka holds')
        if _older_than(working_copy, newest):
            return conflict("this copy is older than kartoteka's v{}".format(newest['version']))
        item.update({'class': 'successor',
                     'reason': 'kartoteka holds only mirror copies of this file, which can only lag'})
        return
    authors = sorted({v['author_agent'] for v in versions if v.get('author_agent')})
    return conflict('kartoteka holds versions written there ({}) that this copy never saw'.format(
        ', '.join(authors)))


def classify(store, config, ticket, logical, sources, decision, pending, fetch_stored=False):
    """This address's items: one per distinct local content (docs/spec-storage.md §7).

    A copy kartoteka holds -- as its newest version (current) or an older one
    (stale) -- is settled on its own. The copies left are unknown to the store.
    One unknown copy is judged against the store's history. Two or more, or one
    beside a copy that could not be read, are each a conflict: nothing says which
    of them is the newer work, so the user picks one.

    An image address goes to classify_image; fetch_stored is its alone.
    """
    if kh.is_image_name(Path(logical).name):
        return classify_image(store, config, ticket, logical, sources, fetch_stored)
    ticket_key, stage, name = address(logical, config)
    context_root = os.path.normpath(trail_roots(ticket, config)[1]) + os.sep
    items, groups, texts = [], {}, {}

    def new_item(copies, **fields):
        item = {'ticket': ticket, 'logical': logical, 'name': name, 'sources': copies,
                'source': copies[0], 'sha256': None, 'class': None, 'reason': None,
                'newest_version': None, 'base_version': None, 'diff': None}
        item.update(fields)
        items.append(item)
        return item

    for source in sources:
        if _outside_the_trail(source, ticket, config):
            new_item([source], **{'class': 'skipped', 'reason': OUTSIDE_THE_TRAIL})
            continue
        try:
            raw = Path(source).read_bytes()
            text = raw.decode('utf-8')
        except (OSError, UnicodeDecodeError) as exc:
            new_item([source], **{'class': 'skipped', 'reason': 'unreadable: {}'.format(exc)})
            continue
        if len(raw) > kh.MAX_BYTES:
            new_item([source], **{'class': 'skipped',
                                  'reason': 'over the {}-byte artifact limit'.format(kh.MAX_BYTES)})
            continue
        digest = _sha(text)
        if digest in groups:
            groups[digest]['sources'].append(source)
        else:
            groups[digest], texts[digest] = new_item([source], sha256=digest), text
    versions = store.versions(ticket_key, stage, name)
    newest = versions[0] if versions else None
    for item in items:
        item['newest_version'] = newest['version'] if newest else None
    fetched = []

    def stored():
        if not fetched:
            fetched.append(store.get(ticket_key, stage, name) or {})
        return fetched[0]

    unknown = []
    for digest, item in groups.items():
        if newest is not None and not _redacted(newest) and digest == newest['content_hash']:
            item['class'] = 'current'
        elif digest in [v['content_hash'] for v in versions[1:] if not _redacted(v)]:
            item.update({'class': 'stale',
                         'reason': 'kartoteka has moved on to v{}'.format(newest['version'])})
        else:
            unknown.append(item)
    skipped = [i for i in items if i['class'] == 'skipped']
    if len(unknown) == 1 and not skipped:
        item = unknown[0]
        working = next((s for s in item['sources']
                        if not os.path.normpath(s).startswith(context_root)), None)
        _judge(item, texts[item['sha256']], versions, stored, decision, pending, working)
        return items
    differing = ', '.join(i['source'] for i in unknown)
    unread = ', '.join(i['source'] for i in skipped)
    if len(unknown) > 1:
        reason = 'the local copies differ: {}'.format(differing)
        if unread:
            reason += '; another copy was not read: {}'.format(unread)
    else:
        reason = 'another copy of this document was not read: {}'.format(unread)
    # A redaction withholds every diff of this address, local against local included:
    # any of these copies may still carry the text kartoteka removed.
    redaction = any(_redacted(v) for v in versions)
    for item in unknown:
        text, diffs = texts[item['sha256']], []
        if not redaction:
            diffs = [_diff(texts[other['sha256']], text, other['source'], item['source'])
                     for other in unknown if other is not item]
            if newest is not None:
                diffs.append(_diff(stored().get('content', ''), text,
                                   'kartoteka v{}'.format(newest['version']), item['source']))
        item.update({'class': 'conflict', 'reason': reason,
                     'diff': None if redaction else ''.join(diffs)})
    return items


# ---- Images: spec-images §8 ------------------------------------------------------
# IMAGE_CAP_BYTES, OUTSIDE_THE_GRAMMAR and OVER_THE_CAP are plan 1's (the sweep's).


def cache_stored_image(store, ticket_key, path, row):
    """The local path now holding this stored version's bytes, or None.

    It is the file `image fetch <logical> --version N` prints (image_cache_path),
    written by an atomic replace, so the user can open the stored side of a
    conflict. It is always fetched afresh, because migration never reads the
    cache (spec-images §5). None when kartoteka will not serve that version, or
    serves bytes that are not the listed hash."""
    status, data, _ = store.image_get(ticket_key, path, version=row['version'])
    if status != 200 or sha256_bytes(data) != row.get('content_hash'):
        return None
    target = image_cache_path(ticket_key, path, row['version'])
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name('.{}.migrate-tmp'.format(target.name))
    tmp.write_bytes(data)
    os.replace(str(tmp), str(target))
    return str(target)


def classify_image(store, config, ticket, logical, sources, fetch_stored=False):
    """This image address's items: one per distinct local content (spec-images §8).

    classify's judgement, on raw bytes. A copy whose sha256 kartoteka holds, as
    its newest version (current) or an older live one (stale), is settled on
    its own. An image has no editing base, so a copy kartoteka does not hold
    uploads on its own only as a successor. That needs the one unknown copy,
    from the working tree, written after the newest stored version, over a
    history with no redaction: the 2026-09-22 §9.2 invariant, because a
    redacted version's bytes are gone and nothing shows this copy is not them.
    Anything else unknown is a conflict. A conflict carries no diff: it carries
    its sizes and hashes, and the stored newest version, which `migrate plan`
    (fetch_stored) also fetches into image fetch's cache so the user can look at
    both. An image kartoteka cannot address, a symbolic link, a path outside
    the trail, an oversized or unreadable file is skipped, never read."""
    identity = kh.image_identity(logical, config)
    ticket_key, path = identity if identity else (None, None)
    context_root = os.path.normpath(trail_roots(ticket, config)[1]) + os.sep
    items, groups = [], {}

    def new_item(copies, **fields):
        item = {'kind': 'image', 'ticket': ticket, 'logical': logical, 'name': path,
                'sources': copies, 'source': copies[0], 'sha256': None, 'byte_size': None,
                'class': None, 'reason': None, 'newest_version': None, 'base_version': None,
                'diff': None, 'stored': None}
        item.update(fields)
        items.append(item)
        return item

    for source in sources:
        if identity is None:
            new_item([source], **{'class': 'skipped', 'reason': OUTSIDE_THE_GRAMMAR})
            continue
        if _outside_the_trail(source, ticket, config):
            new_item([source], **{'class': 'skipped', 'reason': OUTSIDE_THE_TRAIL})
            continue
        try:
            size = os.stat(source).st_size
            if size > IMAGE_CAP_BYTES:
                new_item([source], **{'class': 'skipped', 'byte_size': size,
                                      'reason': OVER_THE_CAP})
                continue
            data = Path(source).read_bytes()
        except OSError as exc:
            new_item([source], **{'class': 'skipped', 'reason': 'unreadable: {}'.format(exc)})
            continue
        digest = sha256_bytes(data)
        if digest in groups:
            groups[digest]['sources'].append(source)
        else:
            groups[digest] = new_item([source], sha256=digest, byte_size=len(data))
    if identity is None:
        return items
    versions = store.image_listing(ticket_key, path)
    newest = versions[0] if versions else None
    for item in items:
        item['newest_version'] = newest['version'] if newest else None
    shown = []

    def stored():
        """The stored newest version as a conflict shows it, computed once per address."""
        if not shown:
            side = None
            if newest is not None:
                side = {'version': newest['version'], 'sha256': newest.get('content_hash'),
                        'byte_size': newest.get('byte_size'), 'redacted': _redacted(newest),
                        'cache_path': None}
                if fetch_stored and not side['redacted']:
                    side['cache_path'] = cache_stored_image(store, ticket_key, path, newest)
            shown.append(side)
        return shown[0]

    def conflict(item, reason):
        item.update({'class': 'conflict', 'reason': reason, 'stored': stored()})

    live = [v['content_hash'] for v in versions[1:] if not _redacted(v)]
    unknown = []
    for digest, item in groups.items():
        if newest is not None and not _redacted(newest) and digest == newest['content_hash']:
            item['class'] = 'current'
        elif digest in live:
            item.update({'class': 'stale',
                         'reason': 'kartoteka has moved on to v{}'.format(newest['version'])})
        else:
            unknown.append(item)
    skipped = [i for i in items if i['class'] == 'skipped']
    redactions = [v for v in versions if _redacted(v)]
    if len(unknown) == 1 and not skipped:
        item = unknown[0]
        working = next((s for s in item['sources']
                        if not os.path.normpath(s).startswith(context_root)), None)
        if newest is None:
            item['class'] = 'absent'
        elif _redacted(newest):
            conflict(item, 'the stored newest version (v{}) is redacted'.format(newest['version']))
        elif redactions:
            conflict(item, 'kartoteka redacted {} of this image; this copy may show the removed '
                           'content — view it before choosing'.format(
                               ', '.join('v{}'.format(v['version']) for v in redactions)))
        elif working is None:
            conflict(item, 'a saved context copy with new bytes; it may be older than what '
                           'kartoteka holds')
        elif _newer_than(working, newest):
            item.update({'class': 'successor',
                         'reason': "written after kartoteka's v{}".format(newest['version'])})
        elif _older_than(working, newest):
            conflict(item, "this copy is older than kartoteka's v{}".format(newest['version']))
        else:
            conflict(item, "nothing shows this copy is newer than kartoteka's v{}".format(
                newest['version']))
        return items
    if not unknown:
        return items
    differing = ', '.join(i['source'] for i in unknown)
    unread = ', '.join(i['source'] for i in skipped)
    if len(unknown) > 1:
        reason = 'the local copies differ: {}'.format(differing)
        if unread:
            reason += '; another copy was not read: {}'.format(unread)
    else:
        reason = 'another copy of this image was not read: {}'.format(unread)
    for item in unknown:
        conflict(item, reason)
    return items


def plan_items(store, config, tickets, pending_only, fetch_stored=False):
    items = []
    for ticket in tickets:
        grouped, decision, pending = candidates(ticket, config, pending_only)
        for logical in sorted(grouped):
            items.extend(classify(store, config, ticket, logical, grouped[logical], decision,
                                  pending, fetch_stored))
    return items


@migrating
def cmd_migrate_plan(args, config):
    """Only the plan fetches a conflict's stored image (fetch_stored): the user
    looks at it before answering. apply and delete never download bytes."""
    tickets = migration_tickets(args, config)
    items = plan_items(migration_store(config, tickets), config, tickets, args.pending_only,
                       fetch_stored=True)
    summary, image_summary = {}, {}
    for item in items:
        counts = image_summary if item.get('kind') == 'image' else summary
        counts[item['class']] = counts.get(item['class'], 0) + 1
    print(json.dumps({'tickets': tickets, 'summary': summary, 'image_summary': image_summary,
                      'items': items}))
    return OK


RESOLUTIONS = ('keep-local', 'keep-stored', 'skip')


def parse_resolutions(values):
    """--resolve <logical>=keep-local[:<source>][@<N>] | keep-stored | skip, repeatable.

    `@<N>` is the stored newest version the conflict was shown against -- the
    answer belongs to the version the user saw, and the upload carries it as
    `expected_version`, so a store that moved meanwhile is refused rather than
    overwritten. It is read off the end with rpartition and a digit check, so a
    source path keeps every `@` it contains."""
    resolved = {}
    for value in values or []:
        logical, eq, answer = value.partition('=')
        head, at, tail = answer.rpartition('@')
        seen = None
        if at and re.match(r'^[0-9]+$', tail):
            answer, seen = head, int(tail)
        action, _, source = answer.partition(':')
        if not eq or action not in RESOLUTIONS or (seen is not None and action != 'keep-local'):
            raise Failure('invalid_argument', 'bad --resolve {!r}: expected <path>={}, and '
                                              '@<version> only on keep-local'.format(
                                                  value, '|'.join(RESOLUTIONS)))
        resolved[os.path.normpath(logical)] = (action, source or None, seen)
    return resolved


def _resolution(resolutions, logical):
    """(action, source, version the user saw) for this address, each None when unset."""
    return resolutions.get(logical, (None, None, None))


def _holds(item, source):
    """Whether `source` names one of this item's local copies (paths compare normalised)."""
    return os.path.normpath(source) in {os.path.normpath(s) for s in item['sources']}


def _by_logical(items):
    grouped = {}
    for item in items:
        grouped.setdefault(item['logical'], []).append(item)
    return grouped


def _kind(entry):
    """An image entry's `kind` tag; a document entry keeps its v0.16.0 shape."""
    return {'kind': 'image'} if entry.get('kind') == 'image' else {}


def _upload_source(item, resolutions):
    """(source path, expected_version) to upload for this item, or None.

    A keep-local that names a copy uploads that copy and no other copy of the
    address: the user chose it. A keep-local that carries `@<N>` uploads against
    that version, not against whatever the store holds now."""
    action, source, seen = _resolution(resolutions, item['logical'])
    if action == 'keep-local' and source is not None and not _holds(item, source):
        return None
    if item['class'] == 'absent':
        return item['source'], 0
    if item['class'] == 'successor':
        return item['source'], item['newest_version']
    if item['class'] == 'conflict' and action == 'keep-local':
        return item['source'], seen if seen is not None else (item['newest_version'] or 0)
    return None


def _validate_resolutions(items, resolutions):
    """Raise before anything is uploaded or deleted when a resolution cannot be
    carried out safely against this plan -- apply and delete both run it:

    - keep-local naming a source that is not one of this address's own local
      copies: it must never read a file from outside the ticket's spec trail (an
      operator typo or a hostile --resolve value could otherwise upload anything
      readable as the ticket's document); nor one that was skipped;
    - a plain keep-local for an address whose local copies differ: it must say
      which copy to keep;
    - keep-stored for an address kartoteka holds no version of: there is no
      stored copy to keep, and discarding the local ones would lose the document.
    """
    by_logical = _by_logical(items)
    for logical, (action, source, _) in sorted(resolutions.items()):
        copies = by_logical.get(logical)
        if not copies:
            continue
        if action == 'keep-stored' and any(i['newest_version'] is None for i in copies):
            raise Failure('invalid_argument', 'keep-stored for {}: kartoteka holds no version '
                                              'of it'.format(logical))
        if action != 'keep-local':
            continue
        if source is None:
            differing = [i['source'] for i in copies if i['class'] == 'conflict']
            if len(differing) > 1:
                raise Failure('invalid_argument', (
                    'keep-local for {} must name the copy to keep -- its local copies differ: '
                    '{}').format(logical, ', '.join(differing)))
            continue
        holder = next((i for i in copies if _holds(i, source)), None)
        if holder is None:
            raise Failure('invalid_argument', (
                'keep-local source {} is not a local copy of {}; its copies are: {}').format(
                    source, logical, ', '.join(s for i in copies for s in i['sources'])))
        if holder['class'] == 'skipped':
            raise Failure('invalid_argument', 'keep-local source {} of {} was skipped: {}'.format(
                source, logical, holder['reason']))


def _settled(item, resolutions, by_logical):
    """Whether this local copy may go: kartoteka verifiably holds this content or
    something newer (current, stale); or the user chose the stored copy
    (keep-stored) of an address kartoteka holds; or the user chose another local
    copy of the address (keep-local:<source>) and kartoteka holds that copy as
    its newest version. A skipped copy is never settled."""
    if item['class'] in ('current', 'stale'):
        return True
    if item['class'] == 'skipped':
        return False
    action, source, _ = _resolution(resolutions, item['logical'])
    if action == 'keep-stored':
        return item['class'] == 'conflict' and item['newest_version'] is not None
    if action == 'keep-local' and source is not None:
        chosen = next((i for i in by_logical[item['logical']] if _holds(i, source)), None)
        return chosen is not None and chosen is not item and chosen['class'] == 'current'
    return False


def _moved_reason(resolutions, item, payload):
    """Why an upload refused with 409 failed: after a keep-local@<N>, the version
    the user saw; otherwise a write that landed during the run."""
    seen = _resolution(resolutions, item['logical'])[2]
    if seen is not None:
        return ('kartoteka moved from v{} to v{} since you decided; run migrate-specs '
                'again').format(seen, payload.get('current_version'))
    return 'kartoteka moved to v{} during the migration; run it again'.format(
        payload.get('current_version'))


def _put_image(store, config, item, source, expected, resolutions):
    """(uploaded entry, None) or (None, the reason it failed) for one image.

    The bytes are re-read and must still hash as classified. The upload
    carries expected_version: 0 for absent, the newest for a successor, the
    version the user saw for keep-local@<N>. It counts only once a fresh
    per-path listing shows those bytes as the newest live version."""
    ticket_key, path = image_address(item['logical'], config)
    data = Path(source).read_bytes()
    if sha256_bytes(data) != item['sha256']:
        return None, 'it changed since it was classified; run migrate-specs again'
    status, payload = store.image_put(ticket_key, path, data, expected_version=expected,
                                      author=MIGRATION_AUTHOR)
    if status == 409:
        return None, _moved_reason(resolutions, item, payload)
    if status != 200:
        return None, answered(status, payload)
    versions = store.image_listing(ticket_key, path)
    if versions and not _redacted(versions[0]) and versions[0]['content_hash'] == item['sha256']:
        return {'kind': 'image', 'logical': item['logical'],
                'version': versions[0]['version']}, None
    return None, 'the upload could not be verified; the local copy is kept'


def deletion_sets(items, resolutions):
    """(paths safe to delete, items to keep) for a plan fresh from disk and the store.

    Every local source of a settled copy goes -- the working tree and the
    context copy alike, when they hold the same content. Image entries carry
    `kind`; document entries keep their shape."""
    by_logical = _by_logical(items)
    deletable, kept = [], []
    for item in items:
        if _settled(item, resolutions, by_logical):
            deletable.extend(dict({'path': source, 'logical': item['logical'],
                                   'ticket': item['ticket'], 'sha256': item['sha256']},
                                  **_kind(item)) for source in item['sources'])
        else:
            kept.append(dict({'logical': item['logical'], 'sources': item['sources'],
                              'class': item['class'], 'reason': item['reason']}, **_kind(item)))
    return sorted(deletable, key=lambda e: e['path']), kept


def _fully_migrated(items, resolutions):
    """Whether a ticket's WHOLE local trail -- every copy of every document and
    image, regardless of this run's --pending-only -- is now safe to hand to
    kartoteka: every copy settled (_settled). Anything else left outstanding (an
    unresolved or skip-resolved conflict, a skipped file, or a copy this run
    never touched) means the ticket is not fully migrated yet. A ticket with no
    local candidates at all is not "fully migrated" either: there was nothing to
    move, and flipping a files decision on that basis would be a flip about
    nothing -- `--all` walks ticket directories that may hold only evidence.

    An image kartoteka cannot address is no part of the trail here: it can never
    move, so it neither holds the flip back nor counts as something moved."""
    by_logical = _by_logical(items)
    trail = [i for i in items if i['reason'] != OUTSIDE_THE_GRAMMAR]
    return bool(trail) and all(_settled(item, resolutions, by_logical) for item in trail)


def _flip_decisions(store, config, tickets, resolutions):
    """Flip to kartoteka only the tickets that are fully migrated; a ticket with
    anything still outstanding keeps its previous decision untouched. Returns the
    tickets actually flipped."""
    flipped = []
    for ticket in tickets:
        items = plan_items(store, config, [ticket], False)
        if not _fully_migrated(items, resolutions):
            continue
        previous = sd.load(ticket) or {}
        versions = {row['name']: row['version'] for row in store.listing(ticket)}
        decision = sd.new_decision('kartoteka', None, 'migrate-specs', versions,
                                   previous.get('pending') or [])
        base = _inherited_files_base(previous)
        # The frozen base outlives the flip while a copy kartoteka does not itself hold is
        # still on disk -- one the user called obsolete (keep-stored), or passed over for
        # another copy. Their classification rests on that base; without it the next run
        # could read such a copy as a successor and upload it over what the user chose.
        # `delete` drops it with the last local document copy (tidy_decisions). It is a
        # document base: an image has none, so an image copy never keeps it.
        if base and any(i['class'] not in ('current', 'stale') for i in items
                        if i.get('kind') != 'image'):
            decision['files_base'] = base
        sd.write(ticket, decision)
        flipped.append(ticket)
    return flipped


def _valid_pending(entry):
    return isinstance(entry, dict) and isinstance(entry.get('path'), str) and entry['path']


def _document_copies(ticket, config):
    """This ticket's local document copies: its trail without the images, which
    never rest on a files-era base."""
    return [p for p in sd.local_trail(ticket, config) if not kh.is_image_name(Path(p).name)]


def tidy_decisions(tickets, config):
    """Tidy each touched ticket's decision; return {ticket: pending entries left}.

    A pending entry is permission to hold one document on disk until kartoteka
    is back. Once its file is gone -- migrated and deleted, or never written at
    all -- the entry only keeps the guard (docs/spec-storage.md §6) open for
    that path forever. What is left is the count a resuming orchestrator reads
    to know whether the outage is drained.

    A ticket whose local document copies are gone also loses its frozen
    `files_base`: it is the base of document copies on disk, and there are none
    left to classify."""
    left = {}
    for ticket in tickets:
        decision = sd.load(ticket)
        pending = (decision or {}).get('pending')
        if decision is not None and decision.get('files_base') is not None \
                and not _document_copies(ticket, config):
            decision.pop('files_base')
            sd.write(ticket, decision)
        if decision is not None and isinstance(pending, list):
            # A hand-corrupted decision file may hold a non-list `pending` (e.g. 5), or
            # entries that are not {path: ...}: only entries with a path are prunable, and
            # everything else is left exactly as it was (candidates()'s own tolerance).
            kept = [p for p in pending if not (_valid_pending(p) and not os.path.lexists(p['path']))]
            if kept != pending:
                decision['pending'] = kept
                sd.write(ticket, decision)
            pending = kept
        left[ticket] = len([p for p in pending if _valid_pending(p)]) if isinstance(
            pending, list) else 0
    return left


@migrating
def cmd_migrate_apply(args, config):
    tickets = migration_tickets(args, config)
    store = migration_store(config, tickets)
    resolutions = parse_resolutions(args.resolve)
    items = plan_items(store, config, tickets, args.pending_only)
    _validate_resolutions(items, resolutions)
    uploaded, failed = [], []
    for item in items:
        chosen = _upload_source(item, resolutions)
        if chosen is None:
            continue
        source, expected = chosen

        def fail(reason):
            failed.append(dict({'logical': item['logical'], 'reason': reason}, **_kind(item)))

        if item.get('kind') == 'image':
            try:
                entry, reason = _put_image(store, config, item, source, expected, resolutions)
            except Failure as exc:
                # One image kartoteka refuses -- over its size cap, a type it will not
                # take -- is this item's failure, whatever kind the client names it. A
                # store-level failure is the run's, and exits 5 (migrating).
                if exc.kind in STORE_DOWN:
                    raise
                entry, reason = None, str(exc)
            except OSError as exc:
                entry, reason = None, 'unreadable: {}'.format(exc)
            if entry:
                uploaded.append(entry)
            else:
                fail(reason)
            continue
        ticket_key, stage, name = address(item['logical'], config)
        try:
            # read_bytes().decode, never read_text: read_text translates newlines, so a
            # CRLF document would go up as LF and be verified against the translated
            # text, while the plan hashed -- and the deletion re-checks -- the raw bytes.
            text = Path(source).read_bytes().decode('utf-8')
            if _sha(text) != item['sha256']:
                fail('it changed since it was classified; run migrate-specs again')
                continue
            status, payload = store.put(ticket_key, stage, name, text, expected, MIGRATION_AUTHOR)
            if status == 409:
                fail(_moved_reason(resolutions, item, payload))
                continue
            versions = store.versions(ticket_key, stage, name)
            if versions and not _redacted(versions[0]) \
                    and versions[0]['content_hash'] == item['sha256']:
                uploaded.append({'logical': item['logical'], 'version': versions[0]['version']})
            else:
                fail('the upload could not be verified; the local copy is kept')
        except Failure as exc:
            # One document kartoteka refuses -- too large for its limit, a body it will
            # not take -- is this item's failure, not the run's: every other document
            # still moves. A store-level failure is the run's, and exits 5 (migrating).
            if exc.kind not in ('rejected', 'too_large'):
                raise
            fail(str(exc))
        except (OSError, UnicodeDecodeError) as exc:
            fail('unreadable: {}'.format(exc))
    flipped = _flip_decisions(store, config, tickets, resolutions)
    pending_left = tidy_decisions(tickets, config)
    deletable, kept = deletion_sets(plan_items(store, config, tickets, args.pending_only),
                                    resolutions)
    print(json.dumps({'uploaded': uploaded, 'failed': failed,
                      'deletable': [entry['path'] for entry in deletable],
                      'kept': kept, 'flipped': flipped, 'pending_left': pending_left}))
    return OK


def _git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True)


def _tracked(path):
    # ':(literal)' stops git reading `path` as a glob -- otherwise an
    # untracked name with a glob character (`design/a*.png`) can match a
    # tracked sibling (`design/abc.png`) and be reported tracked itself.
    return _git('ls-files', '--error-unmatch', '--', ':(literal)' + path).returncode == 0


def _prune_empty_parents(path, config, ticket, keep_root=False):
    """Remove the directories this deletion emptied, up to and including the
    ticket's own trail root -- never above it, and never through a symbolic
    link. It walks the path as written, not as resolved: a worktree's
    .artel/context is a link to the main checkout's store, and resolving would
    walk out of this checkout entirely. `keep_root` stops below the root: the
    sweep empties image folders of a trail that is still in use."""
    root = _trail_root_of(path, ticket, config) if ticket else None
    if root is None:
        return
    parent = os.path.dirname(os.path.normpath(path))
    while True:
        if keep_root and parent == root:
            return
        if os.path.islink(parent):
            return
        try:
            os.rmdir(parent)  # only succeeds when empty
        except OSError:
            return
        if parent == root:
            return
        parent = os.path.dirname(parent)


def _still_verified(path, digest):
    """A reason this copy must not be deleted after all, or None.

    Re-read right before the deletion, closing the window between the check and
    the delete: the file kartoteka was proven to hold is the one that goes, not
    whatever has been written there since. It hashes raw bytes, so one check
    serves a document (whose plan hash is of its UTF-8 bytes) and an image
    alike (spec-images §8)."""
    if os.path.islink(path):
        return OUTSIDE_THE_TRAIL
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return 'unreadable: {}'.format(exc)
    if digest is None or hashlib.sha256(raw).hexdigest() != digest:
        return 'it changed since it was classified; run migrate-specs again'
    return None


def _unremovable(path):
    """A reason `git rm` must not take this tracked file, or None.

    git refuses a file whose index entry differs from BOTH HEAD and the working
    tree, and it is right to: that staged content exists nowhere else, and the
    verified copy is the working tree's. When the index matches either side, the
    staged content is committed or is the verified content itself, so -f is
    safe -- and needed, because an ordinary `git rm` refuses a file edited since
    the last commit even when kartoteka holds that very edit."""
    if _git('diff', '--cached', '--quiet', '--', path).returncode == 0:
        return None
    if _git('diff', '--quiet', '--', path).returncode == 0:
        return None
    return 'has staged changes kartoteka does not hold; commit or unstage them first'


def _commit_subject(tickets):
    if len(tickets) <= 3:
        return 'chore: move {} spec trail to kartoteka'.format(', '.join(tickets))
    return "chore: move {} tickets' spec trails to kartoteka".format(len(tickets))


@migrating
def cmd_migrate_delete(args, config):
    tickets = migration_tickets(args, config)
    store = migration_store(config, tickets)
    resolutions = parse_resolutions(args.resolve)
    items = plan_items(store, config, tickets, args.pending_only)
    _validate_resolutions(items, resolutions)
    deletable, kept = deletion_sets(items, resolutions)
    removed, committed_paths, committed_tickets = [], [], set()
    for entry in deletable:
        path, ticket = entry['path'], entry['ticket']

        def keep(reason):
            kept.append(dict({'logical': entry['logical'], 'source': path, 'class': 'error',
                              'reason': reason}, **_kind(entry)))

        reason = _still_verified(path, entry['sha256'])
        if reason:
            keep(reason)
            continue
        if _tracked(path):
            reason = _unremovable(path)
            if reason is None and _git('rm', '-q', '-f', '--', path).returncode != 0:
                reason = 'git rm failed'
            if reason:
                keep(reason)
                continue
            committed_paths.append(path)
            if ticket:
                committed_tickets.add(ticket)
        else:
            try:
                Path(path).unlink()
            except OSError as exc:
                keep(str(exc))
                continue
        removed.append(path)
        _prune_empty_parents(path, config, ticket)
    pending_left = tidy_decisions(tickets, config)
    commit = None
    if args.commit and committed_paths:
        subject = _commit_subject(sorted(committed_tickets))
        if _git('commit', '-q', '-m', subject, '--', *committed_paths).returncode == 0:
            commit = _git('rev-parse', '--short', 'HEAD').stdout.strip()
    print(json.dumps({'removed': removed, 'kept': kept, 'commit': commit,
                      'pending_left': pending_left}))
    return OK


# ---- Images: docs/spec-storage.md §4.6 ---------------------------------------

ATTACHMENTS_MISSING = 'the kartoteka daemon predates attachments (0.44.0); upgrade it'


def _json_object(raw):
    """The JSON object in an attachment route's non-byte answer, else None."""
    try:
        payload = json.loads(raw.decode('utf-8')) if raw else None
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def image_address(path, config):
    identity = kh.image_identity(path, config)
    if identity is None:
        raise Failure('not_an_image', (
            '{} is not a spec-trail image: a .png, .jpg, .jpeg, .gif or .webp file under '
            '<specs.dir>/<TICKET_ID>/, at most {} path segments of letters, digits, ".", "_" '
            'and "-" (docs/spec-storage.md §4.6)').format(path, kh.MAX_IMAGE_SEGMENTS))
    return identity


def attachment_route(ticket_key, path):
    return '/api/attachments/{}/{}'.format(
        kh.quote(ticket_key), '/'.join(kh.quote(segment) for segment in path.split('/')))


def cmd_image_put(args, config):
    ticket_key, path = image_address(args.path, config)
    source = args.file or args.path
    try:
        data = Path(source).read_bytes()
    except OSError as exc:
        raise Failure('unreadable', 'cannot read {}: {}'.format(source, exc.strerror or exc))
    status, payload = Store(config).image_put(ticket_key, path, data, args.expected_version,
                                              args.author)
    if status == 409:
        current = (payload or {}).get('current_version')
        print(json.dumps({'current_version': current}))
        raise Failure('conflict', 'kartoteka holds {} at version {}, not {}'.format(
            args.path, current, args.expected_version), CONFLICT)
    print(json.dumps(payload))
    return OK


def image_cache_path(ticket_key, path, version=None):
    """Where `image fetch` keeps a stored image for an agent to Read.

    Under .artel/run/, which /artel:setup gitignores: the cache is per worktree,
    disposable, and never read by migration. A named version gets its own
    `@v<N>/` folder -- `@` is outside the attachment path grammar, so it can
    never collide with a stored path."""
    root = h.ticket_run_dir(ticket_key) / 'images'
    if version is not None:
        root = root / '@v{}'.format(version)
    return root / path


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _write_atomically(target, data):
    """Write `data` to `target` so that a reader sees the old file or the new
    one, never half of either: a temporary file beside it, then os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix='.' + target.name + '.',
                                         suffix='.tmp')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, str(target))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _discard(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _shortcut_inside_trail(path, config):
    """Whether `path`, exactly as written, may be read by fetch's free local
    shortcut: under a ticket's trail, no segment spelled '.' or '..', and
    nothing along the way a symlink (spec §5 rule 1; the §11 rule that fetch
    never reads or prints outside a ticket's trail).

    kh._trail_address only matches the ticket-directory segment; a '..' below
    it walks back out and still matches, and Path() silently drops a literal
    '.' segment before _outside_the_trail ever sees the string, so both are
    refused here, by the raw path, before that reuse. _outside_the_trail is
    the one place a real path outside the real trail root, or a symlinked step
    to get there, is caught -- shared with migration and sync so this holds
    to the same containment they do.
    """
    if '.' in path.split('/') or '..' in path.split('/'):
        return False
    trail = kh._trail_address(path, config)
    return trail is not None and not _outside_the_trail(path, trail[0], config)


def cmd_image_fetch(args, config):
    """Print one local path to Read (docs/spec-storage.md §4.6).

    A file not yet swept is the newest copy there is, and costs no request --
    checked before the path is held to kartoteka's attachment grammar (spec
    §5 rule 1), because a name a screenshot tool chose (spaces and all) is
    still the newest copy of itself; the grammar only has to hold once a
    request is addressed. That check never extends outside the ticket's own
    trail, though: _shortcut_inside_trail holds it to the trail the same way
    migration and sync do, so the free read can never be walked out of it.
    Otherwise the cache: revalidated on every call, so a fetch never answers
    with bytes kartoteka no longer holds -- and never at all while kartoteka
    cannot be asked, because an unreachable store is a failing store call,
    not a reason to trust a copy of unknown age.
    """
    local = Path(args.path)
    if (args.version is None and local.is_file() and not local.is_symlink()
            and kh.is_image_name(local.name)
            and _shortcut_inside_trail(args.path, config)):
        print(os.path.abspath(args.path))
        return OK
    ticket_key, path = image_address(args.path, config)
    target = image_cache_path(ticket_key, path, args.version)
    store = Store(config)
    held = None
    if target.is_file() and not target.is_symlink():
        held = sha256_bytes(target.read_bytes())
    status, data, _ = store.image_get(ticket_key, path, args.version, held)
    if status == 304 and held is not None:
        print(os.path.abspath(str(target)))
        return OK
    if status == 200:
        _write_atomically(target, data)
        print(os.path.abspath(str(target)))
        return OK
    if status == 404:
        _discard(target)
        return ABSENT
    if status == 410:
        _discard(target)  # the bytes were removed for a reason; a cached copy keeps them
        raise Failure('redacted', '{}{} is redacted: kartoteka no longer holds its bytes'.format(
            args.path, '' if args.version is None else ' v{}'.format(args.version)))
    raise Failure('rejected', answered(status, _json_object(data)))


def cmd_image_list(args, config):
    ticket = sd.canonical_ticket(args.ticket, config)
    if ticket is None:
        raise Failure('invalid_argument', 'not a ticket id: {}'.format(args.ticket))
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    rows = Store(config).image_listing(ticket)
    print(json.dumps([{'path': r['path'], 'logical': str(Path(specs_dir) / ticket / r['path']),
                       'version': r['version'], 'content_type': r['content_type'],
                       'byte_size': r['byte_size'], 'content_hash': r['content_hash'],
                       'created_at': r['created_at']} for r in rows]))
    return OK


OUTSIDE_THE_GRAMMAR = ("outside kartoteka's image path grammar: 1 to {} segments of letters, "
                       "digits, '.', '_' or '-' below the ticket directory; rename it to move "
                       "it in").format(kh.MAX_IMAGE_SEGMENTS)
IMAGE_CAP_BYTES = 5242880  # kartoteka's default [workspace] max_attachment_bytes (spec-images §2.2)
OVER_THE_CAP = ("over kartoteka's default {}-byte attachment limit; not sent, and the file "
                "is kept").format(IMAGE_CAP_BYTES)


def _linked_images(root):
    """The symbolic links with an image name under root -- sd.image_files leaves
    them out. The sweep reports each as skipped and never reads one."""
    found = []
    for folder, _, names in os.walk(str(root)):
        found.extend(Path(folder) / name for name in names
                     if kh.is_image_name(name) and os.path.islink(os.path.join(folder, name)))
    return found


def sync_images(store, config, ticket, author):
    """Sweep this ticket's untracked images into kartoteka (docs/spec-storage.md §4.6).

    Each image under <specs.dir>/<TICKET_ID>/ is uploaded, verified and moved
    into the cache, where `image fetch` finds it. A file only leaves the trail
    once kartoteka verifiably holds the very bytes it had: the receipt's hash
    must be the hash of what was sent, and the file must still hash the same.
    Anything else stays where it is -- a failed sweep loses nothing and is
    retried at the next sweep point. A tracked image belongs to migration
    (`migrate-specs`), and a link, a path that resolves outside the trail or a
    file over kartoteka's default cap is reported as skipped and never read or
    sent. A per-file refusal is that file's `failed` entry; a store-level
    failure raises, and every file not yet swept stays.
    """
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    trail = Path(specs_dir) / ticket
    result = {'uploaded': [], 'unchanged': [], 'skipped': [], 'failed': [], 'tracked': []}
    for found in sorted(sd.image_files(trail) + _linked_images(trail)):
        source = str(found)

        def fail(reason):
            result['failed'].append({'path': source, 'reason': reason})

        if _outside_the_trail(source, ticket, config):
            result['skipped'].append({'path': source, 'reason': OUTSIDE_THE_TRAIL})
            continue
        if _tracked(source):
            result['tracked'].append(source)
            continue
        identity = kh.image_identity(source, config)
        if identity is None:
            result['skipped'].append({'path': source, 'reason': OUTSIDE_THE_GRAMMAR})
            continue
        ticket_key, path = identity
        try:
            if found.stat().st_size > IMAGE_CAP_BYTES:
                result['skipped'].append({'path': source, 'reason': OVER_THE_CAP})
                continue
            data = found.read_bytes()
        except OSError as exc:
            fail('unreadable: {}'.format(exc.strerror or exc))
            continue
        sent = sha256_bytes(data)
        try:
            status, receipt = store.image_put(ticket_key, path, data, author=author)
        except Failure as exc:
            if exc.kind != 'rejected':
                raise  # the store itself is down: stop, and leave the rest
            fail(str(exc))
            continue
        if status != 200 or (receipt or {}).get('content_hash') != sent:
            fail('kartoteka did not confirm the bytes that were sent; the file is kept')
            continue
        # Move into the cache first, then hash the file that landed there --
        # not the one still at `source`. Hashing before the move leaves a
        # window between that read and os.replace where a write to `source`
        # would move bytes kartoteka never verified into the cache; hashing
        # the moved file closes it, because nothing can write to `target`
        # under a name the sweep only just created.
        target = image_cache_path(ticket_key, path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, str(target))
        except OSError as exc:
            fail('stored, but not moved into the cache: {}'.format(exc.strerror or exc))
            continue
        try:
            still = sha256_bytes(target.read_bytes())
        except OSError:
            still = None
        if still != sent:
            try:
                os.replace(str(target), source)
            except OSError:
                # The move back failed (cross-device, permissions, a directory
                # in the way) -- a copy leaves the trail file in place even
                # when the cache entry cannot be removed from under it. Losing
                # the file here would break the documented guarantee that a
                # failed sweep loses nothing: raise instead of recording this
                # as merely `failed`, so the sweep stops and an operator sees
                # exactly where the bytes are before anything else moves.
                try:
                    shutil.copy2(str(target), source)
                except OSError as exc:
                    # Neither the move nor the copy landed the unverified
                    # bytes back in the trail: `target` is their only copy,
                    # and it doubles as image_cache_path's UNVERSIONED fetch
                    # cache -- the next `image fetch <logical>` or sweep of
                    # this path would overwrite it. Rename it aside first, in
                    # its own directory (a same-device os.replace, so no
                    # EXDEV), to a name no other verb reads or writes, and
                    # name THAT path in the failure. If even the rename fails,
                    # name the cache path as it stands.
                    stranded = str(target)
                    try:
                        aside = target.with_name(target.name + '.unverified')
                        os.replace(str(target), str(aside))
                        stranded = str(aside)
                    except OSError:
                        pass
                    raise Failure('unrecoverable', (
                        "{}'s upload could not be verified, and the cache copy at {} could not "
                        'be moved or copied back to its trail path at {}: {}; restore it by '
                        'hand').format(path, stranded, source, exc.strerror or exc))
                else:
                    # The copy DID land the file back in the trail: only the
                    # leftover cache copy under it could not be removed. That
                    # copy is harmless (the next fetch revalidates it against
                    # kartoteka), so this is an ordinary failed entry -- the
                    # file stays in the trail and is retried at the next
                    # sweep, not a raised `unrecoverable`.
                    try:
                        os.unlink(str(target))
                    except OSError:
                        pass
            fail('it changed while it was being stored; the file is kept for the next sweep')
            continue
        _prune_empty_parents(source, config, ticket, keep_root=True)
        result['unchanged' if receipt.get('unchanged') else 'uploaded'].append(source)
    return result


@migrating  # a store-level failure exits 5, as it does for the migrate verbs
def cmd_image_sync(args, config):
    ticket = sd.canonical_ticket(args.ticket, config)
    if ticket is None:
        raise Failure('invalid_argument', 'not a ticket id: {}'.format(args.ticket))
    print(json.dumps(sync_images(Store(config), config, ticket, args.author)))
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
    decide = verbs.add_parser('decide')
    decide.add_argument('ticket')
    decide.add_argument('--decided-by', required=True)
    mode = decide.add_mutually_exclusive_group()
    mode.add_argument('--local', action='store_true')
    mode.add_argument('--files', metavar='REASON')
    decide.set_defaults(run=cmd_decide)
    decision = verbs.add_parser('decision')
    decision.add_argument('ticket')
    decision.set_defaults(run=cmd_decision)
    pending = verbs.add_parser('pending')
    pending_verbs = pending.add_subparsers(dest='pending_verb', required=True)
    pending_add = pending_verbs.add_parser('add')
    pending_add.add_argument('path')
    pending_add.add_argument('--base-version', type=int, required=True)
    pending_add.set_defaults(run=cmd_pending_add)
    migrate = verbs.add_parser('migrate')
    migrate_verbs = migrate.add_subparsers(dest='migrate_verb', required=True)
    for name, run in (('plan', cmd_migrate_plan), ('apply', cmd_migrate_apply),
                      ('delete', cmd_migrate_delete)):
        sub = migrate_verbs.add_parser(name)
        sub.add_argument('ticket', nargs='*')
        sub.add_argument('--all', action='store_true')
        sub.add_argument('--pending-only', action='store_true')
        if name != 'plan':
            sub.add_argument('--resolve', action='append', default=[])
        if name == 'delete':
            sub.add_argument('--commit', action='store_true')
        sub.set_defaults(run=run)
    image = verbs.add_parser('image')
    image_verbs = image.add_subparsers(dest='image_verb', required=True)
    image_put = image_verbs.add_parser('put')
    image_put.add_argument('path')
    image_put.add_argument('--file')
    image_put.add_argument('--author')
    image_put.add_argument('--expected-version', type=int)
    image_put.set_defaults(run=cmd_image_put)
    image_fetch = image_verbs.add_parser('fetch')
    image_fetch.add_argument('path')
    image_fetch.add_argument('--version', type=int)
    image_fetch.set_defaults(run=cmd_image_fetch)
    image_list = image_verbs.add_parser('list')
    image_list.add_argument('ticket')
    image_list.set_defaults(run=cmd_image_list)
    image_sync = image_verbs.add_parser('sync')
    image_sync.add_argument('ticket')
    image_sync.add_argument('--author', required=True)
    image_sync.set_defaults(run=cmd_image_sync)
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
