#!/usr/bin/env python3
"""parallel-publication: restartable completion publication and dependency promotion.

    next_publication_actions(manifest, task, observed) -> [action, ...]
    record_publication(manifest, task, action_id, receipt) -> {manifest, publication}

Contract: docs/opencode-parallel.md §13.9 (the publication actions, receipts and the durable
record), §13.7 (the cleanup publication proof), §13.8 (the integration transaction this
consumes), §6 (the task states) and the approved serialized integration/publication clauses.

`next_publication_actions` is a pure reducer: it reads the run manifest, the committed
integration record and the coordinator's fresh observations of the configured artifact
store, the queue, the worker evidence and the native worker disposition, and answers which
publication actions are still owed, in the fixed order. `record_publication` is the other
half: it records one action's durable intent (before its external effect) or its confirmed
receipt (after), reconciles a repeat against the durable record, and performs the single
`integrating -> published` transition exactly when every required receipt is durable. No
function here calls a store, a queue, a session or Git, and none invents a second
scheduler: dependencies move because `parallel_scheduler` already treats `published` as
complete.

Ordering. A files-backed run's tick and ordered notes are already in the final cycle commit
(Task 8's `documents`); the reducer recognizes that committed effect and never emits a
post-commit file write or another completion commit. A store-backed run commits the accepted
code first (empty `documents`) and then publishes the expected-version tasklist tick and the
numbered implementation notes; the worker evidence is collected into the coordinator's own
task/attempt-scoped `collected_root`; the owned queue row is marked done **last** of the
external effects; and only then does the manifest task move `integrating -> published`. A
queue-free (local) run keeps the same lifecycle without a queue action.

Durable shapes (schema 1, JSON-safe).

    observed -- the coordinator's fresh observation bundle, exactly these keys:

      {schema, transaction, lifecycle, store, queue, evidence}

      transaction   the Task 8 record, exactly its key list, at stage `committed` or
                    `recovered`, with its `publication` field holding this module's record
                    (the reserved place §13.8 keeps for it) or null.
      lifecycle     {disposition} -- the native worker status fact, `succeeded` required.
      store         {mode: "files"|"store", documents, notes}
                    documents: the pending tasklist tick observation(s), each
                      {path, version, content_sha256, applied} -- `version` is the stored
                      newest, `applied` the coordinator's parse of whether the tick is
                      already in it.
                    notes: null, or {path, version, content_sha256, entries, proposals} --
                      `entries` are the parsed numbered notes [{number, key}], `proposals`
                      the accepted deviation proposals [{key}] for this task/attempt.
      queue         {row} -- the fresh observation of the run's own recorded claim row, or
                    null for a queue-free run.
      evidence      {source_root, collected_root, entries, collected} -- the worker
                    artifact files [{path, sha256}], and the same list as observed at the
                    coordinator's collected root, or null when nothing is collected yet.

    action -- one publication action, exactly these keys:

      {schema, action_id, kind, effect, state, ticket, phase, task, run_id, attempt, mode,
       transaction_id, cycle_commit, cycle_ref, result_commit, result_ref, documents, plan,
       index, payload}

      action_id  `artel-publish-<run_id>-<task>-a<attempt>-<suffix>`, stable across restarts
                 and retries; the suffix is `documents`, `tick`, `note-<proposal index>`,
                 `collect`, `queue` or `promote`.
      kind       committed-documents | store-tick | note-append | collect-evidence |
                 queue-done | promote
      effect     recognize | store | copy | queue | local
      state      `pending` (execute the effect) or `observed` (the effect is already
                 durable; record the receipt without repeating it)
      plan       the full ordered plan of action ids; `index` this action's position
      payload    kind-specific: committed-documents {paths}; store-tick {path,
                 expected_version}; note-append {path, expected_version, number, key};
                 collect-evidence {source_root, collected_root, entries}; queue-done
                 {task_id, actor, row}; promote {}.

    receipt -- what the coordinator hands `record_publication`, exactly these keys:

      {schema, publication, action, state, evidence}

      publication  the durable publication record this receipt is appended to, or null
      action       the action, verbatim as planned
      state        `planned` (the durable intent, persisted before the effect),
                   `applied` (the effect just executed) or `observed` (already durable)
      evidence     the confirmed effect facts (see _EVIDENCE below), or null for `planned`

    publication -- the durable record, persisted by the coordinator into the transaction
    record's reserved `publication` field, exactly these keys:

      {schema, ticket, phase, task, run_id, attempt, mode, transaction_id, cycle_commit,
       cycle_ref, result_commit, result_ref, documents, plan, intents, receipts, complete,
       proof}

      intents    the actions whose durable intent is recorded, in plan order
      receipts   the confirmed effects, in plan order: {schema, action_id, state, evidence}
      complete   true exactly when every plan action has a receipt
      proof      the §13.7 cleanup publication proof once complete, else null

Idempotence and refusals. A repeated receipt for an action is idempotent when it is
byte-identical and a blocking `conflicting_receipt` otherwise; receipts are recorded in plan
order (`out_of_order`, `missing_intent`); an action whose identity or plan differs from the
durable record is `identity_conflict` or `plan_conflict`. A store action whose durable
intent's base moved is re-based from the fresh observation (the expected-version write still
carries the fresh base, so a concurrent edit is never overwritten or dropped); a re-base may
never move the expected version backwards (`intent_conflict`). A queue row observed under a
foreign actor, a different row id or task, or a status that is not `in_progress`/`done` is
never completed (`foreign_row`, `row_changed`, `row_missing`, `row_not_in_progress`). A
collected copy that is missing or changed after its receipt is `collection_lost`.

A receipt proves the effect landed; it is not proof the effect is still there. Before the
publication may finish -- for an incomplete record and for a complete record whose manifest
write was lost -- the fresh observation must still carry every receipted effect: a receipted
tick that a forward rewrite dropped, or a receipted note key that is gone or renumbered,
refuses (`conflicting_receipt`, or `note_conflict` for the exact renumber conflict) and the
reducer never silently re-applies the deletion, overwrites the concurrent edit, removes the
store's history or allocates a new note number. A receipted queue completion is rechecked the
same way: a row reopened, reacquired, replaced or deleted refuses (`queue_conflict`) instead
of releasing dependencies on a historical receipt. A benign forward edit that still carries the
tick and every note key at its recorded number continues. "After completion" means the
manifest is already `published` (the reducer then owes nothing), never merely this record's
`complete` flag.

Trust boundary. `record_publication` receives no observation bundle, so it cannot re-verify
the planning prerequisites: its `receipt` must name an action `next_publication_actions`
returned for the current fresh observation, and the trusted coordinator plans fresh before
every effect. A hand-made action with plausible identity fields is not checked against the
queue row or the store content there; only the planner's fresh observation is.

Every failure raises `PublicationError`, a `ValueError` carrying the `kind` a CLI maps into
its error envelope; nothing is coerced and nothing is dropped.
"""
import os
import re

import parallel_contract
import parallel_integration
import parallel_state
import task_worktree

SCHEMA = 1
PUBLISH_PREFIX = 'artel-publish'
INTEGRATE_PREFIX = 'artel-integrate'
COMMITTED_STAGES = ('committed', 'recovered')
MODES = ('files', 'store')
KINDS = ('committed-documents', 'store-tick', 'note-append', 'collect-evidence',
         'queue-done', 'promote')
EFFECTS = {
    'committed-documents': 'recognize',
    'store-tick': 'store',
    'note-append': 'store',
    'collect-evidence': 'copy',
    'queue-done': 'queue',
    'promote': 'local',
}
ACTION_STATES = ('pending', 'observed')
RECEIPT_STATES = ('planned', 'applied', 'observed')
STORE_KINDS = ('store-tick', 'note-append')
DISPOSITIONS = ('succeeded', 'failed', 'interrupted')
QUEUE_ROW_KEYS = ('task_id', 'title', 'status', 'actor', 'phase', 'task')
ACTION_KEYS = ('schema', 'action_id', 'kind', 'effect', 'state', 'ticket', 'phase', 'task',
               'run_id', 'attempt', 'mode', 'transaction_id', 'cycle_commit', 'cycle_ref',
               'result_commit', 'result_ref', 'documents', 'plan', 'index', 'payload')
