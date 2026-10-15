#!/usr/bin/env python3
"""parallel-state: the coordinator's durable run manifest and the repository-wide ticket lock.

    new_run(ticket, phase, run_id, seats, graph_digest) -> manifest
    read_manifest(path) -> manifest
    write_manifest(path, value, expected_revision) -> manifest
    transition_task(manifest, task, expected, target, fields) -> manifest
    acquire_ticket(repo, ticket, owner) -> stored owner
    release_ticket(repo, ticket, owner) -> receipt

Contract: docs/opencode-parallel.md §6 (task states and transitions) and §13 (the run
manifest, ticket ownership and claim recovery).

The run manifest is `schema: 1`, JSON-safe, and carries:

    schema, ticket, phase, run_id, seats, graph_digest, revision, tasks, claims, stop

The coordinator keeps one manifest per run under
`.artel/run/<TICKET_ID>/parallel/<run-id>/manifest.json` -- host-owned state under `.artel`,
never in the plugin install or cache.

`revision` starts at 0 and is the write guard: the reducers return a manifest whose
`revision` is unchanged, and `write_manifest` is the one place that stamps the next
revision. It refuses a value or a file that is not at exactly `expected_revision` (0 when
nothing is persisted yet) and returns the manifest it wrote. A reader sees the previous
manifest or the next one, never a partial write: the bytes go to a temporary sibling and
then through `os.replace`.

`tasks` maps a `<iteration>.<n>` id to `{state, attempt, decision}`. `state` is the §6
vocabulary; `attempt` starts at 1 and grows by one on the retry transition (a side state
back to `reserved`, which records the decision that produced it); `decision` is the last
recorded retry/recovery decision. `claims` and `stop` are the queue's sections -- the
`parallel_queue` helpers own their contents, and this module validates their structure.

Ticket ownership is one JSON record per ticket in the repository-common Git directory,
`<git-common-dir>/artel/parallel/<TICKET>.json`, so every linked worktree of the repository
resolves the same lock. `acquire_ticket` creates it exclusively and refuses a lock that
already exists, whatever its age; only the owning run (same `run_id` and `phase`)
re-acquires its own record, and only the matching run releases it -- with the `token` proof
it was given and with `completed` and `quiescent` asserted. A lock that cannot be read is
ambiguous and is never overwritten, released or reclaimed.

Every function raises `StateError`, a `ValueError` carrying the `kind` a CLI maps into its
error envelope; nothing is coerced and nothing is dropped.
"""
import json
import os
import secrets
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import parallel_contract

SCHEMA = 1

MANIFEST_KEYS = ('schema', 'ticket', 'phase', 'run_id', 'seats', 'graph_digest', 'revision',
                 'tasks', 'claims', 'stop')
TASK_KEYS = ('state', 'attempt', 'decision')
CLAIMS_KEYS = ('seq', 'intents')
INTENT_KEYS = ('intent_id', 'seq', 'attempt', 'actor', 'state', 'row')
# A claim intent's lifecycle: `pending` is durable before the claim call, `mapped` carries
# the recorded returned row, `wrong-phase` and `foreign` are the stopped observations,
# `orphaned` is this run's reconciled claim, and `lost` is a call whose outcome never landed.
INTENT_STATES = ('pending', 'mapped', 'wrong-phase', 'foreign', 'orphaned', 'lost')
STOP_KEYS = ('reason', 'intent_id', 'row')
OWNER_KEYS = ('schema', 'ticket', 'run_id', 'phase')
STORED_OWNER_KEYS = OWNER_KEYS + ('token', 'acquired_at')
RELEASE_KEYS = OWNER_KEYS + ('token', 'completed', 'quiescent')
ALL_STATES = parallel_contract.TASK_STATES + parallel_contract.SIDE_STATES

# §6: the main sequence, the one return a preparation failure takes, the side entries, and
# the retry that leaves a side state. A retry keeps the task identity and increments attempt.
TRANSITIONS = {
    'reserved': ('preparing', 'blocked', 'cancelled'),
    'preparing': ('running', 'reserved', 'blocked', 'failed', 'cancelled'),
    'running': ('verifying/reviewing', 'blocked', 'failed', 'cancelled'),
    'verifying/reviewing': ('prepared', 'blocked', 'failed', 'cancelled'),
    'prepared': ('integrating', 'blocked', 'cancelled'),
    'integrating': ('published', 'blocked', 'failed', 'cancelled'),
    'published': (),
    'blocked': ('reserved',),
    'failed': ('reserved',),
    'cancelled': ('reserved',),
}


