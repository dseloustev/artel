#!/usr/bin/env python3
"""parallel: the coordinator's explicit CLI over the parallel helpers.

    python3 scripts/parallel.py <command...> --request <JSON-file|->

One command per coordinator operation; the request is one JSON object whose keys are the
selected helper's argument names plus the explicit run context a mutation needs (the
coordinator checkout, the run identity and -- for manifest writes -- the expected revision).
The command routes to exactly one helper function; there is no dynamic lookup and no
evaluated name. The existing deterministic-script envelope goes to stdout -- `{ok, verb,
elapsed_ms, data}` on success, `{ok, verb, elapsed_ms, error: {kind, message}}` on a refusal --
with exit `0` for success, `1` for a blocked outcome (state, ownership, admission, capability,
ordering or evidence), and `2` for invalid input or an environment failure.

Commands and requests (docs/opencode-parallel.md §13.10):

    config               {repo}
    preflight            {repo, capabilities}
    owner acquire        {repo, ticket, owner}
    owner release        {repo, ticket, owner}
    run create           {repo, ticket, phase, run_id, seats, graph_digest}
    run inspect          {repo, ticket, run_id}
    run transition       {repo, ticket, run_id, expected_revision, task, expected, target,
                          fields?}
    claim prepare        {repo, ticket, run_id, expected_revision}
    claim record         {repo, ticket, run_id, expected_revision, intent_id, row}
    claim reconcile      {repo, ticket, run_id, expected_revision, rows}
    admit                {repo, ticket, run_id, tasklist, case_sensitive?}
    worktree create      {repo, assignment}
    worktree inspect     {repo, assignment}
    worktree cleanup     {repo, assignment, publication}
    integration prepare  {repo, assignment, result}
    integration apply    {repo, assignment, prepared}
    integration commit   {repo, transaction, subject}
    integration recover  {repo, transaction}
    publication next     {repo, ticket, run_id, task, observed}
    publication record   {repo, transaction, task, action_id, receipt}
    budget inspect       {repo, ticket, phase}
    budget reserve       {repo, ticket, run_id, phase, kind, scope, attempt, ordinal,
                          expected_revision}

Reads are side-effect-free: `config` and `admit` read the host config and the tasklist,
`preflight` consumes the adapter's read-only capability report plus the coordinator's branch,
cleanliness and reflog state, `run inspect`, `worktree inspect`, `publication next` and
`budget inspect` read durable state and never write. Every mutation is authoritative and
guarded:

* Manifest writes resolve the run's canonical path from the explicit coordinator checkout and
  read the manifest fresh -- a caller-supplied copy is never trusted for an effect. The
  request names the exact run (`ticket`, `run_id`) and the repository-common ticket lock must
  match it (`not_owned`, `ambiguous_owner`, `foreign_owner`), then `expected_revision` must
  equal the revision on disk (`stale_revision`); only then does the reducer run and
  `parallel_state.write_manifest` persist the next revision. A refusal leaves the bytes
  exactly as they were.
* `publication record` reads the durable integration record and requires the caller's copy to
  be exactly it -- a stale caller is `stale_publication`, never a silent clobber -- then
  persists the returned publication record into the transaction's reserved `publication` field
  **first** -- through the reviewed, ownership- and drain-checked writer
  `parallel_integration.recover_cycle`, which validates the record and normalizes its stage --
  and only then writes the promoted manifest, revision-checked, when the reducer changed it. A
  lost manifest write is recovered by the next fresh `promote` instead of repeating an external
  effect.
* `worktree`, `integration` and `owner` mutations call their helpers directly; those helpers
  own their own read-only ownership checks and perform nothing before they pass. `owner
  release` is additionally gated by the CLI itself: the exact run's manifest must prove
  quiescence (every task published, no latched stop, no unfinished owned claim) and the
  matching run state must say `completed: true` before the token proof removes the lock --
  the caller's own `completed`/`quiescent` bytes are an assertion, never the proof.
* `budget reserve` charges the run-wide correction budget (`counters.correction_rounds` in the
  coordinator's `run-state.json`, `MAX_TOTAL_CORRECTION_ROUNDS = 8`) for one stable round
  identity before a fix dispatch, atomically and idempotently; `budget inspect` reads it. The
  budget module is the only writer, and it never creates, resets or decrements the ledger.

The coordinator readiness predicates (`preflight`'s branch/cleanliness/reflog checks) and the
ticket-lock read have exactly one reviewed implementation in `parallel_integration` and
`parallel_state`; this CLI reuses those functions rather than forking their semantics, and
never modifies the reviewed component modules. No store, queue, session or tracker call
happens here -- those remain the configured coordinator's, through its own tools.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / 'hooks'))

import doc_header  # noqa: E402
import parallel_budget  # noqa: E402
import parallel_contract  # noqa: E402
import parallel_integration  # noqa: E402
import parallel_launch  # noqa: E402
import parallel_publication  # noqa: E402
import parallel_queue  # noqa: E402
import parallel_scheduler  # noqa: E402
import parallel_state  # noqa: E402
import task_grammar  # noqa: E402
import task_worktree  # noqa: E402

SCHEMA = 1
CONFIG_PATH = ('.artel', 'config.json')
RUN_DIR = ('.artel', 'run')

# Helper refusals that mean "invalid input or an environment failure" rather than "blocked":
# a malformed payload (`invalid_*`), an unreadable host file (`corrupt_*`), or a host fact the
# operation cannot run against. Everything else is a blocked outcome the coordinator
# reconciles, waits for or reports.
INVALID_KINDS = frozenset({
    'invalid_argument', 'invalid_request', 'internal_error', 'not_a_repository', 'git_failed',
    'spawn_failed', 'tasklist_malformed', 'tasklist_not_found',
})

HELPER_ERRORS = (
    parallel_budget.BudgetError,
    parallel_contract.ContractError, parallel_state.StateError, parallel_queue.QueueError,
    parallel_scheduler.SchedulerError, parallel_integration.IntegrationError,
    parallel_publication.PublicationError, task_worktree.WorktreeError,
    parallel_launch.LaunchError,
)


class CliError(ValueError):
    """A CLI-level refusal: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise CliError(kind, message)


