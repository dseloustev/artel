#!/usr/bin/env python3
"""parallel-queue: the coordinator's pure claim-intent, record and reconcile logic.

    begin_claim(manifest) -> {'manifest': manifest, 'intent': intent}
    record_claim(manifest, intent_id, row) -> {'manifest': manifest, 'action': action}
    reconcile_claims(manifest, rows) -> {'manifest': manifest, 'actions': [action, ...]}

Contract: docs/opencode-parallel.md §13 (claim recovery) and docs/task-queue.md §7 (the
parallel claim order). Nothing here calls the queue: the coordinator performs the external
claim and release calls these results authorize. Every function is pure -- it returns a new
manifest and never mutates its input.

The write order the coordinator keeps:

    1. `begin_claim` appends a `pending` intent; the caller persists the returned manifest
       with `parallel_state.write_manifest` BEFORE the claim call. The intent's `actor` is
       the call's actor -- `artel@<run_id>#<seq>:<attempt>`, unique per run, claim sequence
       and attempt -- so a restart recognises this run's claims by that prefix.
    2. the coordinator calls the queue's claim operation with that actor and passes the
       returned row to `record_claim`.
    3. the caller persists that result BEFORE any worker launch: `action['kind'] == 'run'`
       authorizes the launch only once the returned row mapping is durable.
    4. an unknown outcome is not retried. The intent stays `pending` until
       `reconcile_claims` observes the queue's rows: a row held by this run's prefix is this
       run's orphan and is released, no row means the claim never landed (`lost`, and the
       next `begin_claim` retries that sequence with the next attempt), and a row held by
       anyone else is never adopted, cleared or reclaimed.

Shapes:

    intent  {intent_id, seq, attempt, actor, state, row}
            state is one of pending, mapped, wrong-phase, foreign, orphaned, lost.
            `row` is the recorded row mapping, null until one is recorded.

    row     {task_id, title, status, actor, phase, task} -- the coordinator's normalized
            observation of the claimed queue row: the queue's row id, its title, its
            status, the holder it reports, the phase parsed from the title (`I<N> · `) and
            the `<iteration>.<n>` task parsed from it. All keys are required; a row outside
            this shape is refused.

    action  {'kind': 'run', task_id, task, claim, row}
                the claim is this run's and in phase; launch the worker only after the
                mapping is durable.
            {'kind': 'release', task_id, row, intent_id, reason}
                release the row (reason `wrong-phase` for a claim this run must give back,
                `orphaned-claim` for a reconciled orphan). Admission is stopped for
                reconciliation whenever the manifest's `stop` is set.
            {'kind': 'stop', reason, intent_id, task_id, row, detail}
                stop admission for diagnosis; never adopt or clear the row.

    stop    {reason, intent_id, row} -- latched by a stopped observation and refused by
            `begin_claim` until the run is reconciled. A wrong-phase row is released
            exactly once: the first observation releases it and latches the stop, and a
            later reconciliation never releases that claim again.
"""
import parallel_contract
import parallel_state

ROW_KEYS = ('task_id', 'title', 'status', 'actor', 'phase', 'task')
ACTION_KINDS = ('run', 'release', 'stop')
STOP_REASONS = ('wrong-phase', 'unexpected-holder')


