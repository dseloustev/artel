#!/usr/bin/env python3
"""parallel-budget: the run-wide correction budget the parallel coordinator reserves from.

    read_run_state(repo, ticket, phase) -> (path, value)
    inspect(repo, ticket, phase) -> report
    reserve(repo, ticket, run_id, phase, kind, scope, attempt, ordinal, expected_revision)
        -> result

Contract: docs/autonomous-run.md §2 (`run-state.json`, `counters`) and §5 (capped loops:
`MAX_TOTAL_CORRECTION_ROUNDS = 8` is authoritative in `counters.correction_rounds`, preserved
across resume), docs/opencode-parallel.md §13.12, and the reviewed Task 10 fix round.

The budget is the EXISTING run-state counter -- never a second ledger, a manifest key or a
worker payload field. `reserve` charges one correction round for one stable reservation
identity before a fix dispatch, atomically, under an exclusive transient lock; `inspect`
answers the current counter, the cap, the revision and the recorded reservations. A
reservation is idempotent for its exact identity and stable key (a crash before the write
costs nothing; a lost reply replays for free), a new identity or round charges the counter
once, and nothing ever decrements, recredits or resets it.

The stable key is `<kind>:<scope>:a<attempt>:r<ordinal>`: `kind` is `review`, `runtime` or
`checkpoint` (the fix-list source); `scope` is a task number, `ticket` or `phase-<N>` (the
gate scope); `attempt` is the worker task's attempt for a task-scoped fix and 1 for a
ticket/phase scope; and `ordinal` is the true round ordinal of that scope's canonical artifact
(the per-task review's journal round, `review.md`'s `**Review round:**`, the runtime retry or
the checkpoint verify round). Task and attempt alone never identify a round -- the ordinal
does. The per-scope caps the coordinator reconciles against (`MAX_TASK_REVIEW_ROUNDS = 1`,
`MAX_REVIEW_ROUNDS = 3`, `MAX_RUNTIME_RETRIES = 1`, `MAX_CHECKPOINT_VERIFY_ROUNDS = 2`) are
reported by `inspect` and enforced per scope by `reserve`; the run-wide cap is shared by every
scope. Only an actual fix dispatch is charged: never the initial plan review or a worker's own
execution.

`expected_revision` is the write guard: it must equal the durable reservation revision, so a
stale caller fails before the write. Missing, unreadable, non-matching or not-armed run state
fails closed (`missing_run_state`, `corrupt_run_state`, `run_state_mismatch`,
`run_not_active`) and is never created, reset or overwritten; a refusal leaves every byte
exactly as it was. The transient lock is a kernel lock on a separate `<run-state>.lock` file
(the ledger itself is never the lock), it is never taken over, and it is released on process
exit.

Every failure raises `BudgetError`, a `ValueError` carrying the `kind` a CLI maps into its
error envelope; nothing is coerced and nothing is dropped.
"""
import contextlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / 'hooks'))

import hook_common  # noqa: E402

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX hosts have fcntl; the fallback stays safe
    fcntl = None

SCHEMA = 1
RUN_DIR = ('.artel', 'run')
STATE_NAME = 'run-state.json'
RESERVATIONS_KEY = 'correction_reservations'
CONFIG_PATH = ('.artel', 'config.json')
# docs/autonomous-run.md §5: the workflow defaults, not config keys.
MAX_TOTAL_CORRECTION_ROUNDS = 8
MAX_TASK_REVIEW_ROUNDS = 1
MAX_REVIEW_ROUNDS = 3
MAX_RUNTIME_RETRIES = 1
MAX_CHECKPOINT_VERIFY_ROUNDS = 2
KINDS = ('review', 'runtime', 'checkpoint')
SCOPE_CAPS = {
    'review': {'task': MAX_TASK_REVIEW_ROUNDS, 'ticket': MAX_REVIEW_ROUNDS,
               'phase': MAX_REVIEW_ROUNDS},
    'runtime': {'ticket': MAX_RUNTIME_RETRIES, 'phase': MAX_RUNTIME_RETRIES},
    'checkpoint': {'ticket': MAX_CHECKPOINT_VERIFY_ROUNDS,
                   'phase': MAX_CHECKPOINT_VERIFY_ROUNDS},
}
TASK_RE = re.compile(r'\d+\.\d+\Z')
PHASE_RE = re.compile(r'phase-\d+\Z')
BLOCK_KEYS = ('schema', 'revision', 'rounds')
RESERVATION_KEYS = ('key', 'kind', 'scope', 'attempt', 'ordinal', 'ticket', 'run_id', 'phase',
                    'reserved_at')
