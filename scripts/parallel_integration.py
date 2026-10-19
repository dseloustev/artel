#!/usr/bin/env python3
"""parallel-integration: reviewed transport and serialized integration.

    prepare_transport(repo, assignment, result) -> transaction
    apply_transport(repo, assignment, prepared) -> transaction
    commit_cycle(repo, transaction, subject) -> transaction
    recover_cycle(repo, transaction) -> transaction

Contract: docs/opencode-parallel.md §13.8 (the integration transaction and its shapes),
§4-§6 (assignment, result, task states), §13.1-§13.7 (the manifest, ownership, the
scheduler's drain and the worktree lifecycle), the approved serialized integration section
of the parallel plan, and the Task 8 handoff.

`repo` is the coordinator checkout -- the one whose branch carries the final cycle commit
and whose `.artel/run/<TICKET_ID>/parallel/<run-id>/` holds the run manifest and this
module's transaction record. Every operation is rooted there; the worker tree is reached
only through the validated assignment, and nothing here moves, stashes, switches or cleans
either checkout. The one integration transaction per task mutates the ticket branch: the
coordinator creates a normal hooked, code-only transport commit IN the worker tree, applies
it with `cherry-pick --no-commit`, runs the task gate on the integrated tree and lands
exactly one conventional final cycle commit whose `GIT_REFLOG_ACTION` is the transaction id.

Durable shapes:

    transaction (schema 1, JSON-safe, exactly these keys)
      schema, transaction_id, ticket, phase, task, run_id, attempt, base_commit,
      worker_root, repo_root, branch, pre_head, transport_commit, result_ref, route,
      review, changed_files, excluded, diff, documents, drain, publication, stage,
      subject, cycle_commit, verify, recovery, trees, created_at, updated_at, record

    trees: {transport, landing, integrity} -- the accepted transport tree and the exact
    landing tree are pinned before the commit they bind, and `integrity` records a hook or
    gate mutation (`hook_mutated`/`gate_mutated`) with the expected/actual trees. Recovery
    validates the landing pin, so a bad-hook commit cannot be published after a lost
    receipt. The integrated gate runs on the deduplicated union of the changed paths and
    the worker marker's declared tests.

    stage: preparing -> prepared -> applying -> applied -> committing -> committed
           (side: conflicted, apply_failed, failed, commit_refused, recovered)

The record is persisted at
`<repo>/.artel/run/<TICKET_ID>/parallel/<run-id>/integration/<task>-a<attempt>.json`
before every Git side effect, atomically, and every function returns exactly the record it
persisted. `record_path(repo, identity)` exposes that location for the coordinator.

Ownership. Every operation matches the repository-common ticket lock (§13.4) read-only and
refuses a missing (`not_owned`), unreadable (`ambiguous_owner`) or foreign (`foreign_owner`)
record -- this module never acquires, releases or reclaims it. The worker root must be the
assignment's own marker-validated root in the same repository, and the run manifest at its
conventional path must name this run and hold the task at `integrating` -- the durable
write-ahead state the coordinator persists before integration, which §13.6 treats as the
run's admission latch.

Drain. The transport and the final cycle commit run the host's normal commit hooks, shared
external effects the coordinator cannot prove worker-local; no sibling may still be returning
while they run. The coordinator persists its own `prepared -> integrating` transition first --
the scheduler's durable admission latch (§13.6), so nothing new starts and no reservation
resumes for the whole transaction -- and then waits: `prepare_transport`, `apply_transport` and
`commit_cycle` refuse while any other task is `preparing`, `running`, `verifying/reviewing` or
`integrating` (`drain_required`). A sibling at `prepared` is already at safe return and parks,
recorded in `drain.waiting` with the waiting `reserved` reservations; it is never relaunched,
blocked, cancelled or published to make room, and normal admission resumes once no `integrating`
record remains.

Transport. The transport commit stages exactly the result's actual code diff, computed from
a working-tree snapshot of the worker root (`review_package` with an explicit root) against
the recorded base -- both sides of a rename included. Paths under `.artel/` and the ticket's
spec trail are excluded (the worker's shared-state edits stay dirty and inspectable); every
other actual path must equal the result's `changed_files`. A path outside the assignment's
declared `files` must be recorded in a deviation; a major deviation refuses, and an
undeclared change floors the effective route to `full`, so a light review refuses
(`route_floor`). The commit runs the host's hooks; a refusal leaves the index, HEAD and the
record exactly as they were (`commit_refused`).

Apply. `cherry-pick --no-commit` onto the coordinator at the recorded pre-HEAD, whose tree
must be completely clean outside the cycle -- any tracked, staged or untracked change refuses
before the mutation and preserves every byte, the index, the stash and HEAD; nothing is reset,
cleaned, stashed or staged to pass. A conflict leaves git's own conflict state and the record's
`conflicted` stage inspectable and stops admission; nothing is auto-resolved.

Final commit. The integrated tree is checked with the existing task gate
(`scripts/verify.py task`, run in the coordinator root) before anything is committed, and the
tree it checks is exactly the tree that can land: after staging the transported paths and the
transaction's `documents`, the whole nonignored tree may carry nothing outside those two sets
(`coordinator_dirty`), including anything introduced after a successful apply. A red gate
(`task_gate_failed`) or an environment error (`task_gate_error`) commits nothing. A code fix
stays inside the transported paths; a new path is a new attempt. On the files path the tick and
ordered notes are the pending `documents` and land in the same commit as the code, written after
a successful apply; on the artifact-store path `documents` is empty and Task 9 publishes those
after the code commit. The index must carry no other path (`foreign_staged`); a person's staged
or dirty work is never swept in, never unstaged and never reset. The subject is validated as a
conventional, subject-only message with no task id and no trailer, and `GIT_REFLOG_ACTION`
carries the transaction id, so the commit is attributable without reading its subject.

Recovery. A final commit may have landed before its receipt was persisted.
`recover_cycle` matches the recorded pre-HEAD plus the transaction-marked branch reflog
entry (also when the coordinator itself is linked) and returns the landed commit exactly
once; a missing, expired, ambiguous or unreachable evidence refuses (`reflogs_unusable`,
`expired_evidence`, `ambiguous_evidence`, `unreachable_evidence`) and never re-commits.
`commit_cycle` runs that recognition first, so repeating it never creates a second cycle
commit. `prepare_transport` does not: once the record is `applying`, `applied` or
`committing`, recovery is stage-aware — run `recover_cycle` first and never restart an
advanced transaction through prepare, which rewinds the recorded stage to `prepared` with a
null `cycle_commit` even when the final commit landed.

Every failure raises `IntegrationError`, a `ValueError` carrying the `kind` a CLI maps into
its error envelope; nothing is coerced and nothing is dropped.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import parallel_contract
import parallel_state
import review_package
import task_worktree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common  # noqa: E402

SCHEMA = 1
REF_ROOT = 'refs/artel/parallel'
RECORD_DIR = 'integration'
TRANSPORT_SUBJECT = 'chore: parallel transport'
DEFAULT_SPECS_DIR = 'specs/.current'
VERIFY_SCRIPT = Path(__file__).resolve().parent / 'verify.py'
OUTPUT_TAIL = 2000
# §13.8: the transaction's life; the side stages keep a refusal inspectable.
STAGES = ('preparing', 'prepared', 'applying', 'applied', 'conflicted', 'apply_failed',
          'committing', 'failed', 'commit_refused', 'committed', 'recovered')
# §13.6/§13.8: the work that must have returned before an integration effect. A sibling at
# `prepared` is already at safe return (its only forward exit is `integrating`), so it parks;
# only work still returning, still being verified, or integrating blocks the hooks.
IN_FLIGHT_STATES = ('preparing', 'running', 'verifying/reviewing', 'integrating')
# Parked safe-return work: waiting reservations and prepared results, never relaunched here.
PARKED_STATES = ('reserved', 'prepared')
# tail.md `## Task commits`: the types a cycle commit may carry.
SUBJECT_TYPES = ('feat', 'fix', 'refactor', 'test', 'docs', 'chore')
SUBJECT_RE = re.compile(r'^(?:{})(?:\([^()\s]+\))?: \S.*\Z'.format('|'.join(SUBJECT_TYPES)))
TRANSACTION_KEYS = (
    'schema', 'transaction_id', 'ticket', 'phase', 'task', 'run_id', 'attempt',
    'base_commit', 'worker_root', 'repo_root', 'branch', 'pre_head', 'transport_commit',
    'result_ref', 'route', 'review', 'changed_files', 'excluded', 'diff', 'documents',
    'drain', 'publication', 'stage', 'subject', 'cycle_commit', 'verify', 'recovery',
    'trees', 'created_at', 'updated_at', 'record',
)
DIFF_KEYS = ('base_commit', 'commit', 'sha256', 'files')
DRAIN_KEYS = ('in_flight', 'waiting', 'checked_at')
RECOVERY_KEYS = ('state', 'checked_at')
# The content identity the accepted work is bound to: the tree of the retained transport
# commit, the exact tree the final cycle commit must carry, and the durable evidence of an
# integrity refusal (a hook or gate that changed either tree after acceptance).
TREES_KEYS = ('transport', 'landing', 'integrity')
INTEGRITY_KEYS = ('stage', 'expected', 'actual', 'detail', 'checked_at')
# The two paths `create_worker` writes: structural, never a worker's shared-state edit.
LIFECYCLE_PATHS = ('.artel/parallel-worker.json', '.artel/config.json')
# A commit hook, a setup command or a git call must never inherit a redirection that points
# it at another checkout's index, work tree or object store.
GIT_REDIRECTS = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_PREFIX')


class IntegrationError(ValueError):
    """An integration violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise IntegrationError(kind, message)


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