def _exit_for(kind):
    if kind in INVALID_KINDS or kind.startswith('invalid_') or kind.startswith('corrupt_'):
        return 2
    return 1


# --- request plumbing ---------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error is a CLI refusal the envelope can carry."""

    def error(self, message):
        _fail('invalid_argument', message)


def _parser():
    parser = _Parser(prog='parallel.py', description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    def leaf(path, help_text):
        node = sub.add_parser(path[0], help=help_text)
        if len(path) == 1:
            node.add_argument('--request', required=True,
                              help='JSON request file, or `-` for stdin')
            return node
        actions = node.add_subparsers(dest='action', required=True)
        for action in path[1:]:
            child = actions.add_parser(action)
            child.add_argument('--request', required=True,
                               help='JSON request file, or `-` for stdin')
        return node

    leaf(('config',), 'resolve the host parallel configuration')
    leaf(('preflight',), 'coordinator readiness plus the capability report')
    leaf(('owner', 'acquire', 'release'), 'the repository-wide ticket lock')
    leaf(('run', 'create', 'inspect', 'transition'), 'the run manifest')
    leaf(('claim', 'prepare', 'record', 'reconcile'), 'claim intents and reconciliation')
    leaf(('admit',), 'the admission selector over the current tasklist')
    leaf(('worktree', 'create', 'inspect', 'cleanup'), 'per-task worker worktrees')
    leaf(('integration', 'prepare', 'apply', 'commit', 'recover'), 'the integration transaction')
    leaf(('publication', 'next', 'record'), 'restartable completion publication')
    leaf(('budget', 'inspect', 'reserve'), 'the run-wide correction budget')
    return parser


def _verb(args):
    parts = ['parallel', args.command]
    if getattr(args, 'action', None):
        parts.append(args.action)
    return '-'.join(parts)


def _load_request(path):
    """(request, used_stdin); an unreadable or non-object request is a CLI refusal."""
    if path == '-':
        raw = sys.stdin.read()
        used_stdin = True
    else:
        try:
            raw = Path(path).read_text(encoding='utf-8')
        except OSError as exc:
            _fail('invalid_argument', 'cannot read the request {}: {}'.format(path, exc))
        used_stdin = False
    try:
        value = json.loads(raw)
    except ValueError as exc:
        _fail('invalid_argument', 'the request is not valid JSON: {}'.format(exc))
    if not isinstance(value, dict):
        _fail('invalid_request', 'the request is not a JSON object: {!r}'.format(value))
    return value, used_stdin


def _keys(request, required, optional=()):
    missing = [name for name in required if name not in request]
    if missing:
        _fail('invalid_request', 'the request is missing {}'.format(
            ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in request if name not in set(required) | set(optional))
    if unknown:
        _fail('invalid_request', 'the request carries unknown {}'.format(
            ', '.join('`{}`'.format(name) for name in unknown)))


def _text(request, name):
    value = request[name]
    if not isinstance(value, str) or not value:
        _fail('invalid_request', '`{}` is not a non-empty string: {!r}'.format(name, value))
    return value


def _integer(request, name, minimum=None):
    value = request[name]
    if isinstance(value, bool) or not isinstance(value, int):
        _fail('invalid_request', '`{}` is not an integer: {!r}'.format(name, value))
    if minimum is not None and value < minimum:
        _fail('invalid_request', '`{}` must be at least {}: {!r}'.format(name, minimum, value))
    return value


def _phase(request, name):
    value = request[name]
    if value is None:
        return None
    return _integer(request, name, 1)


def _boolean(request, name, default=None):
    value = request.get(name, default)
    if not isinstance(value, bool):
        _fail('invalid_request', '`{}` is not a boolean: {!r}'.format(name, value))
    return value


def _read_document(path, what, stdin_used):
    if path == '-':
        if stdin_used:
            _fail('invalid_argument', 'the request and the {} cannot both read stdin'.format(
                what))
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail(what + '_not_found', '{} not found: {}'.format(what, path))
    except (OSError, UnicodeDecodeError) as exc:
        _fail('invalid_argument', 'cannot read the {} {}: {}'.format(what, path, exc))


# --- the run's canonical host paths -------------------------------------------------------

def _manifest_path(repo, ticket, run_id):
    """The run manifest's conventional path under the explicit coordinator checkout (§13.1)."""
    return Path(repo).joinpath(*RUN_DIR, ticket, 'parallel', run_id, 'manifest.json')


def _read_manifest(repo, ticket, run_id):
    return parallel_state.read_manifest(str(_manifest_path(repo, ticket, run_id)))


def _check_identity(manifest, ticket, run_id):
    if manifest['ticket'] != ticket or manifest['run_id'] != run_id:
        _fail('invalid_manifest', 'the manifest at the run path is not this run\'s: {!r}'.format(
            {name: manifest[name] for name in ('ticket', 'run_id')}))


def _require_owner(repo, ticket, run_id, phase):
    """The repository-common ticket lock must be exactly this run's (read-only).

    The lock reader is the reviewed one (`parallel_state` owns the lock); this CLI only
    matches the run identity against it before any manifest effect.
    """
    stored = parallel_state._read_lock(parallel_state._lock_path(repo, ticket), ticket)
    if stored is None:
        _fail('not_owned', 'the ticket {} is not owned: acquire the lock before writing its '
                           'run state'.format(ticket))
    if stored['run_id'] != run_id or stored['phase'] != phase:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}), not by run {} '
                               '(phase {})'.format(ticket, stored['run_id'], stored['phase'],
                                                   run_id, phase))
    return stored