LOCK_TIMEOUT = 5.0
LOCK_POLL = 0.05


class BudgetError(ValueError):
    """A budget violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise BudgetError(kind, message)


def _text(value, what, kind='invalid_request'):
    if not isinstance(value, str) or not value:
        _fail(kind, '{} is not a non-empty string: {!r}'.format(what, value))
    if value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _integer(value, what, minimum, kind='invalid_request'):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(kind, '{} is not an integer: {!r}'.format(what, value))
    if value < minimum:
        _fail(kind, '{} must be at least {}: {!r}'.format(what, minimum, value))
    return value


def _utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def scope_class(scope):
    """`task`, `ticket` or `phase` for a reservation scope, or None when unknown."""
    if scope == 'ticket':
        return 'ticket'
    if PHASE_RE.match(scope):
        return 'phase'
    if TASK_RE.match(scope):
        return 'task'
    return None


def stable_key(kind, scope, attempt, ordinal):
    """The stable reservation identity: `<kind>:<scope>:a<attempt>:r<ordinal>`."""
    return '{}:{}:a{}:r{}'.format(kind, scope, attempt, ordinal)


def _state_path(repo, ticket):
    return Path(repo).joinpath(*RUN_DIR, ticket, STATE_NAME)


def _config(repo):
    """The host config under the explicit coordinator root, or {} when absent/unreadable."""
    path = Path(repo).joinpath(*CONFIG_PATH)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _check_ticket(repo, value, ticket, phase, path):
    """The run state must be this exact run's: canonical ticket and phase suffix."""
    kind = 'run_state_mismatch'
    state_ticket = value.get('ticket')
    if not isinstance(state_ticket, str) or not state_ticket.strip():
        _fail('corrupt_run_state', 'the run state at {} has no ticket: {!r}'.format(
            path, state_ticket))
    compiled, project_key = hook_common.ticket_matcher(_config(repo))
    if compiled is None:
        _fail('corrupt_run_state', 'the host ticket pattern is not a valid regular expression')
    match = compiled.match(state_ticket.strip())
    if match is None:
        _fail(kind, 'the run state at {} is for {!r}, which is not this run'.format(
            path, state_ticket))
    canonical = '{}-{}'.format(project_key.upper(), match.group(1))
    if canonical != ticket:
        _fail(kind, 'the run state at {} is for {}, not {}'.format(path, canonical, ticket))
    expected_phase = None if phase is None else str(phase)
    if match.group(2) != expected_phase:
        _fail(kind, 'the run state at {} is phase {}, not {}'.format(
            path, match.group(2), expected_phase))


def _check_record(record, path):
    kind = 'corrupt_run_state'
    if not isinstance(record, dict) or sorted(record) != sorted(RESERVATION_KEYS):
        _fail(kind, 'a reservation at {} is not {!r}: {!r}'.format(path, RESERVATION_KEYS,
                                                                   record))
    for name in ('key', 'kind', 'scope', 'ticket', 'run_id', 'reserved_at'):
        _text(record[name], 'a reservation {}'.format(name), kind)
    _integer(record['attempt'], 'a reservation attempt', 1, kind)
    _integer(record['ordinal'], 'a reservation ordinal', 1, kind)
    if record['phase'] is not None:
        _integer(record['phase'], 'a reservation phase', 1, kind)
    if record['kind'] not in KINDS:
        _fail(kind, 'a reservation kind is not one of {}: {!r}'.format(
            ', '.join(KINDS), record['kind']))
    scope_kind = scope_class(record['scope'])
    if scope_kind is None or scope_kind not in SCOPE_CAPS[record['kind']]:
        _fail(kind, 'a reservation scope is not valid for {}: {!r}'.format(
            record['kind'], record['scope']))
    if record['key'] != stable_key(record['kind'], record['scope'], record['attempt'],
                                   record['ordinal']):
        _fail(kind, 'a reservation key does not match its identity: {!r}'.format(record['key']))
    return record


