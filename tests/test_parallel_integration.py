#!/usr/bin/env python3
"""scripts/parallel_integration.py: reviewed transport and serialized integration.

Contract: docs/opencode-parallel.md §13.8 (the integration transaction and its shapes),
§6 (task states), §13.1-§13.7, the approved serialized integration/publication section of
the parallel plan, and the Task 8 handoff.

Every test builds a real repository: a real coordinator worktree, the repository-common
ticket lock, a real run manifest at the conventional path, and a real worker worktree from
the Task 5 lifecycle. The transport commit, the cherry-pick, the commit hooks and the
reflogs are git's own -- a refusal fixture is a real refusing hook, never a fake commit
object. The facts worth the most: the transport carries only the allowlisted code and never
branch history, the final cycle commit is exactly one and attributable through the
transaction-keyed reflog, a conflict or a red combined-tree gate preserves everything it
touched, and recovery recognizes a commit that landed before its receipt exactly once.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_contract  # noqa: E402
import parallel_integration  # noqa: E402
import parallel_state  # noqa: E402
import task_worktree  # noqa: E402

TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'
TASK = '3.1'
ATTEMPT = 1
PHASE = 2
COORD_BRANCH = 'feature/PROJ-123'
REF_ROOT = 'refs/artel/parallel'
TASKLIST = 'specs/.current/PROJ-123/tasklist.md'
NOTES = 'specs/.current/PROJ-123/implementation-notes.md'
SUBJECT = 'feat: PROJ-123 - do a thing'


def git(cwd, *args, check=True, env=None):
    proc = subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=False,
                          capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise AssertionError('git {} failed in {}: {}'.format(
            ' '.join(str(a) for a in args), cwd, proc.stderr or proc.stdout))
    return proc.stdout.strip()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def rejects(test, kind, call):
    """Assert `call` raises IntegrationError of `kind`; return the exception."""
    with test.assertRaises(parallel_integration.IntegrationError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind, str(caught.exception))
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


class RepoCase(unittest.TestCase):
    """A real repository, a linked coordinator, the lock, a manifest and a worker root."""

    IGNORE = '.artel/run/\nbuild/\n'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.space = Path(os.path.realpath(self._tmp.name))
        self.main = self.space / 'host repo'          # whitespace is part of the fixture
        git(self.space, 'init', '-q', str(self.main))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.main, 'config', key, value)
        self.write('.gitignore', self.IGNORE)
        self.write('app.txt', 'v1\n')
        self.write('other.txt', 'unrelated\n')
        self.write(TASKLIST, '- [ ] 3.1 do a thing\n')
        git(self.main, 'add', '-A')
        git(self.main, 'commit', '-q', '-m', 'init')
        git(self.main, 'branch', '-M', 'trunk')      # keeps `main` free for the guard test
        self.base = git(self.main, 'rev-parse', 'HEAD')
        self.coord = self.main / '.claude' / 'worktrees' / 'PROJ-123'
        git(self.main, 'worktree', 'add', '-q', '-b', COORD_BRANCH, str(self.coord))
        self.write_config()
        parallel_state.acquire_ticket(
            str(self.coord), TICKET,
            {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE})
        self.manifest_path = (self.coord / '.artel' / 'run' / TICKET / 'parallel' / RUN_ID /
                              'manifest.json')
        self.write_manifest()
        self.worker = self.worker_root()
        task_worktree.create_worker(str(self.coord), self.assignment())

    def tearDown(self):
        self._tmp.cleanup()

    # --- fixtures ---------------------------------------------------------------------

    def write(self, rel, text, base=None):
        path = (base or self.main) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def read(self, rel, base=None):
        return ((base or self.main) / rel).read_text(encoding='utf-8')

    def write_config(self, verify=None, base=None):
        value = {'specs': {'dir': 'specs/.current'}}
        if verify:
            value['verify'] = verify
        path = (base or self.coord) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        return path

    def worker_root(self, task=TASK, attempt=ATTEMPT, run_id=RUN_ID):
        return (self.main / '.claude' / 'worktrees' / 'parallel tasks' / TICKET / run_id /
                '{}-a{}'.format(task, attempt))

    def artifact_root(self, task=TASK, attempt=ATTEMPT, run_id=RUN_ID):
        return self.worker_root(task, attempt, run_id) / '.artel' / 'run' / 'parallel' / \
            '{}-a{}'.format(task, attempt)

    def result_ref(self, task=TASK, attempt=ATTEMPT, run_id=RUN_ID):
        return '{}/{}/{}/{}/result'.format(REF_ROOT, run_id, task, attempt)

    def assignment(self, **overrides):
        value = {
            'schema': 1,
            'ticket': TICKET,
            'phase': PHASE,
            'task': TASK,
            'run_id': RUN_ID,
            'attempt': ATTEMPT,
            'claim': 'artel@host',
            'base_commit': self.base,
            'files': ['app.txt'],
            'tests': [],
            'route': 'light',
            'model': None,
            'worker_root': str(self.worker_root()),
            'artifact_root': str(self.artifact_root()),
        }
        value.update(overrides)
        return value

    def review(self, route='light', **overrides):
        if route == 'full':
            value = {'required': True, 'route': 'full', 'verdict': 'passed', 'rounds': 1}
        else:
            value = {'required': False, 'route': 'light', 'verdict': 'skipped', 'rounds': 0}
        value.update(overrides)
        return value

    def result(self, assignment=None, **overrides):
        value = dict(assignment or self.assignment())
        value.update({
            'status': 'succeeded',
            'changed_files': ['app.txt'],
            'verify': [{'command': 'true', 'ok': True, 'output': ''}],
            'review': self.review(route=value['route']),
            'report': None,
            'deviations': [],
        })
        value.update(overrides)
        return value

    def prepare(self, assignment=None, result=None, **overrides):
        assignment = assignment or self.assignment()
        if result is None:
            result = self.result(assignment, **overrides)
        return parallel_integration.prepare_transport(str(self.coord), assignment, result)

    def make_worker(self, name, **overrides):
        """A second worker root whose marker matches a customized assignment.

        The lifecycle marker carries the assignment verbatim, so a test that needs a
        different route or declared files gets its own root.
        """
        root = self.worker_root().parent / '{}-a{}-{}'.format(TASK, ATTEMPT, name)
        value = self.assignment(
            worker_root=str(root),
            artifact_root=str(root / '.artel' / 'run' / 'parallel' /
                              '{}-a{}'.format(TASK, ATTEMPT)))
        value.update(overrides)
        task_worktree.create_worker(str(self.coord), value)
        return value, root

    def apply(self, prepared, assignment=None):
        return parallel_integration.apply_transport(str(self.coord),
                                                    assignment or self.assignment(), prepared)

    def commit(self, transaction, subject=SUBJECT):
        return parallel_integration.commit_cycle(str(self.coord), transaction, subject)

    def recover(self, transaction):
        return parallel_integration.recover_cycle(str(self.coord), transaction)

    def prepare_apply(self, **overrides):
        return self.apply(self.prepare(**overrides))

    # --- manifests --------------------------------------------------------------------

    CHAIN = ('reserved', 'preparing', 'running', 'verifying/reviewing', 'prepared',
             'integrating', 'published')

    def chain(self, manifest, task, state):
        """Drive `task` from nothing to `state` along the §6 sequence."""
        expected = None
        for target in self.CHAIN:
            manifest = parallel_state.transition_task(manifest, task, expected, target,
                                                      {'decision': None})
            expected = target
            if target == state:
                return manifest
        raise AssertionError('unknown state: {}'.format(state))

    def manifest(self, state='integrating', others=()):
        manifest = parallel_state.new_run(TICKET, PHASE, RUN_ID, 2, 'digest')
        manifest = self.chain(manifest, TASK, state)
        for task, other_state in others:
            manifest = self.chain(manifest, task, other_state)
        return manifest

    def write_manifest(self, manifest=None, state='integrating', others=()):
        value = manifest if manifest is not None else self.manifest(state, others)
        current = (parallel_state.read_manifest(str(self.manifest_path))['revision']
                   if self.manifest_path.exists() else 0)
        parallel_state.write_manifest(str(self.manifest_path), dict(value, revision=current),
                                      current)
        return value

    # --- worker edits -----------------------------------------------------------------

    def worker_write(self, rel, text):
        return self.write(rel, text, base=self.worker)

    def worker_move(self, old, new):
        source = self.worker / old
        target = self.worker / new
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def worker_status(self):
        return git(self.worker, 'status', '--porcelain', '--untracked-files=all')

    def porcelain(self, checkout=None):
        """Porcelain lines verbatim (the first line's leading status space is meaningful)."""
        proc = subprocess.run(['git', '-C', str(checkout or self.coord), 'status',
                               '--porcelain', '--untracked-files=all'],
                              capture_output=True, text=True)
        return [line for line in proc.stdout.splitlines() if line.strip()]

    def worker_dirty(self):
        return sorted(line[3:] for line in self.porcelain(self.worker))

    def changed(self, commit, base=None):
        """Both rename sides of `commit`'s diff, sorted; a root commit diffs against empty."""
        base = base or (commit + '^')
        out = git(self.coord, 'diff-tree', '-r', '--no-renames', '--name-only', '-z',
                  base, commit)
        return sorted(name for name in out.split('\0') if name)

    def is_ancestor(self, older, newer):
        proc = subprocess.run(['git', '-C', str(self.coord), 'merge-base', '--is-ancestor',
                               older, newer], capture_output=True)
        return proc.returncode == 0

    def worker_code_dirt(self):
        """Worker dirt that is neither lifecycle state nor the shared spec trail."""
        return [path for path in self.worker_dirty()
                if not path.startswith('.artel/') and not path.startswith('specs/')]

    def reflog(self, branch=COORD_BRANCH):
        out = git(self.coord, 'reflog', 'show', '--format=%H|%gs', branch)
        return [line.split('|', 1) for line in out.splitlines() if line.strip()]

    def status(self, checkout=None):
        return git(checkout or self.coord, 'status', '--porcelain', '--untracked-files=all')

    def snapshot(self, checkout):
        return {
            'branch': git(checkout, 'branch', '--show-current'),
            'head': git(checkout, 'rev-parse', 'HEAD'),
            'status': self.status(checkout),
            'cached': git(checkout, 'diff', '--cached'),
            'unstaged': git(checkout, 'diff'),
            'stash': git(checkout, 'stash', 'list'),
        }

    def assertPreserved(self, before, after):
        for key in sorted(before):
            self.assertEqual(after[key], before[key], '{} changed'.format(key))

    def install_hook(self, name, body):
        hook = self.main / '.git' / 'hooks' / name
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(body, encoding='utf-8')
        hook.chmod(0o755)
        return hook

    def record_path(self):
        return parallel_integration.record_path(str(self.coord), self.assignment())

    def stored_record(self):
        return json.loads(self.record_path().read_text(encoding='utf-8'))

    def rewrite_record(self, **changes):
        value = self.stored_record()
        value.update(changes)
        self.record_path().write_text(json.dumps(value, indent=2, sort_keys=True) + '\n',
                                      encoding='utf-8')
        return value


class TestPrepareTransport(RepoCase):
    """The coordinator's transport commit: code-only, hooked, retained, worker-clean."""

    def test_the_transport_commit_carries_only_the_allowlisted_code(self):
        self.worker_write('app.txt', 'v2\n')
        record = self.prepare()
        self.assertEqual(record['stage'], 'prepared')
        self.assertEqual(record['changed_files'], ['app.txt'])
        self.assertEqual(record['excluded'], [])
        self.assertEqual(record['route'], 'light')
        transport = record['transport_commit']
        self.assertEqual(self.changed(transport), ['app.txt'])
        self.assertEqual(git(self.worker, 'rev-parse', transport + '^'), self.base)
        self.assertEqual(git(self.worker, 'rev-parse', 'HEAD'), transport)
        self.assertEqual(self.worker_code_dirt(), [])
        self.assertEqual(git(self.coord, 'rev-parse', record['result_ref']), transport)
        self.assertEqual(record['result_ref'], self.result_ref())
        self.assertEqual(record['diff']['files'], ['app.txt'])
        self.assertRegex(record['diff']['sha256'], r'^[0-9a-f]{64}$')

    def test_the_transport_is_retained_under_the_namespaced_ref_only(self):
        self.worker_write('app.txt', 'v2\n')
        record = self.prepare()
        self.assertEqual(git(self.coord, 'rev-parse', COORD_BRANCH),
                         git(self.coord, 'rev-parse', 'HEAD'))
        self.assertEqual(git(self.coord, 'diff', '--cached'), '')
        self.assertIn(record['result_ref'], git(self.coord, 'for-each-ref',
                                               '--format=%(refname)', REF_ROOT))

    def test_lifecycle_shared_and_generated_paths_are_excluded(self):
        self.worker_write('app.txt', 'v2\n')
        self.worker_write(TASKLIST, '- [x] 3.1 do a thing\n')
        self.worker_write('.artel/scratch.json', '{}\n')
        self.worker_write('build/out.bin', 'generated\n')
        record = self.prepare()
        self.assertEqual(record['changed_files'], ['app.txt'])
        self.assertEqual(record['excluded'], ['.artel/scratch.json', TASKLIST])
        self.assertEqual(self.changed(record['transport_commit']), ['app.txt'])
        listing = git(self.worker, 'ls-tree', '-r', '--name-only',
                      record['transport_commit'])
        self.assertIn(TASKLIST, listing)          # tracked at base, unchanged in the commit
        self.assertNotIn('.artel/', listing)
        self.assertNotIn('build/', listing)
        # the worker's own shared-state edit is retained dirty for diagnosis, never transported
        self.assertIn(TASKLIST, self.worker_dirty())

    def test_both_sides_of_a_rename_are_transported_and_declared(self):
        assignment, root = self.make_worker('rename', files=['app.txt', 'renamed.txt'])
        (root / 'app.txt').rename(root / 'renamed.txt')
        record = self.prepare(assignment, changed_files=['app.txt', 'renamed.txt'])
        self.assertEqual(record['changed_files'], ['app.txt', 'renamed.txt'])
        self.assertEqual(self.changed(record['transport_commit']),
                         ['app.txt', 'renamed.txt'])
        applied = self.apply(record, assignment)
        committed = self.commit(applied)
        self.assertEqual(self.changed(committed['cycle_commit']),
                         ['app.txt', 'renamed.txt'])
        self.assertFalse((self.coord / 'app.txt').exists())
        self.assertTrue((self.coord / 'renamed.txt').is_file())

    def test_a_missing_rename_side_is_a_mismatch(self):
        self.worker_move('app.txt', 'renamed.txt')
        rejects(self, 'changed_files_mismatch',
                lambda: self.prepare(changed_files=['renamed.txt']))

    def test_an_undeclared_change_without_a_deviation_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        self.worker_write('lib/other.py', 'x = 1\n')
        rejects(self, 'undeclared_change',
                lambda: self.prepare(changed_files=['app.txt', 'lib/other.py']))

    def test_an_undeclared_change_floors_the_route_to_full(self):
        self.worker_write('app.txt', 'v2\n')
        self.worker_write('lib/other.py', 'x = 1\n')
        result = self.result(
            changed_files=['app.txt', 'lib/other.py'],
            deviations=[{'severity': 'minor', 'summary': 'needed a helper',
                         'paths': ['lib/other.py']}])
        rejects(self, 'route_floor', lambda: self.prepare(result=result))
        result['review'] = self.review(route='full')
        record = self.prepare(result=result)
        self.assertEqual(record['route'], 'full')
        self.assertEqual(record['changed_files'], ['app.txt', 'lib/other.py'])
        # the floored route is the transaction's effective route, not the assignment's
        committed = self.commit(self.apply(record))
        self.assertEqual(self.changed(committed['cycle_commit']),
                         ['app.txt', 'lib/other.py'])

    def test_a_major_deviation_refuses(self):
        self.worker_write('app.txt', 'v2\n')
        self.worker_write('lib/other.py', 'x = 1\n')
        result = self.result(
            changed_files=['app.txt', 'lib/other.py'],
            deviations=[{'severity': 'major', 'summary': 'scope change',
                         'paths': ['lib/other.py']}])
        rejects(self, 'major_deviation', lambda: self.prepare(result=result))

    def test_a_deviation_path_outside_the_diff_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        result = self.result(
            deviations=[{'severity': 'minor', 'summary': 'fabricated',
                         'paths': ['lib/ghost.py']}])
        rejects(self, 'deviation_mismatch', lambda: self.prepare(result=result))

    def test_an_empty_diff_has_nothing_to_transport(self):
        rejects(self, 'empty_transport', lambda: self.prepare(changed_files=[]))

    def test_the_review_package_is_worker_scoped(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'coordinator edit\n', base=self.coord)
        record = self.prepare()
        self.assertEqual(record['diff']['files'], ['app.txt'])
        self.assertEqual(self.read('other.txt', base=self.coord), 'coordinator edit\n')
        self.assertIn(' M other.txt', self.porcelain())

    def test_another_attempts_identity_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        result = self.result(attempt=2)
        rejects(self, 'invalid_result', lambda: self.prepare(result=result))


class TestPrepareReview(RepoCase):
    """The accepted review is coordinator-owned, route-consistent and diff-bound."""

    def test_the_worker_submission_null_review_is_not_accepted(self):
        self.worker_write('app.txt', 'v2\n')
        rejects(self, 'review_missing', lambda: self.prepare(review=None))

    def test_a_light_review_cannot_cover_a_full_route(self):
        assignment, root = self.make_worker('full', route='full')
        self.write('app.txt', 'v2\n', base=root)
        result = self.result(assignment, review=self.review(route='light'))
        rejects(self, 'review_route_mismatch',
                lambda: self.prepare(assignment, result))

    def test_a_full_route_needs_a_passed_review(self):
        assignment, root = self.make_worker('full', route='full')
        self.write('app.txt', 'v2\n', base=root)
        result = self.result(assignment, review=self.review(
            route='full', verdict='changes-requested'))
        rejects(self, 'review_not_passed', lambda: self.prepare(assignment, result))

    def test_a_full_route_passed_review_is_accepted(self):
        assignment, root = self.make_worker('full', route='full')
        self.write('app.txt', 'v2\n', base=root)
        result = self.result(assignment, review=self.review(route='full'))
        record = self.prepare(assignment, result)
        self.assertEqual(record['route'], 'full')
        self.assertEqual(record['review']['route'], 'full')

    def test_a_red_worker_check_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        result = self.result(verify=[{'command': 'false', 'ok': False, 'output': 'boom'}])
        rejects(self, 'verify_red', lambda: self.prepare(result=result))

    def test_a_non_succeeded_result_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        rejects(self, 'result_not_succeeded', lambda: self.prepare(status='blocked'))


class TestPreparePreconditions(RepoCase):
    """Ownership, the durable integrating state, the drain and the coordinator's shape."""

    def test_a_foreign_run_lock_stops_the_transport(self):
        self.worker_write('app.txt', 'v2\n')
        common = Path(git(self.coord, 'rev-parse', '--path-format=absolute',
                          '--git-common-dir'))
        lock = common / 'artel' / 'parallel' / (TICKET + '.json')
        lock.write_text(json.dumps({'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN,
                                    'phase': PHASE, 'token': 'x', 'acquired_at': 'now'}),
                        encoding='utf-8')
        rejects(self, 'foreign_owner', lambda: self.prepare())
        self.assertEqual(git(self.worker, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.coord, 'rev-parse', '--verify', '--quiet',
                             self.result_ref(), check=False), '')

    def test_the_durable_integrating_transition_is_required(self):
        self.worker_write('app.txt', 'v2\n')
        self.write_manifest(state='prepared')
        rejects(self, 'not_integrating', lambda: self.prepare())

    def test_a_missing_manifest_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        self.manifest_path.unlink()
        rejects(self, 'missing_manifest', lambda: self.prepare())

    def test_another_tasks_in_flight_work_requires_a_drain(self):
        self.worker_write('app.txt', 'v2\n')
        self.write_manifest(others=[('2.4', 'running')])
        rejects(self, 'drain_required', lambda: self.prepare())

    def test_a_waiting_reservation_does_not_block_but_is_recorded(self):
        self.worker_write('app.txt', 'v2\n')
        self.write_manifest(others=[('2.4', 'reserved')])
        record = self.prepare()
        self.assertEqual(record['drain']['in_flight'], [])
        self.assertEqual(record['drain']['waiting'], ['2.4'])

    def test_a_published_task_does_not_block(self):
        self.worker_write('app.txt', 'v2\n')
        self.write_manifest(others=[('2.4', 'published')])
        self.assertEqual(self.prepare()['stage'], 'prepared')

    def test_a_detached_coordinator_head_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        head = git(self.coord, 'rev-parse', 'HEAD')
        git(self.coord, 'checkout', '-q', '--detach', head)
        rejects(self, 'detached_head', lambda: self.prepare())

    def test_the_default_branch_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        git(self.coord, 'checkout', '-q', '-b', 'main')
        rejects(self, 'default_branch', lambda: self.prepare())

    def test_unusable_reflogs_fail_preflight(self):
        self.worker_write('app.txt', 'v2\n')
        git(self.coord, 'reflog', 'expire', '--expire=now', 'refs/heads/' + COORD_BRANCH)
        rejects(self, 'reflogs_unusable', lambda: self.prepare())

    def test_a_dirty_transport_path_in_the_coordinator_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('app.txt', 'coordinator edit\n', base=self.coord)
        rejects(self, 'coordinator_dirty', lambda: self.prepare())
        self.assertEqual(self.read('app.txt', base=self.coord), 'coordinator edit\n')

    def test_an_unrelated_dirty_coordinator_file_is_preserved(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'wip\n', base=self.coord)
        self.prepare()
        self.assertEqual(self.read('other.txt', base=self.coord), 'wip\n')
        self.assertIn(' M other.txt', self.porcelain())

    def test_a_staged_coordinator_index_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'staged\n', base=self.coord)
        git(self.coord, 'add', '--', 'other.txt')
        rejects(self, 'coordinator_dirty', lambda: self.prepare())
        self.assertEqual(git(self.coord, 'diff', '--cached', '--name-only'), 'other.txt')

    def test_a_foreign_worker_root_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        stranger = self.space / 'stranger'
        stranger.mkdir()
        assignment = self.assignment(worker_root=str(stranger),
                                     artifact_root=str(stranger / 'evidence'))
        rejects(self, 'not_worker_root', lambda: self.prepare(assignment))

    def test_a_foreign_marker_identity_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        marker = self.worker / '.artel' / 'parallel-worker.json'
        value = json.loads(marker.read_text(encoding='utf-8'))
        value['assignment'] = dict(value['assignment'], attempt=2)
        marker.write_text(json.dumps(value), encoding='utf-8')
        rejects(self, 'worker_identity_mismatch', lambda: self.prepare())

    def test_a_worker_base_ref_that_moved_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'second\n')
        git(self.main, 'add', '-A')
        git(self.main, 'commit', '-q', '-m', 'second')
        base_ref = '{}/{}/{}/{}/base'.format(REF_ROOT, RUN_ID, TASK, ATTEMPT)
        git(self.coord, 'update-ref', base_ref, git(self.main, 'rev-parse', 'HEAD'))
        rejects(self, 'worker_not_ready', lambda: self.prepare())
        self.assertEqual(git(self.worker, 'rev-parse', 'HEAD'), self.base)

    def test_a_worker_root_in_another_repository_is_refused(self):
        other = self.space / 'other repo'
        git(self.space, 'init', '-q', str(other))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(other, 'config', key, value)
        (other / '.gitignore').write_text('.artel/run/\n', encoding='utf-8')
        (other / 'app.txt').write_text('v1\n', encoding='utf-8')
        git(other, 'add', '-A')
        git(other, 'commit', '-q', '-m', 'init')
        base = git(other, 'rev-parse', 'HEAD')
        config = other / '.artel' / 'config.json'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{}\n', encoding='utf-8')
        parallel_state.acquire_ticket(
            str(other), TICKET,
            {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE})
        root = (other / '.claude' / 'worktrees' / 'parallel tasks' / TICKET / RUN_ID /
                '{}-a{}'.format(TASK, ATTEMPT))
        assignment = self.assignment(
            base_commit=base, worker_root=str(root),
            artifact_root=str(root / '.artel' / 'run' / 'parallel' /
                              '{}-a{}'.format(TASK, ATTEMPT)))
        task_worktree.create_worker(str(other), assignment)
        self.write('app.txt', 'v2\n', base=root)
        rejects(self, 'foreign_repository', lambda: self.prepare(assignment))

    def test_a_worker_staged_change_is_never_adopted(self):
        self.worker_write('app.txt', 'v2\n')
        self.worker_write('.artel/scratch.json', '{}\n')
        git(self.worker, 'add', '--', '.artel/scratch.json')
        rejects(self, 'worker_staged', lambda: self.prepare())
        self.assertEqual(git(self.worker, 'rev-parse', 'HEAD'), self.base)

    def test_the_main_checkout_is_never_moved(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'main wip\n')
        before = self.snapshot(self.main)
        record = self.prepare()
        self.commit(self.apply(record))
        self.assertPreserved(before, self.snapshot(self.main))