RECEIPT_KEYS = ('schema', 'publication', 'action', 'state', 'evidence')
RECORD_KEYS = ('schema', 'ticket', 'phase', 'task', 'run_id', 'attempt', 'mode',
               'transaction_id', 'cycle_commit', 'cycle_ref', 'result_commit', 'result_ref',
               'documents', 'plan', 'intents', 'receipts', 'complete', 'proof')
RECORDED_RECEIPT_KEYS = ('schema', 'action_id', 'kind', 'state', 'evidence')
OBSERVED_KEYS = ('schema', 'transaction', 'lifecycle', 'store', 'queue', 'evidence')
LIFECYCLE_KEYS = ('disposition',)
STORE_KEYS = ('mode', 'documents', 'notes')
STORE_DOCUMENT_KEYS = ('path', 'version', 'content_sha256', 'applied')
NOTES_KEYS = ('path', 'version', 'content_sha256', 'entries', 'proposals')
NOTE_ENTRY_KEYS = ('number', 'key')
PROPOSAL_KEYS = ('key',)
QUEUE_KEYS = ('row',)
EVIDENCE_KEYS = ('source_root', 'collected_root', 'entries', 'collected')
EVIDENCE_ENTRY_KEYS = ('path', 'sha256')
PAYLOAD_KEYS = {
    'committed-documents': ('paths',),
    'store-tick': ('path', 'expected_version'),
    'note-append': ('path', 'expected_version', 'number', 'key'),
    'collect-evidence': ('source_root', 'collected_root', 'entries'),
    'queue-done': ('task_id', 'actor', 'row'),
    'promote': (),
}
EVIDENCE_FIELDS = {
    'committed-documents': ('paths',),
    'store-tick': ('version', 'content_sha256'),
    'note-append': ('version', 'content_sha256', 'number', 'key'),
    'collect-evidence': ('collected_root', 'entries'),
    'queue-done': ('task_id', 'actor', 'status'),
    'promote': (),
}
SHA256_RE = re.compile(r'[0-9a-f]{64}\Z')


