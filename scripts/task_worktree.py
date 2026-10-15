#!/usr/bin/env python3
"""task-worktree: one owned, detached task worktree per parallel worker.

    create_worker(repo, assignment) -> report
    inspect_worker(repo, assignment) -> report
    cleanup_worker(repo, assignment, publication) -> report

Contract: docs/opencode-parallel.md §3 (worker role, permissions and location), §4
(assignment), §5 (result) and §13.4 (the repository-common ticket lock), plus
docs/worktrees.md §1 for the `.claude/worktrees/` area. `repo` is any checkout of the
repository -- the coordinator may itself live in a ticket worktree -- and every operation is
rooted there: nothing here moves, stashes, switches or cleans the coordinator, and
`scripts/worktree.py`'s move-in/hand-back are never reused. A worker root is a linked
worktree of the same repository, detached at the assignment's `base_commit`, under the main
checkout's `.claude/worktrees/` area; the coordinator's branch, index, stash and
`.artel/run/` stay byte-for-byte as they were.

Ownership. Every operation verifies the repository-common ticket lock
(`<git-common-dir>/artel/parallel/<TICKET>.json`, §13.4) read-only and refuses when the lock
is missing (`not_owned`), unreadable (`ambiguous_owner`) or held by another run
(`foreign_owner`). This module never acquires, releases or reclaims ownership; `parallel_state`
owns the lock and this is a match, not a takeover.

The lifecycle. `create_worker` refuses an existing path that is not this assignment's worker
root (`foreign_root`), adopts a complete one idempotently (`created: false`), and reports an
interrupted create (`interrupted_create`) or an unfinished setup (`incomplete_setup`,
`setup_failed`) instead of masking it. It never deletes or overwrites an uncertain path.

    worker_root/.artel/parallel-worker.json      the ownership marker: role `worker`, the
                                                 validated assignment verbatim, the repository
                                                 identity, the refs, the config digest and
                                                 the setup state (Task 6's role context)
    worker_root/.artel/config.json               the coordinator's validated config, copied
                                                 verbatim unless the base commit tracks it
    worker_root/<artifact_root>                  the only evidence root; it must be inside
                                                 the worker root and git-ignored
    refs/artel/parallel/<run_id>/<task>/<attempt>/base      the exact base commit
    refs/artel/parallel/<run_id>/<task>/<attempt>/result    Tasks 8-9 retain the transport
                                                           result here

Configured `setup.commands` run in the worker root, in order, through a shell with an
explicit `cwd`, stopping at the first non-zero exit; a failure leaves the root and a
`setup.failed` record (`command`, `exit_code`, `output` tail, `timed_out`) in the marker.

`cleanup_worker` removes only an owned, complete root whose work is durable: the publication
proof must carry the canonical `result_ref` pointing at `result_commit`, an existing
`cycle_commit` both descending from the base commit, `published: true`, and `evidence` --
the exact set of files under `artifact_root` with their sha256 digests. Worktree content
outside the lifecycle files that `result_commit` does not represent refuses as
`dirty_worktree`; extra, missing or changed evidence refuses as `evidence_mismatch`. Only
then is the root made clean (`git reset --hard <base>` and `git clean -fd`, every path
already proven durable) and removed with `git worktree remove` -- never `--force`. The
namespaced refs are retained as the recoverable record.

Shapes:

    create  {status, schema, ticket, phase, run_id, task, attempt, root, marker,
             artifact_root, base_commit, base_ref, result_ref, detached, created,
             config: {source, sha256}, setup: {status, ran, commands}}
    inspect {status, schema, ticket, phase, run_id, task, attempt, root, state, reasons,
             marker: {present, role, identity_matches, created_at, setup},
             worktree: {registered, detached, head, at_base},
             refs: {base: {name, exists, commit}, result: {name, exists, commit}},
             artifact_root: {path, exists},
             config: {present, sha256, matches_marker},
             uncommitted: {total, paths}}
            state is one of missing, unsafe, foreign, interrupted-create,
            incomplete-setup, setup-failed, unknown, ready, published.
    cleanup {status, schema, ticket, phase, run_id, task, attempt, root, removed,
             cycle_commit, result_commit, retained_refs: {base, result},
             evidence: {verified, artifact_root},
             cleaned: {reset_to, untracked_removed}}

Every failure raises `WorktreeError`, a `ValueError` carrying the `kind` a CLI maps into its
error envelope; nothing is coerced and nothing is dropped.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import parallel_contract

SCHEMA = 1
WORKTREES_DIR = '.claude/worktrees'
MARKER_PATH = '.artel/parallel-worker.json'
CONFIG_PATH = '.artel/config.json'
REF_ROOT = 'refs/artel/parallel'
# The two paths create_worker itself writes; they are lifecycle metadata, not worker work,
# and cleanup's durability proof ignores them (it still verifies the config digest).
LIFECYCLE_PATHS = (MARKER_PATH, CONFIG_PATH)
PUBLICATION_KEYS = ('schema', 'ticket', 'phase', 'run_id', 'task', 'attempt', 'cycle_commit',
                    'result_commit', 'result_ref', 'evidence', 'published')
EVIDENCE_KEYS = ('path', 'sha256')
LOCK_KEYS = ('schema', 'ticket', 'run_id', 'phase', 'token', 'acquired_at')
SHA256_RE = re.compile(r'[0-9a-f]{64}\Z')
SETUP_TIMEOUT = 600
OUTPUT_TAIL = 2000
UNCOMMITTED_LIMIT = 50
# A setup command inherits the worker environment, but never a git redirection that would
# point its git calls at the coordinator's index, work tree or object store.
GIT_REDIRECTS = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_PREFIX')
# state -> error kind, for the two functions that must refuse an unusable root.
CREATE_KINDS = {
    'unsafe': 'unsafe_root',
    'foreign': 'foreign_root',
    'interrupted-create': 'interrupted_create',
    'incomplete-setup': 'incomplete_setup',
    'setup-failed': 'setup_failed',
    'unknown': 'unknown_worker',
}
CLEANUP_KINDS = dict(CREATE_KINDS, missing='not_created')


class WorktreeError(ValueError):
    """A lifecycle violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise WorktreeError(kind, message)