class QueueError(ValueError):
    """A claim or reconciliation violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise QueueError(kind, message)


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


def _manifest(value):
    """The manifest sections this module reads, or `invalid_manifest`.

    The state-owned fields are `parallel_state`'s to validate; a manifest that reaches a
    reducer is re-checked for the sections the reducers touch (`claims`, `stop`, and the
    `run_id`, `phase` and `revision` they work against).
    """
    kind = 'invalid_manifest'
    if not isinstance(value, dict):
        _fail(kind, 'the manifest is not an object: {!r}'.format(value))
    for name in ('run_id', 'phase', 'revision', 'claims', 'stop'):
        if name not in value:
            _fail(kind, 'the manifest is missing `{}`'.format(name))
    if not isinstance(value['run_id'], str) or not value['run_id']:
        _fail(kind, 'the manifest run_id is not a non-empty string: {!r}'.format(value['run_id']))
    phase = value['phase']
    if phase is not None:
        _integer(phase, 'the manifest phase', kind, 1)
    _integer(value['revision'], 'the manifest revision', kind, 0)
    claims = value['claims']
    if not isinstance(claims, dict) or sorted(claims) != sorted(parallel_state.CLAIMS_KEYS):
        _fail(kind, 'the manifest claims is not {!r}: {!r}'.format(
            parallel_state.CLAIMS_KEYS, claims))
    _integer(claims['seq'], 'the manifest claims seq', kind, 0)
    if not isinstance(claims['intents'], list):
        _fail(kind, 'the manifest claims intents is not an array: {!r}'.format(
            claims['intents']))
    for index, intent in enumerate(claims['intents'], 1):
        _intent(intent, index)
    if value['stop'] is not None:
        _stop_record(value['stop'])
    return value


def _intent(value, index):
    kind = 'invalid_manifest'
    what = 'claims intent {}'.format(index)
    if not isinstance(value, dict):
        _fail(kind, '{} is not an object: {!r}'.format(what, value))
    missing = [name for name in parallel_state.INTENT_KEYS if name not in value]
    if missing:
        _fail(kind, '{} is missing {}'.format(
            what, ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in value if name not in parallel_state.INTENT_KEYS)
    if unknown:
        _fail(kind, '{} carries unknown {}'.format(
            what, ', '.join('`{}`'.format(name) for name in unknown)))
    _text(value['intent_id'], '{} intent_id'.format(what), kind)
    _integer(value['seq'], '{} seq'.format(what), kind, 1)
    _integer(value['attempt'], '{} attempt'.format(what), kind, 1)
    _text(value['actor'], '{} actor'.format(what), kind)
    if value['state'] not in parallel_state.INTENT_STATES:
        _fail(kind, '{} state is not one of {}: {!r}'.format(
            what, ', '.join(parallel_state.INTENT_STATES), value['state']))
    if value['row'] is not None:
        _row(value['row'])
    return value


def _stop_record(value):
    kind = 'invalid_manifest'
    if not isinstance(value, dict) or sorted(value) != sorted(parallel_state.STOP_KEYS):
        _fail(kind, 'the manifest stop is not {!r}: {!r}'.format(
            parallel_state.STOP_KEYS, value))
    if value['reason'] not in STOP_REASONS:
        _fail(kind, 'the manifest stop reason is not one of {}: {!r}'.format(
            ', '.join(STOP_REASONS), value['reason']))
    if value['intent_id'] is not None:
        _text(value['intent_id'], 'the manifest stop intent_id', kind)
    if value['row'] is not None:
        _row(value['row'])
    return value


def _row(value):
    """The normalized queue row, or `invalid_row`; exactly `ROW_KEYS`."""
    kind = 'invalid_row'
    if not isinstance(value, dict):
        _fail(kind, 'the row is not an object: {!r}'.format(value))
    missing = [name for name in ROW_KEYS if name not in value]
    if missing:
        _fail(kind, 'the row is missing {}'.format(
            ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in value if name not in ROW_KEYS)
    if unknown:
        _fail(kind, 'the row carries unknown {}'.format(
            ', '.join('`{}`'.format(name) for name in unknown)))
    _integer(value['task_id'], 'the row task_id', kind, 1)
    _text(value['title'], 'the row title', kind)
    _text(value['status'], 'the row status', kind)
    if value['actor'] is not None:
        _text(value['actor'], 'the row actor', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the row phase', kind, 1)
    if not isinstance(value['task'], str) or not parallel_contract.TASK_RE.match(value['task']):
        _fail(kind, 'the row task is not an `<iteration>.<n>` number: {!r}'.format(value['task']))
    return {name: value[name] for name in ROW_KEYS}


def _run_prefix(run_id):
    """The actor prefix every claim of one run carries, and recovery matches on."""
    return 'artel@{}#'.format(run_id)


def _actor(run_id, seq, attempt):
    return '{}{}:{}'.format(_run_prefix(run_id), seq, attempt)


def _wrong_phase(manifest, row):
    """A phase-scoped run's row that belongs to another phase (or names none)."""
    return manifest['phase'] is not None and row['phase'] != manifest['phase']


def _stop(reason, intent_id, row):
    return {'reason': reason, 'intent_id': intent_id, 'row': row}


def _intent_id_for(intents, actor):
    for intent in intents:
        if intent['actor'] == actor:
            return intent['intent_id']
    return None


def _replaced(intents, index, intent):
    return intents[:index] + [intent] + intents[index + 1:]


def begin_claim(manifest):
    """Append the next claim intent to `manifest`, before the coordinator calls the queue.

    Refused while the run's admission is stopped for reconciliation and while an earlier
    intent has no recorded outcome -- an unknown claim outcome is never blindly retried.
    The sequence numbers claims within the run; a sequence whose claim was reconciled as
    `lost` is retried with the next `attempt`, and every other outcome opens a new one.
    """
    value = _manifest(manifest)
    if value['stop'] is not None:
        _fail('admission_stopped', 'the run stopped admission for reconciliation ({})'.format(
            value['stop']['reason']))
    intents = value['claims']['intents']
    pending = [intent for intent in intents if intent['state'] == 'pending']
    if pending:
        _fail('unresolved_claim', 'claim intent {} has no recorded outcome; reconcile the '
                                  'queue before claiming again'.format(pending[0]['intent_id']))
    last = intents[-1] if intents else None
    if last is not None and last['state'] == 'lost':
        seq, attempt = last['seq'], last['attempt'] + 1
    else:
        seq, attempt = value['claims']['seq'] + 1, 1
    intent = {'intent_id': '{}#{}:{}'.format(value['run_id'], seq, attempt),
              'seq': seq, 'attempt': attempt, 'actor': _actor(value['run_id'], seq, attempt),
              'state': 'pending', 'row': None}
    claims = dict(value['claims'], seq=seq, intents=intents + [intent])
    return {'manifest': dict(value, claims=claims), 'intent': intent}


