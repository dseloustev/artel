#!/usr/bin/env python3
"""parallel-state: the coordinator's durable run manifest and the repository-wide ticket lock.

    new_run(ticket, phase, run_id, seats, graph_digest) -> manifest
    read_manifest(path) -> manifest
    write_manifest(path, value, expected_revision) -> manifest
    transition_task(manifest, task, expected, target, fields) -> manifest
    ticket_guard(repo, ticket) -> context manager (the repository-common guard)
    validate_owner(repo, ticket, run_id, phase, coordinator_id) -> stored owner
    acquire_ticket(repo, ticket, owner) -> stored owner
    recover_ticket(repo, ticket, request) -> stored owner (explicit audited handoff)
    release_ticket(repo, ticket, owner) -> receipt
    release_ticket_locked(repo, ticket, owner) -> receipt (guard already held)

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
already exists, whatever its age; only the owning run *and coordinator* (same `run_id`,
`phase` and `coordinator_id`) re-acquires its own record, and only the matching run releases
it -- with the `token` proof it was given and with `completed` and `quiescent` asserted. A
lock that cannot be read is ambiguous and is never overwritten, released or reclaimed; a
legacy record without `coordinator_id` is never silently adopted (`legacy_owner`), only
explicitly by `recover_ticket`, which rotates the token and records the prior identity and
reason. Every mutation runs under `ticket_guard`, the kernel lock beside the record, and
validates the owner inside it; the manifest write additionally serializes its
compare-and-replace under a per-manifest lock. The guard is held through the effect, never
just for an earlier read.

Every function raises `StateError`, a `ValueError` carrying the `kind` a CLI maps into its
error envelope; nothing is coerced and nothing is dropped.
"""
import contextlib
import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import parallel_contract

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX hosts have fcntl; the fallback stays safe
    fcntl = None

SCHEMA = 1

MANIFEST_KEYS = ('schema', 'ticket', 'phase', 'run_id', 'seats', 'graph_digest', 'revision',
                 'tasks', 'claims', 'stop')
TASK_KEYS = ('state', 'attempt', 'decision')
CLAIMS_KEYS = ('seq', 'intents')
INTENT_KEYS = ('intent_id', 'seq', 'attempt', 'actor', 'state', 'row')
# A claim intent's lifecycle: `pending` is durable before the claim call, `mapped` carries
# the recorded returned row, `wrong-phase` and `foreign` are the stopped observations,
# `orphaned` is this run's reconciled claim whose release is not confirmed yet, `released`
# is an orphan whose external release a fresh complete observation confirmed (the exact
# recorded row is no longer held by the exact actor), and `lost` is a call whose outcome
# never landed.
INTENT_STATES = ('pending', 'mapped', 'wrong-phase', 'foreign', 'orphaned', 'released',
                 'lost')
STOP_KEYS = ('reason', 'intent_id', 'row')
# §13.4: the owner identity the coordinator fence compares. `coordinator_id` is the native
# session id of the coordinator that holds the ticket -- never a run UUID, a token or a CLI
# process id. The stored record also carries the audit trail of an explicit recovery.
COORDINATOR_KEYS = ('schema', 'ticket', 'run_id', 'phase', 'coordinator_id')
OWNER_KEYS = COORDINATOR_KEYS
LEGACY_OWNER_KEYS = ('schema', 'ticket', 'run_id', 'phase', 'token', 'acquired_at')
STORED_OWNER_KEYS = COORDINATOR_KEYS + ('token', 'acquired_at', 'recovered_at',
                                        'prior_coordinator_id', 'recovery_reason')
RELEASE_KEYS = COORDINATOR_KEYS + ('token', 'completed', 'quiescent')
RECOVER_KEYS = COORDINATOR_KEYS + ('reason',)
GUARD_TIMEOUT = 5.0
GUARD_POLL = 0.05
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

    The comparison and the replacement are serialized under a kernel lock on
    `<manifest>.lock` (never unlinked), so two concurrent callers -- the CLI and a direct
    trusted helper caller alike -- cannot both pass the revision check and lose one update.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
            or expected_revision < 0:
        _fail('invalid_revision',
              'expected_revision is not an integer >= 0: {!r}'.format(expected_revision))
    manifest = _manifest(value)
    path = Path(path)
    written = dict(manifest, revision=expected_revision + 1)
    with _file_guard(Path(str(path) + '.lock'), 'the manifest'):
        current = _persisted_revision(path)
        if current != expected_revision:
            _fail('stale_revision', 'the manifest at {} is at revision {}, not {}'.format(
                path, current, expected_revision))
        if manifest['revision'] != expected_revision:
            _fail('stale_revision', 'the value is at revision {}, not the expected {}'.format(
                manifest['revision'], expected_revision))
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