# --- validation helpers -------------------------------------------------------------------

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


def _relative(value, what, kind):
    """A non-empty relative path that does not escape upward on its own."""
    if not isinstance(value, str) or not value:
        _fail(kind, '{} is not a non-empty string: {!r}'.format(what, value))
    if os.path.isabs(value):
        _fail(kind, '{} is not a relative path: {!r}'.format(what, value))
    normalized = os.path.normpath(value)
    if normalized == '..' or normalized.startswith('..' + os.sep):
        _fail(kind, '{} escapes its root: {!r}'.format(what, value))
    if value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _under(base, target):
    """`target` resolves strictly inside `base`."""
    base = os.path.realpath(str(base))
    target = os.path.realpath(str(target))
    return target != base and target.startswith(base + os.sep)


def _tail(text):
    return text[-OUTPUT_TAIL:] if len(text) > OUTPUT_TAIL else text


# --- git plumbing -------------------------------------------------------------------------

def _run(argv, cwd, check=True, text=True, env=None):
    """One subprocess: an explicit argument array and an explicit cwd, always."""
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=text, env=env)
    if check and proc.returncode != 0:
        detail = proc.stderr or proc.stdout or ''
        if isinstance(detail, bytes):
            detail = detail.decode('utf-8', 'replace')
        _fail('git_failed', '{} failed: {}'.format(' '.join(argv), detail.strip()))
    return proc


def _git(repo, *args, **kwargs):
    check = kwargs.pop('check', True)
    text = kwargs.pop('text', True)
    env = kwargs.pop('env', None)
    return _run(['git', '-C', str(repo)] + [str(a) for a in args], cwd=repo, check=check,
                text=text, env=env)


def _out(repo, *args):
    return _git(repo, *args).stdout.strip()


def _common_dir(repo):
    """The repository-common Git directory of `repo` (shared by every linked worktree)."""
    try:
        proc = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute',
                               '--git-common-dir'],
                              cwd=str(repo), capture_output=True, text=True)
    except OSError as exc:
        _fail('not_a_repository', 'cannot run git in {}: {}'.format(repo, exc))
    if proc.returncode != 0:
        _fail('not_a_repository', 'git rev-parse --git-common-dir failed in {}: {}'.format(
            repo, (proc.stderr or proc.stdout).strip()))
    return Path(proc.stdout.strip())


def _worktrees(repo):
    """[{'path', 'branch', 'detached'}] from `git worktree list --porcelain`, main first."""
    entries = []
    for block in _out(repo, 'worktree', 'list', '--porcelain').split('\n\n'):
        entry = {'path': None, 'branch': None, 'detached': False}
        for line in block.splitlines():
            if line.startswith('worktree '):
                entry['path'] = line[len('worktree '):]
            elif line.startswith('branch refs/heads/'):
                entry['branch'] = line[len('branch refs/heads/'):]
            elif line.strip() == 'detached':
                entry['detached'] = True
        if entry['path']:
            entries.append(entry)
    if not entries:
        _fail('not_a_repository', 'no worktrees are listed for {}'.format(repo))
    return entries


def _entry_for(entries, root):
    target = os.path.realpath(str(root))
    for entry in entries:
        if os.path.realpath(entry['path']) == target:
            return entry
    return None