def _check_block(block, path):
    kind = 'corrupt_run_state'
    if not isinstance(block, dict) or sorted(block) != sorted(BLOCK_KEYS):
        _fail(kind, 'counters.{} at {} is not {!r}: {!r}'.format(RESERVATIONS_KEY, path,
                                                                 BLOCK_KEYS, block))
    if isinstance(block['schema'], bool) or block['schema'] != SCHEMA:
        _fail(kind, 'counters.{}.schema at {} must be {}'.format(RESERVATIONS_KEY, path,
                                                                 SCHEMA))
    _integer(block['revision'], 'counters.{}.revision'.format(RESERVATIONS_KEY), 0, kind)
    if not isinstance(block['rounds'], list):
        _fail(kind, 'counters.{}.rounds at {} is not an array'.format(RESERVATIONS_KEY, path))
    for record in block['rounds']:
        _check_record(record, path)
    return block


def _check_counters(counters, path):
    if counters is None:
        return
    if not isinstance(counters, dict):
        _fail('corrupt_run_state', 'the run state counters at {} are not an object'.format(
            path))
    rounds = counters.get('correction_rounds')
    if rounds is not None:
        _integer(rounds, 'counters.correction_rounds', 0, 'corrupt_run_state')
    block = counters.get(RESERVATIONS_KEY)
    if block is not None:
        _check_block(block, path)


def read_run_state(repo, ticket, phase):
    """The validated run state for this exact run, or a typed fail-closed refusal."""
    path = _state_path(repo, ticket)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail('missing_run_state', 'no run state at {}; the run is not armed'.format(path))
    except (OSError, UnicodeDecodeError) as exc:
        _fail('corrupt_run_state', 'the run state at {} cannot be read: {}'.format(path, exc))
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail('corrupt_run_state', 'the run state at {} is not valid JSON: {}'.format(
            path, exc))
    if not isinstance(value, dict):
        _fail('corrupt_run_state', 'the run state at {} is not an object'.format(path))
    _check_ticket(repo, value, ticket, phase, path)
    _check_counters(value.get('counters'), path)
    return path, value


def require_armed(value):
    """The run must be armed: `run_active` exactly true."""
    if value.get('run_active') is not True:
        _fail('run_not_active', 'the run is not armed: run_active is {!r}'.format(
            value.get('run_active')))
    return value


def _counter(value):
    counters = value.get('counters')
    if not isinstance(counters, dict):
        return 0
    rounds = counters.get('correction_rounds')
    return rounds if isinstance(rounds, int) and not isinstance(rounds, bool) else 0


def _block(value):
    counters = value.get('counters')
    block = counters.get(RESERVATIONS_KEY) if isinstance(counters, dict) else None
    if block is None:
        return {'schema': SCHEMA, 'revision': 0, 'rounds': []}
    return block


def _report(ticket, phase, path, value, block):
    counter = _counter(value)
    return {
        'schema': SCHEMA, 'ticket': ticket, 'phase': phase, 'run_state': str(path),
        'cap': MAX_TOTAL_CORRECTION_ROUNDS, 'correction_rounds': counter,
        'remaining': max(0, MAX_TOTAL_CORRECTION_ROUNDS - counter),
        'revision': block['revision'],
        'reservations': [dict(record) for record in block['rounds']],
        'scope_caps': {kind: dict(caps) for kind, caps in SCOPE_CAPS.items()},
    }


def inspect(repo, ticket, phase):
    """The current budget: the counter, the cap, the revision and every recorded round."""
    path, value = read_run_state(repo, ticket, phase)
    require_armed(value)
    return _report(ticket, phase, path, value, _block(value))


def _validate_request(kind, scope, attempt, ordinal, expected_revision):
    kind = _text(kind, 'kind')
    if kind not in KINDS:
        _fail('invalid_request', 'kind is not one of {}: {!r}'.format(', '.join(KINDS), kind))
    scope = _text(scope, 'scope')
    scope_kind = scope_class(scope)
    if scope_kind is None or scope_kind not in SCOPE_CAPS[kind]:
        _fail('invalid_request', 'scope {!r} is not valid for a {} reservation'.format(
            scope, kind))
    attempt = _integer(attempt, 'attempt', 1)
    ordinal = _integer(ordinal, 'ordinal', 1)
    expected_revision = _integer(expected_revision, 'expected_revision', 0)
    return kind, scope, attempt, ordinal, expected_revision


