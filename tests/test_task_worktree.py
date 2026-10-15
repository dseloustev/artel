"""scripts/task_worktree.py: one owned, detached task worktree per parallel worker.

Contract: docs/opencode-parallel.md §3-§5 (worker location, assignment, result) and §13.4
(the repository-common ticket lock), docs/worktrees.md §1 (the `.claude/worktrees` area) and
the approved task-5 lifecycle handoff.

Every test builds a real Git repository with the coordinator already in a linked worktree, a
real ticket lock and real configured setup, so the lifecycle's Git behavior is git's own.
The invariants worth the most: the worker root is detached at the exact base commit, the
coordinator's branch/index/stash/run directory are never touched, an interrupted or failed
setup is retained and reported rather than masked, and cleanup removes only an owned root
that is clean at the retained result, fully accounted for file by file, and backed by a
branch-reachable cycle commit plus evidence collected to a separate destination.
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_contract  # noqa: E402
import parallel_state  # noqa: E402
import task_worktree  # noqa: E402

TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'
TASK = '3.1'
ATTEMPT = 1
PHASE = 2
COORD_BRANCH = 'feature/PROJ-123'
SETUP_COMMAND = 'mkdir -p build && pwd > build/cwd.txt'
REF_ROOT = 'refs/artel/parallel'


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def digest_tree(root):
    """Every file under `root`, relative POSIX path -> sha256; a missing root digests empty."""
    root = Path(root)
    found = {}
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            found[path.relative_to(root).as_posix()] = sha256_file(path)
    return found


def rejects(test, kind, call):
    """Assert `call` raises WorktreeError of `kind`; return the exception."""
    with test.assertRaises(task_worktree.WorktreeError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind)
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


class RepoCase(unittest.TestCase):
    """A real repository, the coordinator already in a linked worktree, and a real lock."""

    IGNORE = '.artel/\nbuild/\n'

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
        self.write('docs/my notes.txt', 'notes\n')
        git(self.main, 'add', '.')
        git(self.main, 'commit', '-q', '-m', 'init')
        self.base = git(self.main, 'rev-parse', 'HEAD')
        self.coord = self.main / '.claude' / 'worktrees' / 'PROJ-123'
        git(self.main, 'worktree', 'add', '-q', '-b', COORD_BRANCH, str(self.coord))
        self.write_config()
        parallel_state.acquire_ticket(
            str(self.coord), TICKET,
            {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE})

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

    def write_config(self, base=None, commands=(), extra=None):
        value = {'specs': {'dir': 'specs/.current'}, 'setup': {'commands': list(commands)}}
        if extra:
            value.update(extra)
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

    def marker_path(self, root=None):
        return (root or self.worker_root()) / '.artel' / 'parallel-worker.json'

    def base_ref(self, task=TASK, attempt=ATTEMPT, run_id=RUN_ID):
        return '{}/{}/{}/{}/base'.format(REF_ROOT, run_id, task, attempt)

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

    def create(self, repo=None, assignment=None):
        return task_worktree.create_worker(str(repo or self.coord),
                                           assignment or self.assignment())

    def inspect(self, repo=None, assignment=None):
        return task_worktree.inspect_worker(str(repo or self.coord),
                                            assignment or self.assignment())

    def cleanup(self, publication=None, repo=None, assignment=None):
        return task_worktree.cleanup_worker(str(repo or self.coord),
                                            assignment or self.assignment(),
                                            publication or self.publication())

    def publication(self, **overrides):
        value = {'schema': 1, 'ticket': TICKET, 'phase': PHASE, 'run_id': RUN_ID,
                 'task': TASK, 'attempt': ATTEMPT, 'cycle_commit': self.base,
                 'cycle_ref': 'refs/heads/' + COORD_BRANCH, 'result_commit': self.base,
                 'result_ref': self.result_ref(),
                 'collected_root': str(self.collected_root()), 'evidence': [],
                 'published': True}
        value.update(overrides)
        return value

    def evidence(self, rel, artifact=None):
        root = artifact or self.artifact_root()
        return {'path': rel, 'sha256': sha256_file(root / rel)}

    def collected_root(self, task=TASK, attempt=ATTEMPT, run_id=RUN_ID):
        return (self.coord / '.artel' / 'run' / TICKET / 'parallel' / run_id /
                '{}-a{}'.format(task, attempt))

    def collect(self, evidence=True, text='{"status": "succeeded"}\n'):
        """The coordinator's copy receipt: the worker evidence copied to the collected root."""
        root = self.collected_root()
        root.mkdir(parents=True, exist_ok=True)
        entries = []
        if evidence:
            artifact = self.artifact_root()
            artifact.mkdir(parents=True, exist_ok=True)
            (artifact / 'result.json').write_text(text, encoding='utf-8')
            shutil.copy2(str(artifact / 'result.json'), str(root / 'result.json'))
            entries.append(self.evidence('result.json'))
        return entries

    def publish(self, root, evidence=True, cycle=None):
        """A real publication: a genuine worker transport commit retained by the result ref,
        the coordinator's integrated cycle commit on its branch, and the collected copies."""
        result = self.transport(root)
        self.set_result_ref(result)
        entries = self.collect(evidence)
        if cycle is None:
            cycle = self.integrate(result)
        return self.publication(cycle_commit=cycle, cycle_ref='refs/heads/' + COORD_BRANCH,
                                result_commit=result,
                                collected_root=str(self.collected_root()), evidence=entries)

    # --- operations -------------------------------------------------------------------

    def transport(self, root, message='transport'):
        """A genuine coordinator-owned transport commit in the worker tree: a normal commit
        that leaves the worker HEAD at the retained result and a clean tracked tree. Host
        state under `.artel/` is never transported."""
        git(root, 'add', '-A')
        git(root, 'reset', '-q', '--', '.artel')
        git(root, 'commit', '-q', '--allow-empty', '-m', message)
        return git(root, 'rev-parse', 'HEAD')

    def integrate(self, result, branch=COORD_BRANCH):
        """The coordinator's cycle commit on its integration branch (a real fixture)."""
        git(self.coord, 'cherry-pick', '--allow-empty', result)
        return git(self.coord, 'rev-parse', 'HEAD')

    def transport_commit(self, root, message='transport'):
        """A transport commit object built without touching the worktree's own index or
        HEAD -- the shape a coordinator that has not committed yet would leave behind."""
        index = Path(tempfile.mkdtemp(dir=str(self.space))) / 'index'
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        subprocess.run(['git', '-C', str(root), 'add', '-A', '.'], cwd=str(root), check=True,
                       capture_output=True, env=env)
        tree = subprocess.run(['git', '-C', str(root), 'write-tree'], cwd=str(root), check=True,
                              capture_output=True, text=True, env=env).stdout.strip()
        head = git(root, 'rev-parse', 'HEAD')
        return subprocess.run(['git', '-C', str(root), 'commit-tree', tree, '-p', head,
                               '-m', message], cwd=str(root), check=True, capture_output=True,
                              text=True).stdout.strip()

    def set_result_ref(self, commit, task=TASK, attempt=ATTEMPT, run_id=RUN_ID):
        git(self.coord, 'update-ref', self.result_ref(task, attempt, run_id), commit)

    def orphan_commit(self):
        """A commit that does not descend from the base."""
        empty = subprocess.run(['git', '-C', str(self.coord), 'mktree'], cwd=str(self.coord),
                               input='', check=True, capture_output=True,
                               text=True).stdout.strip()
        return subprocess.run(['git', '-C', str(self.coord), 'commit-tree', empty, '-m',
                               'orphan'], cwd=str(self.coord), check=True, capture_output=True,
                              text=True).stdout.strip()

    def lock_path(self):
        common = git(self.coord, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        return Path(common) / 'artel' / 'parallel' / (TICKET + '.json')

    def snapshot(self, checkout):
        return {
            'branch': git(checkout, 'branch', '--show-current'),
            'head': git(checkout, 'rev-parse', 'HEAD'),
            'status': git(checkout, 'status', '--porcelain', '--untracked-files=all'),
            'cached': git(checkout, 'diff', '--cached'),
            'unstaged': git(checkout, 'diff'),
            'stash': git(checkout, 'stash', 'list'),
            'run': digest_tree(Path(checkout) / '.artel' / 'run'),
        }

    def assertPreserved(self, before, after, skip=()):
        for key in sorted(before):
            if key in skip:
                continue
            self.assertEqual(before[key], after[key], key)

    def dirty_coordinator(self):
        """Uncommitted work, an index entry, a stash and a run directory to preserve."""
        self.write('stashme.txt', 'stash\n', base=self.coord)
        git(self.coord, 'stash', 'push', '-u', '-m', 'fixture stash')
        self.write('staged.txt', 'staged\n', base=self.coord)
        git(self.coord, 'add', 'staged.txt')
        self.write('app.txt', 'modified\n', base=self.coord)
        self.write('unstaged.txt', 'unstaged\n', base=self.coord)
        self.write('.artel/run/PROJ-123/run-state.json', '{"run_active": false}\n',
                   base=self.coord)


class TestCreate(RepoCase):
    def test_creates_a_detached_worktree_at_the_exact_base(self):
        report = self.create()
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['schema'], 1)
        self.assertTrue(report['created'])
        self.assertTrue(report['detached'])
        self.assertEqual(report['root'], str(self.worker_root()))
        self.assertTrue(self.worker_root().is_dir())
        self.assertEqual(git(self.worker_root(), 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(git(self.worker_root(), 'branch', '--show-current'), '')
        self.assertEqual(git(self.worker_root(), 'rev-parse', '--abbrev-ref', 'HEAD'), 'HEAD')
        self.assertIn(str(self.worker_root()),
                      git(self.coord, 'worktree', 'list', '--porcelain'))

    def test_the_marker_carries_the_validated_assignment_and_worker_role(self):
        self.create()
        marker = json.loads(self.marker_path().read_text(encoding='utf-8'))
        self.assertEqual(marker['schema'], 1)
        self.assertEqual(marker['role'], 'worker')
        self.assertEqual(marker['assignment'], parallel_contract.validate_assignment(
            self.assignment()))
        self.assertEqual(marker['assignment']['worker_root'], str(self.worker_root()))
        self.assertEqual(marker['setup']['status'], 'complete')
        self.assertEqual(marker['repository']['main'], str(self.main))
        self.assertEqual(marker['repository']['common_dir'],
                         git(self.coord, 'rev-parse', '--path-format=absolute',
                             '--git-common-dir'))

    def test_the_refs_are_task_run_attempt_keyed(self):
        report = self.create()
        self.assertEqual(report['base_ref'], self.base_ref())
        self.assertEqual(report['result_ref'], self.result_ref())
        self.assertEqual(git(self.coord, 'rev-parse', self.base_ref()), self.base)
        self.assertIn(self.base_ref(), git(self.coord, 'for-each-ref', '--format=%(refname)'))

    def test_second_create_is_idempotent_and_keeps_the_setup_state(self):
        self.write_config(commands=(SETUP_COMMAND,))
        first = self.create()
        self.assertTrue(first['created'])
        second = self.create()
        self.assertEqual(second['status'], 'ok')
        self.assertFalse(second['created'])
        self.assertEqual(second['root'], first['root'])
        self.assertEqual(self.read('build/cwd.txt', self.worker_root()),
                         str(self.worker_root()) + '\n')
        self.assertEqual(git(self.worker_root(), 'rev-parse', 'HEAD'), self.base)

    def test_create_runs_from_the_main_checkout_too(self):
        self.write_config(base=self.main)
        report = self.create(repo=self.main)
        self.assertTrue(report['created'])
        self.assertEqual(git(self.worker_root(), 'rev-parse', 'HEAD'), self.base)

    def test_the_worktrees_area_is_excluded_from_the_main_checkout_status(self):
        self.assertIn('.claude/', git(self.main, 'status', '--porcelain',
                                     '--untracked-files=all'))
        self.create()
        status = git(self.main, 'status', '--porcelain', '--untracked-files=all')
        self.assertNotIn('.claude/', status)
        self.assertEqual(git(self.main, 'check-ignore', '--quiet',
                             '.claude/worktrees/parallel tasks/{}'.format(TICKET)) or '',
                         '')

    def test_an_unknown_assignment_key_is_refused(self):
        rejects(self, 'invalid_assignment',
                lambda: self.create(assignment=self.assignment(extra=1)))
        self.assertFalse(self.worker_root().exists())

    def test_a_missing_assignment_key_is_refused(self):
        value = self.assignment()
        del value['base_commit']
        rejects(self, 'invalid_assignment', lambda: self.create(assignment=value))
        self.assertFalse(self.worker_root().exists())

    def test_an_unknown_base_commit_is_refused(self):
        rejects(self, 'unknown_base',
                lambda: self.create(assignment=self.assignment(base_commit='0' * 40)))
        self.assertFalse(self.worker_root().exists())

    def test_a_worker_root_outside_the_worktree_area_is_refused(self):
        root = self.main / 'elsewhere' / 'worker'
        rejects(self, 'invalid_root', lambda: self.create(
            assignment=self.assignment(worker_root=str(root))))
        self.assertFalse(root.exists())

    def test_a_worker_root_inside_another_worktree_is_refused(self):
        root = self.coord / 'parallel' / 'worker'
        rejects(self, 'invalid_root', lambda: self.create(
            assignment=self.assignment(worker_root=str(root))))
        self.assertFalse(root.exists())

    def test_an_artifact_root_outside_the_worker_root_is_refused(self):
        rejects(self, 'invalid_root', lambda: self.create(
            assignment=self.assignment(artifact_root=str(self.main / 'artifacts'))))
        self.assertFalse(self.worker_root().exists())

    def test_an_artifact_root_the_checkout_does_not_ignore_is_refused(self):
        rejects(self, 'invalid_root', lambda: self.create(
            assignment=self.assignment(
                artifact_root=str(self.worker_root() / 'evidence'))))
        self.assertFalse(self.worker_root().exists())

    def test_an_existing_unknown_directory_is_refused(self):
        self.worker_root().mkdir(parents=True)
        self.write('app.txt', 'other\n', base=self.worker_root())
        rejects(self, 'foreign_root', self.create)
        self.assertEqual(self.read('app.txt', self.worker_root()), 'other\n')

    def test_an_existing_file_is_refused(self):
        self.worker_root().parent.mkdir(parents=True)
        self.worker_root().write_text('not a directory\n', encoding='utf-8')
        rejects(self, 'foreign_root', self.create)

    def test_a_symlinked_worker_root_is_refused(self):
        self.worker_root().parent.mkdir(parents=True)
        os.symlink(str(self.main), str(self.worker_root()))
        rejects(self, 'unsafe_root', self.create)

    def test_a_mismatched_marker_is_refused(self):
        self.create()
        marker = json.loads(self.marker_path().read_text(encoding='utf-8'))
        marker['assignment']['attempt'] = ATTEMPT + 1
        self.marker_path().write_text(json.dumps(marker), encoding='utf-8')
        rejects(self, 'foreign_root', self.create)

    def test_an_interrupted_create_is_reported_and_never_adopted(self):
        self.create()
        self.marker_path().unlink()
        rejects(self, 'interrupted_create', self.create)
        self.assertTrue(self.worker_root().is_dir())

    def test_a_foreign_base_ref_is_refused(self):
        git(self.main, 'commit', '-q', '--allow-empty', '-m', 'another commit')
        other = git(self.main, 'rev-parse', 'HEAD')
        git(self.coord, 'update-ref', self.base_ref(), other)
        rejects(self, 'ref_conflict', self.create)
        self.assertFalse(self.worker_root().exists())


class TestOwnership(RepoCase):
    def test_create_refuses_without_the_ticket_lock(self):
        self.lock_path().unlink()
        rejects(self, 'not_owned', self.create)
        self.assertFalse(self.worker_root().exists())

    def test_create_refuses_a_foreign_run(self):
        self.lock_path().unlink()
        parallel_state.acquire_ticket(
            str(self.coord), TICKET,
            {'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN, 'phase': PHASE})
        rejects(self, 'foreign_owner', self.create)
        self.assertFalse(self.worker_root().exists())

    def test_create_refuses_an_unreadable_lock(self):
        self.lock_path().write_text('not json', encoding='utf-8')
        rejects(self, 'ambiguous_owner', self.create)
        self.assertEqual(self.lock_path().read_text(encoding='utf-8'), 'not json')

    def test_inspect_and_cleanup_check_ownership_before_the_root(self):
        self.lock_path().unlink()
        rejects(self, 'not_owned', self.inspect)
        rejects(self, 'not_owned', self.cleanup)


class TestSetup(RepoCase):
    def test_setup_runs_in_the_worker_root_and_stays_isolated(self):
        self.write_config(commands=(SETUP_COMMAND,))
        self.create()
        self.assertEqual(self.read('build/cwd.txt', self.worker_root()),
                         str(self.worker_root()) + '\n')
        self.assertFalse((self.main / 'build').exists())
        self.assertFalse((self.coord / 'build').exists())

    def test_setup_sees_the_copied_config(self):
        source = self.write_config()
        report = self.create()
        copied = self.worker_root() / '.artel' / 'config.json'
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        self.assertEqual(report['config']['source'], 'coordinator')
        self.assertEqual(report['config']['sha256'], sha256_file(source))
        marker = json.loads(self.marker_path().read_text(encoding='utf-8'))
        self.assertEqual(marker['config']['sha256'], sha256_file(source))

    def test_a_failing_setup_retains_the_root_with_evidence(self):
        self.write_config(commands=('echo attempt >> attempts.txt && exit 3',))
        exc = rejects(self, 'setup_failed', self.create)
        self.assertIn(str(self.worker_root()), str(exc))
        marker = json.loads(self.marker_path().read_text(encoding='utf-8'))
        self.assertEqual(marker['setup']['status'], 'failed')
        self.assertEqual(marker['setup']['failed']['exit_code'], 3)
        self.assertIn('attempts.txt', marker['setup']['failed']['command'])
        self.assertTrue(self.worker_root().is_dir())
        report = self.inspect()
        self.assertEqual(report['state'], 'setup-failed')
        self.assertIn('attempts.txt', json.dumps(report['reasons']))

    def test_a_failed_setup_is_never_rerun_or_masked(self):
        self.write_config(commands=('echo x >> attempts.txt && exit 3',))
        rejects(self, 'setup_failed', self.create)
        rejects(self, 'setup_failed', self.create)
        self.assertEqual(self.read('attempts.txt', self.worker_root()), 'x\n')

    def test_an_interrupted_setup_is_reported(self):
        self.create()
        marker = json.loads(self.marker_path().read_text(encoding='utf-8'))
        marker['setup']['status'] = 'pending'
        self.marker_path().write_text(json.dumps(marker), encoding='utf-8')
        rejects(self, 'incomplete_setup', self.create)
        self.assertEqual(self.inspect()['state'], 'incomplete-setup')

    def test_setup_can_be_empty(self):
        self.write_config(commands=())
        report = self.create()
        self.assertFalse(report['setup']['ran'])
        self.assertEqual(report['setup']['commands'], 0)
        self.assertEqual(self.inspect()['state'], 'ready')

    def test_an_invalid_config_is_refused_before_any_worktree_is_created(self):
        (self.coord / '.artel' / 'config.json').write_text('{not json', encoding='utf-8')
        rejects(self, 'invalid_config', self.create)
        self.assertFalse(self.worker_root().exists())

    def test_setup_commands_must_be_strings(self):
        self.write_config(commands=(1,))
        rejects(self, 'invalid_config', self.create)
        self.assertFalse(self.worker_root().exists())

    def test_a_tracked_config_that_differs_is_refused(self):
        self.write('.artel/config.json', '{"tracked": true}\n')
        git(self.main, 'add', '-f', '.artel/config.json')
        git(self.main, 'commit', '-q', '-m', 'track a config')
        self.base = git(self.main, 'rev-parse', 'HEAD')
        rejects(self, 'config_conflict', self.create)
        self.assertFalse(self.worker_root().exists())

    def test_a_tracked_config_equal_to_the_coordinator_is_left_untouched(self):
        text = self.read('.artel/config.json', self.coord)
        self.write('.artel/config.json', text)
        git(self.main, 'add', '-f', '.artel/config.json')
        git(self.main, 'commit', '-q', '-m', 'track a config')
        self.base = git(self.main, 'rev-parse', 'HEAD')
        report = self.create()
        self.assertEqual(report['config']['source'], 'checkout')
        self.assertEqual(self.read('.artel/config.json', self.worker_root()), text)
        self.assertEqual(git(self.worker_root(), 'status', '--porcelain'), '')


class TestInspect(RepoCase):
    def test_a_missing_root_is_reported(self):
        report = self.inspect()
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['state'], 'missing')
        self.assertFalse(report['marker']['present'])
        self.assertFalse(report['worktree']['registered'])
        self.assertFalse(self.worker_root().exists())

    def test_a_complete_root_is_ready_with_its_refs(self):
        self.create()
        report = self.inspect()
        self.assertEqual(report['state'], 'ready')
        self.assertTrue(report['marker']['identity_matches'])
        self.assertTrue(report['worktree']['registered'])
        self.assertTrue(report['worktree']['detached'])
        self.assertTrue(report['worktree']['at_base'])
        self.assertEqual(report['worktree']['head'], self.base)
        self.assertTrue(report['refs']['base']['exists'])
        self.assertEqual(report['refs']['base']['commit'], self.base)
        self.assertEqual(report['refs']['base']['name'], self.base_ref())
        self.assertFalse(report['refs']['result']['exists'])
        self.assertTrue(report['artifact_root']['exists'])
        self.assertEqual(report['uncommitted']['total'], 0)

    def test_a_foreign_directory_is_reported(self):
        self.worker_root().mkdir(parents=True)
        report = self.inspect()
        self.assertEqual(report['state'], 'foreign')
        self.assertTrue(report['reasons'])

    def test_a_symlinked_root_is_reported(self):
        self.worker_root().parent.mkdir(parents=True)
        os.symlink(str(self.main), str(self.worker_root()))
        self.assertEqual(self.inspect()['state'], 'unsafe')

    def test_a_failed_setup_is_reported(self):
        self.write_config(commands=('exit 4',))
        rejects(self, 'setup_failed', self.create)
        report = self.inspect()
        self.assertEqual(report['state'], 'setup-failed')
        self.assertEqual(report['marker']['setup']['failed']['exit_code'], 4)

    def test_a_result_ref_marks_the_root_published(self):
        self.create()
        self.set_result_ref(self.base)
        report = self.inspect()
        self.assertEqual(report['state'], 'published')
        self.assertTrue(report['refs']['result']['exists'])
        self.assertEqual(report['refs']['result']['commit'], self.base)

    def test_a_deleted_base_ref_is_reported_unknown(self):
        self.create()
        git(self.coord, 'update-ref', '-d', self.base_ref())
        self.assertEqual(self.inspect()['state'], 'unknown')

    def test_inspect_changes_nothing(self):
        self.create()
        before_root = digest_tree(self.worker_root())
        before_repo = self.snapshot(self.coord)
        self.inspect()
        self.assertEqual(digest_tree(self.worker_root()), before_root)
        self.assertPreserved(before_repo, self.snapshot(self.coord))


class TestCleanup(RepoCase):
    def test_cleanup_removes_a_transported_clean_root_and_keeps_the_collected_evidence(self):
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        self.write('new.txt', 'new\n', base=self.worker_root())
        (self.worker_root() / 'docs' / 'my notes.txt').unlink()
        publication = self.publish(self.worker_root())
        report = self.cleanup(publication)
        self.assertTrue(report['removed'])
        self.assertFalse(self.worker_root().exists())
        self.assertNotIn(str(self.worker_root()),
                         git(self.coord, 'worktree', 'list', '--porcelain'))
        self.assertEqual(report['evidence']['verified'], 1)
        self.assertEqual(report['retained_refs']['base'], self.base_ref())
        self.assertEqual(report['retained_refs']['result'], self.result_ref())
        self.assertEqual(report['cycle_ref'], 'refs/heads/' + COORD_BRANCH)
        self.assertEqual(git(self.coord, 'rev-parse', self.result_ref()),
                         publication['result_commit'])
        # the collected copy outlives the worker root
        self.assertEqual((self.collected_root() / 'result.json').read_text(encoding='utf-8'),
                         '{"status": "succeeded"}\n')

    def test_cleanup_without_evidence_lists_nothing_to_verify(self):
        self.create()
        publication = self.publish(self.worker_root(), evidence=False)
        report = self.cleanup(publication)
        self.assertTrue(report['removed'])
        self.assertEqual(report['evidence']['verified'], 0)

    def test_cleanup_reports_the_publication_commits(self):
        self.create()
        publication = self.publish(self.worker_root())
        report = self.cleanup(publication)
        self.assertEqual(report['result_commit'], publication['result_commit'])
        self.assertEqual(report['cycle_commit'], publication['cycle_commit'])
        self.assertEqual(report['root'], str(self.worker_root()))


class TestCleanupAccounting(RepoCase):
    """A dirty or unaccounted root is preserved, never reset, cleaned or force-removed."""

    def test_cleanup_refuses_an_unknown_ignored_file_the_worker_hid(self):
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        self.write('.gitignore', self.IGNORE + 'dist/\n', base=self.worker_root())
        hidden = self.write('dist/keep.bin', 'worker data\n', base=self.worker_root())
        publication = self.publish(self.worker_root())
        before = digest_tree(self.worker_root())
        rejects(self, 'dirty_worktree', lambda: self.cleanup(publication))
        self.assertEqual(digest_tree(self.worker_root()), before)
        self.assertEqual(hidden.read_text(encoding='utf-8'), 'worker data\n')

    def test_cleanup_refuses_a_tree_retained_but_dirty_root_without_resetting_it(self):
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        retained = self.transport_commit(self.worker_root())  # object only; HEAD stays base
        self.set_result_ref(retained)
        entries = self.collect()
        cycle = self.integrate(retained)
        publication = self.publication(cycle_commit=cycle, result_commit=retained,
                                       collected_root=str(self.collected_root()),
                                       evidence=entries)
        before = digest_tree(self.worker_root())
        calls = []
        real = subprocess.run

        def recording(argv, **kwargs):
            calls.append(argv)
            return real(argv, **kwargs)

        with mock.patch.object(task_worktree.subprocess, 'run', recording):
            rejects(self, 'dirty_worktree', lambda: self.cleanup(publication))
        self.assertEqual(digest_tree(self.worker_root()), before)
        self.assertEqual(git(self.worker_root(), 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(self.read('app.txt', self.worker_root()), 'v2\n')
        for argv in calls:
            self.assertNotIn('reset', argv)
            self.assertNotIn('clean', argv)

    def test_cleanup_refuses_a_modification_made_after_the_transport_commit(self):
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        publication = self.publish(self.worker_root())
        self.write('app.txt', 'v3\n', base=self.worker_root())
        before = digest_tree(self.worker_root())
        rejects(self, 'dirty_worktree', lambda: self.cleanup(publication))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_an_untracked_file_outside_the_evidence_root(self):
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        publication = self.publish(self.worker_root())
        self.write('later.txt', 'not transported\n', base=self.worker_root())
        before = digest_tree(self.worker_root())
        rejects(self, 'dirty_worktree', lambda: self.cleanup(publication))
        self.assertEqual(digest_tree(self.worker_root()), before)
        self.assertEqual(self.read('later.txt', self.worker_root()), 'not transported\n')


class TestCleanupCycle(RepoCase):
    """The cycle proof is a real branch-reachable integration commit, never self-asserted."""

    def setUp(self):
        super().setUp()
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        self.result = self.transport(self.worker_root())
        self.set_result_ref(self.result)
        self.entries = self.collect()
        self.cycle = self.integrate(self.result)
        self.proof = self.publication(cycle_commit=self.cycle, result_commit=self.result,
                                      collected_root=str(self.collected_root()),
                                      evidence=self.entries)

    def proof_with(self, **overrides):
        value = dict(self.proof)
        value.update(overrides)
        return value

    def test_cleanup_refuses_a_cycle_commit_equal_to_the_base(self):
        before = digest_tree(self.worker_root())
        rejects(self, 'unpublished',
                lambda: self.cleanup(self.proof_with(cycle_commit=self.base)))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_cycle_commit_unreachable_from_the_cycle_ref(self):
        before = digest_tree(self.worker_root())
        rejects(self, 'unpublished',
                lambda: self.cleanup(self.proof_with(cycle_commit=self.orphan_commit())))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_cycle_commit_off_the_base(self):
        orphan = self.orphan_commit()
        git(self.coord, 'update-ref', 'refs/heads/side', orphan)
        before = digest_tree(self.worker_root())
        rejects(self, 'unpublished', lambda: self.cleanup(
            self.proof_with(cycle_commit=orphan, cycle_ref='refs/heads/side')))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_missing_cycle_ref(self):
        before = digest_tree(self.worker_root())
        rejects(self, 'unpublished', lambda: self.cleanup(
            self.proof_with(cycle_ref='refs/heads/absent')))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_dangling_cycle_ref(self):
        git(self.coord, 'symbolic-ref', 'refs/heads/dangling', 'refs/heads/absent')
        before = digest_tree(self.worker_root())
        rejects(self, 'unpublished', lambda: self.cleanup(
            self.proof_with(cycle_ref='refs/heads/dangling')))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_non_branch_cycle_ref(self):
        before = digest_tree(self.worker_root())
        for ref in ('refs/artel/parallel/{}/{}/{}/result'.format(RUN_ID, TASK, ATTEMPT),
                    'HEAD~1', COORD_BRANCH):
            rejects(self, 'invalid_publication',
                    lambda ref=ref: self.cleanup(self.proof_with(cycle_ref=ref)))
        self.assertEqual(digest_tree(self.worker_root()), before)


class TestCleanupCollection(RepoCase):
    """The collected copies are independently verified at both roots before removal."""

    def setUp(self):
        super().setUp()
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        self.result = self.transport(self.worker_root())
        self.set_result_ref(self.result)
        self.entries = self.collect()
        self.cycle = self.integrate(self.result)
        self.proof = self.publication(cycle_commit=self.cycle, result_commit=self.result,
                                      collected_root=str(self.collected_root()),
                                      evidence=self.entries)

    def proof_with(self, **overrides):
        value = dict(self.proof)
        value.update(overrides)
        return value

    def test_cleanup_refuses_a_missing_collected_copy(self):
        (self.collected_root() / 'result.json').unlink()
        before = digest_tree(self.worker_root())
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))
        self.assertEqual(digest_tree(self.worker_root()), before)
        self.assertTrue((self.artifact_root() / 'result.json').is_file())

    def test_cleanup_refuses_a_missing_collected_root(self):
        before = digest_tree(self.worker_root())
        shutil.rmtree(str(self.collected_root()))
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_changed_collected_copy(self):
        (self.collected_root() / 'result.json').write_text('tampered\n', encoding='utf-8')
        before = digest_tree(self.worker_root())
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_symlinked_collected_copy(self):
        before = digest_tree(self.worker_root())
        target = self.collected_root() / 'result.json'
        target.unlink()
        os.symlink(str(self.artifact_root() / 'result.json'), str(target))
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_hardlinked_collected_copy(self):
        before = digest_tree(self.worker_root())
        target = self.collected_root() / 'result.json'
        target.unlink()
        os.link(str(self.artifact_root() / 'result.json'), str(target))
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))
        self.assertEqual(digest_tree(self.worker_root()), before)

    def test_cleanup_refuses_a_collected_root_inside_the_worker(self):
        inside = self.artifact_root() / 'collected'
        inside.mkdir(parents=True, exist_ok=True)
        rejects(self, 'invalid_publication',
                lambda: self.cleanup(self.proof_with(collected_root=str(inside))))

    def test_cleanup_refuses_a_collected_root_outside_the_run_directory(self):
        outside = self.coord / '.artel' / 'run' / TICKET / 'elsewhere'
        outside.mkdir(parents=True, exist_ok=True)
        rejects(self, 'invalid_publication',
                lambda: self.cleanup(self.proof_with(collected_root=str(outside))))

    def test_cleanup_refuses_a_symlinked_collected_root(self):
        link = self.collected_root().parent / 'alias-a1'
        os.symlink(str(self.collected_root()), str(link))
        rejects(self, 'invalid_publication',
                lambda: self.cleanup(self.proof_with(collected_root=str(link))))


class TestCleanupRefusals(RepoCase):
    def setUp(self):
        super().setUp()
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        self.result = self.transport(self.worker_root())
        self.set_result_ref(self.result)
        self.entries = self.collect()
        self.cycle = self.integrate(self.result)
        self.proof = self.publication(cycle_commit=self.cycle, result_commit=self.result,
                                      collected_root=str(self.collected_root()),
                                      evidence=self.entries)

    def proof_with(self, **overrides):
        value = dict(self.proof)
        value.update(overrides)
        return value

    def test_cleanup_refuses_a_missing_root(self):
        git(self.coord, 'worktree', 'remove', '--force', str(self.worker_root()))
        rejects(self, 'not_created', lambda: self.cleanup(self.proof))

    def test_cleanup_refuses_a_foreign_root(self):
        self.marker_path().unlink()
        rejects(self, 'foreign_root', lambda: self.cleanup(self.proof))
        self.assertTrue(self.worker_root().is_dir())

    def test_cleanup_refuses_a_missing_result_ref(self):
        git(self.coord, 'update-ref', '-d', self.result_ref())
        rejects(self, 'unpublished', lambda: self.cleanup(self.proof))
        self.assertTrue(self.worker_root().is_dir())

    def test_cleanup_refuses_a_moved_result_ref(self):
        self.set_result_ref(self.base)
        rejects(self, 'unpublished', lambda: self.cleanup(self.proof))

    def test_cleanup_refuses_a_missing_cycle_commit(self):
        rejects(self, 'unpublished',
                lambda: self.cleanup(self.proof_with(cycle_commit='f' * 40)))

    def test_cleanup_refuses_a_result_commit_off_the_base(self):
        orphan = self.orphan_commit()
        self.set_result_ref(orphan)
        rejects(self, 'unpublished',
                lambda: self.cleanup(self.proof_with(result_commit=orphan)))

    def test_cleanup_refuses_a_publication_identity_mismatch(self):
        rejects(self, 'invalid_publication',
                lambda: self.cleanup(self.proof_with(attempt=ATTEMPT + 1)))

    def test_cleanup_refuses_unknown_publication_keys(self):
        rejects(self, 'invalid_publication', lambda: self.cleanup(self.proof_with(extra=True)))

    def test_cleanup_refuses_a_missing_collected_root_key(self):
        value = self.proof_with()
        del value['collected_root']
        rejects(self, 'invalid_publication', lambda: self.cleanup(value))

    def test_cleanup_refuses_a_missing_cycle_ref_key(self):
        value = self.proof_with()
        del value['cycle_ref']
        rejects(self, 'invalid_publication', lambda: self.cleanup(value))

    def test_cleanup_refuses_published_false(self):
        rejects(self, 'invalid_publication',
                lambda: self.cleanup(self.proof_with(published=False)))

    def test_cleanup_refuses_a_noncanonical_result_ref(self):
        rejects(self, 'invalid_publication',
                lambda: self.cleanup(self.proof_with(result_ref='refs/heads/parallel')))

    def test_cleanup_refuses_unproven_evidence(self):
        artifact = self.artifact_root()
        (artifact / 'extra.log').write_text('scratch\n', encoding='utf-8')
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))
        self.assertTrue((artifact / 'extra.log').is_file())

    def test_cleanup_refuses_a_changed_evidence_file(self):
        (self.artifact_root() / 'result.json').write_text('changed after collection\n',
                                                          encoding='utf-8')
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof))

    def test_cleanup_refuses_a_missing_evidence_file(self):
        rejects(self, 'evidence_mismatch', lambda: self.cleanup(self.proof_with(
            evidence=[{'path': 'result.json', 'sha256': 'a' * 64}])))

    def test_cleanup_refuses_an_interrupted_setup_root(self):
        marker = json.loads(self.marker_path().read_text(encoding='utf-8'))
        marker['setup']['status'] = 'pending'
        self.marker_path().write_text(json.dumps(marker), encoding='utf-8')
        rejects(self, 'incomplete_setup', lambda: self.cleanup(self.proof))
        self.assertTrue(self.worker_root().is_dir())

    def test_cleanup_refuses_a_deleted_base_ref(self):
        git(self.coord, 'update-ref', '-d', self.base_ref())
        rejects(self, 'unknown_worker', lambda: self.cleanup(self.proof))
        self.assertTrue(self.worker_root().is_dir())