def _require_revision(manifest, expected_revision):
    if manifest['revision'] != expected_revision:
        _fail('stale_revision', 'the manifest is at revision {}, not the expected {}'.format(
            manifest['revision'], expected_revision))


def _config_seats(repo):
    """`parallel.seats` from the host config; a missing file is the serial default."""
    path = Path(repo).joinpath(*CONFIG_PATH)
    if not path.is_file():
        return parallel_contract.resolve_config({})['seats']
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _fail('invalid_config', 'unreadable {}: {}'.format(path, exc))
    if not isinstance(config, dict):
        _fail('invalid_config', 'the config root is not an object: {!r}'.format(config))
    return parallel_contract.resolve_config(config)['seats']


def _case_sensitive(request):
    if 'case_sensitive' in request:
        return _boolean(request, 'case_sensitive')
    return os.path.normcase('A') == 'A'


# --- commands -----------------------------------------------------------------------------

def cmd_config(request, _stdin_used):
    _keys(request, ('repo',))
    return {'seats': _config_seats(_text(request, 'repo'))}


def cmd_preflight(request, _stdin_used):
    _keys(request, ('repo', 'capabilities'))
    repo = _text(request, 'repo')
    report = parallel_launch.capability_report(request['capabilities'])
    if not report['ok']:
        _fail(report['kind'] or 'unsupported_api', '; '.join(report['reasons']))
    root = parallel_integration._top_level(repo)
    head, branch = parallel_integration._coordinator_preflight(repo)
    parallel_integration._require_clean_coordinator(root)
    return {'schema': SCHEMA, 'ok': True, 'head': head, 'branch': branch,
            'capabilities': report}