def _ref_value(repo, name):
    proc = _git(repo, 'rev-parse', '--verify', '--quiet', name, check=False)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _commit_exists(repo, commit):
    return _git(repo, 'cat-file', '-e', commit + '^{commit}',
                check=False).returncode == 0


def _is_ancestor(repo, older, newer):
    return _git(repo, 'merge-base', '--is-ancestor', older, newer,
                check=False).returncode == 0


def _ref_name(assignment, suffix):
    """`refs/artel/parallel/<run_id>/<task>/<attempt>/<suffix>` -- the run's own namespace."""
    return '{}/{}/{}/{}/{}'.format(REF_ROOT, assignment['run_id'], assignment['task'],
                                   assignment['attempt'], suffix)


# --- assignment, ownership, paths ---------------------------------------------------------

def _assignment(value):
    """The schema-1 assignment of docs/opencode-parallel.md §4, or `invalid_assignment`."""
    try:
        return parallel_contract.validate_assignment(value)
    except parallel_contract.ContractError as exc:
        raise WorktreeError(exc.kind, exc.message)


def _verify_ownership(common, assignment):
    """Match the repository-common ticket lock to this run, read-only (§13.4).

    `parallel_state` owns the lock; this only reads it. A missing lock is `not_owned`, an
    unreadable one `ambiguous_owner` and another run's `foreign_owner` -- never a takeover,
    never a reclaim.
    """
    path = common / 'artel' / 'parallel' / (assignment['ticket'] + '.json')
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _fail('not_owned', 'the ticket {} is not owned: no lock at {}'.format(
            assignment['ticket'], path))
    except (OSError, UnicodeDecodeError) as exc:
        _fail('ambiguous_owner', 'the ticket lock at {} cannot be read: {}'.format(path, exc))
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail('ambiguous_owner', 'the ticket lock at {} is not valid JSON: {}'.format(path, exc))
    if not isinstance(value, dict):
        _fail('ambiguous_owner', 'the ticket lock at {} is not an object'.format(path))
    missing = [name for name in LOCK_KEYS if name not in value]
    if missing:
        _fail('ambiguous_owner', 'the ticket lock at {} is missing {}'.format(
            path, ', '.join('`{}`'.format(name) for name in missing)))
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA \
            or value['ticket'] != assignment['ticket']:
        _fail('ambiguous_owner', 'the ticket lock at {} is not a lock for {}'.format(
            path, assignment['ticket']))
    if value['run_id'] != assignment['run_id'] or value['phase'] != assignment['phase']:
        _fail('foreign_owner', 'the ticket {} is owned by run {} (phase {}), not by run {} '
                               '(phase {})'.format(assignment['ticket'], value['run_id'],
                                                   value['phase'], assignment['run_id'],
                                                   assignment['phase']))
    return value


def _root_paths(assignment):
    root = Path(os.path.normpath(assignment['worker_root']))
    artifact = Path(os.path.normpath(assignment['artifact_root']))
    return root, artifact


def _check_root_shape(main, entries, root):
    """The worker root lives in the main checkout's worktree area, in no existing worktree."""
    if os.path.islink(str(root)):
        _fail('unsafe_root', '{} is a symlink; refusing to treat it as a worker root'.format(
            root))
    area = main / WORKTREES_DIR
    if not _under(area, root):
        _fail('invalid_root', 'the worker root {} is not under {}'.format(root, area))
    for entry in entries[1:]:
        if os.path.realpath(entry['path']) == os.path.realpath(str(root)):
            continue  # the root's own registration, if it exists already
        if _under(entry['path'], root):
            _fail('invalid_root', 'the worker root {} is inside the worktree {}'.format(
                root, entry['path']))


def _check_artifact_shape(root, artifact):
    if not _under(root, artifact):
        _fail('invalid_root', 'the artifact root {} is not inside the worker root {}'.format(
            artifact, root))


def _check_artifact_ignored(root, artifact):
    """The evidence root must be ignored, or its files would block the worktree's removal."""
    relative = os.path.relpath(str(artifact), str(root))
    proc = _git(root, 'check-ignore', '-q', '--', relative, check=False)
    if proc.returncode != 0:
        _fail('invalid_root', 'the artifact root {} is not git-ignored by {}; worker '
                              'evidence would surface as uncommitted work'.format(
                                  artifact, root))


# --- config -------------------------------------------------------------------------------

def _setup_commands(parsed, source):
    setup = parsed.get('setup')
    if setup is None:
        return []
    if not isinstance(setup, dict):
        _fail('invalid_config', '{} `setup` is not an object'.format(source))
    commands = setup.get('commands')
    if commands is None:
        return []
    if not isinstance(commands, list):
        _fail('invalid_config', '{} `setup.commands` is not an array'.format(source))
    for command in commands:
        if not isinstance(command, str) or not command or command != command.strip() \
                or any(char in command for char in '\x00\n\r'):
            _fail('invalid_config', '{} `setup.commands` carries a command that is not a '
                                    'non-empty single line'.format(source))
    return list(commands)