def record_claim(manifest, intent_id, row):
    """Record the row a claim returned for `intent_id` and answer the next action.

    The row must be held by the intent's own actor; anything else is an unexpected holder:
    it is recorded as `foreign`, the stop is latched, and nothing is ever adopted or
    cleared. A phase-scoped run's row from another phase is released exactly once and the
    stop is latched; a row in phase is `mapped` and the action is the run. The returned
    manifest must be written before the action's worker launch.
    """
    value = _manifest(manifest)
    recorded = _row(row)
    intents = value['claims']['intents']
    index = next((position for position, intent in enumerate(intents)
                  if intent['intent_id'] == intent_id), None)
    if index is None:
        _fail('unknown_intent', 'no claim intent {} in the manifest'.format(intent_id))
    intent = intents[index]
    if intent['state'] != 'pending':
        _fail('intent_resolved', 'claim intent {} is {}'.format(intent_id, intent['state']))
    if recorded['actor'] != intent['actor']:
        updated = dict(intent, state='foreign', row=recorded)
        stop = _stop('unexpected-holder', intent_id, recorded)
        action = {'kind': 'stop', 'reason': 'unexpected-holder', 'intent_id': intent_id,
                  'task_id': recorded['task_id'], 'row': recorded,
                  'detail': 'the row is held by {!r}, not by this run'.format(recorded['actor'])}
    elif _wrong_phase(value, recorded):
        updated = dict(intent, state='wrong-phase', row=recorded)
        if value['stop'] is None:
            stop = _stop('wrong-phase', intent_id, recorded)
            action = {'kind': 'release', 'reason': 'wrong-phase', 'intent_id': intent_id,
                      'task_id': recorded['task_id'], 'row': recorded}
        else:
            stop = value['stop']
            action = {'kind': 'stop', 'reason': 'wrong-phase-released', 'intent_id': intent_id,
                      'task_id': recorded['task_id'], 'row': recorded,
                      'detail': 'a wrong-phase row was already released once; admission '
                                'is stopped for reconciliation'}
    else:
        updated = dict(intent, state='mapped', row=recorded)
        stop = value['stop']
        action = {'kind': 'run', 'task_id': recorded['task_id'], 'task': recorded['task'],
                  'claim': intent['actor'], 'row': recorded}
    claims = dict(value['claims'], intents=_replaced(intents, index, updated))
    return {'manifest': dict(value, claims=claims, stop=stop), 'action': action}


def reconcile_claims(manifest, rows):
    """Reconcile the run's claim intents against the queue's rows, before claiming again.

    Every `pending` intent is resolved: a row held by its actor is this run's orphan
    (`orphaned`, and released), and no such row means the claim never landed (`lost`).
    Every row held by this run's actor prefix that is not a live claim is released with
    `orphaned-claim`; a foreign holder is never released, adopted or cleared, and a row
    whose intent was already released as `wrong-phase` is never released again. The
    returned `actions` are what the coordinator performs -- the reducers call nothing.
    """
    value = _manifest(manifest)
    if not isinstance(rows, list):
        _fail('invalid_rows', 'rows is not an array: {!r}'.format(rows))
    observed = [_row(row) for row in rows]
    intents = value['claims']['intents']
    live = {intent['actor'] for intent in intents if intent['state'] == 'mapped'}
    released = {intent['actor'] for intent in intents if intent['state'] == 'wrong-phase'}
    updated = []
    for intent in intents:
        if intent['state'] != 'pending':
            updated.append(intent)
            continue
        match = next((row for row in observed if row['actor'] == intent['actor']), None)
        if match is None:
            updated.append(dict(intent, state='lost'))
        else:
            updated.append(dict(intent, state='orphaned', row=match))
    stop = value['stop']
    actions = []
    prefix = _run_prefix(value['run_id'])
    for row in observed:
        actor = row['actor']
        if actor is None or not actor.startswith(prefix) or row['status'] != 'in_progress':
            continue
        if actor in live or actor in released:
            continue
        intent_id = _intent_id_for(intents, actor)
        actions.append({'kind': 'release', 'reason': 'orphaned-claim',
                        'task_id': row['task_id'], 'row': row, 'intent_id': intent_id})
        if stop is None and _wrong_phase(value, row):
            stop = _stop('wrong-phase', intent_id, row)
    claims = dict(value['claims'], intents=updated)
    return {'manifest': dict(value, claims=claims, stop=stop), 'actions': actions}