def cmd_owner_acquire(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'owner'))
    return parallel_state.acquire_ticket(_text(request, 'repo'), _text(request, 'ticket'),
                                         request['owner'])


def _release_identity(owner):
    """The release request's run identity and token proof, or `invalid_request`."""
    if not isinstance(owner, dict):
        _fail('invalid_request', '`owner` is not an object: {!r}'.format(owner))
    run_id = owner.get('run_id')
    if not isinstance(run_id, str) or not run_id:
        _fail('invalid_request', '`owner.run_id` is not a non-empty string: {!r}'.format(
            run_id))
    phase = owner.get('phase')
    if phase is not None and (isinstance(phase, bool) or not isinstance(phase, int)
                              or phase < 1):
        _fail('invalid_request', '`owner.phase` is not an integer >= 1 or null: {!r}'.format(
            phase))
    token = owner.get('token')
    if not isinstance(token, str) or not token:
        _fail('invalid_request', '`owner.token` is not a non-empty string: {!r}'.format(token))
    return run_id, phase, token


def _require_quiescent(manifest):
    """The exact run's manifest must prove quiescence before its lock can be released.

    A caller's `completed`/`quiescent` bytes are an assertion, never the proof: every task
    must be durably published, no reconciliation stop may be latched, and no claim the run
    still owns may be unfinished. Nothing is reset, published or discarded to make this pass.
    """
    if manifest['stop'] is not None:
        _fail('premature_release', 'the run stopped admission for reconciliation ({}); the '
                                   'lock is retained'.format(
                                       manifest['stop'].get('reason') or 'no reason recorded'))
    unfinished = sorted(task for task, record in manifest['tasks'].items()
                        if record['state'] != 'published')
    if unfinished:
        _fail('premature_release', 'the run is not quiescent: {} not published; the lock is '
                                   'retained'.format(', '.join(unfinished)))
    claims = []
    for intent in manifest['claims']['intents']:
        if intent['state'] == 'pending':
            claims.append(intent['intent_id'])
        elif intent['state'] == 'mapped':
            task = intent['row'].get('task') if isinstance(intent['row'], dict) else None
            record = manifest['tasks'].get(task)
            if record is None or record['state'] != 'published':
                claims.append(intent['intent_id'])
    if claims:
        _fail('premature_release', 'the run still holds an unfinished claim ({}); the lock is '
                                   'retained'.format(', '.join(claims)))