class TestWriteAhead(RepoCase):
    """The record is durable before every Git side effect, and hooks never run undrained."""

    def test_the_transport_record_exists_before_its_commit_hook_runs(self):
        self.worker_write('app.txt', 'v2\n')
        snapshot = self.space / 'record-at-transport-hook.json'
        self.install_hook('pre-commit', '#!/bin/sh\ncp "{}" "{}"\n'.format(
            self.record_path(), snapshot))
        self.prepare()
        seen = json.loads(snapshot.read_text(encoding='utf-8'))
        self.assertEqual(seen['stage'], 'preparing')
        self.assertIsNone(seen['transport_commit'])
        self.assertEqual(seen['pre_head'], git(self.coord, 'rev-parse', 'HEAD'))
        self.assertEqual(seen['changed_files'], ['app.txt'])

    def test_the_final_record_exists_before_its_commit_hook_runs(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        snapshot = self.space / 'record-at-final-hook.json'
        self.install_hook('pre-commit', '#!/bin/sh\ncp "{}" "{}"\n'.format(
            self.record_path(), snapshot))
        committed = self.commit(applied)
        seen = json.loads(snapshot.read_text(encoding='utf-8'))
        self.assertEqual(seen['stage'], 'committing')
        self.assertEqual(seen['subject'], SUBJECT)
        self.assertEqual(seen['transport_commit'], applied['transport_commit'])
        self.assertEqual(seen['documents'], [])
        self.assertEqual(committed['stage'], 'committed')

    def test_no_hook_runs_while_another_task_is_in_flight(self):
        self.worker_write('app.txt', 'v2\n')
        log = self.space / 'hooks.log'
        self.install_hook('pre-commit', '#!/bin/sh\necho ran >> {}\n'.format(log))
        self.write_manifest(others=[('2.4', 'running')])
        rejects(self, 'drain_required', lambda: self.prepare())
        self.assertFalse(log.exists(), 'an external hook ran before the drain')
        self.assertEqual(git(self.worker, 'rev-parse', 'HEAD'), self.base)
        self.assertFalse(self.record_path().exists())


class TestTransportHooks(RepoCase):
    """The transport commit runs the host's normal hooks, in the worker root."""

    def test_the_transport_commit_runs_normal_hooks_in_the_worker_root(self):
        self.worker_write('app.txt', 'v2\n')
        log = self.space / 'hooks.log'
        self.install_hook('pre-commit', '#!/bin/sh\npwd >> {}\n'.format(log))
        self.prepare()
        self.assertEqual(log.read_text(encoding='utf-8').strip(), str(self.worker))

    def test_a_refusing_transport_hook_leaves_the_worker_inspectable(self):
        self.worker_write('app.txt', 'v2\n')
        self.install_hook('pre-commit', '#!/bin/sh\necho nope >&2\nexit 1\n')
        rejects(self, 'commit_refused', lambda: self.prepare())
        self.assertEqual(git(self.worker, 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.worker, 'diff', '--cached', '--name-only'), 'app.txt')
        self.assertEqual(git(self.coord, 'rev-parse', '--verify', '--quiet',
                             self.result_ref(), check=False), '')
        self.assertEqual(self.stored_record()['stage'], 'commit_refused')
        self.assertIsNone(self.stored_record()['transport_commit'])
        # the refusal is a stop-and-ask: fixing the hook lets the same attempt retry once
        self.install_hook('pre-commit', '#!/bin/sh\nexit 0\n')
        record = self.prepare()
        self.assertEqual(record['stage'], 'prepared')
        self.assertEqual(self.changed(record['transport_commit']), ['app.txt'])

    def test_the_final_commit_runs_normal_hooks_in_the_coordinator_root(self):
        self.worker_write('app.txt', 'v2\n')
        log = self.space / 'hooks.log'
        self.install_hook('pre-commit', '#!/bin/sh\npwd >> {}\n'.format(log))
        committed = self.commit(self.prepare_apply())
        self.assertEqual(log.read_text(encoding='utf-8').splitlines(),
                         [str(self.worker), str(self.coord)])
        self.assertEqual(committed['stage'], 'committed')

    def test_a_refusing_final_hook_leaves_the_integrated_tree_inspectable(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        self.install_hook('pre-commit', '#!/bin/sh\necho nope >&2\nexit 1\n')
        rejects(self, 'commit_refused', lambda: self.commit(applied))
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), applied['pre_head'])
        self.assertEqual(git(self.coord, 'diff', '--cached', '--name-only'), 'app.txt')
        self.assertEqual(self.stored_record()['stage'], 'commit_refused')
        self.assertIsNone(self.stored_record()['cycle_commit'])
        self.install_hook('pre-commit', '#!/bin/sh\nexit 0\n')
        committed = self.commit(applied)
        self.assertEqual(committed['stage'], 'committed')
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')