def _text(value, what, kind):
    if not isinstance(value, str):
        _fail(kind, '{} is not a string: {!r}'.format(what, value))
    if not value or value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _commit(value, what, kind):
    if value is not None and (not isinstance(value, str)
                              or not parallel_contract.COMMIT_RE.match(value)):
        _fail(kind, '{} is not a full 40-hex commit id: {!r}'.format(what, value))
    return value


def _relative(value, what, kind):
    _text(value, what, kind)
    if os.path.isabs(value):
        _fail(kind, '{} is not a relative path: {!r}'.format(what, value))
    normalized = os.path.normpath(value)
    if normalized == '..' or normalized.startswith('..' + os.sep):
        _fail(kind, '{} escapes its root: {!r}'.format(what, value))
    if ',' in value:
        _fail(kind, '{} carries a comma, which the task gate\'s file list cannot express: '
                    '{!r}'.format(what, value))
    return value


def _paths(value, what, kind):
    if not isinstance(value, list):
        _fail(kind, '{} is not an array: {!r}'.format(what, value))
    for entry in value:
        _relative(entry, '{} entry'.format(what), kind)
    return value


def _trees(value, kind):
    """The transaction's pinned content identity, or `invalid_transaction`."""
    _object(value, 'trees', kind)
    _keys(value, TREES_KEYS, 'trees', kind)
    _commit(value['transport'], 'trees.transport', kind)
    _commit(value['landing'], 'trees.landing', kind)
    if value['integrity'] is not None:
        _object(value['integrity'], 'trees.integrity', kind)
        _keys(value['integrity'], INTEGRITY_KEYS, 'trees.integrity', kind)
        _text(value['integrity']['stage'], 'trees.integrity.stage', kind)
        _commit(value['integrity']['expected'], 'trees.integrity.expected', kind)
        _commit(value['integrity']['actual'], 'trees.integrity.actual', kind)
        _text(value['integrity']['detail'], 'trees.integrity.detail', kind)
        _text(value['integrity']['checked_at'], 'trees.integrity.checked_at', kind)
    return value


def _assignment(value):
    """The schema-1 assignment of §4, or `invalid_assignment`."""
    try:
        return parallel_contract.validate_assignment(value)
    except parallel_contract.ContractError as exc:
        raise IntegrationError(exc.kind, exc.message)


def _result(value, assignment):
    """The schema-1 result of §5 against its assignment, or `invalid_result`."""
    try:
        return parallel_contract.validate_result(value, assignment)
    except parallel_contract.ContractError as exc:
        raise IntegrationError(exc.kind, exc.message)


def _utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _tail(text):
    return text[-OUTPUT_TAIL:] if len(text) > OUTPUT_TAIL else text


def _atomic_write(path, text):
    path = Path(path)
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


# --- git plumbing -------------------------------------------------------------------------

def _env():
    return {name: value for name, value in os.environ.items()
            if name not in GIT_REDIRECTS}


def _run(argv, cwd, check=True, env=None):
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                          env=env if env is not None else _env())
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()
        _fail('git_failed', '{} failed in {}: {}'.format(
            ' '.join(str(a) for a in argv), cwd, detail))
    return proc


def _git(root, *args, **kwargs):
    check = kwargs.pop('check', True)
    env = kwargs.pop('env', None)
    return _run(['git', '-C', str(root)] + [str(a) for a in args], cwd=root, check=check,
                env=env)


def _out(root, *args):
    return _git(root, *args).stdout.strip()


def _common_dir(repo):
    """The repository-common Git directory, shared by every linked worktree."""
    try:
        proc = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute',
                               '--git-common-dir'], cwd=str(repo), capture_output=True,
                              text=True, env=_env())
    except OSError as exc:
        _fail('not_a_repository', 'cannot run git in {}: {}'.format(repo, exc))
    if proc.returncode != 0:
        _fail('not_a_repository', 'git rev-parse --git-common-dir failed in {}: {}'.format(
            repo, (proc.stderr or proc.stdout).strip()))
    return Path(proc.stdout.strip())


def _top_level(repo):
    proc = _git(repo, 'rev-parse', '--show-toplevel', check=False)
    if proc.returncode != 0:
        _fail('not_a_repository', '{} is not a Git checkout: {}'.format(
            repo, (proc.stderr or proc.stdout).strip()))
    return Path(os.path.realpath(proc.stdout.strip()))