def cmd_owner_release(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'owner'))
    repo = _text(request, 'repo')
    ticket = _text(request, 'ticket')
    owner = request['owner']
    run_id, phase, token = _release_identity(owner)
    stored = _require_owner(repo, ticket, run_id, phase)
    if stored['token'] != token:
        _fail('foreign_owner', 'the ticket {} is held under another token; only the acquiring '
                               'run releases it'.format(ticket))
    manifest = _read_manifest(repo, ticket, run_id)
    if manifest['ticket'] != ticket or manifest['run_id'] != run_id \
            or manifest['phase'] != phase:
        _fail('manifest_mismatch', 'the run manifest is not this run\'s: {!r}'.format(
            {name: manifest[name] for name in ('ticket', 'run_id', 'phase')}))
    _require_quiescent(manifest)
    _path, state = parallel_budget.read_run_state(repo, ticket, phase)
    if state.get('completed') is not True:
        _fail('premature_release', 'the run state says completed={!r}; the final gates and the '
                                   'completion update come before release; the lock is '
                                   'retained'.format(state.get('completed')))
    if state.get('pause_reason') is not None:
        _fail('premature_release', 'the run is paused ({!r}); a pause retains ownership and '
                                   'recovery state'.format(state.get('pause_reason')))
    return parallel_state.release_ticket(repo, ticket, owner)


def cmd_run_create(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'phase', 'run_id', 'seats', 'graph_digest'))
    repo = _text(request, 'repo')
    ticket = _text(request, 'ticket')
    phase = _phase(request, 'phase')
    run_id = _text(request, 'run_id')
    seats = _integer(request, 'seats', 1)
    graph_digest = _text(request, 'graph_digest')
    _require_owner(repo, ticket, run_id, phase)
    if seats != _config_seats(repo):
        _fail('invalid_seats', 'the requested seats {} does not match the host\'s '
                               '`parallel.seats`'.format(seats))
    manifest = parallel_state.new_run(ticket, phase, run_id, seats, graph_digest)
    return parallel_state.write_manifest(str(_manifest_path(repo, ticket, run_id)), manifest, 0)


def cmd_run_inspect(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id'))
    return _read_manifest(_text(request, 'repo'), _text(request, 'ticket'),
                          _text(request, 'run_id'))


def _authoritative_manifest(request):
    """The fresh manifest for a run mutation, with its exact owner and revision context."""
    repo = _text(request, 'repo')
    ticket = _text(request, 'ticket')
    run_id = _text(request, 'run_id')
    expected_revision = _integer(request, 'expected_revision', 0)
    manifest = _read_manifest(repo, ticket, run_id)
    _check_identity(manifest, ticket, run_id)
    _require_owner(repo, ticket, run_id, manifest['phase'])
    _require_revision(manifest, expected_revision)
    return repo, ticket, run_id, expected_revision, manifest


def _persist_manifest(repo, ticket, run_id, value, expected_revision):
    return parallel_state.write_manifest(str(_manifest_path(repo, ticket, run_id)), value,
                                         expected_revision)


def cmd_run_transition(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'expected_revision', 'task', 'expected',
                    'target'), optional=('fields',))
    task = _text(request, 'task')
    expected = request['expected']
    if expected is not None and not isinstance(expected, str):
        _fail('invalid_request', '`expected` is not a state name or null: {!r}'.format(expected))
    target = _text(request, 'target')
    fields = request.get('fields', {})
    if not isinstance(fields, dict):
        _fail('invalid_request', '`fields` is not an object: {!r}'.format(fields))
    repo, ticket, run_id, revision, manifest = _authoritative_manifest(request)
    updated = parallel_state.transition_task(manifest, task, expected, target, fields)
    return _persist_manifest(repo, ticket, run_id, updated, revision)