class PublicationError(ValueError):
    """A publication violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise PublicationError(kind, message)


# --- validation helpers -------------------------------------------------------------------

def _object(value, what, kind):
    if not isinstance(value, dict):
        _fail(kind, '{} is not an object: {!r}'.format(what, value))
    return value


def _keys(value, required, what, kind):
    missing = [name for name in required if name not in value]
    if missing:
        _fail(kind, '{} is missing {}'.format(
            what, ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in value if name not in required)
    if unknown:
        _fail(kind, '{} carries unknown {}'.format(
            what, ', '.join('`{}`'.format(name) for name in unknown)))


def _text(value, what, kind, allow_empty=False):
    if not isinstance(value, str):
        _fail(kind, '{} is not a string: {!r}'.format(what, value))
    if not value and not allow_empty:
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


def _relative(value, what, kind):
    _text(value, what, kind)
    if os.path.isabs(value):
        _fail(kind, '{} is not a relative path: {!r}'.format(what, value))
    normalized = os.path.normpath(value)
    if normalized == '..' or normalized.startswith('..' + os.sep):
        _fail(kind, '{} escapes its root: {!r}'.format(what, value))
    return value


def _sha256(value, what, kind):
    if not isinstance(value, str) or not SHA256_RE.match(value):
        _fail(kind, '{} is not a sha256 digest: {!r}'.format(what, value))
    return value


def _commit(value, what, kind):
    if value is not None and (not isinstance(value, str)
                              or not parallel_contract.COMMIT_RE.match(value)):
        _fail(kind, '{} is not a full 40-hex commit id: {!r}'.format(what, value))
    return value


def _ticket(value, what, kind):
    if not isinstance(value, str) or not parallel_contract.TICKET_RE.match(value):
        _fail(kind, '{} is not a canonical <PROJECT_KEY>-<number>: {!r}'.format(what, value))
    return value


def _task_id(value, what, kind):
    if not isinstance(value, str) or not parallel_contract.TASK_RE.match(value):
        _fail(kind, '{} is not an `<iteration>.<n>` number: {!r}'.format(what, value))
    return value


def _run_id(value, what, kind):
    return _text(value, what, kind)


def _paths(value, what, kind):
    if not isinstance(value, list):
        _fail(kind, '{} is not an array: {!r}'.format(what, value))
    for entry in value:
        _relative(entry, '{} entry'.format(what), kind)
    return value


# --- the manifest and the integration record ----------------------------------------------

def _manifest(value):
    """The manifest sections this module reads, or `invalid_manifest`.

    The state-owned fields are `parallel_state`'s to validate; a manifest that reaches a
    reducer is re-checked for the sections publication depends on (`ticket`, `phase`,
    `run_id`, `revision`, `tasks`, `claims`).
    """
    kind = 'invalid_manifest'
    _object(value, 'the manifest', kind)
    for name in ('ticket', 'phase', 'run_id', 'revision', 'tasks', 'claims'):
        if name not in value:
            _fail(kind, 'the manifest is missing `{}`'.format(name))
    _ticket(value['ticket'], 'the manifest ticket', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the manifest phase', kind, 1)
    _run_id(value['run_id'], 'the manifest run_id', kind)
    _integer(value['revision'], 'the manifest revision', kind, 0)
    tasks = _object(value['tasks'], 'the manifest tasks', kind)
    for task, record in tasks.items():
        _task_id(task, 'the manifest task id', kind)
        _object(record, 'the task {}'.format(task), kind)
        if record.get('state') not in parallel_contract.TASK_STATES \
                + parallel_contract.SIDE_STATES:
            _fail(kind, 'the task {} state is not a task state: {!r}'.format(
                task, record.get('state')))
        _integer(record.get('attempt'), 'the task {} attempt'.format(task), kind, 1)
    claims = _object(value['claims'], 'the manifest claims', kind)
    for name in ('seq', 'intents'):
        if name not in claims:
            _fail(kind, 'the manifest claims is missing `{}`'.format(name))
    if not isinstance(claims['intents'], list):
        _fail(kind, 'the manifest claims intents is not an array: {!r}'.format(
            claims['intents']))
    return value


def _task_record(value, task):
    record = value['tasks'].get(task)
    if record is None:
        _fail('unknown_task', 'the task {} is not in the manifest'.format(task))
    return record


def _owned_claim(value, task):
    """The run's own recorded claim row for `task`, or None for a queue-free run.

    Only a `mapped` intent whose recorded row names this task is the owned mapping: a
    pending, foreign, wrong-phase, orphaned or lost intent is never a completion proof.
    """
    matches = [intent for intent in value['claims']['intents']
               if intent.get('state') == 'mapped'
               and isinstance(intent.get('row'), dict)
               and intent['row'].get('task') == task]
    if len(matches) > 1:
        _fail('invalid_manifest', 'the run records more than one mapped claim for {}'.format(
            task))
    return matches[0] if matches else None


def _transaction(value):
    """The committed Task 8 record this publication consumes, or `invalid_transaction`.

    The exact key list is the integration module's own; the stage, the identity, the
    retained transport, the branch and the accepted review/gate envelopes are checked here
    because publication refuses to manufacture any of them.
    """
    kind = 'invalid_transaction'
    _object(value, 'the transaction', kind)
    _keys(value, parallel_integration.TRANSACTION_KEYS, 'the transaction', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the transaction schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    _ticket(value['ticket'], 'the transaction ticket', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the transaction phase', kind, 1)
    _task_id(value['task'], 'the transaction task', kind)
    _run_id(value['run_id'], 'the transaction run_id', kind)
    _integer(value['attempt'], 'the transaction attempt', kind, 1)
    _text(value['transaction_id'], 'the transaction transaction_id', kind)
    _text(value['branch'], 'the transaction branch', kind)
    _commit(value['base_commit'], 'the transaction base_commit', kind)
    _commit(value['pre_head'], 'the transaction pre_head', kind)
    _commit(value['transport_commit'], 'the transaction transport_commit', kind)
    _commit(value['cycle_commit'], 'the transaction cycle_commit', kind)
    _text(value['result_ref'], 'the transaction result_ref', kind)
    _paths(value['documents'], 'the transaction documents', kind)
    if value['stage'] not in parallel_integration.STAGES:
        _fail(kind, 'the transaction stage is not one of {}: {!r}'.format(
            ', '.join(parallel_integration.STAGES), value['stage']))
    if value['review'] is not None:
        review = _object(value['review'], 'the transaction review', kind)
        _keys(review, ('required', 'route', 'verdict', 'rounds'), 'the transaction review',
              kind)
    if value['verify'] is not None:
        _object(value['verify'], 'the transaction verify', kind)
    if value['publication'] is not None:
        _publication(value['publication'])
    return value


def _publication(value):
    """The durable publication record, or `invalid_publication`."""
    kind = 'invalid_publication'
    _object(value, 'the publication', kind)
    _keys(value, RECORD_KEYS, 'the publication', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the publication schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    _ticket(value['ticket'], 'the publication ticket', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the publication phase', kind, 1)
    _task_id(value['task'], 'the publication task', kind)
    _run_id(value['run_id'], 'the publication run_id', kind)
    _integer(value['attempt'], 'the publication attempt', kind, 1)
    if value['mode'] not in MODES:
        _fail(kind, 'the publication mode is not `files` or `store`: {!r}'.format(
            value['mode']))
    _text(value['transaction_id'], 'the publication transaction_id', kind)
    _commit(value['cycle_commit'], 'the publication cycle_commit', kind)
    _cycle_ref(value['cycle_ref'])
    _commit(value['result_commit'], 'the publication result_commit', kind)
    _text(value['result_ref'], 'the publication result_ref', kind)
    _paths(value['documents'], 'the publication documents', kind)
    if not isinstance(value['plan'], list) or not value['plan']:
        _fail(kind, 'the publication plan is not a non-empty array: {!r}'.format(
            value['plan']))
    for action_id in value['plan']:
        _text(action_id, 'a publication plan entry', kind)
    if not isinstance(value['intents'], list) or not isinstance(value['receipts'], list):
        _fail(kind, 'the publication intents/receipts are not arrays')
    for action in value['intents']:
        _action(action, value['plan'])
    for receipt in value['receipts']:
        _recorded_receipt(receipt)
    if not isinstance(value['complete'], bool):
        _fail(kind, 'the publication complete is not a boolean: {!r}'.format(value['complete']))
    if value['proof'] is not None:
        _proof(value['proof'])
    if value['complete'] and value['proof'] is None:
        _fail(kind, 'the publication is complete without its cleanup proof')
    return value


def _recorded_receipt(value):
    kind = 'invalid_publication'
    _object(value, 'a recorded receipt', kind)
    _keys(value, RECORDED_RECEIPT_KEYS, 'a recorded receipt', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the recorded receipt schema must be {}: {!r}'.format(
            SCHEMA, value['schema']))
    _text(value['action_id'], 'a recorded receipt action_id', kind)
    if value['kind'] not in KINDS:
        _fail(kind, 'a recorded receipt kind is not one of {}: {!r}'.format(
            ', '.join(KINDS), value['kind']))
    if value['state'] not in ('applied', 'observed'):
        _fail(kind, 'a recorded receipt state is not `applied` or `observed`: {!r}'.format(
            value['state']))
    _object(value['evidence'], 'a recorded receipt evidence', kind)
    return value


def _cycle_ref(value):
    if not isinstance(value, str) or not value.startswith('refs/heads/') \
            or value == 'refs/heads/' or value != value.strip() \
            or any(char in value for char in '\x00\n\r'):
        _fail('invalid_publication', 'the cycle_ref is not a `refs/heads/<branch>` refname: '
                                     '{!r}'.format(value))
    return value


def _proof(value):
    """The §13.7 cleanup publication proof, exactly its key list."""
    kind = 'invalid_publication'
    _object(value, 'the cleanup proof', kind)
    _keys(value, task_worktree.PUBLICATION_KEYS, 'the cleanup proof', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the cleanup proof schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    _ticket(value['ticket'], 'the cleanup proof ticket', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the cleanup proof phase', kind, 1)
    _run_id(value['run_id'], 'the cleanup proof run_id', kind)
    _task_id(value['task'], 'the cleanup proof task', kind)
    _integer(value['attempt'], 'the cleanup proof attempt', kind, 1)
    _commit(value['cycle_commit'], 'the cleanup proof cycle_commit', kind)
    _cycle_ref(value['cycle_ref'])
    _commit(value['result_commit'], 'the cleanup proof result_commit', kind)
    _text(value['result_ref'], 'the cleanup proof result_ref', kind)
    _text(value['collected_root'], 'the cleanup proof collected_root', kind)
    if not os.path.isabs(value['collected_root']):
        _fail(kind, 'the cleanup proof collected_root is not an absolute path')
    if value['published'] is not True:
        _fail(kind, 'the cleanup proof must assert `published: true`')
    _evidence_entries(value['evidence'], 'the cleanup proof evidence')
    return value


# --- the observed bundle -------------------------------------------------------------------

def _observed(value):
    kind = 'invalid_observed'
    _object(value, 'the observed bundle', kind)
    _keys(value, OBSERVED_KEYS, 'the observed bundle', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the observed bundle schema must be {}: {!r}'.format(SCHEMA,
                                                                        value['schema']))
    _transaction(value['transaction'])
    lifecycle = _object(value['lifecycle'], 'the observed lifecycle', kind)
    _keys(lifecycle, LIFECYCLE_KEYS, 'the observed lifecycle', kind)
    if lifecycle['disposition'] not in DISPOSITIONS:
        _fail(kind, 'the observed lifecycle disposition is not one of {}: {!r}'.format(
            ', '.join(DISPOSITIONS), lifecycle['disposition']))
    _store(value['store'])
    queue = _object(value['queue'], 'the observed queue', kind)
    _keys(queue, QUEUE_KEYS, 'the observed queue', kind)
    if queue['row'] is not None:
        _queue_row(queue['row'])
    _evidence(value['evidence'])
    return value


def _store(value):
    kind = 'invalid_store'
    _object(value, 'the observed store', kind)
    _keys(value, STORE_KEYS, 'the observed store', kind)
    if value['mode'] not in MODES:
        _fail(kind, 'the store mode is not `files` or `store`: {!r}'.format(value['mode']))
    if not isinstance(value['documents'], list):
        _fail(kind, 'the store documents is not an array: {!r}'.format(value['documents']))
    if len(value['documents']) > 1:
        _fail(kind, 'the store path publishes one tasklist tick, not {}'.format(
            len(value['documents'])))
    for document in value['documents']:
        _object(document, 'a store document', kind)
        _keys(document, STORE_DOCUMENT_KEYS, 'a store document', kind)
        _relative(document['path'], 'a store document path', kind)
        _integer(document['version'], 'a store document version', kind, 0)
        _sha256(document['content_sha256'], 'a store document content_sha256', kind)
        if not isinstance(document['applied'], bool):
            _fail(kind, 'a store document applied is not a boolean: {!r}'.format(
                document['applied']))
    if value['notes'] is not None:
        _notes(value['notes'])
    return value


def _notes(value):
    kind = 'invalid_notes'
    _object(value, 'the observed notes', kind)
    _keys(value, NOTES_KEYS, 'the observed notes', kind)
    _relative(value['path'], 'the notes path', kind)
    _integer(value['version'], 'the notes version', kind, 0)
    _sha256(value['content_sha256'], 'the notes content_sha256', kind)
    if not isinstance(value['entries'], list):
        _fail(kind, 'the notes entries is not an array: {!r}'.format(value['entries']))
    numbers = []
    keys = []
    for entry in value['entries']:
        _object(entry, 'a notes entry', kind)
        _keys(entry, NOTE_ENTRY_KEYS, 'a notes entry', kind)
        numbers.append(_integer(entry['number'], 'a notes entry number', kind, 1))
        keys.append(_text(entry['key'], 'a notes entry key', kind))
    if len(set(numbers)) != len(numbers):
        _fail(kind, 'the notes entries repeat a number')
    if len(set(keys)) != len(keys):
        _fail(kind, 'the notes entries repeat a key')
    if not isinstance(value['proposals'], list):
        _fail(kind, 'the notes proposals is not an array: {!r}'.format(value['proposals']))
    proposal_keys = []
    for proposal in value['proposals']:
        _object(proposal, 'a notes proposal', kind)
        _keys(proposal, PROPOSAL_KEYS, 'a notes proposal', kind)
        proposal_keys.append(_text(proposal['key'], 'a notes proposal key', kind))
    if len(set(proposal_keys)) != len(proposal_keys):
        _fail(kind, 'the notes proposals repeat a key')
    return value


def _queue_row(value):
    kind = 'invalid_queue'
    _object(value, 'the queue row', kind)
    _keys(value, QUEUE_ROW_KEYS, 'the queue row', kind)
    _integer(value['task_id'], 'the queue row task_id', kind, 1)
    _text(value['title'], 'the queue row title', kind)
    _text(value['status'], 'the queue row status', kind)
    if value['actor'] is not None:
        _text(value['actor'], 'the queue row actor', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the queue row phase', kind, 1)
    _task_id(value['task'], 'the queue row task', kind)
    return value


def _evidence(value):
    kind = 'invalid_evidence'
    _object(value, 'the observed evidence', kind)
    _keys(value, EVIDENCE_KEYS, 'the observed evidence', kind)
    for name in ('source_root', 'collected_root'):
        _text(value[name], 'the evidence {}'.format(name), kind)
        if not os.path.isabs(value[name]):
            _fail(kind, 'the evidence {} is not an absolute path: {!r}'.format(
                name, value[name]))
    _evidence_entries(value['entries'], 'the evidence entries')
    if value['collected'] is not None:
        _evidence_entries(value['collected'], 'the collected evidence')
    return value


def _evidence_entries(value, what):
    kind = 'invalid_evidence'
    if not isinstance(value, list):
        _fail(kind, '{} is not an array: {!r}'.format(what, value))
    paths = []
    for entry in value:
        _object(entry, '{} entry'.format(what), kind)
        _keys(entry, EVIDENCE_ENTRY_KEYS, '{} entry'.format(what), kind)
        paths.append(_relative(entry['path'], '{} path'.format(what), kind))
        _sha256(entry['sha256'], '{} sha256'.format(what), kind)
    if len(set(paths)) != len(paths):
        _fail(kind, '{} lists a path twice'.format(what))
    return value


# --- actions -------------------------------------------------------------------------------

def _action_id(identity, suffix):
    return '{}-{}-{}-a{}-{}'.format(PUBLISH_PREFIX, identity['run_id'], identity['task'],
                                    identity['attempt'], suffix)


def _transaction_id(value, task):
    return '{}-{}-{}-a{}'.format(INTEGRATE_PREFIX, value['run_id'], task,
                                 value['tasks'][task]['attempt'])


def _action(value, plan=None):
    """One publication action, or `invalid_action`; `plan` pins its plan when known."""
    kind = 'invalid_action'
    _object(value, 'the action', kind)
    _keys(value, ACTION_KEYS, 'the action', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the action schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    _text(value['action_id'], 'the action_id', kind)
    if value['kind'] not in KINDS:
        _fail(kind, 'the action kind is not one of {}: {!r}'.format(
            ', '.join(KINDS), value['kind']))
    if value['kind'] == 'promote' and value['action_id'] in value['plan']:
        _fail(kind, 'a promote action is never a plan member')
    if value['effect'] != EFFECTS[value['kind']]:
        _fail(kind, 'the action effect {!r} is not {!r}'.format(
            value['effect'], EFFECTS[value['kind']]))
    if value['state'] not in ACTION_STATES:
        _fail(kind, 'the action state is not `pending` or `observed`: {!r}'.format(
            value['state']))
    _ticket(value['ticket'], 'the action ticket', kind)
    if value['phase'] is not None:
        _integer(value['phase'], 'the action phase', kind, 1)
    _task_id(value['task'], 'the action task', kind)
    _run_id(value['run_id'], 'the action run_id', kind)
    _integer(value['attempt'], 'the action attempt', kind, 1)
    if value['mode'] not in MODES:
        _fail(kind, 'the action mode is not `files` or `store`: {!r}'.format(value['mode']))
    _text(value['transaction_id'], 'the action transaction_id', kind)
    _commit(value['cycle_commit'], 'the action cycle_commit', kind)
    _cycle_ref(value['cycle_ref'])
    _commit(value['result_commit'], 'the action result_commit', kind)
    _text(value['result_ref'], 'the action result_ref', kind)
    _paths(value['documents'], 'the action documents', kind)
    if not isinstance(value['plan'], list) or not value['plan']:
        _fail(kind, 'the action plan is not a non-empty array: {!r}'.format(value['plan']))
    for entry in value['plan']:
        _text(entry, 'an action plan entry', kind)
    if value['action_id'] not in value['plan'] and not (
            value['kind'] == 'promote' and value['index'] == len(value['plan'])):
        _fail(kind, 'the action {} is not in its own plan'.format(value['action_id']))
    _integer(value['index'], 'the action index', kind, 0)
    if value['index'] > len(value['plan']):
        _fail(kind, 'the action index {} is past its plan'.format(value['index']))
    _payload(value['payload'], value['kind'])
    if plan is not None and value['plan'] != plan:
        _fail(kind, 'the action plan is not the durable plan')
    return value


def _payload(value, kind):
    what = 'the {} payload'.format(kind)
    _object(value, what, 'invalid_action')
    _keys(value, PAYLOAD_KEYS[kind], what, 'invalid_action')
    if kind == 'committed-documents':
        _paths(value['paths'], '{} paths'.format(what), 'invalid_action')
    elif kind in STORE_KINDS:
        _relative(value['path'], '{} path'.format(what), 'invalid_action')
        _integer(value['expected_version'], '{} expected_version'.format(what),
                 'invalid_action', 0)
        if kind == 'note-append':
            _integer(value['number'], '{} number'.format(what), 'invalid_action', 1)
            _text(value['key'], '{} key'.format(what), 'invalid_action')
    elif kind == 'collect-evidence':
        for name in ('source_root', 'collected_root'):
            _text(value[name], '{} {}'.format(what, name), 'invalid_action')
            if not os.path.isabs(value[name]):
                _fail('invalid_action', '{} {} is not an absolute path: {!r}'.format(
                    what, name, value[name]))
        _evidence_entries(value['entries'], '{} entries'.format(what))
    elif kind == 'queue-done':
        _integer(value['task_id'], '{} task_id'.format(what), 'invalid_action', 1)
        _text(value['actor'], '{} actor'.format(what), 'invalid_action')
        _queue_row(value['row'])
    return value


def _action_identity(value, task, transaction, mode, documents):
    return {
        'schema': SCHEMA, 'ticket': value['ticket'], 'phase': value['phase'], 'task': task,
        'run_id': value['run_id'], 'attempt': value['tasks'][task]['attempt'], 'mode': mode,
        'transaction_id': transaction['transaction_id'],
        'cycle_commit': transaction['cycle_commit'],
        'cycle_ref': 'refs/heads/' + transaction['branch'],
        'result_commit': transaction['transport_commit'],
        'result_ref': transaction['result_ref'],
        'documents': list(documents),
    }


def _make_action(identity, kind, suffix, index, plan, payload, state):
    action = dict(identity)
    action.update({
        'schema': SCHEMA, 'action_id': _action_id(identity, suffix), 'kind': kind,
        'effect': EFFECTS[kind], 'state': state, 'plan': list(plan), 'index': index,
        'payload': payload,
    })
    return _action(action)


def _receipt_state(prior, action_id):
    if prior is None:
        return None
    for receipt in prior['receipts']:
        if receipt['action_id'] == action_id:
            return receipt
    return None


def _intent_state(prior, action_id):
    if prior is None:
        return None
    for action in prior['intents']:
        if action['action_id'] == action_id:
            return action
    return None


def _identity_of(value):
    return {name: value[name] for name in ('ticket', 'phase', 'task', 'run_id', 'attempt',
                                           'mode', 'transaction_id', 'cycle_commit',
                                           'cycle_ref', 'result_commit', 'result_ref',
                                           'documents')}


def _same_identity(left, right):
    return _identity_of(left) == _identity_of(right)


# --- the plan ------------------------------------------------------------------------------

def _require_committed(transaction, value, task):
    """The record must be a landed commit with an accepted review and a green gate."""
    if transaction['stage'] not in COMMITTED_STAGES:
        _fail('not_committed', 'the integration transaction is {!r}; publication needs '
                               '`committed` or `recovered`'.format(transaction['stage']))
    if transaction['cycle_commit'] is None or transaction['transport_commit'] is None:
        _fail('not_committed', 'the transaction carries no final cycle commit or retained '
                               'transport commit')
    if (transaction['ticket'], transaction['phase'], transaction['task'],
            transaction['run_id'], transaction['attempt']) != (
                value['ticket'], value['phase'], task, value['run_id'],
                value['tasks'][task]['attempt']):
        _fail('identity_mismatch', 'the transaction does not belong to this task/attempt')
    if transaction['transaction_id'] != _transaction_id(value, task):
        _fail('identity_mismatch', 'the transaction id {!r} is not this attempt\'s'.format(
            transaction['transaction_id']))
    expected_ref = '{}/{}/{}/{}/result'.format(parallel_integration.REF_ROOT, value['run_id'],
                                               task, value['tasks'][task]['attempt'])
    if transaction['result_ref'] != expected_ref:
        _fail('identity_mismatch', 'the transaction result_ref {!r} is not the run\'s '
                                   'canonical {!r}'.format(transaction['result_ref'],
                                                           expected_ref))
    if transaction['base_commit'] == transaction['cycle_commit']:
        _fail('not_committed', 'the cycle commit is the base commit itself; nothing was '
                               'integrated')
    review = transaction['review']
    if review is None:
        _fail('review_missing', 'the transaction carries no accepted review')
    if review['route'] != transaction['route']:
        _fail('review_incomplete', 'the accepted review route {!r} does not cover the '
                                   'transaction route {!r}'.format(review['route'],
                                                                   transaction['route']))
    if transaction['route'] == 'full':
        if review['verdict'] != 'passed' or review['rounds'] < 1 or not review['required']:
            _fail('review_incomplete', 'the full-route review is {!r} after {} round(s)'.format(
                review['verdict'], review['rounds']))
    elif review['verdict'] not in ('passed', 'skipped') or review['required']:
        _fail('review_incomplete', 'the light-route review is {!r}'.format(
            review['verdict']))
    verify = transaction['verify']
    if verify is None or verify.get('ok') is not True:
        _fail('gate_missing', 'the transaction carries no green integrated-tree gate')


def _check_collected_root(identity, evidence, transaction):
    """The collected root is task/attempt-scoped under the run's own directory, separate
    from the worker roots; the lifecycle re-verifies the real copies and symlinks."""
    collected = os.path.normpath(evidence['collected_root'])
    root = os.path.normpath(transaction['repo_root'])
    prefix = os.path.join(root, '.artel', 'run', identity['ticket'], 'parallel',
                          identity['run_id'])
    if collected == prefix or not collected.startswith(prefix + os.sep):
        _fail('invalid_evidence', 'the collected root {} is not under the run\'s {}'.format(
            collected, prefix))
    for other, what in ((os.path.normpath(evidence['source_root']), 'source root'),
                        (os.path.normpath(transaction['worker_root']), 'worker root')):
        if collected == other or collected.startswith(other + os.sep) \
                or other.startswith(collected + os.sep):
            _fail('invalid_evidence', 'the collected root {} overlaps the {} {}'.format(
                collected, what, other))
    return collected


def _store_tick_action(identity, plan, index, document, prior):
    action_id = plan[index]
    intent = _intent_state(prior, action_id)
    expected = intent['payload']['expected_version'] if intent is not None \
        else document['version']
    if document['version'] < expected:
        _fail('invalid_observation', 'the store holds {} at version {}, behind the '
                                     'expected {}'.format(document['path'],
                                                          document['version'], expected))
    if document['version'] == expected:
        if document['applied']:
            _fail('invalid_observation', '{} is observed ticked at its own base version '
                                         '{}'.format(document['path'], expected))
        state = 'pending'
    elif document['applied']:
        state = 'observed'
    else:
        state = 'pending'
    if state == 'pending':
        expected = document['version']
    payload = {'path': document['path'], 'expected_version': expected}
    return _make_action(identity, 'store-tick', 'tick', index, plan, payload, state)


def _note_plan(identity, plan, index, note_index, notes, proposal, prior, projected):
    """One note action, its durable number and its projected expected version.

    The number is the recorded entry's when the note already landed, the durable intent's
    when one is recorded, and otherwise the next free number in proposal order; a fresh
    plan projects the version each earlier pending append will create, so the store's
    expected-version write is exact.
    """
    action_id = plan[index]
    intent = _intent_state(prior, action_id)
    key = proposal['key']
    recorded = {entry['key']: entry['number'] for entry in notes['entries']}
    if key in recorded:
        number = recorded[key]
        if intent is not None and intent['payload']['number'] != number:
            _fail('note_conflict', 'the note {} is durably at number {}, not the recorded '
                                   '{}'.format(key, number, intent['payload']['number']))
        expected = intent['payload']['expected_version'] if intent is not None \
            else max(0, notes['version'] - 1)
        action = _make_action(identity, 'note-append',
                              'note-{}'.format(note_index), index, plan,
                              {'path': notes['path'], 'expected_version': expected,
                               'number': number, 'key': key}, 'observed')
        return action, projected
    if intent is not None:
        number = intent['payload']['number']
        expected = intent['payload']['expected_version']
        if notes['version'] < expected:
            _fail('invalid_observation', 'the notes are at version {}, behind the recorded '
                                         'intent {}'.format(notes['version'], expected))
        if notes['version'] > expected:
            expected = notes['version']
            if number in {entry['number'] for entry in notes['entries']}:
                number = _next_note_number(notes['entries'], notes['proposals'], key)
    else:
        number = _next_note_number(notes['entries'], notes['proposals'], key)
        expected = projected
    action = _make_action(identity, 'note-append', 'note-{}'.format(note_index), index, plan,
                          {'path': notes['path'], 'expected_version': expected,
                           'number': number, 'key': key}, 'pending')
    return action, expected + 1


def _plan_actions(value, task, transaction, observed, prior):
    """The deterministic ordered plan, reconciled against the durable intents."""
    store = observed['store']
    documents = list(transaction['documents'])
    if store['mode'] == 'store' and documents:
        _fail('mode_conflict', 'the artifact-store path commits code first; the transaction '
                               'carries committed documents {}'.format(
                                   ', '.join(documents)))
    if store['mode'] == 'files' and store['documents']:
        _fail('mode_conflict', 'the files path commits its documents with the code; the '
                               'observation carries store patches')
    identity = _action_identity(value, task, transaction, store['mode'], documents)
    entries = []
    if store['mode'] == 'files' and documents:
        entries.append(('committed-documents', 'documents', {'paths': documents}))
    for document in store['documents']:
        entries.append(('store-tick', 'tick', {'path': document['path']}))
    if store['notes'] is not None:
        for number, proposal in enumerate(store['notes']['proposals'], 1):
            entries.append(('note-append', 'note-{}'.format(number), {'key': proposal['key']}))
    entries.append(('collect-evidence', 'collect', {}))
    claim = _owned_claim(value, task)
    if claim is not None:
        entries.append(('queue-done', 'queue', {'row': claim['row'], 'actor': claim['actor']}))
    plan = [_action_id(identity, suffix) for _kind, suffix, _seed in entries]

    actions = []
    projected = store['notes']['version'] if store['notes'] is not None else None
    note_index = 0
    for index, (kind, _suffix, seed) in enumerate(entries):
        action_id = plan[index]
        intent = _intent_state(prior, action_id)
        if kind == 'committed-documents':
            payload = {'paths': documents}
            actions.append(_make_action(identity, kind, 'documents', index, plan, payload,
                                        'observed'))
        elif kind == 'store-tick':
            document = next(entry for entry in store['documents']
                            if entry['path'] == seed['path'])
            actions.append(_store_tick_action(identity, plan, index, document, prior))
        elif kind == 'note-append':
            notes = store['notes']
            note_index += 1
            proposal = next(entry for entry in notes['proposals']
                            if entry['key'] == seed['key'])
            action, projected = _note_plan(identity, plan, index, note_index, notes, proposal,
                                           prior, projected)
            actions.append(action)
        elif kind == 'collect-evidence':
            evidence = observed['evidence']
            _check_collected_root(identity, evidence, transaction)
            entries_value = [dict(entry) for entry in evidence['entries']]
            payload = {'source_root': evidence['source_root'],
                       'collected_root': evidence['collected_root'],
                       'entries': entries_value}
            if intent is not None:
                payload = intent['payload']
            landed = evidence['collected'] is not None \
                and _same_entries(evidence['collected'], payload['entries'])
            state = 'observed' if landed else 'pending'
            actions.append(_make_action(identity, kind, 'collect', index, plan, payload,
                                        state))
        elif kind == 'queue-done':
            action = _queue_action(identity, plan, index, value, task, observed)
            actions.append(action)
    return actions


def _next_note_number(entries, proposals, key):
    """The next free durable number for a proposal: one past the highest recorded, in
    proposal order, so a restarted coordinator assigns the same number."""
    numbers = [entry['number'] for entry in entries]
    next_number = (max(numbers) + 1) if numbers else 1
    recorded = {entry['key']: entry['number'] for entry in entries}
    for proposal in proposals:
        if proposal['key'] in recorded:
            next_number = max(next_number, recorded[proposal['key']] + 1)
            if proposal['key'] == key:
                return recorded[proposal['key']]
        else:
            if proposal['key'] == key:
                return next_number
            next_number += 1
    return next_number


def _same_entries(left, right):
    return sorted((entry['path'], entry['sha256']) for entry in left) == \
        sorted((entry['path'], entry['sha256']) for entry in right)


def _queue_action(identity, plan, index, value, task, observed):
    claim = _owned_claim(value, task)
    row = observed['queue']['row']
    if row is None:
        _fail('row_missing', 'the run owns a claim for {} but the queue shows no such '
                             'row'.format(task))
    if row['task_id'] != claim['row']['task_id']:
        _fail('row_changed', 'the owned row id {} is observed as {}'.format(
            claim['row']['task_id'], row['task_id']))
    if row['actor'] != claim['actor']:
        _fail('foreign_row', 'the row {} is observed held by {!r}, not by this run\'s '
                             '{!r}'.format(row['task_id'], row['actor'], claim['actor']))
    if row['task'] != task:
        _fail('row_changed', 'the owned row is observed for task {!r}'.format(row['task']))
    if row['status'] == 'done':
        state = 'observed'
    elif row['status'] == 'in_progress':
        state = 'pending'
    else:
        _fail('row_not_in_progress', 'the owned row {} is {!r}; only an owned in-progress '
                                     'row is ever completed'.format(row['task_id'],
                                                                    row['status']))
    payload = {'task_id': claim['row']['task_id'], 'actor': claim['actor'],
               'row': dict(claim['row'])}
    return _make_action(identity, 'queue-done', 'queue', index, plan, payload, state)


def _promote_action(record):
    identity = _identity_of(record)
    identity['schema'] = SCHEMA
    return _make_action(identity, 'promote', 'promote', len(record['plan']), record['plan'],
                        {}, 'observed')


# --- the public reducers -------------------------------------------------------------------

def next_publication_actions(manifest, task, observed):
    """The publication actions still owed for `task`, in their fixed order, or `[]`.

    The task must be durably `integrating` (a `published` task owes nothing); the observed
    transaction must be a landed commit with its accepted review and green gate, and the
    native disposition must be `succeeded`. The plan follows the storage path: the committed
    documents (files path) or the tasklist tick and numbered notes (artifact-store path),
    then the worker evidence collection, then the owned queue completion last; every action
    carries its stable identity and the full ordered plan. An action whose effect the fresh
    observation already proves is returned as `observed` (record its receipt, never repeat
    it); a store action whose durable base moved is re-based on the fresh version. Every
    receipted effect must still be present in the fresh observation -- a receipted tick a
    forward rewrite dropped, or a receipted note key gone or renumbered, refuses
    (`conflicting_receipt`/`note_conflict`) instead of completing on the historical receipt.
    That check runs before a complete record offers its single `promote` action too, so a
    complete record whose manifest write was lost re-checks the fresh content and evidence;
    "after completion" is the manifest's `published` state, never merely the record's
    `complete` flag. Nothing here calls the store, the queue, a session or Git.
    """
    value = _manifest(manifest)
    _task_id(task, 'the task', 'invalid_task')
    record = _task_record(value, task)
    if record['state'] == 'published':
        return []
    if record['state'] != 'integrating':
        _fail('not_integrating', 'the task {} is {}; publication needs the durable '
                                 '`integrating` transition'.format(task, record['state']))
    bundle = _observed(observed)
    transaction = bundle['transaction']
    _require_committed(transaction, value, task)
    if bundle['lifecycle']['disposition'] != 'succeeded':
        _fail('worker_not_succeeded', 'the native worker disposition is {!r}; a worker '
                                      'completion is not publication'.format(
                                          bundle['lifecycle']['disposition']))
    prior = transaction['publication']
    if prior is not None:
        if not _same_identity(prior, _action_identity(value, task, transaction,
                                                      bundle['store']['mode'],
                                                      transaction['documents'])):
            _fail('identity_conflict', 'the durable publication record does not belong to '
                                       'this transaction')
    actions = _plan_actions(value, task, transaction, bundle, prior)
    plan = [action['action_id'] for action in actions]
    if prior is not None and prior['plan'] != plan:
        _fail('plan_conflict', 'the durable plan {} is not the recomputed {}'.format(
            prior['plan'], plan))
    if prior is not None:
        # A receipt proves the effect landed; it is not proof the effect is still there.
        # The fresh observation must carry every receipted effect before the publication
        # may finish -- for an incomplete record and equally for a complete record whose
        # manifest write was lost, so a reverted tick or note is never mistaken for
        # current completion. "After completion" is the manifest's `published` state, not
        # this record's `complete` flag; a refusal leaves the record, the effects and the
        # integrating latch exactly as they were.
        _require_recorded_receipts(prior, actions, bundle)
        if prior['complete']:
            return [_promote_action(prior)]
    return [action for action in actions
            if _receipt_state(prior, action['action_id']) is None]


def _observed_document(observed, path):
    """The fresh observation of one tasklist document, or `conflicting_receipt`.

    A receipted tick whose document is no longer observed at all is a content loss, not a
    shape error: the refusal is typed, never a raw indexing error.
    """
    for document in observed['store']['documents']:
        if document['path'] == path:
            return document
    _fail('conflicting_receipt', 'the ticked document {} is no longer observed'.format(path))


def _observed_notes(observed, path):
    """The fresh observation of the numbered notes document, or `conflicting_receipt`."""
    notes = observed['store']['notes']
    if notes is None or notes['path'] != path:
        _fail('conflicting_receipt', 'the numbered notes {} are no longer observed'.format(
            path))
    return notes


def _require_recorded_receipts(prior, actions, observed):
    """Every durable receipt must still be consistent with the fresh observation.

    The version floor is the weakest check: a forward rewrite that drops an already
    receipted tick, or a note key that is gone or renumbered, refuses with
    `conflicting_receipt` (an exact renumber conflict with `note_conflict`). The reducer
    never silently re-applies the deletion, overwrites the concurrent edit, removes the
    store's history or allocates a new note number; the durable record is returned
    untouched. A benign forward edit that still carries the tick and every note key at its
    recorded number continues.
    """
    by_id = {action['action_id']: action for action in actions}
    for receipt in prior['receipts']:
        action = by_id.get(receipt['action_id'])
        if action is None:
            _fail('plan_conflict', 'the recorded receipt {} is not in the plan'.format(
                receipt['action_id']))
        kind = action['kind']
        if kind == 'collect-evidence':
            evidence = observed['evidence']
            if evidence['collected'] is None \
                    or not _same_entries(evidence['collected'],
                                         receipt['evidence']['entries']):
                _fail('collection_lost', 'the collected evidence for {} is missing or '
                                         'changed after its receipt'.format(
                                             receipt['action_id']))
        elif kind == 'store-tick':
            document = _observed_document(observed, action['payload']['path'])
            if document['version'] < receipt['evidence']['version']:
                _fail('conflicting_receipt', 'the store moved behind the recorded receipt '
                                             '{}'.format(receipt['action_id']))
            if not document['applied']:
                _fail('conflicting_receipt', 'the receipted tick for {} is absent from the '
                                             'newest store version; the concurrent rewrite '
                                             'is preserved'.format(
                                                 action['payload']['path']))
        elif kind == 'note-append':
            notes = _observed_notes(observed, action['payload']['path'])
            if notes['version'] < receipt['evidence']['version']:
                _fail('conflicting_receipt', 'the store moved behind the recorded receipt '
                                             '{}'.format(receipt['action_id']))
            landed = next((entry for entry in notes['entries']
                           if entry['key'] == receipt['evidence']['key']), None)
            if landed is None:
                _fail('conflicting_receipt', 'the receipted note {} is absent from the '
                                             'newest notes version'.format(
                                                 receipt['evidence']['key']))
            if landed['number'] != receipt['evidence']['number']:
                _fail('note_conflict', 'the receipted note {} is now at number {}, not the '
                                       'recorded {}'.format(receipt['evidence']['key'],
                                                            landed['number'],
                                                            receipt['evidence']['number']))
        elif kind == 'queue-done':
            # A receipt proves the queue completion landed; it is not proof the row is still
            # done. The exact receipted row/actor/status is rechecked against the fresh
            # observation before any promotion: a reopened, reacquired, replaced or deleted
            # row keeps the integrating latch and refuses for reconciliation, and the old
            # receipt is never silently re-done.
            row = observed['queue']['row']
            if row is None:
                _fail('queue_conflict', 'the receipted queue completion {} is no longer '
                                        'observed; the integration latch is retained'.format(
                                            receipt['action_id']))
            if row['task_id'] != action['payload']['task_id'] \
                    or row['actor'] != action['payload']['actor']:
                _fail('queue_conflict', 'the receipted queue row {} is now observed as '
                                        '{} held by {!r}, not the recorded {!r}'.format(
                                            action['payload']['task_id'], row['task_id'],
                                            row['actor'], action['payload']['actor']))
            if row['status'] != 'done':
                _fail('queue_conflict', 'the receipted queue row {} is observed {!r} again; '
                                        'the integration latch is retained and the current '
                                        'queue fact is never overwritten'.format(
                                            row['task_id'], row['status']))


def record_publication(manifest, task, action_id, receipt):
    """Record one action's durable intent or confirmed receipt and answer the next state.

    `receipt` is the `{schema, publication, action, state, evidence}` envelope. `planned`
    persists the intent before the external effect; `applied`/`observed` records the
    confirmed receipt after it, in plan order, and only for the action's own durable intent
    (a re-based store intent supersedes its predecessor, never backwards). An identical
    repeat is idempotent; a conflicting one is refused. When the last required receipt
    lands, the publication is complete, the §13.7 cleanup proof is built and the returned
    manifest carries the one `integrating -> published` transition -- the state
    `parallel_scheduler` treats as complete, so dependents move only now. The receipt must
    name an action `next_publication_actions` returned for the current fresh observation:
    this function has no observation bundle and cannot re-verify the planning prerequisites
    (queue ownership, fresh store content) itself.
    """
    value = _manifest(manifest)
    _task_id(task, 'the task', 'invalid_task')
    record = _task_record(value, task)
    if record['state'] not in ('integrating', 'published'):
        _fail('not_integrating', 'the task {} is {}; publication needs the durable '
                                 '`integrating` transition'.format(task, record['state']))
    envelope = _receipt(receipt)
    if envelope['action']['action_id'] != action_id:
        _fail('invalid_receipt', 'the receipt action {} is not the requested {}'.format(
            envelope['action']['action_id'], action_id))
    action = _require_action_identity(value, task, envelope['action'])
    prior = envelope['publication']
    if record['state'] == 'published':
        return _published_repeat(value, task, prior, action, envelope)
    if action['kind'] == 'promote':
        return _promote(value, task, prior, action)
    publication = _apply(value, task, action, envelope, prior)
    return _finish(value, task, publication)


def _receipt(value):
    kind = 'invalid_receipt'
    _object(value, 'the receipt', kind)
    _keys(value, RECEIPT_KEYS, 'the receipt', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the receipt schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    if value['publication'] is not None:
        _publication(value['publication'])
    _action(value['action'])
    if value['state'] not in RECEIPT_STATES:
        _fail(kind, 'the receipt state is not one of {}: {!r}'.format(
            ', '.join(RECEIPT_STATES), value['state']))
    if value['state'] == 'planned':
        if value['evidence'] is not None:
            _fail(kind, 'a planned intent carries no effect evidence')
    elif value['action']['kind'] == 'promote':
        if value['evidence'] is not None:
            _fail(kind, 'a promote receipt carries no effect evidence')
    else:
        _object(value['evidence'], 'the receipt evidence', kind)
        _keys(value['evidence'], EVIDENCE_FIELDS[value['action']['kind']],
              'the receipt evidence', kind)
    return value


def _require_action_identity(value, task, action):
    """The action belongs to this manifest's task/attempt/transaction, or refuse."""
    kind = 'invalid_action'
    if (action['ticket'], action['phase'], action['task'], action['run_id'],
            action['attempt']) != (value['ticket'], value['phase'], task, value['run_id'],
                                   value['tasks'][task]['attempt']):
        _fail(kind, 'the action does not belong to this task/attempt')
    if action['transaction_id'] != _transaction_id(value, task):
        _fail(kind, 'the action transaction_id is not this attempt\'s')
    expected_ref = '{}/{}/{}/{}/result'.format(parallel_integration.REF_ROOT, value['run_id'],
                                               task, value['tasks'][task]['attempt'])
    if action['result_ref'] != expected_ref:
        _fail(kind, 'the action result_ref is not the run\'s canonical ref')
    if action['mode'] == 'store' and action['documents']:
        _fail('mode_conflict', 'a store action carries committed documents')
    if action['mode'] == 'files' and action['kind'] == 'store-tick':
        _fail('mode_conflict', 'a files action carries a store tick')
    return action