def _tracked_config(repo, assignment):
    """The config the base commit tracks, or None when it tracks none."""
    proc = _git(repo, 'show', '{}:{}'.format(assignment['base_commit'], CONFIG_PATH),
                check=False, text=False)
    return proc.stdout if proc.returncode == 0 else None


def _adopted_config(repo, assignment, marker):
    """The config a complete worker root already carries, from its own marker."""
    recorded = (marker or {}).get('config')
    if not isinstance(recorded, dict) or not isinstance(recorded.get('sha256'), str):
        return {'source': None, 'sha256': None, 'bytes': None, 'commands': [],
                'path': CONFIG_PATH}
    tracked = _tracked_config(repo, assignment) is not None
    return {'source': 'checkout' if tracked else 'coordinator',
            'sha256': recorded['sha256'], 'bytes': None, 'commands': [],
            'path': CONFIG_PATH}


def _plan_config(repo, assignment):
    """The validated coordinator config, and how the worker gets it.

    `source` is `coordinator` (copied verbatim), `checkout` (the base commit tracks the same
    bytes; never overwritten) or `None` (no config: the worker's hooks stay inert).
    """
    source_path = Path(repo) / CONFIG_PATH
    try:
        data = source_path.read_bytes()
    except FileNotFoundError:
        return {'source': None, 'sha256': None, 'bytes': None, 'commands': [],
                'path': CONFIG_PATH}
    except OSError as exc:
        _fail('invalid_config', 'cannot read {}: {}'.format(source_path, exc))
    try:
        parsed = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        _fail('invalid_config', '{} is not valid JSON'.format(source_path))
    if not isinstance(parsed, dict):
        _fail('invalid_config', '{} is not a JSON object'.format(source_path))
    commands = _setup_commands(parsed, source_path)
    tracked = _tracked_config(repo, assignment)
    if tracked is not None:
        if tracked == data:
            return {'source': 'checkout', 'sha256': _sha256(data), 'bytes': None,
                    'commands': commands, 'path': CONFIG_PATH}
        _fail('config_conflict', 'the worker checkout tracks a different {}; refusing to '
                                 'overwrite it'.format(CONFIG_PATH))
    return {'source': 'coordinator', 'sha256': _sha256(data), 'bytes': data,
            'commands': commands, 'path': CONFIG_PATH}


def _setup_env():
    return {name: value for name, value in os.environ.items()
            if name not in GIT_REDIRECTS}


def _run_setup(root, commands):
    """Run the configured commands in the worker root; the first failure is returned."""
    shell = shutil.which('sh') or '/bin/sh'
    for command in commands:
        try:
            proc = subprocess.run([shell, '-c', command], cwd=str(root), capture_output=True,
                                  text=True, timeout=SETUP_TIMEOUT, env=_setup_env())
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ''
            if isinstance(output, bytes):
                output = output.decode('utf-8', 'replace')
            return {'command': command, 'exit_code': None, 'output': _tail(output),
                    'timed_out': True}
        except OSError as exc:
            return {'command': command, 'exit_code': None, 'output': str(exc),
                    'timed_out': False}
        if proc.returncode != 0:
            return {'command': command, 'exit_code': proc.returncode,
                    'output': _tail(proc.stdout + proc.stderr), 'timed_out': False}
    return None


# --- the marker ---------------------------------------------------------------------------

def _atomic_write(path, text):
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


def _write_marker(root, marker):
    _atomic_write(root / MARKER_PATH, json.dumps(marker, indent=2, sort_keys=True) + '\n')


def _read_marker(root):
    try:
        text = (root / MARKER_PATH).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _marker(assignment, common, main, config, setup):
    return {
        'schema': SCHEMA,
        'role': 'worker',
        'created_at': _utc_now(),
        'assignment': assignment,
        'repository': {'common_dir': str(common), 'main': str(main)},
        'base_ref': _ref_name(assignment, 'base'),
        'result_ref': _ref_name(assignment, 'result'),
        'config': None if config['source'] is None else {'path': config['path'],
                                                         'sha256': config['sha256']},
        'setup': setup,
    }


# --- probing ------------------------------------------------------------------------------