def cmd_claim_prepare(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'expected_revision'))
    repo, ticket, run_id, revision, manifest = _authoritative_manifest(request)
    result = parallel_queue.begin_claim(manifest)
    written = _persist_manifest(repo, ticket, run_id, result['manifest'], revision)
    return {'schema': SCHEMA, 'manifest': written, 'intent': result['intent']}


def cmd_claim_record(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'expected_revision', 'intent_id', 'row'))
    intent_id = _text(request, 'intent_id')
    repo, ticket, run_id, revision, manifest = _authoritative_manifest(request)
    result = parallel_queue.record_claim(manifest, intent_id, request['row'])
    written = _persist_manifest(repo, ticket, run_id, result['manifest'], revision)
    return {'schema': SCHEMA, 'manifest': written, 'action': result['action']}


def cmd_claim_reconcile(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'expected_revision', 'rows'))
    rows = request['rows']
    if not isinstance(rows, list):
        _fail('invalid_request', '`rows` is not an array: {!r}'.format(rows))
    repo, ticket, run_id, revision, manifest = _authoritative_manifest(request)
    result = parallel_queue.reconcile_claims(manifest, rows)
    written = _persist_manifest(repo, ticket, run_id, result['manifest'], revision)
    return {'schema': SCHEMA, 'manifest': written, 'actions': result['actions']}


def cmd_admit(request, stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'tasklist'), optional=('case_sensitive',))
    repo = _text(request, 'repo')
    ticket = _text(request, 'ticket')
    run_id = _text(request, 'run_id')
    case_sensitive = _case_sensitive(request)
    manifest = _read_manifest(repo, ticket, run_id)
    _check_identity(manifest, ticket, run_id)
    text = _read_document(_text(request, 'tasklist'), 'tasklist', stdin_used)
    body = doc_header.body(text)
    if not task_grammar.is_task_format(body):
        _fail('tasklist_malformed', 'the tasklist carries no `### Task <N>.<m>:` block; '
                                    'parallel admission reads the task grammar')
    offset = text[:len(text) - len(body)].count('\n')
    iterations, problems, _warnings = task_grammar.parse(body, line_offset=offset)
    if problems:
        first = problems[0]
        _fail('tasklist_malformed', 'the tasklist has {} problem(s); first: line {}: '
                                    '{}'.format(len(problems), first['line'],
                                                first['message']))
    return parallel_scheduler.select_admissions(iterations, manifest, manifest['seats'],
                                                case_sensitive)


def cmd_worktree_create(request, _stdin_used):
    _keys(request, ('repo', 'assignment'))
    return task_worktree.create_worker(_text(request, 'repo'), request['assignment'])


def cmd_worktree_inspect(request, _stdin_used):
    _keys(request, ('repo', 'assignment'))
    return task_worktree.inspect_worker(_text(request, 'repo'), request['assignment'])


def cmd_worktree_cleanup(request, _stdin_used):
    _keys(request, ('repo', 'assignment', 'publication'))
    return task_worktree.cleanup_worker(_text(request, 'repo'), request['assignment'],
                                        request['publication'])


def cmd_integration_prepare(request, _stdin_used):
    _keys(request, ('repo', 'assignment', 'result'))
    return parallel_integration.prepare_transport(_text(request, 'repo'),
                                                  request['assignment'], request['result'])


def cmd_integration_apply(request, _stdin_used):
    _keys(request, ('repo', 'assignment', 'prepared'))
    return parallel_integration.apply_transport(_text(request, 'repo'), request['assignment'],
                                                request['prepared'])


def cmd_integration_commit(request, _stdin_used):
    _keys(request, ('repo', 'transaction', 'subject'))
    return parallel_integration.commit_cycle(_text(request, 'repo'), request['transaction'],
                                             _text(request, 'subject'))