def reserve(repo, ticket, run_id, phase, kind, scope, attempt, ordinal, expected_revision):
    """Charge one durable correction round for the stable identity, or replay it.

    The reservation is written atomically under an exclusive transient lock; the same
    identity and stable key replay for free (a lost reply never charges twice), a new identity
    or round charges the shared counter once, and the run-wide and per-scope caps refuse
    before any write. `expected_revision` must equal the durable reservation revision.
    """
    kind, scope, attempt, ordinal, expected_revision = _validate_request(
        kind, scope, attempt, ordinal, expected_revision)
    path = _state_path(repo, ticket)
    with _exclusive(path):
        path, value = read_run_state(repo, ticket, phase)
        require_armed(value)
        block = _block(value)
        if expected_revision != block['revision']:
            _fail('stale_revision', 'the budget is at revision {}, not the expected {}; '
                                    'inspect fresh and retry'.format(block['revision'],
                                                                     expected_revision))
        key = stable_key(kind, scope, attempt, ordinal)
        existing = next((record for record in block['rounds']
                         if record['key'] == key and record['ticket'] == ticket
                         and record['run_id'] == run_id and record['phase'] == phase), None)
        if existing is not None:
            report = _report(ticket, phase, path, value, block)
            report.update({'run_id': run_id, 'replayed': True, 'reservation': existing})
            return report
        cap = SCOPE_CAPS[kind][scope_class(scope)]
        if ordinal > cap:
            _fail('scope_cap', 'a {} fix round at {} is capped at {} round(s); ordinal {} '
                               'is refused'.format(kind, scope, cap, ordinal))
        counter = _counter(value)
        if counter >= MAX_TOTAL_CORRECTION_ROUNDS:
            _fail('budget_exhausted', 'the run-wide correction budget is exhausted ({}/{}); '
                                      'no new round is charged'.format(
                                          counter, MAX_TOTAL_CORRECTION_ROUNDS))
        record = {'key': key, 'kind': kind, 'scope': scope, 'attempt': attempt,
                  'ordinal': ordinal, 'ticket': ticket, 'run_id': run_id, 'phase': phase,
                  'reserved_at': _utc_now()}
        counters = dict(value.get('counters') or {})
        counters['correction_rounds'] = counter + 1
        counters[RESERVATIONS_KEY] = {'schema': SCHEMA, 'revision': block['revision'] + 1,
                                      'rounds': block['rounds'] + [record]}
        _write_state(path, dict(value, counters=counters))
        updated = dict(value, counters=counters)
        report = _report(ticket, phase, path, updated, counters[RESERVATIONS_KEY])
        report.update({'run_id': run_id, 'replayed': False, 'reservation': record})
        return report


def _write_state(path, value):
    """The atomic ledger write: temp sibling, original mode, os.replace."""
    mode = 0o644
    try:
        mode = os.stat(str(path)).st_mode & 0o777
    except OSError:
        pass
    text = json.dumps(value, indent=2) + '\n'
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name + '.',
                                         suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _exclusive(state_path):
    """The transient reservation lock: a kernel lock on a separate file, never taken over.

    `fcntl.flock` is released by the kernel on process exit, so a crashed coordinator leaves
    no stale lock to reclaim; the portable fallback creates the lock file exclusively and
    refuses a busy one rather than removing someone else's. The lock directory is never
    created: a run with no state directory has no budget, and a refusal leaves the tree
    exactly as it was.
    """
    lock = Path(str(state_path) + '.lock')
    try:
        handle = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except FileNotFoundError:
        _fail('missing_run_state', 'no run state directory at {}; the run is not armed'.format(
            lock.parent))
    if fcntl is not None:
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        _fail('budget_busy', 'another reservation holds {}; retry'.format(lock))
                    time.sleep(LOCK_POLL)
            yield
        finally:
            os.close(handle)
        return
    os.close(handle)
    deadline = time.monotonic() + LOCK_TIMEOUT  # pragma: no cover - non-POSIX fallback
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                _fail('budget_busy', 'another reservation holds {}; retry'.format(lock))
            time.sleep(LOCK_POLL)
        except FileNotFoundError:
            _fail('missing_run_state', 'no run state directory at {}; the run is not '
                                       'armed'.format(lock.parent))
    try:
        yield
    finally:
        os.close(handle)
        try:
            os.unlink(str(lock))
        except OSError:
            pass