def _probe(repo, root, assignment, entries):
    """Read-only findings about what is at `root`; nothing here writes or deletes."""
    probe = {'exists': os.path.lexists(str(root)), 'symlink': os.path.islink(str(root)),
             'is_dir': root.is_dir(), 'marker': None, 'identity_matches': False,
             'registered': False, 'detached': False, 'head': None, 'at_base': False,
             'clean': False, 'base_ref': None, 'result_ref': None,
             'config_present': False, 'config_sha256': None, 'config_matches': False,
             'setup': None}
    if not probe['exists']:
        return probe
    probe['base_ref'] = _ref_value(repo, _ref_name(assignment, 'base'))
    probe['result_ref'] = _ref_value(repo, _ref_name(assignment, 'result'))
    if probe['is_dir'] and _entry_for(entries, root) is not None:
        probe['registered'] = True
        head = _git(root, 'rev-parse', 'HEAD', check=False)
        probe['head'] = head.stdout.strip() or None if head.returncode == 0 else None
        probe['detached'] = _git(root, 'symbolic-ref', '-q', 'HEAD',
                                 check=False).returncode != 0
        probe['at_base'] = probe['head'] == assignment['base_commit']
        status = _git(root, 'status', '--porcelain', '--untracked-files=all', check=False)
        probe['clean'] = status.returncode == 0 and not status.stdout.strip()
    config_path = root / CONFIG_PATH
    if config_path.is_file():
        probe['config_present'] = True
        probe['config_sha256'] = _sha256(config_path.read_bytes())
    marker = _read_marker(root)
    probe['marker'] = marker
    if marker is not None:
        probe['identity_matches'] = (marker.get('schema') == SCHEMA
                                     and marker.get('role') == 'worker'
                                     and marker.get('assignment') == assignment)
        setup = marker.get('setup')
        probe['setup'] = setup if isinstance(setup, dict) else None
        recorded = marker.get('config')
        if recorded is None:
            probe['config_matches'] = not probe['config_present']
        elif isinstance(recorded, dict) and isinstance(recorded.get('sha256'), str):
            probe['config_matches'] = (probe['config_present']
                                       and probe['config_sha256'] == recorded['sha256'])
    return probe


def _state(probe, assignment):
    """`(state, reasons)` for what the probe found -- never a refusal by itself."""
    if not probe['exists']:
        return 'missing', ['no worker root at {}'.format(assignment['worker_root'])]
    if probe['symlink']:
        return 'unsafe', ['{} is a symlink'.format(assignment['worker_root'])]
    if not probe['is_dir']:
        return 'foreign', ['{} is not a directory'.format(assignment['worker_root'])]
    marker = probe['marker']
    if marker is None:
        if probe['registered'] and probe['detached'] and probe['at_base'] \
                and probe['clean'] and probe['base_ref'] == assignment['base_commit']:
            return 'interrupted-create', [
                '{} is a registered worktree at the base commit but carries no marker; '
                'inspect it before reusing the path'.format(assignment['worker_root'])]
        return 'foreign', ['{} is not this assignment\'s worker root (no marker)'.format(
            assignment['worker_root'])]
    if not probe['identity_matches']:
        return 'foreign', ['the marker at {} does not carry this assignment'.format(
            assignment['worker_root'])]
    setup = probe['setup'] or {}
    status = setup.get('status')
    if status == 'failed':
        failure = setup.get('failed') or {}
        return 'setup-failed', ['setup failed: {!r} (exit {})'.format(
            failure.get('command'), failure.get('exit_code'))]
    if status != 'complete':
        return 'incomplete-setup', ['the marker records setup {!r}; the root is retained'.format(
            status)]
    if not probe['registered']:
        return 'unknown', ['{} is not a registered worktree of this repository'.format(
            assignment['worker_root'])]
    if not probe['detached']:
        return 'unknown', ['the worker root is not on a detached HEAD']
    if probe['base_ref'] != assignment['base_commit']:
        return 'unknown', ['the base ref does not point at the base commit']
    if not probe['config_matches']:
        return 'unknown', ['the copied config no longer matches the marker digest']
    if probe['result_ref'] is not None:
        return 'published', []
    return 'ready', []


# --- create -------------------------------------------------------------------------------

def _create_report(assignment, root, artifact, config, marker, created):
    setup = marker.get('setup') or {}
    return {
        'status': 'ok',
        'schema': SCHEMA,
        'ticket': assignment['ticket'],
        'phase': assignment['phase'],
        'run_id': assignment['run_id'],
        'task': assignment['task'],
        'attempt': assignment['attempt'],
        'root': str(root),
        'marker': str(root / MARKER_PATH),
        'artifact_root': str(artifact),
        'base_commit': assignment['base_commit'],
        'base_ref': _ref_name(assignment, 'base'),
        'result_ref': _ref_name(assignment, 'result'),
        'detached': True,
        'created': created,
        'config': {'source': config['source'], 'sha256': config['sha256']},
        'setup': {'status': setup.get('status', 'complete'), 'ran': bool(setup.get('ran')),
                  'commands': setup.get('commands', 0)},
    }