class LooseArtelCase(RepoCase):
    """A host that ignores only `.artel/run/`, so the marker and config stay untracked."""

    IGNORE = '.artel/run/\nbuild/\n'


class TestCleanupRemoval(LooseArtelCase):
    def test_cleanup_retains_a_root_git_refuses_to_remove(self):
        self.create()
        self.write('app.txt', 'v2\n', base=self.worker_root())
        publication = self.publish(self.worker_root())
        before = digest_tree(self.worker_root())
        rejects(self, 'removal_failed', lambda: self.cleanup(publication))
        self.assertEqual(digest_tree(self.worker_root()), before)
        self.assertTrue(self.marker_path().is_file())
        self.assertIn(str(self.worker_root()),
                      git(self.coord, 'worktree', 'list', '--porcelain'))


class TestCoordinatorPreservation(RepoCase):
    def test_create_inspect_and_cleanup_leave_the_coordinator_untouched(self):
        before = self.snapshot(self.coord)
        before_main = self.snapshot(self.main)
        self.create()
        self.assertPreserved(before, self.snapshot(self.coord))
        # The only main-checkout change is the local worktree-area exclude entry
        # (docs/worktrees.md §1), asserted by its own test.
        self.assertPreserved(before_main, self.snapshot(self.main), skip=('status',))
        self.write('app.txt', 'v2\n', base=self.worker_root())
        publication = self.publish(self.worker_root())   # the integration simulation
        self.dirty_coordinator()
        after_publish = self.snapshot(self.coord)
        after_main = self.snapshot(self.main)
        self.inspect()
        self.assertPreserved(after_publish, self.snapshot(self.coord))
        self.cleanup(publication)
        self.assertPreserved(after_publish, self.snapshot(self.coord))
        self.assertPreserved(after_main, self.snapshot(self.main), skip=('status',))
        self.assertEqual(git(self.coord, 'branch', '--show-current'), COORD_BRANCH)


class TestSubprocessDiscipline(RepoCase):
    def test_every_subprocess_has_an_explicit_cwd_and_argv(self):
        calls = []
        real = subprocess.run

        def recording(argv, **kwargs):
            calls.append((argv, kwargs))
            return real(argv, **kwargs)

        with mock.patch.object(task_worktree.subprocess, 'run', recording):
            self.create()
            self.inspect()
            self.cleanup(self.publish(self.worker_root()))
        self.assertTrue(calls)
        for argv, kwargs in calls:
            self.assertIsInstance(argv, list, argv)
            self.assertIsNotNone(kwargs.get('cwd'), argv)


class TestWhitespacePaths(RepoCase):
    def test_whitespace_paths_survive_the_whole_lifecycle(self):
        self.assertIn(' ', str(self.worker_root()))
        self.assertIn(' ', str(self.artifact_root()))
        self.create()
        self.assertEqual(git(self.worker_root(), 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(self.inspect()['state'], 'ready')
        publication = self.publish(self.worker_root())
        report = self.cleanup(publication)
        self.assertTrue(report['removed'])
        self.assertFalse(self.worker_root().exists())


if __name__ == '__main__':
    unittest.main()