def _published_repeat(value, task, prior, action, envelope):
    """An already-published task answers only an idempotent repeat."""
    if action['kind'] == 'promote':
        if prior is None or not prior['complete']:
            _fail('incomplete_publication', 'the promote action carries no complete '
                                            'publication record')
        return {'schema': SCHEMA, 'manifest': value, 'publication': prior}
    if prior is not None and envelope['state'] != 'planned':
        recorded = _receipt_state(prior, action['action_id'])
        if recorded is not None and recorded['state'] == envelope['state'] \
                and recorded['evidence'] == envelope['evidence']:
            return {'schema': SCHEMA, 'manifest': value, 'publication': prior}
    _fail('already_published', 'the task {} is already published; only an identical repeat '
                               'is accepted'.format(task))


def _promote(value, task, prior, action):
    if prior is None or not prior['complete']:
        _fail('incomplete_publication', 'the promote action carries no complete publication '
                                        'record')
    if not _same_identity(prior, action):
        _fail('identity_conflict', 'the promote action does not match the durable record')
    if action['plan'] != prior['plan'] or action['index'] != len(prior['plan']):
        _fail('invalid_action', 'the promote action is not the durable plan\'s final step')
    promoted = _transition(value, task)
    return {'schema': SCHEMA, 'manifest': promoted, 'publication': prior}