def _ensure_excluded(main, root):
    """Keep the worktree area out of the main checkout's `git status`, locally only."""
    probe = os.path.relpath(str(root), str(main))
    if _git(main, 'check-ignore', '-q', '--', probe, check=False).returncode == 0:
        return
    exclude = Path(_out(main, 'rev-parse', '--git-path', 'info/exclude'))
    if not exclude.is_absolute():
        exclude = main / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open('a', encoding='utf-8') as handle:
        handle.write('\n/{}/\n'.format(WORKTREES_DIR))


def create_worker(repo, assignment):
    """Create the assignment's detached worker worktree, or adopt a complete one.

    The root is a linked worktree of `repo`'s repository, detached at `base_commit`, under
    the main checkout's `.claude/worktrees/` area, owned by the run that holds the
    repository-common ticket lock. The marker, the copied config and the namespaced base ref
    are written before any configured setup runs; the setup state is recorded in the marker
    so a restart can tell a complete root from an interrupted or failed one.
    """
    value = _assignment(assignment)
    root, artifact = _root_paths(value)
    common = _common_dir(repo)
    entries = _worktrees(repo)
    main = Path(entries[0]['path'])
    _check_root_shape(main, entries, root)
    _check_artifact_shape(root, artifact)
    _verify_ownership(common, value)
    if not _commit_exists(repo, value['base_commit']):
        _fail('unknown_base', 'the base commit {} is not a commit in {}'.format(
            value['base_commit'], repo))
    existing = _ref_value(repo, _ref_name(value, 'base'))
    if existing is not None and existing != value['base_commit']:
        _fail('ref_conflict', 'the base ref {} already points at {}, not at {}'.format(
            _ref_name(value, 'base'), existing, value['base_commit']))
    probe = _probe(repo, root, value, entries)
    state, reasons = _state(probe, value)
    if state in ('ready', 'published'):
        _ensure_excluded(main, root)
        adopted = _adopted_config(repo, value, probe['marker'])
        return _create_report(value, root, artifact, adopted, probe['marker'], created=False)
    if state != 'missing':
        _fail(CREATE_KINDS[state], reasons[0])
    config = _plan_config(repo, value)
    _ensure_excluded(main, root)
    if _entry_for(entries, root) is not None:
        _git(repo, 'worktree', 'prune')  # a registration whose directory is gone
    _git(repo, 'worktree', 'add', '--detach', str(root), value['base_commit'])
    try:
        _check_artifact_ignored(root, artifact)
        if existing is None:
            _git(repo, 'update-ref', _ref_name(value, 'base'), value['base_commit'],
                 '0' * 40)
        if config['bytes'] is not None:
            destination = root / CONFIG_PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(config['bytes'])
    except BaseException:
        # Nothing of the worker's exists yet: the marker is written only after this point,
        # so a root without one is never adopted -- remove the fresh, empty root instead.
        _git(repo, 'worktree', 'remove', str(root), check=False)
        raise
    marker = _marker(value, common, main, config,
                     {'status': 'pending', 'ran': False, 'commands': len(config['commands']),
                      'failed': None})
    _write_marker(root, marker)
    artifact.mkdir(parents=True, exist_ok=True)
    failure = _run_setup(root, config['commands'])
    if failure is not None:
        marker['setup'] = {'status': 'failed', 'ran': True,
                           'commands': len(config['commands']), 'failed': failure}
        _write_marker(root, marker)
        _fail('setup_failed', 'setup command {!r} failed with exit {} in {}: {}'.format(
            failure['command'], failure['exit_code'], root, failure['output']))
    marker['setup'] = {'status': 'complete', 'ran': bool(config['commands']),
                       'commands': len(config['commands']), 'failed': None}
    _write_marker(root, marker)
    return _create_report(value, root, artifact, config, marker, created=True)


# --- inspect ------------------------------------------------------------------------------