class TestApplyTransport(RepoCase):
    """`cherry-pick --no-commit` onto the coordinator: staged, never branch history."""

    def test_apply_stages_the_transport_without_committing(self):
        self.worker_write('app.txt', 'v2\n')
        prepared = self.prepare()
        applied = self.apply(prepared)
        self.assertEqual(applied['stage'], 'applied')
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), prepared['pre_head'])
        self.assertEqual(git(self.coord, 'diff', '--cached', '--name-only'), 'app.txt')
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             prepared['pre_head'] + '..HEAD'), '0')
        self.assertFalse(self.is_ancestor(prepared['transport_commit'], 'HEAD'))
        self.assertIn('+++ b/app.txt', git(self.coord, 'diff', '--cached'))

    def test_apply_preserves_unrelated_coordinator_work(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'wip\n', base=self.coord)
        prepared = self.prepare()
        self.apply(prepared)
        self.assertEqual(self.read('other.txt', base=self.coord), 'wip\n')

    def test_apply_refuses_a_conflict_and_preserves_both_sides(self):
        self.worker_write('app.txt', 'worker\n')
        self.write('app.txt', 'other\n', base=self.coord)
        git(self.coord, 'add', '--', 'app.txt')
        git(self.coord, 'commit', '-q', '-m', 'feat: earlier task')
        self.write('other.txt', 'wip\n', base=self.coord)
        prepared = self.prepare()
        rejects(self, 'cherry_pick_conflict', lambda: self.apply(prepared))
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), prepared['pre_head'])
        status = self.status()
        self.assertIn('UU app.txt', status)
        self.assertIn('<<<<<<<', self.read('app.txt', base=self.coord))
        self.assertEqual(self.read('other.txt', base=self.coord), 'wip\n')
        self.assertEqual(self.stored_record()['stage'], 'conflicted')

    def test_apply_is_idempotent_across_its_write_ahead_window(self):
        self.worker_write('app.txt', 'v2\n')
        prepared = self.prepare()
        applied = self.apply(prepared)
        again = self.apply(applied)
        self.assertEqual(again['stage'], 'applied')
        rewound = dict(applied, stage='applying')
        recovered = self.apply(rewound)
        self.assertEqual(recovered['stage'], 'applied')
        self.assertEqual(git(self.coord, 'diff', '--cached', '--name-only'), 'app.txt')

    def test_apply_refuses_a_moved_pre_head(self):
        self.worker_write('app.txt', 'v2\n')
        prepared = self.prepare()
        self.write('other.txt', 'moved\n', base=self.coord)
        git(self.coord, 'add', '--', 'other.txt')
        git(self.coord, 'commit', '-q', '-m', 'chore: moved')
        rejects(self, 'stale_pre_head', lambda: self.apply(prepared))
        self.assertEqual(git(self.coord, 'rev-parse', '--verify', '--quiet',
                             self.result_ref(), check=False),
                         prepared['transport_commit'])