def _transition(value, task):
    try:
        return parallel_state.transition_task(value, task, 'integrating', 'published',
                                              {'decision': None})
    except parallel_state.StateError as exc:
        raise PublicationError(exc.kind, exc.message)


def _seed(action):
    record = {'schema': SCHEMA}
    record.update(_identity_of(action))
    record.update({'plan': list(action['plan']), 'intents': [], 'receipts': [],
                   'complete': False, 'proof': None})
    return record


def _supersedes(recorded, action):
    """A re-based store intent: same identity, a never-backwards expected version."""
    if recorded['kind'] not in STORE_KINDS or recorded['kind'] != action['kind']:
        return False
    old, new = recorded['payload'], action['payload']
    if old['path'] != new['path'] or old.get('key') != new.get('key'):
        return False
    return new['expected_version'] >= old['expected_version']


def _apply(value, task, action, envelope, prior):
    """The new publication record for one intent or receipt, or a refusal."""
    if prior is None:
        if action['index'] != 0:
            _fail('out_of_order', 'the first recorded action must open the plan')
        publication = _seed(action)
    else:
        if not _same_identity(prior, action):
            _fail('identity_conflict', 'the action does not match the durable publication '
                                       'record')
        if prior['plan'] != action['plan']:
            _fail('plan_conflict', 'the action plan is not the durable plan')
        publication = prior
    if envelope['state'] == 'planned':
        return _record_intent(publication, action)
    return _record_receipt(publication, action, envelope)