def inspect_worker(repo, assignment):
    """Read-only state of the assignment's worker root; never writes, never deletes.

    `state` is the one fact a restart reconciles on: `missing`, `unsafe`, `foreign`,
    `interrupted-create`, `incomplete-setup`, `setup-failed`, `unknown`, `ready` or
    `published`. An unreadable lock or another run's lock is refused rather than reported.
    """
    value = _assignment(assignment)
    root, artifact = _root_paths(value)
    common = _common_dir(repo)
    entries = _worktrees(repo)
    _verify_ownership(common, value)
    probe = _probe(repo, root, value, entries)
    state, reasons = _state(probe, value)
    marker = probe['marker'] or {}
    uncommitted = {'total': 0, 'paths': []}
    if probe['registered']:
        lines = [line for line in _git(root, 'status', '--porcelain', '--untracked-files=all',
                                       check=False).stdout.splitlines() if line.strip()]
        uncommitted = {'total': len(lines),
                       'paths': [line[3:] for line in lines][:UNCOMMITTED_LIMIT]}
    return {
        'status': 'ok',
        'schema': SCHEMA,
        'ticket': value['ticket'],
        'phase': value['phase'],
        'run_id': value['run_id'],
        'task': value['task'],
        'attempt': value['attempt'],
        'root': str(root),
        'state': state,
        'reasons': reasons,
        'marker': {'present': probe['marker'] is not None,
                   'role': marker.get('role'),
                   'identity_matches': probe['identity_matches'],
                   'created_at': marker.get('created_at'),
                   'setup': probe['setup']},
        'worktree': {'registered': probe['registered'], 'detached': probe['detached'],
                     'head': probe['head'], 'at_base': probe['at_base']},
        'refs': {
            'base': {'name': _ref_name(value, 'base'), 'exists': probe['base_ref'] is not None,
                     'commit': probe['base_ref']},
            'result': {'name': _ref_name(value, 'result'),
                       'exists': probe['result_ref'] is not None,
                       'commit': probe['result_ref']},
        },
        'artifact_root': {'path': str(artifact), 'exists': artifact.is_dir()},
        'config': {'present': probe['config_present'], 'sha256': probe['config_sha256'],
                   'matches_marker': probe['config_matches']},
        'uncommitted': uncommitted,
    }


# --- cleanup ------------------------------------------------------------------------------

def _publication(value, assignment):
    """The publication proof of Tasks 8-9, or `invalid_publication`."""
    kind = 'invalid_publication'
    _object(value, 'the publication', kind)
    _keys(value, PUBLICATION_KEYS, 'the publication', kind)
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail(kind, 'the publication schema must be {}: {!r}'.format(SCHEMA, value['schema']))
    for name in ('ticket', 'phase', 'run_id', 'task', 'attempt'):
        if value[name] != assignment[name]:
            _fail(kind, 'the publication {} does not match the assignment: {!r} != {!r}'.format(
                name, value[name], assignment[name]))
    for name in ('cycle_commit', 'result_commit'):
        commit = value[name]
        if not isinstance(commit, str) or not parallel_contract.COMMIT_RE.match(commit):
            _fail(kind, 'the publication {} is not a full 40-hex commit id: {!r}'.format(
                name, commit))
    if value['result_ref'] != _ref_name(assignment, 'result'):
        _fail(kind, 'the publication result_ref is not the run\'s canonical ref {}: {!r}'.format(
            _ref_name(assignment, 'result'), value['result_ref']))
    if value['published'] is not True:
        _fail(kind, 'the publication must assert `published: true`')
    if not isinstance(value['evidence'], list):
        _fail(kind, 'the publication evidence is not an array: {!r}'.format(value['evidence']))
    seen = set()
    for entry in value['evidence']:
        _object(entry, 'an evidence entry', kind)
        _keys(entry, EVIDENCE_KEYS, 'an evidence entry', kind)
        path = _relative(entry['path'], 'an evidence path', kind)
        if not isinstance(entry['sha256'], str) or not SHA256_RE.match(entry['sha256']):
            _fail(kind, 'the evidence {} is not a sha256 digest: {!r}'.format(
                path, entry['sha256']))
        if path in seen:
            _fail(kind, 'the evidence {} is listed twice'.format(path))
        seen.add(path)
    return value


def _verify_evidence(artifact, evidence):
    """The evidence under `artifact_root` is exactly what the publication collected."""
    listed = {entry['path']: entry['sha256'] for entry in evidence}
    if not artifact.is_dir():
        if listed:
            _fail('evidence_mismatch', 'the artifact root {} does not exist but the '
                                       'publication lists evidence'.format(artifact))
        return 0
    found = {}
    for dirpath, dirnames, filenames in os.walk(str(artifact)):
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                _fail('evidence_mismatch', 'the artifact root carries the symlink {}'.format(
                    os.path.join(dirpath, name)))
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                _fail('evidence_mismatch', 'the artifact root carries the symlink {}'.format(
                    path))
            found[path.relative_to(artifact).as_posix()] = _sha256(path.read_bytes())
    extra = sorted(set(found) - set(listed))
    if extra:
        _fail('evidence_mismatch', 'the artifact root carries evidence the publication does '
                                   'not list: {}'.format(', '.join(extra)))
    missing = sorted(set(listed) - set(found))
    if missing:
        _fail('evidence_mismatch', 'the publication lists evidence that is not in the '
                                   'artifact root: {}'.format(', '.join(missing)))
    for path in sorted(listed):
        if found[path] != listed[path]:
            _fail('evidence_mismatch', 'the evidence {} changed after collection'.format(path))
    return len(listed)