class TestCommitCycle(RepoCase):
    """One attributable final commit: the combined-tree gate, the documents, the subject."""

    def test_the_combined_tree_task_gate_blocks_a_red_integration(self):
        self.worker_write('app.txt', 'worker\n')
        self.write('extra.txt', 'present\n', base=self.coord)
        git(self.coord, 'add', '--', 'extra.txt')
        git(self.coord, 'commit', '-q', '-m', 'feat: earlier task')
        self.write_config(verify={'fast': '[ -f extra.txt ] && grep -q ok app.txt'})
        applied = self.prepare_apply()
        rejects(self, 'task_gate_failed', lambda: self.commit(applied))
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), applied['pre_head'])
        self.assertEqual(git(self.coord, 'diff', '--cached', '--name-only'), 'app.txt')
        stored = self.stored_record()
        self.assertEqual(stored['stage'], 'failed')
        self.assertEqual(stored['verify']['exit_code'], 1)
        self.assertEqual([stage['name'] for stage in stored['verify']['data']['stages']],
                         ['fast'])          # the gate stops at the first red stage
        # a bounded coordinator fix inside the transported path lets the same cycle land
        self.write('app.txt', 'ok\n', base=self.coord)
        committed = self.commit(stored)
        self.assertEqual(committed['stage'], 'committed')
        self.assertEqual(committed['verify']['exit_code'], 0)
        self.assertEqual(self.changed(committed['cycle_commit']), ['app.txt'])
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')

    def test_files_mode_commits_the_tick_and_notes_with_the_code(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        self.write(TASKLIST, '- [x] 3.1 do a thing\n', base=self.coord)
        self.write(NOTES, '# notes\n', base=self.coord)
        self.write('other.txt', 'wip\n', base=self.coord)
        transaction = dict(applied, documents=[TASKLIST, NOTES])
        committed = self.commit(transaction)
        self.assertEqual(self.changed(committed['cycle_commit']),
                         ['app.txt', NOTES, TASKLIST])
        self.assertEqual(self.read('other.txt', base=self.coord), 'wip\n')
        self.assertNotIn('other.txt', self.changed(committed['cycle_commit']))
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')

    def test_store_mode_commits_the_code_before_publication(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        committed = self.commit(applied)
        self.assertEqual(self.changed(committed['cycle_commit']), ['app.txt'])
        self.assertIsNone(committed['publication'])
        self.assertEqual(committed['documents'], [])

    def test_a_document_that_does_not_exist_is_refused(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        rejects(self, 'missing_document',
                lambda: self.commit(dict(applied, documents=[NOTES])))
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), applied['pre_head'])

    def test_foreign_staged_work_is_never_swept_into_the_cycle(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        self.write('other.txt', 'staged\n', base=self.coord)
        git(self.coord, 'add', '--', 'other.txt')
        rejects(self, 'foreign_staged', lambda: self.commit(applied))
        staged = git(self.coord, 'diff', '--cached', '--name-only')
        self.assertIn('other.txt', staged)
        self.assertIn('app.txt', staged)
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), applied['pre_head'])
        self.assertEqual(self.stored_record()['stage'], 'committing')

    def test_the_subject_is_conventional_subject_only(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        for subject in ('do a thing', 'feat: PROJ-123 - 3.1 do a thing',
                        'feat: PROJ-123 - do a thing\n\nTask: 3.1',
                        'feat: PROJ-123 - do a thing\n'):
            with self.subTest(subject=subject):
                rejects(self, 'invalid_subject', lambda: self.commit(applied, subject))
        self.assertEqual(git(self.coord, 'rev-parse', 'HEAD'), applied['pre_head'])

    def test_exactly_one_commit_is_attributable_to_the_transaction(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        committed = self.commit(applied)
        cycle = committed['cycle_commit']
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')
        self.assertEqual(git(self.coord, 'rev-parse', COORD_BRANCH), cycle)
        self.assertEqual(git(self.coord, 'log', '-1', '--format=%s', cycle), SUBJECT)
        entry = self.reflog()[0]
        self.assertEqual(entry[0], cycle)
        self.assertTrue(entry[1].startswith(committed['transaction_id'] + ':'), entry[1])
        self.assertFalse(self.is_ancestor(committed['transport_commit'], cycle))
        self.assertNotIn('3.1', git(self.coord, 'log', '-1', '--format=%B', cycle))

    def test_a_second_commit_call_adopts_instead_of_committing_again(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        committed = self.commit(applied)
        again = self.commit(applied)
        self.assertEqual(again['cycle_commit'], committed['cycle_commit'])
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')


class TestRecovery(RepoCase):
    """A commit that landed before its receipt is recognized exactly once, from a linked
    coordinator; missing, expired, ambiguous or unreachable evidence refuses."""

    def test_recovery_finds_a_landed_commit_before_its_receipt(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        committed = self.commit(applied)
        recovered = self.recover(applied)          # the receipt was never written
        self.assertEqual(recovered['recovery']['state'], 'landed')
        self.assertEqual(recovered['cycle_commit'], committed['cycle_commit'])
        self.assertEqual(recovered['stage'], 'recovered')
        again = self.recover(recovered)
        self.assertEqual(again['cycle_commit'], committed['cycle_commit'])
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')

    def test_recovery_reports_no_commit_when_none_landed(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        recovered = self.recover(applied)
        self.assertEqual(recovered['recovery']['state'], 'none')
        self.assertIsNone(recovered['cycle_commit'])
        committed = self.commit(recovered)
        self.assertEqual(git(self.coord, 'rev-list', '--count',
                             applied['pre_head'] + '..HEAD'), '1')
        self.assertEqual(committed['stage'], 'committed')

    def test_recovery_refuses_when_the_reflog_window_is_gone(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        committed = self.commit(applied)
        git(self.coord, 'reflog', 'expire', '--expire=now', 'refs/heads/' + COORD_BRANCH)
        rejects(self, 'reflogs_unusable', lambda: self.recover(applied))
        self.assertEqual(git(self.coord, 'rev-parse', COORD_BRANCH),
                         committed['cycle_commit'])

    def test_recovery_refuses_ambiguous_transaction_evidence(self):
        self.worker_write('app.txt', 'v2\n')
        self.write('other.txt', 'earlier\n', base=self.coord)
        git(self.coord, 'add', '--', 'other.txt')
        git(self.coord, 'commit', '-q', '-m', 'feat: earlier task')  # pre-HEAD != base
        applied = self.prepare_apply()
        env = dict(os.environ, GIT_REFLOG_ACTION=applied['transaction_id'])
        git(self.coord, 'reset', '-q', '--hard', self.base)   # a different parent
        self.write('other.txt', 'decoy\n', base=self.coord)
        git(self.coord, 'add', '--', 'other.txt')
        git(self.coord, 'commit', '-q', '-m', SUBJECT, env=env)
        rejects(self, 'ambiguous_evidence', lambda: self.recover(applied))

    def test_recovery_refuses_unreachable_evidence(self):
        self.worker_write('app.txt', 'v2\n')
        applied = self.prepare_apply()
        committed = self.commit(applied)
        git(self.coord, 'reset', '-q', '--hard', applied['pre_head'])
        rejects(self, 'unreachable_evidence', lambda: self.recover(applied))
        self.assertEqual(git(self.coord, 'rev-parse', COORD_BRANCH), applied['pre_head'])

    def test_prepare_adopts_a_transport_commit_that_lost_its_receipt(self):
        self.worker_write('app.txt', 'v2\n')
        record = self.prepare()
        transport = record['transport_commit']
        # the crash window: the commit landed, the ref and the receipt did not
        git(self.coord, 'update-ref', '-d', self.result_ref())
        self.rewrite_record(stage='preparing', transport_commit=None,
                            cycle_commit=None, subject=None)
        again = self.prepare()
        self.assertEqual(again['transport_commit'], transport)
        self.assertEqual(again['stage'], 'prepared')
        self.assertEqual(git(self.worker, 'rev-list', '--count',
                             self.base + '..HEAD'), '1')
        self.assertEqual(git(self.coord, 'rev-parse', self.result_ref()), transport)

    def test_prepare_refuses_an_unexplained_moved_worker_head(self):
        self.worker_write('app.txt', 'v2\n')
        git(self.worker, 'add', '-A')
        git(self.worker, 'commit', '-q', '-m', 'chore: not ours')
        rejects(self, 'worker_not_at_base', lambda: self.prepare())
        self.assertEqual(git(self.coord, 'rev-parse', '--verify', '--quiet',
                             self.result_ref(), check=False), '')


class TestIntegrationDocs(unittest.TestCase):
    """docs/opencode-parallel.md §13.8 is the contract Tasks 9-10 consume."""

    def test_the_section_documents_the_integration_contract(self):
        text = (ROOT / 'docs' / 'opencode-parallel.md').read_text(encoding='utf-8')
        section = text[text.index('\n### 13.8 '):]
        for phrase in ('prepare_transport', 'apply_transport', 'commit_cycle',
                       'recover_cycle', 'cherry-pick --no-commit', 'GIT_REFLOG_ACTION',
                       'transport commit', 'exactly one completed-cycle commit',
                       'drain', 'never resets', 'files path', 'artifact-store path',
                       'Task 9', 'refuses rather than re-committing'):
            self.assertIn(phrase, section, phrase)


if __name__ == '__main__':
    unittest.main()