def _guard_path(repo, ticket):
    return _common_dir(repo) / 'artel' / 'parallel' / (ticket + '.guard')


@contextlib.contextmanager
def _file_guard(path, what):
    """A bounded kernel lock on `path`, never unlinked while held.

    POSIX hosts take an `flock` on the guard file itself, which the kernel releases when the
    process exits -- a crashed holder leaves no stale lock and the file is never unlinked
    (unlinking would let a later lock bind to a different inode). Without `fcntl` the
    fallback claims `<path>.claim` by exclusive creation, removes only its own claim, and
    refuses a busy one after the same bounded wait; the strategy is chosen before the claim
    file exists, so first use is never blocked by the lock's own file.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail('guard_unavailable', 'cannot create the {} lock directory {}: {}'.format(
            what, path.parent, exc))
    deadline = time.monotonic() + GUARD_TIMEOUT
    if fcntl is not None:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError as exc:
            _fail('guard_unavailable', 'cannot open the {} lock {}: {}'.format(
                what, path, exc))
        try:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        _fail('owner_busy', 'another operation holds the {} lock {}; '
                                            'retry'.format(what, path))
                    time.sleep(GUARD_POLL)
            yield
        finally:
            os.close(handle)
        return
    claim = Path(str(path) + '.claim')  # pragma: no cover - non-POSIX fallback
    handle = None
    while True:
        try:
            handle = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                _fail('owner_busy', 'another operation holds the {} lock {}; retry'.format(
                    what, claim))
            time.sleep(GUARD_POLL)
        except OSError as exc:
            _fail('guard_unavailable', 'cannot claim the {} lock {}: {}'.format(
                what, claim, exc))
    try:
        yield
    finally:
        os.close(handle)
        try:
            os.unlink(str(claim))
        except OSError:
            pass


def ticket_guard(repo, ticket):
    """The repository-common coordinator guard for `ticket`: hold it through every effect.

    The guard is one kernel-locked file per ticket next to the lock record, resolved through
    `git rev-parse --git-common-dir`, so every linked checkout and every coordinator CLI or
    native operation serializes on the same inode. Acquisition is bounded; a busy guard is a
    retryable `owner_busy`, never a takeover. Ownership is validated *inside* the guard, and
    the caller keeps it held until its effect -- a manifest write, a Git effect, a session
    launch, an owner recovery -- has finished, so a read-only check can never race the effect
    it authorized.
    """
    _ticket(ticket)
    return _file_guard(_guard_path(repo, ticket), 'coordinator')


def _read_lock(path, ticket):
    """The stored owner record, `None` when there is no lock, or `ambiguous_owner`.

    Two shapes are accepted: the current record with `coordinator_id` and the explicit
    recovery audit trail, and the legacy record written before coordinator identity existed.
    A legacy record is returned as it is stored -- never upgraded, adopted or deleted here;
    `recover_ticket` is the one explicit path that may replace it.
    """
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
    keys = sorted(value)
    if keys != sorted(STORED_OWNER_KEYS) and keys != sorted(LEGACY_OWNER_KEYS):
        _fail(kind, 'the ticket lock at {} is not a known owner record: {!r}'.format(
            path, keys))
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the ticket lock schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    if value['ticket'] != ticket:
        _fail(kind, 'the ticket lock at {} is not a lock for {}'.format(path, ticket))
    _canonical_uuid(value['run_id'], kind, 'the ticket lock run_id')
    if value['phase'] is not None:
        _integer(value['phase'], 'the ticket lock phase', kind, 1)
    _text(value['token'], 'the ticket lock token', kind)
    _text(value['acquired_at'], 'the ticket lock acquired_at', kind)
    if 'coordinator_id' in value:
        _text(value['coordinator_id'], 'the ticket lock coordinator_id', kind)
        if value['recovered_at'] is not None:
            _text(value['recovered_at'], 'the ticket lock recovered_at', kind)
        if value['prior_coordinator_id'] is not None:
            _text(value['prior_coordinator_id'], 'the ticket lock prior_coordinator_id', kind)
        if value['recovery_reason'] is not None:
            _text(value['recovery_reason'], 'the ticket lock recovery_reason', kind)
    return value


def _owner(value, keys, what):
    """The owner identity request (`acquire`), the release request or the recovery request."""
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
    _text(value['coordinator_id'], '{} coordinator_id'.format(what), kind)
    return value


def _utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _legacy(stored):
    """True for a lock record written before coordinator identity existed."""
    return 'coordinator_id' not in stored


def _held(stored, owner):
    """The stored record when `owner` is the run and coordinator that already holds it."""
    if stored['run_id'] == owner['run_id'] and stored['phase'] == owner['phase']:
        if _legacy(stored):
            _fail('legacy_owner', 'the ticket lock for {} predates coordinator identity; '
                                  'adopt it explicitly with `owner recover` before any '
                                  'mutation'.format(stored['ticket']))
        if stored['coordinator_id'] != owner['coordinator_id']:
            _fail('coordinator_conflict', 'the ticket {} is held by run {} under coordinator '
                                          '{!r}, not {!r}; only that coordinator may mutate '
                                          'the run, or an explicit `owner recover` hands it '
                                          'over'.format(stored['ticket'], stored['run_id'],
                                                        stored['coordinator_id'],
                                                        owner['coordinator_id']))
        return stored
    _fail('ticket_owned', 'the ticket {} is owned by run {} (phase {}), acquired {}'.format(
        stored['ticket'], stored['run_id'], stored['phase'], stored['acquired_at']))


def validate_owner(repo, ticket, run_id, phase, coordinator_id):
    """Match the stored lock to the exact run *and* coordinator, read-only.

    The caller must hold `ticket_guard(repo, ticket)` across its whole effect: this read
    establishes that the lock names the exact run/phase and that `coordinator_id` is its
    current coordinator. A missing lock is `not_owned`, a legacy record is `legacy_owner`
    (never silently adopted), another run is `foreign_owner` and another coordinator of the
    same run is `coordinator_conflict`.
    """
    _ticket(ticket)
    _text(coordinator_id, 'the coordinator_id', 'invalid_owner')
    stored = _read_lock(_lock_path(repo, ticket), ticket)
    if stored is None:
        _fail('not_owned', 'the ticket {} is not owned: acquire the lock before its run '
                           'state is mutated'.format(ticket))
    if _legacy(stored):
        _fail('legacy_owner', 'the ticket lock for {} predates coordinator identity; adopt '
                              'it explicitly with `owner recover` before any mutation'.format(
                                  ticket))
    if stored['run_id'] != run_id or stored['phase'] != phase:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}), not by run {} '
                               '(phase {})'.format(ticket, stored['run_id'], stored['phase'],
                                                   run_id, phase))
    if stored['coordinator_id'] != coordinator_id:
        _fail('coordinator_conflict', 'the ticket {} is held by run {} under coordinator '
                                      '{!r}, not {!r}; only that coordinator may mutate the '
                                      'run, or an explicit `owner recover` hands it over'.format(
                                          ticket, stored['run_id'], stored['coordinator_id'],
                                          coordinator_id))
    return stored


def acquire_ticket(repo, ticket, owner):
    """Take the repository-wide ticket lock before arming a run, or refuse.

    The lock is `<git-common-dir>/artel/parallel/<TICKET>.json`, so a linked worktree
    cannot bypass it. An existing lock is never reclaimed -- not for another run, not for
    another phase of the same run, and never because time passed; the run that already
    holds it (same `run_id`, `phase` and `coordinator_id`) re-acquires its own record. A
    different coordinator of the same run is refused (`coordinator_conflict`), even when the
    run UUID is known; a legacy record is refused (`legacy_owner`) and only an explicit
    `recover_ticket` may adopt it. A lock that cannot be read is `ambiguous_owner` and is
    left exactly as found. The returned record is the run's ownership proof: `token` is the
    release proof and `acquired_at` its record.
    """
    _ticket(ticket)
    _owner(owner, OWNER_KEYS, 'the owner request')
    if owner['ticket'] != ticket:
        _fail('invalid_owner', 'the owner request names {} but {} was asked for'.format(
            owner['ticket'], ticket))
    with ticket_guard(repo, ticket):
        return _acquire_locked(repo, ticket, owner)


def _acquire_locked(repo, ticket, owner):
    """`acquire_ticket` with the ticket guard already held."""
    path = _lock_path(repo, ticket)
    stored = _read_lock(path, ticket)
    if stored is not None:
        return _held(stored, owner)
    record = {'schema': SCHEMA, 'ticket': ticket, 'run_id': owner['run_id'],
              'phase': owner['phase'], 'coordinator_id': owner['coordinator_id'],
              'token': secrets.token_hex(16), 'acquired_at': _utc_now(),
              'recovered_at': None, 'prior_coordinator_id': None, 'recovery_reason': None}
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


def recover_ticket(repo, ticket, request):
    """Hand the same run's lock to a deliberate, audited replacement coordinator.

    This is the one explicit path that changes `coordinator_id`: the requesting coordinator
    must be a different one, the run and phase must match, and the request must carry a
    reason. The token is rotated, so every command and native effect of the previous
    coordinator fails fenced (`coordinator_conflict`) until it reacquires -- which it cannot,
    because the lock now names the new coordinator. A legacy record (no `coordinator_id`) is
    adopted by the same explicit step: its prior identity is recorded as null and the reason
    is preserved; nothing is deleted, inferred from age, reclaimed across runs, or TTL-based.
    The new record keeps `acquired_at` and adds `recovered_at`, `prior_coordinator_id` and
    `recovery_reason`. This proves a handoff was requested and recorded; it does not prove the
    previous session is dead.
    """
    _ticket(ticket)
    _owner(request, RECOVER_KEYS, 'the recovery request')
    _text(request['reason'], 'the recovery reason', 'invalid_owner')
    if request['ticket'] != ticket:
        _fail('invalid_owner', 'the recovery request names {} but {} was asked for'.format(
            request['ticket'], ticket))
    with ticket_guard(repo, ticket):
        return _recover_locked(repo, ticket, request)


def _recover_locked(repo, ticket, request):
    """`recover_ticket` with the ticket guard already held."""
    path = _lock_path(repo, ticket)
    stored = _read_lock(path, ticket)
    if stored is None:
        _fail('not_owned', 'the ticket {} is not owned: there is no lock to recover'.format(
            ticket))
    if stored['run_id'] != request['run_id'] or stored['phase'] != request['phase']:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}), not by run {} '
                               '(phase {}); recovery never crosses runs'.format(
                                   ticket, stored['run_id'], stored['phase'],
                                   request['run_id'], request['phase']))
    prior = None if _legacy(stored) else stored['coordinator_id']
    if prior == request['coordinator_id']:
        _fail('coordinator_current', 'the ticket {} is already held by coordinator {!r}; '
                                     'recovery hands the run to a different one'.format(
                                         ticket, request['coordinator_id']))
    record = {'schema': SCHEMA, 'ticket': ticket, 'run_id': stored['run_id'],
              'phase': stored['phase'], 'coordinator_id': request['coordinator_id'],
              'token': secrets.token_hex(16), 'acquired_at': stored['acquired_at'],
              'recovered_at': _utc_now(), 'prior_coordinator_id': prior,
              'recovery_reason': request['reason']}
    _atomic_write(path, _dump(record))
    return record


def release_ticket(repo, ticket, owner):
    """Release the ticket lock for the matching completed, quiescent run, or refuse.

    The request must carry the owning run's `run_id`, `phase`, `coordinator_id` and `token`
    proof, and must assert `completed` and `quiescent`: a pause, an unfinished run, a foreign
    run and a replaced coordinator all keep the lock. Nothing is inferred from age, a legacy
    record is never removed, and an unreadable lock is never removed.
    """
    _ticket(ticket)
    _owner(owner, RELEASE_KEYS, 'the release request')
    _text(owner['token'], 'the release request token', 'invalid_owner')
    if not isinstance(owner['completed'], bool) or not isinstance(owner['quiescent'], bool):
        _fail('invalid_owner', 'the release request completed and quiescent are not booleans')
    if owner['ticket'] != ticket:
        _fail('invalid_owner', 'the release request names {} but {} was asked for'.format(
            owner['ticket'], ticket))
    with ticket_guard(repo, ticket):
        return release_ticket_locked(repo, ticket, owner)


def release_ticket_locked(repo, ticket, owner):
    """`release_ticket` with the ticket guard already held by the caller.

    The CLI uses this to keep its own manifest/run-state quiescence checks inside the same
    guard as the removal; every other caller uses `release_ticket`.
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
    if _legacy(stored):
        _fail('legacy_owner', 'the ticket lock for {} predates coordinator identity; adopt it '
                              'explicitly with `owner recover` before releasing it'.format(
                                  ticket))
    if stored['run_id'] != owner['run_id'] or stored['phase'] != owner['phase'] \
            or stored['token'] != owner['token']:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}); only the run '
                               'that holds it may release it'.format(
                                   ticket, stored['run_id'], stored['phase']))
    if stored['coordinator_id'] != owner['coordinator_id']:
        _fail('coordinator_conflict', 'the ticket {} is held under coordinator {!r}, not '
                                      '{!r}; a replaced coordinator never releases it'.format(
                                          ticket, stored['coordinator_id'],
                                          owner['coordinator_id']))
    if not (owner['completed'] and owner['quiescent']):
        _fail('premature_release', 'run {} is not a completed quiescent run; ownership and '
                                   'recovery state are retained'.format(owner['run_id']))
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        _fail('not_owned', 'the ticket {} was released while releasing'.format(ticket))
    return {'schema': SCHEMA, 'ticket': ticket, 'run_id': owner['run_id'],
            'phase': owner['phase'], 'released': True}