def _ref_value(repo, name):
    proc = _git(repo, 'rev-parse', '--verify', '--quiet', name, check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _commit_exists(repo, commit):
    return _git(repo, 'cat-file', '-e', str(commit) + '^{commit}', check=False).returncode == 0


def _is_ancestor(repo, older, newer):
    return _git(repo, 'merge-base', '--is-ancestor', str(older), str(newer),
                check=False).returncode == 0


def _parent(repo, commit):
    proc = _git(repo, 'rev-parse', '--verify', '--quiet', str(commit) + '^', check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _subject_of(repo, commit):
    return _git(repo, 'log', '-1', '--format=%s', str(commit)).stdout.strip()


def _zsplit(text):
    return sorted(name for name in text.split('\0') if name)


def _staged_paths(root):
    return _zsplit(_git(root, 'diff', '--cached', '--name-only', '--no-renames', '-z').stdout)


def _unstaged_paths(root):
    return _zsplit(_git(root, 'diff', '--name-only', '--no-renames', '-z').stdout)


def _untracked_paths(root):
    return _zsplit(_git(root, 'ls-files', '--others', '--exclude-standard', '-z').stdout)


def _unmerged_paths(root):
    return _zsplit(_git(root, 'ls-files', '-u', '-z').stdout)


def _tracked_in_index(root, path):
    out = _git(root, 'ls-files', '-z', '--', ':(literal)' + path).stdout
    return bool(out.split('\0')[0])


def _reflog(root, branch):
    """[(commit, message)] newest first; an unreadable branch reflog is refused."""
    proc = _git(root, 'reflog', 'show', '--format=%H|%gs', branch, check=False)
    if proc.returncode != 0:
        _fail('reflogs_unusable', 'the branch reflog of {} cannot be read in {}: {}'.format(
            branch, root, (proc.stderr or proc.stdout).strip()))
    entries = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        commit, _, message = line.partition('|')
        entries.append((commit.strip(), message.strip()))
    return entries


def _diff_paths(root, older, newer):
    """Both rename sides of `older..newer`, sorted, with the raw (never quoted) names."""
    return _zsplit(_git(root, 'diff-tree', '-r', '--no-renames', '--name-only', '-z',
                        older, newer).stdout)


def _tree_of(root, commit):
    """The tree object id a commit carries."""
    return _out(root, 'rev-parse', str(commit) + '^{tree}')


def _write_tree(root):
    """The tree object id of the current index: exactly what a commit would land."""
    return _out(root, 'write-tree')


def _integrity(stage, expected, actual, detail):
    """The durable evidence of one content-identity refusal."""
    return {'stage': stage, 'expected': expected, 'actual': actual, 'detail': detail,
            'checked_at': _utc_now()}


def _refuse_mutation(root, transaction, stage, expected, actual, detail,
                     kind='hook_mutated'):
    """Persist the integrity failure and refuse; nothing is reset, amended or rewritten.

    The hook- or gate-created commit, index and working tree stay exactly as they are, and
    the transaction keeps the pinned tree plus this evidence for the operator or a new
    review; the run never reports a successful integration for a mutated tree.
    """
    failed = _persist(root, transaction, stage='failed',
                      trees=dict(transaction['trees'],
                                 integrity=_integrity(stage, expected, actual, detail)))
    _fail(kind, '{}; the transaction {} records the pinned tree {} against the actual {} and '
                'every byte is retained'.format(detail, failed['transaction_id'], expected,
                                                actual))


# --- identity, ownership, the manifest ----------------------------------------------------

def _transaction_id(assignment):
    return 'artel-integrate-{}-{}-a{}'.format(assignment['run_id'], assignment['task'],
                                              assignment['attempt'])


def _result_ref(assignment):
    return '{}/{}/{}/{}/result'.format(REF_ROOT, assignment['run_id'], assignment['task'],
                                       assignment['attempt'])


def record_path(repo, identity):
    """The transaction record's conventional path under the coordinator's run directory.

    `identity` is a validated assignment or a stored transaction -- both carry the four
    keys the path is built from.
    """
    kind = 'invalid_transaction'
    _object(identity, 'the identity', kind)
    for name in ('ticket', 'task', 'run_id', 'attempt'):
        if name not in identity:
            _fail(kind, 'the identity is missing `{}`'.format(name))
    if not isinstance(identity['ticket'], str) or not parallel_contract.TICKET_RE.match(
            identity['ticket']):
        _fail(kind, 'the identity ticket is not canonical: {!r}'.format(identity['ticket']))
    if not isinstance(identity['task'], str) or not parallel_contract.TASK_RE.match(
            identity['task']):
        _fail(kind, 'the identity task is not an `<iteration>.<n>` number: {!r}'.format(
            identity['task']))
    if not isinstance(identity['run_id'], str) or not identity['run_id']:
        _fail(kind, 'the identity run_id is not a non-empty string: {!r}'.format(
            identity['run_id']))
    if isinstance(identity['attempt'], bool) or not isinstance(identity['attempt'], int) \
            or identity['attempt'] < 1:
        _fail(kind, 'the identity attempt is not an integer >= 1: {!r}'.format(
            identity['attempt']))
    return (Path(repo) / '.artel' / 'run' / identity['ticket'] / 'parallel' /
            identity['run_id'] / RECORD_DIR /
            '{}-a{}.json'.format(identity['task'], identity['attempt']))


def _manifest_path(repo, identity):
    return (Path(repo) / '.artel' / 'run' / identity['ticket'] / 'parallel' /
            identity['run_id'] / 'manifest.json')


def _require_owned(common, identity):
    """Match the repository-common ticket lock to this run, read-only (§13.4).

    The lock is the run's single-writer proof across every linked checkout; this module
    never acquires, releases or reclaims it. A missing lock is `not_owned`, an unreadable
    one `ambiguous_owner`, another run's `foreign_owner`.
    """
    path = Path(common) / 'artel' / 'parallel' / (identity['ticket'] + '.json')
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail('not_owned', 'the ticket {} is not owned: no lock at {}'.format(
            identity['ticket'], path))
    except (OSError, UnicodeDecodeError) as exc:
        _fail('ambiguous_owner', 'the ticket lock at {} cannot be read: {}'.format(path, exc))
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail('ambiguous_owner', 'the ticket lock at {} is not valid JSON: {}'.format(path, exc))
    _object(value, 'the ticket lock', 'ambiguous_owner')
    for name in ('schema', 'ticket', 'run_id', 'phase', 'token', 'acquired_at'):
        if name not in value:
            _fail('ambiguous_owner', 'the ticket lock at {} is missing `{}`'.format(path, name))
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA \
            or value['ticket'] != identity['ticket']:
        _fail('ambiguous_owner', 'the ticket lock at {} is not a lock for {}'.format(
            path, identity['ticket']))
    if value['run_id'] != identity['run_id'] or value['phase'] != identity['phase']:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}), not by run {} '
                               '(phase {})'.format(identity['ticket'], value['run_id'],
                                                   value['phase'], identity['run_id'],
                                                   identity['phase']))
    return value


def _require_integrating(repo, identity):
    """The durable `integrating` state and the drained run, or a refusal.

    The coordinator persists the task's `prepared -> integrating` transition before polling
    for safe returns or creating any transport/commit; that record is the scheduler's
    admission latch (§13.6). Because the transport and the final commit run the host's normal
    hooks -- unproven shared effects -- no sibling may still be `preparing`, `running` or
    `verifying/reviewing`, and no other task may be `integrating`. A `prepared` sibling is
    already at safe return: it parks (like a waiting `reserved` reservation) and is recorded
    in `drain.waiting` without being relaunched, blocked, cancelled or published.
    """
    path = _manifest_path(repo, identity)
    try:
        manifest = parallel_state.read_manifest(str(path))
    except parallel_state.StateError as exc:
        raise IntegrationError(exc.kind, exc.message)
    if manifest['ticket'] != identity['ticket'] or manifest['phase'] != identity['phase'] \
            or manifest['run_id'] != identity['run_id']:
        _fail('manifest_mismatch', 'the manifest at {} is not this run\'s: {!r}'.format(
            path, {name: manifest[name] for name in ('ticket', 'phase', 'run_id')}))
    record = manifest['tasks'].get(identity['task'])
    if record is None or record['state'] != 'integrating':
        _fail('not_integrating', 'the task {} is {} in {}; integration requires the durable '
                                 '`integrating` transition'.format(
                                     identity['task'],
                                     'absent' if record is None else repr(record['state']),
                                     path))
    in_flight = sorted(task for task, entry in manifest['tasks'].items()
                       if task != identity['task'] and entry['state'] in IN_FLIGHT_STATES)
    if in_flight:
        _fail('drain_required', 'the run is not drained: {} still unresolved; stop admission '
                                'and let the workers return before integrating'.format(
                                    ', '.join(in_flight)))
    waiting = sorted(task for task, entry in manifest['tasks'].items()
                     if entry['state'] in PARKED_STATES)
    return {'in_flight': [], 'waiting': waiting, 'checked_at': _utc_now()}


def _require_worker(value, common):
    """The assignment's own marker-validated worker root, in this repository."""
    context = hook_common.worker_context(root=value['worker_root'])
    if context is None:
        _fail('not_worker_root', '{} is not a launch-ready worker root (the lifecycle marker '
                                 'is missing, malformed, foreign, wrong-root or not '
                                 'complete)'.format(value['worker_root']))
    if context['assignment'] != value:
        _fail('worker_identity_mismatch', 'the worker marker at {} carries a different '
                                          'assignment'.format(value['worker_root']))
    marker_common = os.path.realpath(context['repository']['common_dir'])
    if marker_common != os.path.realpath(str(common)):
        _fail('foreign_repository', 'the worker root {} belongs to another repository '
                                    '({})'.format(value['worker_root'], marker_common))
    return context


def _worker_state(repo, value):
    """The lifecycle's own read-only state report, or its refusal kind."""
    try:
        report = task_worktree.inspect_worker(str(repo), value)
    except task_worktree.WorktreeError as exc:
        raise IntegrationError(exc.kind, exc.message)
    if report['state'] not in ('ready', 'published'):
        _fail('worker_not_ready', 'the worker root {} is {!r}: {}'.format(
            value['worker_root'], report['state'],
            report['reasons'][0] if report['reasons'] else 'not integration-ready'))
    return report


# --- the coordinator's shape ---------------------------------------------------------------

def _default_branch(repo):
    """The repository's authoritative default branch, or a typed ambiguity.

    Resolution order: the remote HEAD (`refs/remotes/origin/HEAD`) when it exists, then the
    repository's configured `init.defaultBranch`. There is no guessed conventional name: a
    default that cannot be resolved refuses (`default_unknown`) instead of permitting a
    cycle commit on a branch nobody established as a task branch. No network call is made.
    """
    proc = _git(repo, 'symbolic-ref', '--short', '-q', 'refs/remotes/origin/HEAD', check=False)
    name = proc.stdout.strip()
    if name.startswith('origin/'):
        return name[len('origin/'):]
    if name:
        return name
    configured = _git(repo, 'config', '--get', 'init.defaultBranch', check=False)
    name = configured.stdout.strip()
    if name:
        return name
    _fail('default_unknown', 'the repository {} has no remote HEAD and no configured '
                             '`init.defaultBranch`; the default branch cannot be resolved '
                             'and a cycle commit is never made on a guess. Configure '
                             '`init.defaultBranch` or add a remote HEAD.'.format(repo))


