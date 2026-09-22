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

Exit codes: 0 ok · 2 error (JSON envelope on stderr) · 3 absent ·
4 version conflict · 5 kartoteka unavailable (decide only).
Contract: docs/spec-storage.md
"""
import argparse
import difflib
import hashlib
import json
import os
import subprocess
import sys
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


def _emit(decision, ticket, config):
    out = dict(decision, ticket=ticket, written=True, local_trail=[])
    if decision['store'] == 'kartoteka':
        pending = {p.get('path') for p in decision.get('pending') or []}
        out['local_trail'] = [p for p in sd.local_trail(ticket, config) if p not in pending]
    print(json.dumps(out))
    return OK


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

    def files(reason):
        decision = sd.new_decision('files', reason, args.decided_by, **carried)
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
    versions = {row['name']: row['version'] for row in rows}
    decision = sd.new_decision('kartoteka', None, args.decided_by, versions, carried['pending'])
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
    """The store, proven able to take these trails, or Failure(unavailable, exit 5)."""
    store = Store(config)
    record = probe(store, tickets[0]) if tickets else None
    if record:
        raise Failure('unavailable', record, UNAVAILABLE)
    return store


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
    for source in sd.local_trail(ticket, config):
        logical = source
        if source.startswith(context_prefix):
            logical = str(Path(specs_dir) / ticket / source[len(context_prefix):])
        if pending_only and source not in pending:
            continue
        grouped.setdefault(logical, []).append(source)
    return grouped, decision, pending


def _known_base(source, name, decision, pending):
    """The stored version this copy was made from, when the trail says; else None.

    A pending save records it exactly. A files decision froze the ticket's
    versions when the run went local, so a document it lacks was new then (0).
    Nothing else records a base: a legacy trail from the mirror era has none.
    """
    if source in pending:
        return pending[source]
    if decision.get('store') == 'files' and decision.get('versions'):
        return decision['versions'].get(name, 0)
    return None


def classify(store, config, ticket, logical, sources, decision, pending):
    ticket_key, stage, name = address(logical, config)
    item = {'ticket': ticket, 'logical': logical, 'name': name, 'sources': sources,
            'source': None, 'sha256': None, 'class': None, 'reason': None,
            'newest_version': None, 'base_version': None, 'diff': None}
    texts = []
    for source in sources:
        try:
            raw = Path(source).read_bytes()
            text = raw.decode('utf-8')
        except (OSError, UnicodeDecodeError) as exc:
            item.update({'class': 'skipped', 'reason': 'unreadable: {}'.format(exc)})
            return item
        if len(raw) > kh.MAX_BYTES:
            item.update({'class': 'skipped',
                         'reason': 'over the {}-byte artifact limit'.format(kh.MAX_BYTES)})
            return item
        texts.append((source, text))
    distinct = {}
    for source, text in texts:
        distinct.setdefault(_sha(text), (source, text))
    versions = store.versions(ticket_key, stage, name)
    newest = versions[0] if versions else None
    item['newest_version'] = newest['version'] if newest else None
    if len(distinct) > 1:
        (a_source, a_text), (b_source, b_text) = list(distinct.values())[:2]
        item.update({'class': 'conflict',
                     'reason': 'the local copies differ: {} and {}'.format(a_source, b_source),
                     'diff': _diff(a_text, b_text, a_source, b_source)})
        return item
    digest, (source, text) = next(iter(distinct.items()))
    item.update({'sha256': digest, 'source': source})
    if newest is None:
        item['class'] = 'absent'
        return item

    def conflict(reason):
        stored = store.get(ticket_key, stage, name) or {}
        item.update({'class': 'conflict', 'reason': reason, 'diff': _diff(
            stored.get('content', ''), text, 'kartoteka v{}'.format(newest['version']), source)})
        return item

    if newest.get('redacted_at') is not None:
        return conflict('the stored newest version (v{}) is redacted'.format(newest['version']))
    if digest == newest['content_hash']:
        item['class'] = 'current'
        return item
    if digest in [v['content_hash'] for v in versions[1:]]:
        item.update({'class': 'stale',
                     'reason': 'kartoteka has moved on to v{}'.format(newest['version'])})
        return item
    base = _known_base(source, name, decision, pending)
    item['base_version'] = base
    if base is not None:
        if newest['version'] == base:
            item.update({'class': 'successor', 'reason': 'made from v{}'.format(base)})
            return item
        return conflict('kartoteka moved from v{} to v{} since this copy was made'.format(
            base, newest['version']))
    if all(v.get('author_agent') is None for v in versions):
        item.update({'class': 'successor',
                     'reason': 'kartoteka holds only mirror copies of this file, which can only lag'})
        return item
    authors = sorted({v['author_agent'] for v in versions if v.get('author_agent')})
    return conflict('kartoteka holds versions written there ({}) that this copy never saw'.format(
        ', '.join(authors)))


def plan_items(store, config, tickets, pending_only):
    items = []
    for ticket in tickets:
        grouped, decision, pending = candidates(ticket, config, pending_only)
        for logical in sorted(grouped):
            items.append(classify(store, config, ticket, logical, grouped[logical], decision,
                                  pending))
    return items


def cmd_migrate_plan(args, config):
    tickets = migration_tickets(args, config)
    items = plan_items(migration_store(config, tickets), config, tickets, args.pending_only)
    summary = {}
    for item in items:
        summary[item['class']] = summary.get(item['class'], 0) + 1
    print(json.dumps({'tickets': tickets, 'summary': summary, 'items': items}))
    return OK


RESOLUTIONS = ('keep-local', 'keep-stored', 'skip')


def parse_resolutions(values):
    """--resolve <logical>=keep-local[:<source>] | keep-stored | skip, repeatable."""
    resolved = {}
    for value in values or []:
        logical, eq, action = value.partition('=')
        action, _, source = action.partition(':')
        if not eq or action not in RESOLUTIONS:
            raise Failure('invalid_argument', 'bad --resolve {!r}: expected <path>={}'.format(
                value, '|'.join(RESOLUTIONS)))
        resolved[logical] = (action, source or None)
    return resolved


def _upload_source(item, resolutions):
    """(source path, expected_version) to upload for this item, or None."""
    if item['class'] == 'absent':
        return item['source'], 0
    if item['class'] == 'successor':
        return item['source'], item['newest_version']
    action, source = resolutions.get(item['logical'], (None, None))
    if item['class'] == 'conflict' and action == 'keep-local':
        return source or item['source'] or item['sources'][0], item['newest_version'] or 0
    return None


def deletion_sets(store, config, tickets, pending_only, resolutions):
    """(paths safe to delete, items to keep), recomputed from disk and the store.

    Safe means kartoteka verifiably holds this content or something newer
    (current, stale), or the user chose the stored copy (keep-stored). Every
    local source of such an address goes -- the working tree and the context
    copy alike."""
    deletable, kept = [], []
    for item in plan_items(store, config, tickets, pending_only):
        action = resolutions.get(item['logical'], (None, None))[0]
        if item['class'] in ('current', 'stale') or (
                item['class'] == 'conflict' and action == 'keep-stored'):
            deletable.extend(item['sources'])
        else:
            kept.append({'logical': item['logical'], 'class': item['class'],
                         'reason': item['reason']})
    return sorted(deletable), kept


def _flip_decisions(store, tickets):
    for ticket in tickets:
        previous = sd.load(ticket) or {}
        versions = {row['name']: row['version'] for row in store.listing(ticket)}
        sd.write(ticket, sd.new_decision('kartoteka', None, 'migrate-specs', versions,
                                         previous.get('pending') or []))


def cmd_migrate_apply(args, config):
    tickets = migration_tickets(args, config)
    store = migration_store(config, tickets)
    resolutions = parse_resolutions(args.resolve)
    uploaded, failed = [], []
    for item in plan_items(store, config, tickets, args.pending_only):
        chosen = _upload_source(item, resolutions)
        if chosen is None:
            continue
        source, expected = chosen
        ticket_key, stage, name = address(item['logical'], config)
        text = Path(source).read_text(encoding='utf-8')
        status, payload = store.put(ticket_key, stage, name, text, expected, MIGRATION_AUTHOR)
        if status == 409:
            failed.append({'logical': item['logical'], 'reason': (
                'kartoteka moved to v{} during the migration; run it again').format(
                    payload.get('current_version'))})
            continue
        versions = store.versions(ticket_key, stage, name)
        if versions and versions[0]['content_hash'] == _sha(text):
            uploaded.append({'logical': item['logical'], 'version': versions[0]['version']})
        else:
            failed.append({'logical': item['logical'],
                           'reason': 'the upload could not be verified; the local copy is kept'})
    _flip_decisions(store, tickets)
    deletable, kept = deletion_sets(store, config, tickets, args.pending_only, resolutions)
    print(json.dumps({'uploaded': uploaded, 'failed': failed, 'deletable': deletable,
                      'kept': kept}))
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
    for name, run in (('plan', cmd_migrate_plan), ('apply', cmd_migrate_apply)):
        sub = migrate_verbs.add_parser(name)
        sub.add_argument('ticket', nargs='*')
        sub.add_argument('--all', action='store_true')
        sub.add_argument('--pending-only', action='store_true')
        if name != 'plan':
            sub.add_argument('--resolve', action='append', default=[])
        sub.set_defaults(run=run)
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