class StateError(ValueError):
    """A run-state or ownership violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise StateError(kind, message)


def _object(value, what, kind):
    if not isinstance(value, dict):
        _fail(kind, '{} is not an object: {!r}'.format(what, value))
    return value


def _keys(value, required, what, kind):
    """Every required key present, and no key outside the shape."""
    missing = [name for name in required if name not in value]
    if missing:
        _fail(kind, '{} is missing {}'.format(
            what, ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in value if name not in required)
    if unknown:
        _fail(kind, '{} carries unknown {}'.format(
            what, ', '.join('`{}`'.format(name) for name in unknown)))


def _text(value, what, kind):
    if not isinstance(value, str):
        _fail(kind, '{} is not a string: {!r}'.format(what, value))
    if not value:
        _fail(kind, '{} is empty'.format(what))
    if value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _integer(value, what, kind, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(kind, '{} is not an integer: {!r}'.format(what, value))
    if value < minimum:
        _fail(kind, '{} must be at least {}: {!r}'.format(what, minimum, value))
    return value


def _canonical_uuid(value, kind, what='the run_id'):
    if not isinstance(value, str):
        _fail(kind, '{} is not a UUID: {!r}'.format(what, value))
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _fail(kind, '{} is not a UUID: {!r}'.format(what, value))
    if str(parsed) != value:
        _fail(kind, '{} is not a canonical UUID: {!r}'.format(what, value))
    return value


def _ticket(value):
    if not isinstance(value, str) or not parallel_contract.TICKET_RE.match(value):
        _fail('invalid_ticket',
              'the ticket is not a canonical <PROJECT_KEY>-<number>: {!r}'.format(value))
    return value


def _dump(value):
    """The manifest's on-disk form: pretty, key-sorted JSON, or `invalid_manifest`."""
    try:
        return json.dumps(value, indent=2, sort_keys=True) + '\n'
    except (TypeError, ValueError) as exc:
        _fail('invalid_manifest', 'the manifest is not JSON-safe: {}'.format(exc))


def _task_record(value, what, kind):
    _object(value, what, kind)
    _keys(value, TASK_KEYS, what, kind)
    if value['state'] not in ALL_STATES:
        _fail(kind, '{} state is not one of {}: {!r}'.format(
            what, ', '.join(ALL_STATES), value['state']))
    _integer(value['attempt'], '{} attempt'.format(what), kind, 1)
    if value['decision'] is not None:
        _object(value['decision'], '{} decision'.format(what), kind)
    return value


def _intent_record(value, what, kind):
    _object(value, what, kind)
    _keys(value, INTENT_KEYS, what, kind)
    _text(value['intent_id'], '{} intent_id'.format(what), kind)
    _integer(value['seq'], '{} seq'.format(what), kind, 1)
    _integer(value['attempt'], '{} attempt'.format(what), kind, 1)
    _text(value['actor'], '{} actor'.format(what), kind)
    if value['state'] not in INTENT_STATES:
        _fail(kind, '{} state is not one of {}: {!r}'.format(
            what, ', '.join(INTENT_STATES), value['state']))
    if value['row'] is not None:
        _object(value['row'], '{} row'.format(what), kind)
    return value


def _stop_record(value, what, kind):
    _object(value, what, kind)
    _keys(value, STOP_KEYS, what, kind)
    _text(value['reason'], '{} reason'.format(what), kind)
    if value['intent_id'] is not None:
        _text(value['intent_id'], '{} intent_id'.format(what), kind)
    if value['row'] is not None:
        _object(value['row'], '{} row'.format(what), kind)
    return value