def _same_action(recorded, action):
    """The action equals the durable intent in everything but its plan-time `state`."""
    return {name: value for name, value in recorded.items() if name != 'state'} == \
        {name: value for name, value in action.items() if name != 'state'}


def _record_intent(publication, action):
    recorded = _intent_state(publication, action['action_id'])
    if recorded is not None:
        if _same_action(recorded, action):
            return publication
        if _supersedes(recorded, action):
            intents = [action if entry['action_id'] == action['action_id'] else entry
                       for entry in publication['intents']]
            return dict(publication, intents=intents)
        _fail('intent_conflict', 'the action {} already has a different durable '
                                 'intent'.format(action['action_id']))
    if _receipt_state(publication, action['action_id']) is not None:
        _fail('out_of_order', 'the action {} already has a receipt'.format(
            action['action_id']))
    next_index = _next_unrecorded(publication)
    if action['index'] != next_index:
        _fail('out_of_order', 'the intent for {} must follow the recorded plan order '
                              '(expected index {})'.format(action['action_id'],
                                                           next_index))
    return dict(publication, intents=publication['intents'] + [action])


def _next_unrecorded(publication):
    recorded = {entry['action_id'] for entry in publication['intents']}
    recorded.update(entry['action_id'] for entry in publication['receipts'])
    for index, action_id in enumerate(publication['plan']):
        if action_id not in recorded:
            return index
    return len(publication['plan'])