def cmd_integration_recover(request, _stdin_used):
    _keys(request, ('repo', 'transaction'))
    return parallel_integration.recover_cycle(_text(request, 'repo'), request['transaction'])


def cmd_publication_next(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'task', 'observed'))
    repo = _text(request, 'repo')
    ticket = _text(request, 'ticket')
    run_id = _text(request, 'run_id')
    task = _text(request, 'task')
    manifest = _read_manifest(repo, ticket, run_id)
    _check_identity(manifest, ticket, run_id)
    return parallel_publication.next_publication_actions(manifest, task, request['observed'])


def _read_record(repo, identity):
    """The durable integration record for `identity`, or a typed refusal.

    The record has no revision field: its publication field is the concurrency guard, so the
    CLI reads the durable record and requires the caller's copy to be exactly it before any
    effect -- a stale caller can never clobber a newer record.
    """
    path = parallel_integration.record_path(repo, identity)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail('not_committed', 'no integration record at {}; the cycle is not committed'.format(
            path))
    except (OSError, UnicodeDecodeError) as exc:
        _fail('corrupt_record', 'the integration record at {} cannot be read: {}'.format(
            path, exc))
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail('corrupt_record', 'the integration record at {} is not valid JSON: {}'.format(
            path, exc))
    if not isinstance(value, dict):
        _fail('corrupt_record', 'the integration record at {} is not an object'.format(path))
    for name in ('ticket', 'task', 'run_id', 'attempt', 'transaction_id'):
        if value.get(name) != identity.get(name):
            _fail('record_mismatch', 'the integration record at {} belongs to another '
                                     'attempt'.format(path))
    return value


def cmd_publication_record(request, _stdin_used):
    _keys(request, ('repo', 'transaction', 'task', 'action_id', 'receipt'))
    repo = _text(request, 'repo')
    transaction = request['transaction']
    if not isinstance(transaction, dict):
        _fail('invalid_request', '`transaction` is not an object: {!r}'.format(transaction))
    for name in ('ticket', 'run_id'):
        if not isinstance(transaction.get(name), str) or not transaction[name]:
            _fail('invalid_request', '`transaction.{}` is not a non-empty string: {!r}'.format(
                name, transaction.get(name)))
    if isinstance(transaction.get('attempt'), bool) \
            or not isinstance(transaction.get('attempt'), int) \
            or transaction['attempt'] < 1:
        _fail('invalid_request', '`transaction.attempt` is not an integer >= 1: {!r}'.format(
            transaction.get('attempt')))
    if not isinstance(transaction.get('transaction_id'), str) \
            or not transaction['transaction_id']:
        _fail('invalid_request', '`transaction.transaction_id` is not a non-empty string: '
                                 '{!r}'.format(transaction.get('transaction_id')))
    task = _text(request, 'task')
    receipt = request['receipt']
    if not isinstance(receipt, dict):
        _fail('invalid_request', '`receipt` is not an object: {!r}'.format(receipt))
    identity = {name: transaction.get(name)
                for name in ('ticket', 'task', 'run_id', 'attempt', 'transaction_id')}
    identity['task'] = task
    durable = _read_record(repo, identity)
    _require_owner(repo, durable['ticket'], durable['run_id'], durable['phase'])
    if durable.get('publication') != receipt.get('publication'):
        _fail('stale_publication', 'the receipt was planned against publication {!r}, not the '
                                   'durable {!r}; re-plan from the fresh record'.format(
                                       receipt.get('publication'),
                                       durable.get('publication')))
    manifest = _read_manifest(repo, transaction['ticket'], transaction['run_id'])
    _check_identity(manifest, transaction['ticket'], transaction['run_id'])
    result = parallel_publication.record_publication(manifest, task, _text(request, 'action_id'),
                                                     receipt)
    # Persist the returned publication into the durable transaction's reserved field FIRST: the
    # reviewed writer validates the record, matches ownership and the integrating latch and
    # writes atomically. Only then may the manifest move.
    stored = durable
    if stored.get('publication') != result['publication']:
        stored = parallel_integration.recover_cycle(repo, dict(stored,
                                                               publication=result['publication']))
    written = manifest
    if result['manifest'] != manifest:
        written = parallel_state.write_manifest(
            str(_manifest_path(repo, transaction['ticket'], transaction['run_id'])),
            result['manifest'], manifest['revision'])
    return {'schema': SCHEMA, 'manifest': written, 'publication': stored['publication'],
            'transaction': stored}