def _manifest(value, kind='invalid_manifest'):
    """The schema-1 manifest shape, or `kind`."""
    _object(value, 'the manifest', kind)
    _keys(value, MANIFEST_KEYS, 'the manifest', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the manifest schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    if not isinstance(value['ticket'], str) or not parallel_contract.TICKET_RE.match(
            value['ticket']):
        _fail(kind, 'the manifest ticket is not a canonical <PROJECT_KEY>-<number>: {!r}'.format(
            value['ticket']))
    if value['phase'] is not None:
        _integer(value['phase'], 'the manifest phase', kind, 1)
    _canonical_uuid(value['run_id'], kind)
    _integer(value['seats'], 'the manifest seats', kind, 1)
    _text(value['graph_digest'], 'the manifest graph_digest', kind)
    _integer(value['revision'], 'the manifest revision', kind, 0)
    tasks = _object(value['tasks'], 'the manifest tasks', kind)
    for task, record in tasks.items():
        if not isinstance(task, str) or not parallel_contract.TASK_RE.match(task):
            _fail(kind, 'the manifest task id is not an `<iteration>.<n>` number: {!r}'.format(
                task))
        _task_record(record, 'the task {}'.format(task), kind)
    claims = _object(value['claims'], 'the manifest claims', kind)
    _keys(claims, CLAIMS_KEYS, 'the manifest claims', kind)
    _integer(claims['seq'], 'the manifest claims seq', kind, 0)
    if not isinstance(claims['intents'], list):
        _fail(kind, 'the manifest claims intents is not an array: {!r}'.format(
            claims['intents']))
    for index, intent in enumerate(claims['intents'], 1):
        _intent_record(intent, 'claims intent {}'.format(index), kind)
    if value['stop'] is not None:
        _stop_record(value['stop'], 'the manifest stop', kind)
    return value


def new_run(ticket, phase, run_id, seats, graph_digest):
    """A fresh run manifest at revision 0: the ticket, the optional phase, the run's
    transaction id, the seat count and the digest of the pinned task graph.

    `phase` is an integer or null (a ticket-wide run); `seats` is a positive integer (a
    boolean is not an integer); `graph_digest` is an opaque non-empty single-line string.
    """
    kind = 'invalid_run'
    if not isinstance(ticket, str) or not parallel_contract.TICKET_RE.match(ticket):
        _fail(kind, 'the run ticket is not a canonical <PROJECT_KEY>-<number>: {!r}'.format(
            ticket))
    if phase is not None:
        _integer(phase, 'the run phase', kind, 1)
    _canonical_uuid(run_id, kind)
    _integer(seats, 'the run seats', kind, 1)
    _text(graph_digest, 'the run graph_digest', kind)
    return {
        'schema': SCHEMA,
        'ticket': ticket,
        'phase': phase,
        'run_id': run_id,
        'seats': seats,
        'graph_digest': graph_digest,
        'revision': 0,
        'tasks': {},
        'claims': {'seq': 0, 'intents': []},
        'stop': None,
    }


def read_manifest(path):
    """The validated manifest at `path`; `missing_manifest` when there is none and
    `corrupt_manifest` when the file is unreadable or not valid JSON."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail('missing_manifest', 'no manifest at {}'.format(path))
    except UnicodeDecodeError as exc:
        _fail('corrupt_manifest', '{} is not UTF-8 text: {}'.format(path, exc))
    except OSError as exc:
        _fail('corrupt_manifest', 'cannot read {}: {}'.format(path, exc))
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail('corrupt_manifest', '{} is not valid JSON: {}'.format(path, exc))
    return _manifest(value)


def _persisted_revision(path):
    """The revision on disk, 0 when nothing is persisted; a corrupt file is refused."""
    if not path.exists():
        return 0
    return read_manifest(path)['revision']


def write_manifest(path, value, expected_revision):
    """Persist `value` when the manifest is at exactly `expected_revision`, atomically.

    `expected_revision` is the revision the caller last read or wrote (0 when nothing is
    persisted); `value` must carry that same revision. The written manifest's revision is
    `expected_revision + 1`, and it is returned. A file that is not at `expected_revision`,
    a stale value, and a corrupt file on disk are all refused -- never silently overwritten.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
            or expected_revision < 0:
        _fail('invalid_revision',
              'expected_revision is not an integer >= 0: {!r}'.format(expected_revision))
    manifest = _manifest(value)
    path = Path(path)
    current = _persisted_revision(path)
    if current != expected_revision:
        _fail('stale_revision', 'the manifest at {} is at revision {}, not {}'.format(
            path, current, expected_revision))
    if manifest['revision'] != expected_revision:
        _fail('stale_revision', 'the value is at revision {}, not the expected {}'.format(
            manifest['revision'], expected_revision))
    written = dict(manifest, revision=expected_revision + 1)
    _atomic_write(path, _dump(written))
    return written


def _atomic_write(path, text):
    """Write `text` through a temporary sibling, then `os.replace`, cleaning up on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name + '.',
                                         suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _fields(value):
    """The transition's optional fields: a `decision` object when given, nothing else."""
    kind = 'invalid_fields'
    _object(value, 'fields', kind)
    unknown = sorted(name for name in value if name != 'decision')
    if unknown:
        _fail(kind, 'fields carries unknown {}'.format(
            ', '.join('`{}`'.format(name) for name in unknown)))
    decision = value.get('decision')
    if decision is not None:
        _object(decision, 'the decision', kind)
        if not decision:
            _fail(kind, 'the decision is empty')
    return decision


def transition_task(manifest, task, expected, target, fields):
    """One task's next state, as a new manifest (the input is never mutated).

    `expected` is the state the caller believes the task is in, or `None` for a task that
    does not exist yet -- only `reserved` admits it. `target` must be a legal transition
    (docs/opencode-parallel.md §6): the main sequence, a side entry, `preparing` back to
    `reserved`, or a side state back to `reserved`, which is the retry -- it increments
    `attempt`. A side entry, the preparation-failure return and a retry must carry
    `fields['decision']`, the retry or recovery decision that produced the transition; the
    decision stays on the record until another one replaces it. The returned manifest's
    `revision` is unchanged.
    """
    value = _manifest(manifest)
    if not isinstance(task, str) or not parallel_contract.TASK_RE.match(task):
        _fail('invalid_task', 'the task is not an `<iteration>.<n>` number: {!r}'.format(task))
    decision = _fields(fields)
    tasks = dict(value['tasks'])
    current = tasks.get(task)
    if expected is None:
        if current is not None:
            _fail('state_mismatch', 'the task {} already exists at state {}'.format(
                task, current['state']))
        if target not in ALL_STATES:
            _fail('unknown_state', 'the target is not a task state: {!r}'.format(target))
        if target != 'reserved':
            _fail('illegal_transition', 'a new task is admitted as `reserved`, not {!r}'.format(
                target))
        record = {'state': 'reserved', 'attempt': 1, 'decision': decision}
    else:
        if expected not in ALL_STATES:
            _fail('unknown_state', 'the expected state is not a task state: {!r}'.format(
                expected))
        if target not in ALL_STATES:
            _fail('unknown_state', 'the target is not a task state: {!r}'.format(target))
        if current is None:
            _fail('state_mismatch', 'the task {} is not in the manifest'.format(task))
        if current['state'] != expected:
            _fail('state_mismatch', 'the task {} is at state {}, not {}'.format(
                task, current['state'], expected))
        if target not in TRANSITIONS[expected]:
            _fail('illegal_transition', 'a task cannot move from {} to {}'.format(
                expected, target))
        retry = expected in parallel_contract.SIDE_STATES
        recovery = expected == 'preparing' and target == 'reserved'
        if (target in parallel_contract.SIDE_STATES or retry or recovery) and decision is None:
            kind = 'retry' if retry else 'recovery' if recovery else 'side'
            _fail('invalid_fields',
                  'a {} transition records the decision that produced it'.format(kind))
        record = {'state': target,
                  'attempt': current['attempt'] + (1 if retry else 0),
                  'decision': decision if decision is not None else current['decision']}
    tasks[task] = record
    return dict(value, tasks=tasks)


# --- repository-wide ticket ownership -----------------------------------------------------

def _common_dir(repo):
    """The repository-common Git directory of `repo` (shared by every linked worktree)."""
    try:
        proc = subprocess.run(['git', 'rev-parse', '--path-format=absolute',
                               '--git-common-dir'],
                              cwd=str(repo), capture_output=True, text=True)
    except OSError as exc:
        _fail('not_a_repository', 'cannot run git in {}: {}'.format(repo, exc))
    if proc.returncode != 0:
        _fail('not_a_repository', 'git rev-parse --git-common-dir failed in {}: {}'.format(
            repo, (proc.stderr or proc.stdout).strip()))
    return Path(proc.stdout.strip())


def _lock_path(repo, ticket):
    return _common_dir(repo) / 'artel' / 'parallel' / (ticket + '.json')


def _read_lock(path, ticket):
    """The stored owner record, `None` when there is no lock, or `ambiguous_owner`."""
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        _fail('ambiguous_owner', 'the ticket lock at {} cannot be read: {}'.format(path, exc))
    kind = 'ambiguous_owner'
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail(kind, 'the ticket lock at {} is not valid JSON: {}'.format(path, exc))
    _object(value, 'the ticket lock', kind)
    _keys(value, STORED_OWNER_KEYS, 'the ticket lock', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the ticket lock schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    if value['ticket'] != ticket:
        _fail(kind, 'the ticket lock at {} is not a lock for {}'.format(path, ticket))
    _canonical_uuid(value['run_id'], kind, 'the ticket lock run_id')
    if value['phase'] is not None:
        _integer(value['phase'], 'the ticket lock phase', kind, 1)
    _text(value['token'], 'the ticket lock token', kind)
    _text(value['acquired_at'], 'the ticket lock acquired_at', kind)
    return value


def _owner(value, keys, what):
    """The owner identity request (`acquire`) or the release request."""
    kind = 'invalid_owner'
    _object(value, what, kind)
    _keys(value, keys, what, kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, '{} schema must be {}: {!r}'.format(what, SCHEMA, value['schema']))
    if not isinstance(value['ticket'], str) or not parallel_contract.TICKET_RE.match(
            value['ticket']):
        _fail(kind, '{} ticket is not a canonical <PROJECT_KEY>-<number>: {!r}'.format(
            what, value['ticket']))
    _canonical_uuid(value['run_id'], kind, '{} run_id'.format(what))
    if value['phase'] is not None:
        _integer(value['phase'], '{} phase'.format(what), kind, 1)
    return value


def _utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _held(stored, owner):
    """The stored record when `owner` is the run that already holds the ticket."""
    if stored['run_id'] == owner['run_id'] and stored['phase'] == owner['phase']:
        return stored
    _fail('ticket_owned', 'the ticket {} is owned by run {} (phase {}), acquired {}'.format(
        stored['ticket'], stored['run_id'], stored['phase'], stored['acquired_at']))


def acquire_ticket(repo, ticket, owner):
    """Take the repository-wide ticket lock before arming a run, or refuse.

    The lock is `<git-common-dir>/artel/parallel/<TICKET>.json`, so a linked worktree
    cannot bypass it. An existing lock is never reclaimed -- not for another run, not for
    another phase of the same run, and never because time passed; the run that already
    holds it (same `run_id` and `phase`) re-acquires its own record. A lock that cannot be
    read is `ambiguous_owner` and is left exactly as found. The returned record is the
    run's ownership proof: `token` is the release proof and `acquired_at` its record.
    """
    _ticket(ticket)
    _owner(owner, OWNER_KEYS, 'the owner request')
    if owner['ticket'] != ticket:
        _fail('invalid_owner', 'the owner request names {} but {} was asked for'.format(
            owner['ticket'], ticket))
    path = _lock_path(repo, ticket)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = _read_lock(path, ticket)
    if stored is not None:
        return _held(stored, owner)
    record = {'schema': SCHEMA, 'ticket': ticket, 'run_id': owner['run_id'],
              'phase': owner['phase'], 'token': secrets.token_hex(16),
              'acquired_at': _utc_now()}
    try:
        handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        stored = _read_lock(path, ticket)
        if stored is None:
            _fail('ticket_owned', 'the ticket {} was taken while acquiring; retry'.format(
                ticket))
        return _held(stored, owner)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(_dump(record))
    except BaseException:
        try:
            os.unlink(str(path))
        except OSError:
            pass
        raise
    return record


def release_ticket(repo, ticket, owner):
    """Release the ticket lock for the matching completed, quiescent run, or refuse.

    The request must carry the owning run's `run_id`, `phase` and `token` proof, and must
    assert `completed` and `quiescent`: a pause, an unfinished run and a foreign run all
    keep the lock. Nothing is inferred from age, and an unreadable lock is never removed.
    """
    _ticket(ticket)
    _owner(owner, RELEASE_KEYS, 'the release request')
    _text(owner['token'], 'the release request token', 'invalid_owner')
    if not isinstance(owner['completed'], bool) or not isinstance(owner['quiescent'], bool):
        _fail('invalid_owner', 'the release request completed and quiescent are not booleans')
    if owner['ticket'] != ticket:
        _fail('invalid_owner', 'the release request names {} but {} was asked for'.format(
            owner['ticket'], ticket))
    path = _lock_path(repo, ticket)
    stored = _read_lock(path, ticket)
    if stored is None:
        _fail('not_owned', 'the ticket {} is not owned'.format(ticket))
    if stored['run_id'] != owner['run_id'] or stored['phase'] != owner['phase'] \
            or stored['token'] != owner['token']:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}); only the run '
                               'that holds it may release it'.format(
                                   ticket, stored['run_id'], stored['phase']))
    if not (owner['completed'] and owner['quiescent']):
        _fail('premature_release', 'run {} is not a completed quiescent run; ownership and '
                                   'recovery state are retained'.format(owner['run_id']))
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        _fail('not_owned', 'the ticket {} was released while releasing'.format(ticket))
    return {'schema': SCHEMA, 'ticket': ticket, 'run_id': owner['run_id'],
            'phase': owner['phase'], 'released': True}