def _coordinator_preflight(repo, transaction=None):
    """The intended branch, a usable branch reflog and (with a transaction) the pre-HEAD.

    A branch reflog is usable only while it exists and its newest entry is the branch tip:
    that is the evidence recovery matches. The default branch is a stop-and-ask, exactly as
    a task commit's branch guard.
    """
    proc = _git(repo, 'symbolic-ref', '--short', '-q', 'HEAD', check=False)
    branch = proc.stdout.strip()
    if proc.returncode != 0 or not branch:
        _fail('detached_head', 'the coordinator {} is on a detached HEAD; integration lands '
                               'on the intended branch'.format(repo))
    if branch == _default_branch(repo):
        _fail('default_branch', 'the coordinator is on the default branch {}; a cycle commit '
                                'is never made there'.format(branch))
    head = _out(repo, 'rev-parse', 'HEAD')
    entries = _reflog(repo, branch)
    if not entries or entries[0][0] != head:
        _fail('reflogs_unusable', 'the branch reflog of {} does not track its tip in {}; '
                                  'recovery evidence would be unusable'.format(branch, repo))
    if transaction is not None:
        if branch != transaction['branch']:
            _fail('branch_mismatch', 'the coordinator is on {} but the transaction was '
                                     'recorded on {}'.format(branch, transaction['branch']))
        if head != transaction['pre_head']:
            _fail('stale_pre_head', 'the coordinator HEAD {} is not the transaction pre-HEAD '
                                    '{}'.format(head, transaction['pre_head']))
        if transaction['pre_head'] not in [entry[0] for entry in entries]:
            _fail('expired_evidence', 'the pre-HEAD {} is no longer in the branch reflog; '
                                      'recovery evidence has expired'.format(
                                          transaction['pre_head']))
    return head, branch


def _require_repo_root(repo, transaction):
    """The transaction belongs to exactly this coordinator checkout and record path."""
    root = _top_level(repo)
    if os.path.realpath(transaction['repo_root']) != str(root):
        _fail('repo_mismatch', 'the transaction was recorded for {} but this is {}'.format(
            transaction['repo_root'], root))
    expected = record_path(root, transaction)
    if os.path.realpath(transaction['record']) != os.path.realpath(str(expected)):
        _fail('record_mismatch', 'the transaction record path {} is not the run\'s own '
                                 '{}'.format(transaction['record'], expected))
    return root


def _status_paths(root):
    """Every nonignored path `git status` reports at `root`, rename origins included."""
    out = _git(root, 'status', '--porcelain', '--untracked-files=all', '-z').stdout
    tokens = out.split('\0')
    paths = []
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if code[0] in ('R', 'C') and index < len(tokens):
            paths.append(tokens[index])
            index += 1
    return sorted(set(paths))


def _require_clean_coordinator(root, allowed=()):
    """The coordinator's whole nonignored tree may carry nothing outside `allowed`.

    `allowed` is the open transaction's own set: the transported paths and the transaction's
    pending documents. Everything else -- a person's dirty, staged or untracked work -- is a
    refusal before any gate or hook runs; nothing is reset, cleaned, stashed or staged to
    make the tree pass, so the integrated gate and the final commit always see exactly the
    tree that can land.
    """
    offenders = sorted(path for path in _status_paths(root) if path not in set(allowed))
    if offenders:
        _fail('coordinator_dirty', 'the coordinator carries changes outside this cycle: '
                                   '{}'.format(', '.join(offenders)))


def _check_coordinator_paths(root, paths):
    """prepare's narrower guard: the transported paths are clean and nothing is staged.

    Unlike apply/commit this runs before any coordinator effect and tolerates unrelated
    work in the coordinator, because `prepare_transport` only commits in the worker tree;
    the strict, completely-clean contract belongs to `apply_transport` and `commit_cycle`.
    """
    staged = _staged_paths(root)
    if staged:
        _fail('coordinator_dirty', 'the coordinator index already carries {}; a person\'s '
                                   'staged work is never swept into a cycle'.format(
                                       ', '.join(staged)))
    dirty = set(_unstaged_paths(root)) | set(_untracked_paths(root))
    overlapping = sorted(dirty & set(paths))
    if overlapping:
        _fail('coordinator_dirty', 'the coordinator has local changes on transported paths: '
                                   '{}'.format(', '.join(overlapping)))


# --- the transaction record -----------------------------------------------------------------

def _review_shape(value, kind='invalid_transaction'):
    if value is None:
        return None
    _object(value, 'review', kind)
    _keys(value, ('required', 'route', 'verdict', 'rounds'), 'review', kind)
    if not isinstance(value['required'], bool) or value['route'] not in parallel_contract.ROUTES \
            or value['verdict'] not in parallel_contract.REVIEW_VERDICTS \
            or isinstance(value['rounds'], bool) or not isinstance(value['rounds'], int) \
            or value['rounds'] < 0:
        _fail(kind, 'review is not the §5 {required, route, verdict, rounds} object: {!r}'.format(
            value))
    return value