def cmd_budget_inspect(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'phase'))
    return parallel_budget.inspect(_text(request, 'repo'), _text(request, 'ticket'),
                                   _phase(request, 'phase'))


def cmd_budget_reserve(request, _stdin_used):
    _keys(request, ('repo', 'ticket', 'run_id', 'phase', 'kind', 'scope', 'attempt',
                    'ordinal', 'expected_revision'))
    return parallel_budget.reserve(
        _text(request, 'repo'), _text(request, 'ticket'), _text(request, 'run_id'),
        _phase(request, 'phase'), _text(request, 'kind'), _text(request, 'scope'),
        _integer(request, 'attempt', 1), _integer(request, 'ordinal', 1),
        _integer(request, 'expected_revision', 0))


DISPATCH = {
    ('config',): cmd_config,
    ('preflight',): cmd_preflight,
    ('owner', 'acquire'): cmd_owner_acquire,
    ('owner', 'release'): cmd_owner_release,
    ('run', 'create'): cmd_run_create,
    ('run', 'inspect'): cmd_run_inspect,
    ('run', 'transition'): cmd_run_transition,
    ('claim', 'prepare'): cmd_claim_prepare,
    ('claim', 'record'): cmd_claim_record,
    ('claim', 'reconcile'): cmd_claim_reconcile,
    ('admit',): cmd_admit,
    ('worktree', 'create'): cmd_worktree_create,
    ('worktree', 'inspect'): cmd_worktree_inspect,
    ('worktree', 'cleanup'): cmd_worktree_cleanup,
    ('integration', 'prepare'): cmd_integration_prepare,
    ('integration', 'apply'): cmd_integration_apply,
    ('integration', 'commit'): cmd_integration_commit,
    ('integration', 'recover'): cmd_integration_recover,
    ('publication', 'next'): cmd_publication_next,
    ('publication', 'record'): cmd_publication_record,
    ('budget', 'inspect'): cmd_budget_inspect,
    ('budget', 'reserve'): cmd_budget_reserve,
}


def _emit(value, code):
    print(json.dumps(value))
    return code


def _envelope(ok, verb, elapsed_ms, data=None, error=None):
    out = {'ok': ok, 'verb': verb, 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return out


def main(argv=None):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    verb = 'parallel'
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
        verb = _verb(args)
        key = (args.command,) if not getattr(args, 'action', None) \
            else (args.command, args.action)
        handler = DISPATCH.get(key)
        if handler is None:  # unreachable: the parser builds exactly these paths
            _fail('invalid_argument', 'unknown command: {}'.format(' '.join(key)))
        request, stdin_used = _load_request(args.request)
        data = handler(request, stdin_used)
    except CliError as exc:
        return _emit(_envelope(False, verb, elapsed(), error={'kind': exc.kind,
                                                              'message': exc.message}),
                     _exit_for(exc.kind))
    except HELPER_ERRORS as exc:
        return _emit(_envelope(False, verb, elapsed(), error={'kind': exc.kind,
                                                              'message': exc.message}),
                     _exit_for(exc.kind))
    except Exception as exc:  # never mask an internal failure as a policy refusal
        return _emit(_envelope(False, verb, elapsed(),
                               error={'kind': 'internal_error', 'message': str(exc)}), 2)
    return _emit(_envelope(True, verb, elapsed(), data=data), 0)


if __name__ == '__main__':
    sys.exit(main())