def _next_receipt_index(publication):
    recorded = {entry['action_id'] for entry in publication['receipts']}
    for index, action_id in enumerate(publication['plan']):
        if action_id not in recorded:
            return index
    return len(publication['plan'])


def _record_receipt(publication, action, envelope):
    recorded = _receipt_state(publication, action['action_id'])
    if recorded is not None:
        if recorded['state'] == envelope['state'] \
                and recorded['evidence'] == envelope['evidence']:
            return publication
        _fail('conflicting_receipt', 'the action {} already has a different confirmed '
                                     'receipt'.format(action['action_id']))
    intent = _intent_state(publication, action['action_id'])
    if intent is None:
        _fail('missing_intent', 'the action {} has no durable intent; persist the intent '
                                'before its effect'.format(action['action_id']))
    if not _same_action(intent, action):
        _fail('intent_conflict', 'the receipt action is not the durable intent for '
                                 '{}'.format(action['action_id']))
    if action['state'] == 'observed' and envelope['state'] != 'observed':
        _fail('invalid_receipt', 'an observed action is receipted as `observed`')
    if action['state'] == 'pending' and envelope['state'] != 'applied':
        _fail('invalid_receipt', 'a pending action is receipted as `applied`')
    next_index = _next_receipt_index(publication)
    if action['index'] != next_index:
        _fail('out_of_order', 'the receipt for {} must follow the recorded plan order '
                              '(expected index {})'.format(action['action_id'], next_index))
    _require_evidence(action, envelope['evidence'], envelope['state'])
    receipt = {'schema': SCHEMA, 'action_id': action['action_id'], 'kind': action['kind'],
               'state': envelope['state'], 'evidence': envelope['evidence']}
    receipts = publication['receipts'] + [receipt]
    publication = dict(publication, receipts=receipts)
    if len(receipts) == len(publication['plan']):
        publication = dict(publication, complete=True, proof=_build_proof(publication))
    return publication