def _transaction(value):
    """The schema-1 integration transaction, or `invalid_transaction`."""
    kind = 'invalid_transaction'
    _object(value, 'the transaction', kind)
    _keys(value, TRANSACTION_KEYS, 'the transaction', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the transaction schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    _text(value['transaction_id'], 'the transaction_id', kind)
    if not isinstance(value['ticket'], str) or not parallel_contract.TICKET_RE.match(
            value['ticket']):
        _fail(kind, 'the transaction ticket is not canonical: {!r}'.format(value['ticket']))
    if value['phase'] is not None and (isinstance(value['phase'], bool)
                                       or not isinstance(value['phase'], int)
                                       or value['phase'] < 1):
        _fail(kind, 'the transaction phase is not an integer >= 1 or null: {!r}'.format(
            value['phase']))
    if not isinstance(value['task'], str) or not parallel_contract.TASK_RE.match(value['task']):
        _fail(kind, 'the transaction task is not an `<iteration>.<n>` number: {!r}'.format(
            value['task']))
    _text(value['run_id'], 'the transaction run_id', kind)
    if isinstance(value['attempt'], bool) or not isinstance(value['attempt'], int) \
            or value['attempt'] < 1:
        _fail(kind, 'the transaction attempt is not an integer >= 1: {!r}'.format(
            value['attempt']))
    _commit(value['base_commit'], 'the transaction base_commit', kind)
    _commit(value['pre_head'], 'the transaction pre_head', kind)
    _commit(value['transport_commit'], 'the transaction transport_commit', kind)
    _commit(value['cycle_commit'], 'the transaction cycle_commit', kind)
    for name in ('worker_root', 'repo_root', 'record'):
        _text(value[name], 'the transaction {}'.format(name), kind)
        if not os.path.isabs(value[name]):
            _fail(kind, 'the transaction {} is not an absolute path: {!r}'.format(
                name, value[name]))
    _text(value['branch'], 'the transaction branch', kind)
    _text(value['result_ref'], 'the transaction result_ref', kind)
    if value['stage'] not in STAGES:
        _fail(kind, 'the transaction stage is not one of {}: {!r}'.format(
            ', '.join(STAGES), value['stage']))
    if value['route'] not in parallel_contract.ROUTES:
        _fail(kind, 'the transaction route is not `light` or `full`: {!r}'.format(
            value['route']))
    _review_shape(value['review'], kind)
    _paths(value['changed_files'], 'changed_files', kind)
    _paths(value['excluded'], 'excluded', kind)
    _paths(value['documents'], 'documents', kind)
    if value['subject'] is not None:
        _text(value['subject'], 'the transaction subject', kind)
    if value['diff'] is not None:
        _object(value['diff'], 'diff', kind)
        _keys(value['diff'], DIFF_KEYS, 'diff', kind)
        _commit(value['diff']['base_commit'], 'diff.base_commit', kind)
        _commit(value['diff']['commit'], 'diff.commit', kind)
        if not isinstance(value['diff']['sha256'], str) \
                or not re.match(r'[0-9a-f]{64}\Z', value['diff']['sha256']):
            _fail(kind, 'diff.sha256 is not a sha256 digest: {!r}'.format(value['diff']['sha256']))
        _paths(value['diff']['files'], 'diff.files', kind)
    _object(value['drain'], 'drain', kind)
    _keys(value['drain'], DRAIN_KEYS, 'drain', kind)
    for name in ('in_flight', 'waiting'):
        if not isinstance(value['drain'][name], list):
            _fail(kind, 'drain.{} is not an array: {!r}'.format(name, value['drain'][name]))
    _text(value['drain']['checked_at'], 'drain.checked_at', kind)
    _trees(value['trees'], kind)
    if value['publication'] is not None:
        _object(value['publication'], 'publication', kind)
    if value['verify'] is not None:
        _object(value['verify'], 'verify', kind)
    if value['recovery'] is not None:
        _object(value['recovery'], 'recovery', kind)
        _keys(value['recovery'], RECOVERY_KEYS, 'recovery', kind)
        if value['recovery']['state'] not in ('landed', 'none'):
            _fail(kind, 'recovery.state is not `landed` or `none`: {!r}'.format(
                value['recovery']['state']))
    _text(value['created_at'], 'the transaction created_at', kind)
    _text(value['updated_at'], 'the transaction updated_at', kind)
    return value


def _identity_of(transaction):
    return {name: transaction[name] for name in ('ticket', 'phase', 'task', 'run_id',
                                                 'attempt')}


def _stored_record(root, value):
    """The existing record for this attempt, or None; malformed or foreign ones refuse."""
    path = record_path(root, value)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        _fail('corrupt_record', 'the transaction record at {} cannot be read: {}'.format(
            path, exc))
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        _fail('corrupt_record', 'the transaction record at {} is not valid JSON: {}'.format(
            path, exc))
    stored = _transaction(parsed)
    if _identity_of(stored) != _identity_of(value):
        _fail('record_mismatch', 'the transaction record at {} belongs to another '
                                 'attempt'.format(path))
    return stored


def _persist(root, transaction, **changes):
    """Write the record atomically, keeping its identity and creation time."""
    value = dict(transaction)
    value.update(changes)
    value['updated_at'] = _utc_now()
    _transaction(value)
    _atomic_write(record_path(root, value), json.dumps(value, indent=2, sort_keys=True) + '\n')
    return value


# --- prepare_transport ----------------------------------------------------------------------

def _require_succeeded(result):
    if result['status'] != 'succeeded':
        _fail('result_not_succeeded', 'the result status is {!r}; only a succeeded result '
                                      'can be integrated'.format(result['status']))
    red = [entry['command'] for entry in result['verify'] if not entry['ok']]
    if red:
        _fail('verify_red', 'the worker\'s own checks are red: {}'.format(', '.join(red)))


def _spec_prefixes(worker_root, ticket):
    """`.artel` and the ticket's shared spec trail: coordinator-owned, never transported."""
    prefixes = ['.artel/']
    path = Path(worker_root) / '.artel' / 'config.json'
    if not path.is_file():
        return prefixes
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _fail('invalid_config', 'the worker config at {} cannot be read: {}'.format(path, exc))
    _object(config, 'the worker config', 'invalid_config')
    specs = config.get('specs')
    directory = DEFAULT_SPECS_DIR
    if isinstance(specs, dict) and 'dir' in specs:
        directory = specs['dir']
    if not isinstance(directory, str) or not directory or os.path.isabs(directory) \
            or directory != directory.strip() or any(char in directory
                                                     for char in '\x00\n\r') \
            or '..' in Path(directory).parts:
        _fail('invalid_config', 'the worker config specs.dir is not a repo-relative path: '
                                '{!r}'.format(directory))
    directory = directory.rstrip('/')
    prefixes.append(directory + '/' + ticket + '/')
    prefixes.append(directory + '/.active_ticket')
    return prefixes


def _split_excluded(paths, prefixes):
    excluded = []
    code = []
    for path in paths:
        if any(path == prefix.rstrip('/') or path.startswith(prefix) for prefix in prefixes):
            if path not in LIFECYCLE_PATHS:
                excluded.append(path)
        else:
            code.append(path)
    return sorted(code), sorted(excluded)


def _snapshot_tree(root):
    """A working-tree snapshot through the review engine's temporary index (explicit root)."""
    try:
        return review_package.snapshot_tree(root)
    except review_package.GitError as exc:
        _fail('git_failed', 'cannot snapshot the tree {}: {}'.format(root, exc))


def _working_diff(root, against):
    """The actual working-tree diff of `root` from `against`, both rename sides."""
    return _diff_paths(root, against, _snapshot_tree(root))


def _require_review(result, effective, floored):
    """The accepted review must cover the effective route of exactly this diff."""
    review = result['review']
    if review is None:
        _fail('review_missing', 'the worker submission carries `review: null`; the '
                                'coordinator-owned accepted review is required before '
                                'integration')
    mismatch = 'route_floor' if floored else 'review_route_mismatch'
    if review['route'] != effective:
        _fail(mismatch, 'the review route {!r} does not cover the effective route {!r}'.format(
            review['route'], effective))
    if review['required'] != (effective == 'full'):
        _fail(mismatch, 'review.required is {!r} for the effective route {!r}'.format(
            review['required'], effective))
    if effective == 'full':
        if review['verdict'] != 'passed' or review['rounds'] < 1:
            _fail('route_floor' if floored else 'review_not_passed',
                  'the full-route review is {!r} after {} round(s)'.format(
                      review['verdict'], review['rounds']))
    elif review['verdict'] not in ('passed', 'skipped'):
        _fail('review_not_passed', 'the review verdict is {!r}'.format(review['verdict']))


def _require_changes(value, result, code):
    """The declared actual changes must be the whole actual diff, deviation by deviation.

    Returns the effective route: an actual path outside the assignment's declared `files`
    is an undeclared change -- it must be recorded in a deviation and it floors the route
    to `full`, so a light review cannot cover it.
    """
    if sorted(code) != sorted(result['changed_files']):
        _fail('changed_files_mismatch', 'the result declares changed_files {} but the worker '
                                        'tree changed {}'.format(
                                            sorted(result['changed_files']), sorted(code)))
    if not code:
        _fail('empty_transport', 'the worker tree has no code change to transport')
    for path in code:
        _relative(path, 'a changed path', 'invalid_path')
    declared = set(value['files'])
    undeclared = sorted(path for path in code if path not in declared)
    deviation_paths = set()
    for deviation in result['deviations']:
        if deviation['severity'] == 'major':
            _fail('major_deviation', 'the result records a major deviation ({}); it halts and '
                                     'escalates before integration'.format(
                                         deviation['summary']))
        deviation_paths.update(deviation['paths'])
    ghost = sorted(deviation_paths - set(code))
    if ghost:
        _fail('deviation_mismatch', 'the result records deviation paths that did not change: '
                                    '{}'.format(', '.join(ghost)))
    if undeclared:
        unrecorded = sorted(path for path in undeclared if path not in deviation_paths)
        if unrecorded:
            _fail('undeclared_change', 'the worker changed paths outside the task\'s declared '
                                       'files with no recorded deviation: {}'.format(
                                           ', '.join(unrecorded)))
        _require_review(result, 'full', True)
        return 'full'
    _require_review(result, value['route'], False)
    return value['route']


def _stageable(root, path):
    """A path is stageable while it exists here or is still tracked in the index."""
    return (Path(root) / path).exists() or _tracked_in_index(root, path)


def _stage_paths(root, paths):
    stageable = sorted(path for path in paths if _stageable(root, path))
    if stageable:
        _git(root, 'add', '-A', '--', *[':(literal)' + path for path in stageable])
    return stageable


def _transaction_commit(worker_root, transaction_id, base_commit):
    """The transport commit our own transaction left in the worker HEAD reflog, if any."""
    proc = _git(worker_root, 'reflog', 'show', '--format=%H|%gs', 'HEAD', check=False)
    if proc.returncode != 0:
        return None
    candidates = []
    for line in proc.stdout.splitlines():
        commit, _, message = line.partition('|')
        if message.strip() == transaction_id or message.strip().startswith(transaction_id + ':'):
            candidates.append(commit.strip())
    matching = [commit for commit in dict.fromkeys(candidates)
                if _commit_exists(worker_root, commit)
                and _parent(worker_root, commit) == base_commit]
    if len(matching) > 1:
        _fail('ambiguous_worker', 'the worker HEAD reflog carries more than one transport '
                                  'commit for {}'.format(transaction_id))
    return matching[0] if matching else None


def _adoptable_transport(repo, value, report, transaction_id):
    """The attempt's existing transport commit, or None when a fresh one is needed."""
    ref = _ref_value(repo, _result_ref(value))
    head = report['worktree']['head']
    if ref is None and head == value['base_commit']:
        return None
    if ref is not None:
        if head != ref:
            _fail('worker_not_ready', 'the worker HEAD {} is not the retained result {}; the '
                                      'root is preserved'.format(head, ref))
        transport = ref
    else:
        transport = _transaction_commit(value['worker_root'], transaction_id,
                                        value['base_commit'])
        if transport is None:
            _fail('worker_not_at_base', 'the worker HEAD {} is neither the base {} nor a '
                                        'transaction-marked transport commit'.format(
                                            head, value['base_commit']))
    if not _commit_exists(repo, transport):
        _fail('ref_conflict', 'the retained result {} does not exist in {}'.format(
            transport, repo))
    if _parent(repo, transport) != value['base_commit']:
        _fail('ref_conflict', 'the retained result {} does not descend directly from the '
                              'base {}'.format(transport, value['base_commit']))
    return transport


def _skeleton(root, value, branch, pre_head, drain):
    now = _utc_now()
    return {
        'schema': SCHEMA, 'transaction_id': _transaction_id(value),
        'ticket': value['ticket'], 'phase': value['phase'], 'task': value['task'],
        'run_id': value['run_id'], 'attempt': value['attempt'],
        'base_commit': value['base_commit'], 'worker_root': value['worker_root'],
        'repo_root': str(root), 'branch': branch, 'pre_head': pre_head,
        'transport_commit': None, 'result_ref': _result_ref(value), 'route': value['route'],
        'review': None, 'changed_files': [], 'excluded': [], 'diff': None, 'documents': [],
        'drain': drain, 'publication': None, 'stage': 'preparing', 'subject': None,
        'cycle_commit': None, 'verify': None, 'recovery': None,
        'trees': {'transport': None, 'landing': None, 'integrity': None},
        'created_at': now, 'updated_at': now, 'record': str(record_path(root, value)),
    }


def _update_result_ref(repo, value, transport):
    name = _result_ref(value)
    current = _ref_value(repo, name)
    if current == transport:
        return
    if current is not None:
        _fail('ref_conflict', 'the result ref {} already points at {}, not at {}'.format(
            name, current, transport))
    _git(repo, 'update-ref', name, transport, '0' * 40)


def prepare_transport(repo, assignment, result):
    """Validate the prepared result and create its retained transport commit.

    The transport commit is a normal hooked commit of the result's actual code diff in the
    worker tree; the worker is left clean at it and the namespaced result ref retains it.
    A crash between the commit and the receipt is reconciled from the transaction-marked
    worker reflog, never by creating a second commit.
    """
    value = _assignment(assignment)
    accepted = _result(result, value)
    _require_succeeded(accepted)
    root = _top_level(repo)
    common = _common_dir(repo)
    _require_owned(common, value)
    _require_worker(value, common)
    report = _worker_state(repo, value)
    transaction_id = _transaction_id(value)
    stored = _stored_record(root, value)
    if stored is not None and stored['stage'] in ('committed', 'recovered'):
        _fail('invalid_transaction', 'the task {} is already integrated by this attempt '
                                     '({})'.format(value['task'], stored['cycle_commit']))
    pre_head, branch = _coordinator_preflight(repo)
    drain = _require_integrating(repo, value)
    transport = _adoptable_transport(repo, value, report, transaction_id)
    prefixes = _spec_prefixes(value['worker_root'], value['ticket'])
    if transport is None:
        code, excluded = _split_excluded(
            _working_diff(value['worker_root'], value['base_commit']), prefixes)
        route = _require_changes(value, accepted, code)
        _check_coordinator_paths(root, code)
        staged = _staged_paths(value['worker_root'])
        if staged and sorted(staged) != sorted(code):
            _fail('worker_staged', 'the worker index already carries {}; a worker stages '
                                   'nothing'.format(', '.join(staged)))
        transaction = _persist(root, _skeleton(root, value, branch, pre_head, drain),
                               changed_files=code, excluded=excluded, route=route,
                               review=accepted['review'])
        _stage_paths(value['worker_root'], code)
        staged = _staged_paths(value['worker_root'])
        if sorted(staged) != sorted(code):
            _fail('staging_mismatch', 'the worker index carries {} after staging {}, not the '
                                      'allowlist'.format(staged, sorted(code)))
        # Pin the accepted content before the normal hooks run: the tree a commit would
        # land now is what the review accepted, and a hook that rewrites or restages any
        # path -- same name or new -- produces a different tree and is refused.
        pinned = _write_tree(value['worker_root'])
        transaction = _persist(root, transaction,
                               trees=dict(transaction['trees'], transport=pinned))
        env = dict(_env(), GIT_REFLOG_ACTION=transaction_id)
        proc = _git(value['worker_root'], 'commit', '-m', TRANSPORT_SUBJECT, check=False,
                    env=env)
        if proc.returncode != 0:
            _persist(root, transaction, stage='commit_refused')
            _fail('commit_refused', 'the transport commit was refused in {}: {}'.format(
                value['worker_root'], _tail((proc.stderr or '') + (proc.stdout or ''))))
        transport = _out(value['worker_root'], 'rev-parse', 'HEAD')
        actual = _tree_of(value['worker_root'], transport)
        if actual != pinned:
            _refuse_mutation(root, transaction, 'transport', pinned, actual,
                             'the transport hooks changed the accepted tree (commit {})'.format(
                                 transport))
        if _diff_paths(value['worker_root'], value['base_commit'], transport) != code:
            _fail('git_failed', 'the transport commit {} does not carry the allowlisted '
                                'diff'.format(transport))
        remaining = sorted(set(_staged_paths(value['worker_root']))
                           | (set(_unstaged_paths(value['worker_root'])) & set(code))
                           | (set(_untracked_paths(value['worker_root'])) & set(code)))
        if remaining:
            _fail('worker_changed', 'the worker changed {} while its transport commit was '
                                    'made'.format(', '.join(remaining)))
    else:
        tree_code, excluded = _split_excluded(
            _working_diff(value['worker_root'], transport), prefixes)
        if tree_code:
            _fail('worker_changed', 'the worker changed {} after its retained transport '
                                    'commit {}'.format(', '.join(tree_code), transport))
        recorded = (stored or {}).get('trees', {}).get('transport')
        if recorded is not None:
            actual = _tree_of(repo, transport)
            if actual != recorded:
                _fail('integrity_failed', 'the retained transport {} carries tree {}, not the '
                                          'accepted {}; a new attempt and review are '
                                          'required'.format(transport, actual, recorded))
        code = _diff_paths(repo, value['base_commit'], transport)
        route = _require_changes(value, accepted, code)
        transaction = stored if stored is not None else _skeleton(root, value, branch, pre_head,
                                                                  drain)
    _update_result_ref(repo, value, transport)
    package, _files = review_package.build_package(value['worker_root'], value['base_commit'],
                                                   transport)
    return _persist(root, transaction,
                    transport_commit=transport, result_ref=_result_ref(value), route=route,
                    review=accepted['review'], changed_files=code, excluded=excluded,
                    diff={'base_commit': value['base_commit'], 'commit': transport,
                          'sha256': _sha256_text(package), 'files': code},
                    drain=drain, stage='prepared', recovery=None)


# --- apply_transport ------------------------------------------------------------------------

def _require_identity(transaction, value):
    for name in ('schema', 'ticket', 'phase', 'task', 'run_id', 'attempt', 'base_commit',
                 'worker_root'):
        if transaction[name] != value[name]:
            _fail('invalid_transaction', 'the transaction {} does not match the assignment: '
                                         '{!r} != {!r}'.format(name, transaction[name],
                                                               value[name]))
    if transaction['transaction_id'] != _transaction_id(value):
        _fail('invalid_transaction', 'the transaction id is not this attempt\'s')
    if transaction['result_ref'] != _result_ref(value):
        _fail('invalid_transaction', 'the transaction result_ref is not the run\'s canonical '
                                     'ref')
    return transaction


def apply_transport(repo, assignment, prepared):
    """Apply the retained transport commit with `cherry-pick --no-commit`, or preserve a
    conflict.

    The coordinator must be on the transaction's branch at its recorded pre-HEAD with a
    usable branch reflog and a **completely clean nonignored tree**: any tracked, staged or
    untracked change outside the cycle refuses before the cherry-pick, preserving every byte,
    the index, the stash and HEAD. A conflict leaves git's own conflict state and a
    `conflicted` record -- nothing is resolved, reset or discarded.
    """
    value = _assignment(assignment)
    transaction = _require_identity(_transaction(prepared), value)
    root = _require_repo_root(repo, transaction)
    common = _common_dir(repo)
    _require_owned(common, value)
    _require_worker(value, common)
    _worker_state(repo, value)
    drain = _require_integrating(repo, value)
    _coordinator_preflight(repo, transaction)
    transport = transaction['transport_commit']
    if transport is None:
        _fail('invalid_transaction', 'the transaction carries no transport commit')
    if _ref_value(repo, transaction['result_ref']) != transport:
        _fail('ref_conflict', 'the result ref {} no longer points at the retained transport '
                              '{}'.format(transaction['result_ref'], transport))
    if transaction['stage'] == 'applying':
        staged = _staged_paths(root)
        if sorted(staged) == sorted(transaction['changed_files']):
            return _persist(root, transaction, drain=drain, stage='applied')
        if staged:
            _fail('ambiguous_apply', 'the coordinator index carries {}; the interrupted apply '
                                     'cannot be told apart'.format(', '.join(staged)))
    elif transaction['stage'] in ('applied', 'failed', 'conflicted', 'committing', 'committed',
                                  'recovered'):
        return _persist(root, transaction, drain=drain)
    _require_clean_coordinator(root)
    transaction = _persist(root, transaction, drain=drain, stage='applying')
    proc = _git(root, 'cherry-pick', '--no-commit', transport, check=False)
    if proc.returncode != 0:
        detail = _tail((proc.stderr or '') + (proc.stdout or ''))
        unmerged = _unmerged_paths(root)
        if unmerged:
            _persist(root, transaction, stage='conflicted')
            _fail('cherry_pick_conflict', 'the transport conflicts with {}; the conflict state '
                                          'is preserved: {}'.format(', '.join(unmerged),
                                                                    detail))
        _persist(root, transaction, stage='apply_failed')
        _fail('apply_failed', 'cherry-pick --no-commit failed in {}: {}'.format(root, detail))
    staged = _staged_paths(root)
    if sorted(staged) != sorted(transaction['changed_files']):
        _persist(root, transaction, stage='apply_failed')
        _fail('staging_mismatch', 'the applied index carries {} but the transport carries '
                                  '{}'.format(staged, transaction['changed_files']))
    return _persist(root, transaction, drain=drain, stage='applied')


# --- commit_cycle ---------------------------------------------------------------------------

def _subject(value, transaction):
    """A conventional, subject-only cycle message with no task id and no trailer."""
    kind = 'invalid_subject'
    if not isinstance(value, str) or not value or value != value.strip() \
            or any(char in value for char in '\x00\n\r'):
        _fail(kind, 'the subject is not a single non-empty line: {!r}'.format(value))
    if not SUBJECT_RE.match(value):
        _fail(kind, 'the subject is not a conventional `<type>: <summary>` message: '
                    '{!r}'.format(value))
    task = transaction['task']
    if re.search(r'(?<![0-9A-Za-z.])' + re.escape(task) + r'(?![0-9A-Za-z.])', value):
        _fail(kind, 'the subject carries the task id {!r}; transaction metadata belongs in '
                    'the refs, reflogs and run state'.format(task))
    if transaction['transaction_id'] in value:
        _fail(kind, 'the subject carries the transaction id; recovery metadata never enters '
                    'the message')
    return value


def _documents(root, transaction):
    """The cycle's pending documents: the files-path tick/ordered notes, or none on the
    artifact-store path. Each must exist here; a code fix stays inside the transported
    paths, and a new path is a new attempt rather than an unreviewed commit."""
    documents = []
    for path in transaction['documents']:
        _relative(path, 'a document path', 'invalid_path')
        if path.startswith('.artel/') or path in LIFECYCLE_PATHS:
            _fail('invalid_transaction', 'the document {} is coordinator run state, never a '
                                         'cycle document'.format(path))
        if not (Path(root) / path).is_file():
            _fail('missing_document', 'the pending document {} does not exist in {}'.format(
                path, root))
        documents.append(path)
    return sorted(documents)


def _reflog_entries_for(root, transaction):
    """The branch reflog entries that can carry this transaction's final commit."""
    entries = _reflog(root, transaction['branch'])
    if not entries:
        _fail('reflogs_unusable', 'the branch reflog of {} is empty; recovery evidence is '
                                  'unusable'.format(transaction['branch']))
    tip = _ref_value(root, transaction['branch'])
    if tip is None or entries[0][0] != tip:
        _fail('reflogs_unusable', 'the branch reflog of {} does not track its tip; recovery '
                                  'evidence is unusable'.format(transaction['branch']))
    if transaction['pre_head'] not in [entry[0] for entry in entries]:
        _fail('expired_evidence', 'the pre-HEAD {} is no longer in the branch reflog; '
                                  'recovery evidence has expired'.format(
                                      transaction['pre_head']))
    return entries


def _landed_commit(root, transaction):
    """The final commit this transaction landed, or None; ambiguity refuses.

    The match is exact: the transaction-marked branch reflog entry whose commit's first
    parent is the recorded pre-HEAD (and whose subject is the recorded one when the record
    has it). Evidence with the marker but no match is ambiguous; a matching commit that is
    no longer reachable from the branch is unreachable -- neither is ever re-committed.
    """
    entries = _reflog_entries_for(root, transaction)
    marker = transaction['transaction_id']
    candidates = [commit for commit, message in entries
                  if message == marker or message.startswith(marker + ':')]
    matching = []
    for commit in dict.fromkeys(candidates):
        if not _commit_exists(root, commit):
            continue
        if _parent(root, commit) != transaction['pre_head']:
            continue
        if transaction['subject'] is not None \
                and _subject_of(root, commit) != transaction['subject']:
            continue
        matching.append(commit)
    if len(matching) > 1:
        _fail('ambiguous_evidence', 'more than one branch reflog entry matches transaction '
                                    '{}'.format(marker))
    if matching:
        commit = matching[0]
        if not _is_ancestor(root, commit, transaction['branch']):
            _fail('unreachable_evidence', 'the commit {} for transaction {} is no longer '
                                          'reachable from {}'.format(
                                              commit, marker, transaction['branch']))
        pinned = transaction['trees']['landing']
        if pinned is not None and _tree_of(root, commit) != pinned:
            # The commit landed, but the hooks changed the pinned tree: never recognize it
            # as the accepted landing. A crash before the post-commit check must not let the
            # reflog parent match publish a bad-hook commit.
            _fail('integrity_failed', 'the transaction-marked commit {} carries tree {}, not '
                                      'the pinned landing tree {}; the branch and the '
                                      'transaction are retained for a new review'.format(
                                          commit, _tree_of(root, commit), pinned))
        return commit
    if candidates:
        _fail('ambiguous_evidence', 'branch reflog entries carry transaction {} but none '
                                    'matches its pre-HEAD {}'.format(
                                        marker, transaction['pre_head']))
    return None


def _task_gate(root, paths):
    """The existing task gate, run in the explicit coordinator root (docs/gates.md §1)."""
    argv = [sys.executable or 'python3', str(VERIFY_SCRIPT), 'task', '--files',
            ','.join(paths)]
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, env=_env())
    except OSError as exc:
        return {'exit_code': 2, 'ok': False, 'data': None,
                'error': {'kind': 'spawn_failed', 'message': str(exc)}}
    envelope = None
    for line in reversed(proc.stdout.splitlines()):
        if line.strip():
            try:
                envelope = json.loads(line)
            except ValueError:
                envelope = None
            break
    if not isinstance(envelope, dict):
        return {'exit_code': proc.returncode, 'ok': False, 'data': None,
                'error': {'kind': 'invalid_envelope',
                          'message': _tail(proc.stdout + proc.stderr)}}
    return {'exit_code': proc.returncode, 'ok': bool(envelope.get('ok')),
            'data': envelope.get('data'), 'error': envelope.get('error')}