def _durable_offenders(root, commit):
    """Worktree paths whose content `commit` does not represent; empty when durable.

    The worktree is staged into a temporary index -- the worker's own index is never touched
    -- and compared to `commit`; the lifecycle files create_worker writes are excluded.
    """
    directory = tempfile.mkdtemp(prefix='artel-task-worktree-')
    try:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(directory, 'index'))
        try:
            _git(root, 'add', '-A', '.', env=env)
            diff = _git(root, 'diff', '--name-status', '-z', commit, env=env).stdout
        except WorktreeError as exc:
            _fail('dirty_worktree', 'cannot prove the worker root durable: {}'.format(exc))
        fields = diff.split('\0')
        paths = []
        index = 0
        while index < len(fields):
            status = fields[index]
            if not status:
                index += 1
                continue
            if status[0] in 'RC':
                if index + 2 < len(fields):
                    paths.extend([fields[index + 1], fields[index + 2]])
                index += 3
            else:
                if index + 1 < len(fields):
                    paths.append(fields[index + 1])
                index += 2
        return sorted(path for path in paths if path and path not in LIFECYCLE_PATHS)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def cleanup_worker(repo, assignment, publication):
    """Remove the owned worker root whose code and evidence the publication proves durable.

    The root must be this assignment's complete worker root and the publication must carry
    the run's canonical result ref, the transport result it retains, an integrated cycle
    commit descending from the base, and the exact evidence digests. Any worktree content
    `result_commit` does not represent refuses as `dirty_worktree`; a root that is not
    provably durable is never cleaned, reset or force-removed. The namespaced refs stay.
    """
    value = _assignment(assignment)
    proof = _publication(publication, value)
    root, artifact = _root_paths(value)
    common = _common_dir(repo)
    entries = _worktrees(repo)
    _verify_ownership(common, value)
    probe = _probe(repo, root, value, entries)
    state, reasons = _state(probe, value)
    if state not in ('ready', 'published'):
        _fail(CLEANUP_KINDS[state], reasons[0] if reasons else 'the worker root is not cleanable')
    if _ref_value(repo, proof['result_ref']) != proof['result_commit']:
        _fail('unpublished', 'the result ref {} does not point at {}'.format(
            proof['result_ref'], proof['result_commit']))
    if not _commit_exists(repo, proof['result_commit']):
        _fail('unpublished', 'the result commit {} does not exist in {}'.format(
            proof['result_commit'], repo))
    if not _commit_exists(repo, proof['cycle_commit']):
        _fail('unpublished', 'the cycle commit {} does not exist in {}'.format(
            proof['cycle_commit'], repo))
    if not _is_ancestor(repo, value['base_commit'], proof['result_commit']):
        _fail('unpublished', 'the result commit {} does not descend from the base {}'.format(
            proof['result_commit'], value['base_commit']))
    if not _is_ancestor(repo, value['base_commit'], proof['cycle_commit']):
        _fail('unpublished', 'the cycle commit {} does not descend from the base {}'.format(
            proof['cycle_commit'], value['base_commit']))
    verified = _verify_evidence(artifact, proof['evidence'])
    offenders = _durable_offenders(root, proof['result_commit'])
    if offenders:
        _fail('dirty_worktree', 'the worker root carries content the publication does not '
                                'represent: {}'.format(', '.join(offenders)))
    cleaned = {'reset_to': None, 'untracked_removed': []}
    status = [line for line in _git(root, 'status', '--porcelain', '--untracked-files=all')
              .stdout.splitlines() if line.strip()]
    if status:
        cleaned['untracked_removed'] = sorted(line[3:] for line in status
                                              if line.startswith('??'))
        _git(root, 'reset', '--hard', value['base_commit'])
        _git(root, 'clean', '-fd')
        cleaned['reset_to'] = value['base_commit']
    proc = _git(repo, 'worktree', 'remove', str(root), check=False)
    if proc.returncode != 0:
        _fail('removal_failed', 'git worktree remove failed for {}: {}'.format(
            root, (proc.stderr or proc.stdout).strip()))
    return {
        'status': 'ok',
        'schema': SCHEMA,
        'ticket': value['ticket'],
        'phase': value['phase'],
        'run_id': value['run_id'],
        'task': value['task'],
        'attempt': value['attempt'],
        'root': str(root),
        'removed': True,
        'cycle_commit': proof['cycle_commit'],
        'result_commit': proof['result_commit'],
        'retained_refs': {'base': _ref_name(value, 'base'),
                          'result': _ref_name(value, 'result')},
        'evidence': {'verified': verified, 'artifact_root': str(artifact)},
        'cleaned': cleaned,
    }