def _require_evidence(action, evidence, state):
    kind = 'invalid_receipt'
    payload = action['payload']
    if action['kind'] == 'committed-documents':
        if sorted(evidence['paths']) != sorted(payload['paths']):
            _fail(kind, 'the committed-documents receipt does not list the committed '
                        'documents')
        return
    if action['kind'] in STORE_KINDS:
        version = _integer(evidence['version'], 'the receipt version', kind, 1)
        _sha256(evidence['content_sha256'], 'the receipt content_sha256', kind)
        if state == 'applied':
            if version != payload['expected_version'] + 1:
                _fail(kind, 'the applied store write created version {}, not the expected '
                            '{}'.format(version, payload['expected_version'] + 1))
        elif version < payload['expected_version']:
            _fail(kind, 'the observed store version {} is behind the expected base '
                        '{}'.format(version, payload['expected_version']))
        if action['kind'] == 'note-append':
            if evidence['number'] != payload['number'] or evidence['key'] != payload['key']:
                _fail(kind, 'the note receipt is not the planned proposal')
        return
    if action['kind'] == 'collect-evidence':
        if evidence['collected_root'] != payload['collected_root']:
            _fail(kind, 'the collection receipt names another collected root')
        if not _same_entries(evidence['entries'], payload['entries']):
            _fail(kind, 'the collection receipt does not list the observed evidence')
        return
    if action['kind'] == 'queue-done':
        if evidence['status'] != 'done':
            _fail(kind, 'the queue receipt is not `done`')
        if evidence['task_id'] != payload['task_id'] or evidence['actor'] != payload['actor']:
            _fail(kind, 'the queue receipt is not the owned row/actor')
        return


def _build_proof(publication):
    collect = next((receipt for receipt in publication['receipts']
                    if receipt['action_id'].endswith('-collect')), None)
    if collect is None:
        _fail('invalid_publication', 'the completed publication has no collection receipt')
    return {
        'schema': SCHEMA, 'ticket': publication['ticket'], 'phase': publication['phase'],
        'run_id': publication['run_id'], 'task': publication['task'],
        'attempt': publication['attempt'], 'cycle_commit': publication['cycle_commit'],
        'cycle_ref': publication['cycle_ref'],
        'result_commit': publication['result_commit'],
        'result_ref': publication['result_ref'],
        'collected_root': collect['evidence']['collected_root'],
        'evidence': [dict(entry) for entry in collect['evidence']['entries']],
        'published': True,
    }


def _finish(value, task, publication):
    if publication['complete']:
        return {'schema': SCHEMA, 'manifest': _transition(value, task),
                'publication': publication}
    return {'schema': SCHEMA, 'manifest': value, 'publication': publication}