def _task_gate_paths(transaction):
    """The deduplicated changed-path plus declared-test union for the integrated gate.

    The pinned assignment lives in the worker's lifecycle marker (the same validated record
    the launch consumed); its `tests` are the worker's declared verification scope and must
    run on the combined tree even when the integration did not change them. The marker must
    be this attempt's own, or the gate scope cannot be established.
    """
    context = hook_common.worker_context(root=transaction['worker_root'])
    if context is None:
        _fail('not_worker_root', '{} is not a launch-ready worker root; the integrated gate '
                                 'cannot establish its declared test scope'.format(
                                     transaction['worker_root']))
    assignment = context.get('assignment')
    if not isinstance(assignment, dict) \
            or (assignment.get('ticket'), assignment.get('task'), assignment.get('run_id'),
                assignment.get('attempt')) != (transaction['ticket'], transaction['task'],
                                               transaction['run_id'],
                                               transaction['attempt']):
        _fail('worker_identity_mismatch', 'the worker marker at {} carries a different '
                                          'assignment'.format(transaction['worker_root']))
    tests = assignment.get('tests')
    if not isinstance(tests, list):
        _fail('invalid_assignment', 'the worker marker assignment carries no test list')
    return sorted(set(transaction['changed_files']) | set(tests))


def commit_cycle(repo, transaction, subject):
    """Land exactly one conventional cycle commit for the applied transaction.

    The integrated tree is checked with the existing task gate first; a red or broken gate
    commits nothing. The commit stages exactly the transported paths plus the transaction's
    `documents`, runs the host's normal hooks and carries the transaction id as its
    `GIT_REFLOG_ACTION`. The staged tree is pinned before the gate and before the commit: a
    gate or hook that changes the index or the working tree refuses (`gate_mutated`,
    `hook_mutated`) instead of committing bytes the accepted review never covered. A commit
    that already landed is recognized, never repeated.
    """
    transaction = _transaction(transaction)
    root = _require_repo_root(repo, transaction)
    identity = _identity_of(transaction)
    _require_owned(_common_dir(repo), identity)
    _require_integrating(repo, identity)
    if transaction['trees']['integrity'] is not None:
        _fail('integrity_failed', 'the transaction {} already records an integrity failure '
                                  '({}); reconcile it explicitly before any new commit'.format(
                                      transaction['transaction_id'],
                                      transaction['trees']['integrity']['detail']))
    transport = transaction['transport_commit']
    if transport is None:
        _fail('invalid_transaction', 'the transaction carries no transport commit')
    if _ref_value(repo, transaction['result_ref']) != transport:
        _fail('ref_conflict', 'the result ref {} no longer points at the retained transport '
                              '{}'.format(transaction['result_ref'], transport))
    if transaction['stage'] in ('committed', 'recovered') and transaction['cycle_commit']:
        if not _commit_exists(repo, transaction['cycle_commit']) \
                or not _is_ancestor(repo, transaction['cycle_commit'],
                                    transaction['branch']):
            _fail('unreachable_evidence', 'the recorded cycle commit {} is not reachable from '
                                          '{}'.format(transaction['cycle_commit'],
                                                      transaction['branch']))
        pinned = transaction['trees']['landing']
        if pinned is not None \
                and _tree_of(repo, transaction['cycle_commit']) != pinned:
            _refuse_mutation(repo, transaction, 'landing',
                             pinned, _tree_of(repo, transaction['cycle_commit']),
                             'the recorded cycle commit {} does not carry the pinned '
                             'tree'.format(transaction['cycle_commit']),
                             kind='integrity_failed')
        return transaction
    if transaction['stage'] not in ('applied', 'failed', 'conflicted', 'commit_refused',
                                    'applying', 'committing'):
        _fail('invalid_transaction', 'a cycle commit needs an applied transaction, not '
                                     '{!r}'.format(transaction['stage']))
    landed = _landed_commit(root, transaction)
    if landed is not None:
        return _persist(root, transaction, stage='recovered', cycle_commit=landed,
                        recovery={'state': 'landed', 'checked_at': _utc_now()})
    _coordinator_preflight(repo, transaction)
    message = _subject(subject, transaction)
    documents = _documents(root, transaction)
    transaction = _persist(root, transaction, stage='committing', subject=message,
                           documents=documents)
    paths = sorted(set(_diff_paths(repo, transaction['base_commit'], transport))
                   | set(documents))
    _stage_paths(root, paths)
    staged = _staged_paths(root)
    if not staged:
        _fail('empty_commit', 'the cycle left nothing staged; nothing is committed')
    foreign = sorted(path for path in staged if path not in set(paths))
    if foreign:
        _fail('foreign_staged', 'the index carries {} outside the cycle; a person\'s staged '
                                'work is never swept into a run commit'.format(
                                    ', '.join(foreign)))
    # The gate and the final hooks must see exactly the tree that can land: a change outside
    # the transported paths and the pending documents -- including one introduced after a
    # successful apply -- refuses here, before the gate runs.
    _require_clean_coordinator(root, paths)
    landing = _write_tree(root)
    verify = _task_gate(root, _task_gate_paths(transaction))
    transaction = _persist(root, transaction, verify=verify)
    # The gate ran on the pinned tree; anything it changed -- the index, the working tree
    # inside the cycle, or content outside it -- is a mutation, never silently restaged to
    # make the green receipt describe an earlier tree.
    if _write_tree(root) != landing or sorted(_staged_paths(root)) != staged:
        _refuse_mutation(root, transaction, 'gate', landing, _write_tree(root),
                         'the integrated-tree gate changed the staged tree', kind='gate_mutated')
    mutated = sorted(set(_unstaged_paths(root)) | set(_untracked_paths(root)))
    if mutated:
        _refuse_mutation(root, transaction, 'gate', landing, _write_tree(root),
                         'the integrated-tree gate changed the working tree: {}'.format(
                             ', '.join(mutated)), kind='gate_mutated')
    _require_clean_coordinator(root, paths)
    if verify['exit_code'] == 2:
        _persist(root, transaction, stage='failed')
        _fail('task_gate_error', 'the integrated-tree task gate could not run: {}'.format(
            verify['error'] or verify['data']))
    if not verify['ok']:
        _persist(root, transaction, stage='failed')
        _fail('task_gate_failed', 'the integrated-tree task gate is red: {}'.format(
            verify['data'] or verify['error']))
    transaction = _persist(root, transaction,
                           trees=dict(transaction['trees'], landing=landing))
    env = dict(_env(), GIT_REFLOG_ACTION=transaction['transaction_id'])
    proc = _git(root, 'commit', '-m', message, check=False, env=env)
    if proc.returncode != 0:
        _persist(root, transaction, stage='commit_refused')
        _fail('commit_refused', 'the cycle commit was refused in {}: {}'.format(
            root, _tail((proc.stderr or '') + (proc.stdout or ''))))
    cycle = _out(root, 'rev-parse', 'HEAD')
    if _parent(root, cycle) != transaction['pre_head']:
        _fail('git_failed', 'the cycle commit {} does not sit on the pre-HEAD {}'.format(
            cycle, transaction['pre_head']))
    actual = _tree_of(root, cycle)
    if actual != landing:
        _refuse_mutation(root, transaction, 'landing', landing, actual,
                         'the final hooks changed the committed tree (commit {})'.format(cycle))
    return _persist(root, transaction, stage='committed', cycle_commit=cycle)


# --- recover_cycle --------------------------------------------------------------------------

def recover_cycle(repo, transaction):
    """Recognize a final commit that landed before its receipt, exactly once.

    The recorded pre-HEAD plus the transaction-marked branch reflog entry are the evidence,
    from a linked coordinator as well. Missing, expired, ambiguous or unreachable evidence
    refuses (`reflogs_unusable`, `expired_evidence`, `ambiguous_evidence`,
    `unreachable_evidence`); this function never commits, resets or re-commits.
    """
    transaction = _transaction(transaction)
    root = _require_repo_root(repo, transaction)
    identity = _identity_of(transaction)
    _require_owned(_common_dir(repo), identity)
    _require_integrating(repo, identity)
    if transaction['trees']['integrity'] is not None:
        _fail('integrity_failed', 'the transaction {} records an integrity failure ({}); '
                                  'recovery never publishes a mutated tree'.format(
                                      transaction['transaction_id'],
                                      transaction['trees']['integrity']['detail']))
    if transaction['cycle_commit']:
        commit = transaction['cycle_commit']
        if not _commit_exists(repo, commit) \
                or not _is_ancestor(repo, commit, transaction['branch']):
            _fail('unreachable_evidence', 'the recorded cycle commit {} is not reachable from '
                                          '{}'.format(commit, transaction['branch']))
        pinned = transaction['trees']['landing']
        if pinned is not None and _tree_of(repo, commit) != pinned:
            _fail('integrity_failed', 'the recorded cycle commit {} carries tree {}, not the '
                                      'pinned {}; the branch is retained for a new '
                                      'review'.format(commit, _tree_of(repo, commit), pinned))
        return _persist(root, transaction, stage='recovered',
                        recovery={'state': 'landed', 'checked_at': _utc_now()})
    landed = _landed_commit(root, transaction)
    recovery = {'state': 'landed' if landed else 'none', 'checked_at': _utc_now()}
    if landed is None:
        return _persist(root, transaction, recovery=recovery)
    return _persist(root, transaction, stage='recovered', cycle_commit=landed,
                    recovery=recovery)
