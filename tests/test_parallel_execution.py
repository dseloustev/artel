"""Task 11: behavioral concurrency, crash recovery and publication coverage.

Contract: docs/opencode-parallel.md §6, §13.1-§13.12, the approved interface map, the
reviewed Task 9/Task 10 handoffs and task-11-contract-input.md.

Everything here runs the real helpers through `scripts/parallel.py` against temporary Git
repositories, with real per-task worktrees, refs, integration transactions and publication
records. The only fakes are the worker *processes* (`tests/fixtures/parallel_worker.py`,
with an entered/release barrier) and the external queue/session-lifecycle services
(`tests/fixtures/parallel_execution.py`): whole-process overlap, seat limits, crash windows
and lost replies are observable and deterministic, and no test re-implements a helper's
policy. Bounded condition waits and unconditional process cleanup replace sleeps.

Native session behavior is NOT established here: these are the deterministic fake-process
proofs; the native two-root proof lives with the Task 11 native evidence.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'tests' / 'fixtures'
sys.path.insert(0, str(FIXTURES))
import parallel_execution as px  # noqa: E402

TICKET = px.TICKET
RUN_ID = px.RUN_ID
OTHER_RUN = px.OTHER_RUN
PHASE = px.PHASE
BRANCH = 'feature/PROJ-123'


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def task_block(number, files=('src/a.txt',), deps='none', concurrency=None):
    head = '### Task {}: work on {}'.format(number, number)
    lines = [head,
             '- **Files:** {}'.format(', '.join('`{}`'.format(path) for path in files)),
             '- **Depends on:** {}'.format(deps),
             '- **Route:** light',
             '- **Test:** none — nothing to test']
    if concurrency:
        lines.append('- **Concurrency:** {}'.format(concurrency))
    lines.append('- [ ] do the work')
    return '\n'.join(lines)


def iteration(number, *blocks):
    return '## Iteration {}: iteration {}\n\n{}'.format(number, number, '\n\n'.join(blocks))


def tasklist(*iterations):
    return '# Tasklist\n\n' + '\n\n'.join(iterations) + '\n'


class ExecutionCase(unittest.TestCase):
    """A temporary repository with real Git worktrees and the real coordinator CLI."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.space = Path(os.path.realpath(self._tmp.name))
        self._coordinators = []
        self.make_repo()

    def tearDown(self):
        for coord in self._coordinators:
            for handle in list(coord.workers.values()):
                handle.terminate()
        self._tmp.cleanup()

    def make_repo(self, name='repo'):
        (self.space / 'fake-queue.json').unlink(missing_ok=True)
        self.repo = self.space / name
        git(self.space, 'init', '-q', str(self.repo))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.repo, 'config', key, value)
        self.write('.gitignore', '.artel/\n')
        exclude = self.repo / '.git' / 'info' / 'exclude'
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text('/.opencode/worktrees/\n', encoding='utf-8')
        self.write('src/a.txt', 'base a\n')
        self.write('src/b.txt', 'base b\n')
        self.write('src/c.txt', 'base c\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'init')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        git(self.repo, 'branch', '-m', 'main')
        # The repository's legitimate default branch (I9): the integration preflight
        # resolves it from this configured name and never guesses `main`.
        git(self.repo, 'config', 'init.defaultBranch', 'main')
        git(self.repo, 'checkout', '-q', '-b', BRANCH)
        self.write_config()
        return self.repo

    def write(self, rel, text, root=None):
        path = (root or self.repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def write_config(self, config=None, root=None):
        value = config if config is not None else {
            'specs': {'dir': 'specs/.current'}, 'ticket': {'projectKey': 'PROJ'},
            'parallel': {'seats': 2}, 'verify': {'commands': []}}
        return self.write('.artel/config.json', json.dumps(value), root=root)

    def write_tasklist(self, text, name='tasklist.md'):
        path = self.space / name
        path.write_text(text, encoding='utf-8')
        return path

    def coordinator(self, seats=2, tasklist=None, run_id=RUN_ID, phase=PHASE,
                    store_mode='files'):
        coord = px.Coordinator(self.repo, self.space, ticket=TICKET, run_id=run_id,
                               phase=phase, seats=seats, tasklist=tasklist,
                               store_mode=store_mode)
        self._coordinators.append(coord)
        return coord

    def write_run_state(self, completed=True, run_active=True, ticket=None, extra=None):
        value = {'ticket': ticket or '{}-{}'.format(TICKET, PHASE), 'run_active': run_active,
                 'completed': completed, 'pause_reason': None,
                 'started_at': '2026-10-16T00:00:00Z',
                 'counters': {'verify': 0, 'review_rounds': 0, 'escalations': 0,
                              'correction_rounds': 0},
                 'effective_mode': 'yolo', 'gates_confirmed': ['TASKLIST_READY'],
                 'deviation_files': []}
        value.update(extra or {})
        path = self.repo / '.artel' / 'run' / TICKET / 'run-state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        return path

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def head(self):
        return git(self.repo, 'rev-parse', 'HEAD')

    def commit_count(self):
        return int(git(self.repo, 'rev-list', '--count', '{}..HEAD'.format(self.base)))

    def changed_paths(self, commit):
        return git(self.repo, 'show', '--pretty=format:', '--name-only', commit).split()

    def fresh_recover(self, task, timeout=120, store_mode='files'):
        """Run the recovery procedure in a NEW process and return its report."""
        report = self.space / 'recover-{}.json'.format(task)
        proc = subprocess.run(
            [sys.executable, str(FIXTURES / 'parallel_execution.py'), 'recover',
             '--repo', str(self.repo), '--space', str(self.space), '--ticket', TICKET,
             '--run-id', RUN_ID, '--phase', str(PHASE), '--task', task,
             '--store-mode', store_mode, '--out', str(report)],
            cwd=str(self.repo), capture_output=True, text=True, timeout=timeout)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return self.read_json(report)

    # --- shared driver steps ------------------------------------------------------------

    def launch_all_resumable(self, coord, changed):
        """One coordinator pass: claim what admission lists, then launch every resumable
        reservation after a FRESH admission; returns the newly launched handles."""
        data = coord.admit()
        self.assertFalse(data['drain'], data)
        for candidate in data['start'][:data['claim_capacity']]:
            action = coord.claim(candidate)['action']
            self.assertEqual(action['kind'], 'run', action)
            self.assertEqual(action['task'], candidate, action)
        data = coord.admit()
        fresh = []
        for task in data['resume']:
            if coord.task_state(task) == 'reserved':
                fresh.append(coord.launch(task, changed=changed[task]))
        if fresh:
            coord.wait_entered(fresh)
        return data, fresh

    def release_and_settle(self, coord, changed, tasks):
        """Release every alive worker, wait for its exit and walk it to `prepared`."""
        alive = [handle for handle in coord.workers.values() if handle.alive()]
        for handle in alive:
            handle.release()
        coord.wait_exited(alive)
        for task in tasks:
            if coord.task_state(task) in ('running', 'verifying/reviewing'):
                coord.finish(task)

    def integrate_and_publish(self, coord, task):
        coord.integrate(task)
        return coord.publish(task)


# ============================================================================================
# whole-process overlap and seat limits
# ============================================================================================

class TestProcessOverlap(ExecutionCase):
    def test_two_workers_overlap_before_either_result_completes(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/b.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        changed = {'2.1': 'src/a.txt', '2.2': 'src/b.txt'}

        data, fresh = self.launch_all_resumable(coord, changed)
        self.assertEqual(data['resume'], ['2.1', '2.2'])
        self.assertEqual(len(fresh), 2)
        coord.wait_entered(fresh)

        # The overlap is real and complete BEFORE either worker may finish: both processes
        # are alive at the same instant, both have entered the barrier, and neither result
        # exists yet.
        self.assertEqual(coord.alive_count(), 2)
        self.assertTrue(all(handle.alive() for handle in fresh))
        self.assertFalse(any(handle.result_exists() for handle in fresh))
        pids = [self.read_json(handle.paths['entered'])['pid'] for handle in fresh]
        self.assertEqual(len(set(pids)), 2)
        self.assertIsNone(coord.disposition('2.1'))
        self.assertIsNone(coord.disposition('2.2'))

        # While both seats are busy nothing new may be admitted.
        data = coord.admit()
        self.assertEqual(data['start'], [])
        self.assertEqual(data['resume'], [])
        self.assertEqual(data['claim_capacity'], 0)

        for handle in fresh:
            handle.release()
        coord.wait_exited(fresh)
        self.assertEqual(coord.alive_count(), 0)
        for task in ('2.1', '2.2'):
            self.assertEqual(coord.disposition(task), 'succeeded')
            self.assertTrue(os.path.isfile(coord.artifact_root(task, 1) / 'result.json'))

    def test_seats_one_two_three_bound_the_actual_simultaneous_workers(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/b.txt',)),
                                  task_block('2.3', files=('src/c.txt',))))
        changed = {'2.1': 'src/a.txt', '2.2': 'src/b.txt', '2.3': 'src/c.txt'}
        for seats in (1, 2, 3):
            with self.subTest(seats=seats):
                self.make_repo('repo-seats-{}'.format(seats))
                self.write_config({
                    'specs': {'dir': 'specs/.current'}, 'ticket': {'projectKey': 'PROJ'},
                    'parallel': {'seats': seats}, 'verify': {'commands': []}})
                coord = self.coordinator(seats=seats,
                                         tasklist=self.write_tasklist(text))
                coord.arm(seats=seats)
                tasks = ['2.1', '2.2', '2.3']
                max_alive = 0
                while any(coord.task_state(task) != 'published' for task in tasks):
                    data, fresh = self.launch_all_resumable(coord, changed)
                    if fresh:
                        max_alive = max(max_alive, coord.alive_count())
                        self.assertLessEqual(coord.alive_count(), seats)
                        if coord.alive_count() == seats:
                            blocked = coord.admit()
                            self.assertEqual(blocked['start'], [])
                            self.assertEqual(blocked['resume'], [])
                    self.release_and_settle(coord, changed, tasks)
                    for task in tasks:
                        if coord.task_state(task) == 'prepared':
                            self.integrate_and_publish(coord, task)
                    max_alive = max(max_alive, coord.alive_count())
                self.assertEqual(max_alive, min(seats, len(tasks)))
                for task in tasks:
                    self.assertEqual(coord.task_state(task), 'published')
                self.assertEqual(self.commit_count(), 3)


# ============================================================================================
# dependents, prepared siblings and the integration drain
# ============================================================================================

class TestDependencyAndDrain(ExecutionCase):
    def test_dependent_waits_for_code_review_evidence_queue_and_persisted_publication(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/c.txt',), deps='2.1')))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        changed = {'2.1': 'src/a.txt', '2.2': 'src/c.txt'}

        # A queue-backed claim: the fake queue returns this run's row.
        data = coord.admit()
        self.assertEqual(data['start'], ['2.1'])
        action = coord.claim('2.1')['action']
        self.assertEqual(action['kind'], 'run')
        data = coord.admit()
        self.assertEqual(data['resume'], ['2.1'])
        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.1')
        self.assertEqual(coord.task_state('2.1'), 'prepared')

        # Not even a prepared, reviewed, gate-green result unlocks the dependent.
        data = coord.admit()
        self.assertNotIn('2.2', data['start'])
        self.assertNotIn('2.2', data['resume'])

        coord.integrate('2.1')
        self.assertEqual(self.commit_count(), 1)
        data = coord.admit()
        self.assertTrue(data['drain'], data)
        self.assertEqual(data['start'], [])
        self.assertNotIn('2.2', data['start'])

        # The publication plan: evidence collection, then the owned queue completion LAST.
        actions = coord.publication_next('2.1')
        self.assertEqual([a['kind'] for a in actions], ['collect-evidence', 'queue-done'])
        collect = actions[0]
        coord.publication_record('2.1', collect, 'planned', None)
        evidence = coord.perform_effect(collect)
        coord.publication_record('2.1', collect, 'applied', evidence)
        self.assertEqual(coord.task_state('2.1'), 'integrating')
        self.assertEqual(self.read_json(coord.manifest_path())['tasks']['2.1']['state'],
                         'integrating')
        data = coord.admit()
        self.assertTrue(data['drain'])
        self.assertNotIn('2.2', data['start'])
        self.assertTrue((coord.collected_root('2.1', 1) / 'result.json').is_file())

        queue = coord.publication_next('2.1')[0]
        self.assertEqual(queue['kind'], 'queue-done')
        coord.publication_record('2.1', queue, 'planned', None)
        coord.publication_record('2.1', queue, 'applied', coord.perform_effect(queue))
        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertEqual(coord.queue.row('2.1')['status'], 'done')
        self.assertEqual(coord.queue.completions('2.1'), 1)

        # Only now does the dependent enter admission.
        data = coord.admit()
        self.assertFalse(data['drain'], data)
        self.assertIn('2.2', data['start'])

        # A queue-free reservation goes through the same lifecycle with no queue action.
        coord.transition('2.2', None, 'reserved')
        data = coord.admit()
        self.assertEqual(data['resume'], ['2.2'])
        handle = coord.launch('2.2', changed='src/c.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.2')
        coord.integrate('2.2')
        actions = coord.publication_next('2.2')
        self.assertEqual([a['kind'] for a in actions], ['collect-evidence'])
        coord.publication_record('2.2', actions[0], 'planned', None)
        coord.publication_record('2.2', actions[0], 'applied',
                                 coord.perform_effect(actions[0]))
        self.assertEqual(coord.task_state('2.2'), 'published')
        self.assertEqual(self.commit_count(), 2)

    def test_two_prepared_results_park_and_then_forward_progress(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/b.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        changed = {'2.1': 'src/a.txt', '2.2': 'src/b.txt'}
        data, fresh = self.launch_all_resumable(coord, changed)
        self.assertEqual(len(fresh), 2)
        self.release_and_settle(coord, changed, ['2.1', '2.2'])
        self.assertEqual(coord.task_state('2.1'), 'prepared')
        self.assertEqual(coord.task_state('2.2'), 'prepared')

        # 2.1 integrates first; the prepared sibling parks, named in the drain record.
        coord.transition('2.1', 'prepared', 'integrating')
        assignment = coord.workers['2.1'].assignment
        result = coord.read_json(coord.artifact_root('2.1', 1) / 'result.json')
        transaction = coord.ok('integration', 'prepare', request={
            'repo': str(coord.repo), 'assignment': assignment, 'result': result})
        self.assertEqual(transaction['drain']['in_flight'], [])
        self.assertEqual(transaction['drain']['waiting'], ['2.2'])
        self.assertEqual(coord.task_state('2.2'), 'prepared')
        self.assertFalse(coord.workers['2.2'].alive())
        data = coord.admit()
        self.assertTrue(data['drain'], data)
        self.assertEqual(data['start'], [])
        self.assertEqual(data['resume'], [])

        transaction = coord.ok('integration', 'apply', request={
            'repo': str(coord.repo), 'assignment': assignment, 'prepared': transaction})
        transaction = coord.ok('integration', 'commit', request={
            'repo': str(coord.repo), 'transaction': transaction,
            'subject': px.SUBJECT})
        coord.publish('2.1')
        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertEqual(coord.task_state('2.2'), 'prepared')

        # Forward progress: the parked sibling integrates on its own turn, untouched.
        data = coord.admit()
        self.assertFalse(data['drain'])
        coord.integrate('2.2')
        coord.publish('2.2')
        self.assertEqual(coord.task_state('2.2'), 'published')
        self.assertEqual(self.commit_count(), 2)
        self.assertEqual(self.changed_paths(git(self.repo, 'rev-parse', 'HEAD')),
                         ['src/b.txt'])

    def test_exclusive_task_holds_the_run_alone_until_it_is_published(self):
        text = tasklist(iteration(
            2, task_block('2.1', files=('src/a.txt',),
                          concurrency='exclusive — the fixture database is shared'),
            task_block('2.2', files=('src/b.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        data = coord.admit()
        self.assertEqual(data['start'], ['2.1'])
        self.assertTrue(any('exclusive' in reason for reason in data['reasons']))
        action = coord.claim('2.1')['action']
        self.assertEqual(action['kind'], 'run')

        # While the exclusive reservation is unresolved nothing else is admitted.
        data = coord.admit()
        self.assertEqual(data['resume'], ['2.1'])
        self.assertEqual(data['start'], [])
        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.1')
        # The exclusive result at `prepared` holds the run from admission to publication.
        data = coord.admit()
        self.assertTrue(data['drain'], data)
        self.assertEqual(data['start'], [])
        self.assertNotIn('2.2', data['resume'])

        coord.integrate('2.1')
        coord.publish('2.1')
        data = coord.admit()
        self.assertFalse(data['drain'], data)
        self.assertIn('2.2', data['start'])

    def test_integration_refuses_until_the_sibling_reaches_safe_return(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/b.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        changed = {'2.1': 'src/a.txt', '2.2': 'src/b.txt'}
        _, fresh = self.launch_all_resumable(coord, changed)
        self.assertEqual(len(fresh), 2)
        for handle in fresh:
            handle.release()
        coord.wait_exited(fresh)
        coord.finish('2.1')
        coord.transition('2.1', 'prepared', 'integrating')

        # 2.2 is still running: the transport/commit hooks must not run beside it.
        assignment = coord.workers['2.1'].assignment
        result = coord.read_json(coord.artifact_root('2.1', 1) / 'result.json')
        error = coord.refused(1, 'integration', 'prepare', request={
            'repo': str(coord.repo), 'assignment': assignment, 'result': result})
        self.assertEqual(error['kind'], 'drain_required')
        self.assertIn('2.2', error['message'])
        self.assertFalse(coord.record_path('2.1', 1).exists())

        coord.finish('2.2')
        transaction = coord.ok('integration', 'prepare', request={
            'repo': str(coord.repo), 'assignment': assignment, 'result': result})
        self.assertEqual(transaction['drain']['waiting'], ['2.2'])


# ============================================================================================
# isolation of roots, diffs and evidence
# ============================================================================================

class TestIsolation(ExecutionCase):
    def test_worker_roots_diffs_refs_and_evidence_stay_isolated(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/b.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        changed = {'2.1': 'src/a.txt', '2.2': 'src/b.txt'}
        _, fresh = self.launch_all_resumable(coord, changed)
        self.assertEqual(len(fresh), 2)
        self.release_and_settle(coord, changed, ['2.1', '2.2'])
        transactions = {}
        for task in ('2.1', '2.2'):
            transactions[task] = coord.integrate(task)
            coord.publish(task)

        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertEqual(coord.task_state('2.2'), 'published')
        self.assertEqual(self.commit_count(), 2)
        first = transactions['2.1']['cycle_commit']
        second = transactions['2.2']['cycle_commit']
        self.assertEqual(self.changed_paths(first), ['src/a.txt'])
        self.assertEqual(self.changed_paths(second), ['src/b.txt'])

        # Each root carries exactly its own change; the sibling's file is untouched.
        self.assertEqual((coord.worker_root('2.1', 1) / 'src/b.txt').read_text(
            encoding='utf-8'), 'base b\n')
        self.assertEqual((coord.worker_root('2.2', 1) / 'src/a.txt').read_text(
            encoding='utf-8'), 'base a\n')

        # Namespaced refs per task/attempt, and the collected evidence is a real copy of
        # exactly this task's artifact set.
        for task, transaction in transactions.items():
            result_ref = 'refs/artel/parallel/{}/{}/1/result'.format(RUN_ID, task)
            self.assertEqual(git(self.repo, 'rev-parse', result_ref),
                             transaction['transport_commit'])
            collected = coord.collected_root(task, 1)
            self.assertEqual([path.name for path in sorted(collected.iterdir())],
                             ['result.json'])
            self.assertEqual(
                px.sha256_file(collected / 'result.json'),
                px.sha256_file(coord.artifact_root(task, 1) / 'result.json'))
            # The evidence copy is exactly this task's result: the other task's collected
            # root is a different directory holding its own result.
            other = '2.2' if task == '2.1' else '2.1'
            self.assertNotEqual(px.sha256_file(collected / 'result.json'),
                                px.sha256_file(coord.collected_root(other, 1) /
                                               'result.json'))


# ============================================================================================
# restart, missed completion, interruption, cancellation
# ============================================================================================

class TestCrashRecovery(ExecutionCase):
    def one_task_coordinator(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        return coord

    def claim_launch_release(self, coord):
        action = coord.claim('2.1')['action']
        self.assertEqual(action['kind'], 'run')
        data = coord.admit()
        self.assertEqual(data['resume'], ['2.1'])
        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        return handle

    def test_fresh_process_recovers_a_missed_completion(self):
        coord = self.one_task_coordinator()
        self.claim_launch_release(coord)
        # The coordinator never observed the completion: no transitions, no integration.
        self.assertEqual(coord.task_state('2.1'), 'running')
        self.assertTrue((coord.artifact_root('2.1', 1) / 'result.json').is_file())
        self.assertEqual(coord.disposition('2.1'), 'succeeded')
        self.assertFalse(coord.record_path('2.1', 1).exists())

        report = self.fresh_recover('2.1')
        self.assertEqual(report['state'], 'published')
        self.assertIn('running->verifying/reviewing', report['steps'])
        self.assertIn('verifying/reviewing->prepared', report['steps'])
        self.assertIn('prepared->integrating', report['steps'])
        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertEqual(self.commit_count(), 1)
        self.assertEqual(self.changed_paths(git(self.repo, 'rev-parse', 'HEAD')),
                         ['src/a.txt'])
        self.assertEqual(coord.queue.row('2.1')['status'], 'done')
        self.assertEqual(coord.queue.completions('2.1'), 1)
        self.assertTrue((coord.collected_root('2.1', 1) / 'result.json').is_file())

    def test_crash_between_commit_and_receipt_recovers_the_landed_cycle_exactly_once(self):
        coord = self.one_task_coordinator()
        self.claim_launch_release(coord)
        coord.finish('2.1')
        coord.transition('2.1', 'prepared', 'integrating')
        assignment = coord.workers['2.1'].assignment
        result = coord.read_json(coord.artifact_root('2.1', 1) / 'result.json')
        transaction = coord.ok('integration', 'prepare', request={
            'repo': str(coord.repo), 'assignment': assignment, 'result': result})
        coord.ok('integration', 'apply', request={
            'repo': str(coord.repo), 'assignment': assignment, 'prepared': transaction})

        # Widen the real crash window with a real post-commit hook: the commit lands, the
        # hook holds the git process until the test releases it, and the coordinator is
        # killed before its receipt.
        marker = self.space / 'commit-landed'
        hook_release = self.space / 'commit-hook-release'
        hooks = self.repo / '.git' / 'hooks'
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / 'post-commit'
        hook.write_text(
            '#!/bin/sh\n'
            'touch "{}"\n'
            'i=0\n'
            'while [ ! -f "{}" ] && [ $i -lt 200 ]; do sleep 0.05; i=$((i+1)); done\n'
            'touch "{}.done"\n'.format(marker, hook_release, marker),
            encoding='utf-8')
        hook.chmod(0o755)
        request = self.space / 'commit-request.json'
        request.write_text(json.dumps({'repo': str(coord.repo),
                                       'transaction': coord.read_json(
                                           coord.record_path('2.1', 1)),
                                       'subject': px.SUBJECT,
                                       'coordinator_id': px.COORDINATOR}),
                           encoding='utf-8')
        proc = subprocess.Popen([sys.executable, str(px.CLI), 'integration', 'commit',
                                 '--request', str(request)], cwd=str(coord.repo),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertIsNotNone(px.wait_for(marker.exists, timeout=30),
                             'the cycle commit never landed')
        proc.kill()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
        # Release and join the hook's git process: no test process may outlive the test.
        hook_release.write_text('release\n', encoding='utf-8')
        done = Path(str(marker) + '.done')
        self.assertIsNotNone(px.wait_for(done.exists, timeout=20),
                             'the post-commit hook never finished')
        hook.unlink()
        head = self.head()
        self.assertEqual(self.commit_count(), 1)
        durable = coord.read_json(coord.record_path('2.1', 1))
        self.assertEqual(durable['stage'], 'committing')
        self.assertIsNone(durable['cycle_commit'])
        self.assertEqual(coord.task_state('2.1'), 'integrating')

        report = self.fresh_recover('2.1')
        self.assertEqual(report['state'], 'published')
        self.assertEqual(report['cycle_commit'], head)
        self.assertIn('integration:recovered', report['steps'])
        self.assertEqual(self.commit_count(), 1, 'a second cycle commit was created')
        self.assertEqual(self.head(), head)

    def test_interrupted_worker_over_a_contradictory_result_is_never_published(self):
        coord = self.one_task_coordinator()
        action = coord.claim('2.1')['action']
        self.assertEqual(action['kind'], 'run')
        coord.admit()
        handle = coord.launch('2.1', changed='src/a.txt', result_first=True)
        coord.wait_entered([handle])
        self.assertTrue(handle.result_exists())
        self.assertEqual(self.read_json(handle.paths['result'])['status'], 'succeeded')
        coord.interrupt('2.1')
        self.assertEqual(coord.disposition('2.1'), 'interrupted')

        report = self.fresh_recover('2.1')
        self.assertEqual(report.get('blocked'), "worker disposition is 'interrupted'; "
                                                'never integrated')
        self.assertEqual(coord.task_state('2.1'), 'running')
        self.assertFalse(coord.record_path('2.1', 1).exists())
        self.assertEqual(self.commit_count(), 0)

        # Even after a wrong coordinator walk to `integrating`, the durable interruption
        # wins over the contradictory succeeded result: no publication action exists.
        coord.transition('2.1', 'running', 'verifying/reviewing')
        coord.fill_review('2.1')
        coord.transition('2.1', 'verifying/reviewing', 'prepared')
        coord.transition('2.1', 'prepared', 'integrating')
        assignment = coord.workers['2.1'].assignment
        result = coord.read_json(coord.artifact_root('2.1', 1) / 'result.json')
        transaction = coord.ok('integration', 'prepare', request={
            'repo': str(coord.repo), 'assignment': assignment, 'result': result})
        transaction = coord.ok('integration', 'apply', request={
            'repo': str(coord.repo), 'assignment': assignment, 'prepared': transaction})
        coord.ok('integration', 'commit', request={
            'repo': str(coord.repo), 'transaction': transaction, 'subject': px.SUBJECT})
        error = coord.refused(1, 'publication', 'next', request={
            'repo': str(coord.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'task': '2.1',
            'observed': coord.observed('2.1')})
        self.assertEqual(error['kind'], 'worker_not_succeeded')
        self.assertEqual(self.read_json(coord.artifact_root('2.1', 1) /
                                        'result.json')['status'], 'succeeded')

    def test_cancelled_task_retries_with_a_new_attempt_before_unlocking_a_dependent(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/c.txt',), deps='2.1')))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        coord.transition('2.1', None, 'reserved')
        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        coord.transition('2.1', 'running', 'cancelled',
                         fields={'decision': {'reason': 'interrupted by the person'}})
        coord.interrupt('2.1')
        self.assertEqual(coord.task_state('2.1'), 'cancelled')
        data = coord.admit()
        self.assertFalse(data['drain'])
        self.assertNotIn('2.2', data['start'])
        self.assertNotIn('2.2', data['resume'])
        self.assertTrue(coord.worker_root('2.1', 1).exists())

        coord.transition('2.1', 'cancelled', 'reserved',
                         fields={'decision': {'reason': 'retried as attempt 2'}})
        self.assertEqual(coord.attempt('2.1'), 2)
        data = coord.admit()
        self.assertEqual(data['resume'], ['2.1'])
        handle = coord.launch('2.1', changed='src/a.txt', attempt=2)
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.1', attempt=2)
        coord.integrate('2.1')
        coord.publish('2.1')
        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertTrue(coord.worker_root('2.1', 1).exists(),
                        'the cancelled attempt root was not retained')
        self.assertTrue(coord.worker_root('2.1', 2).exists())
        data = coord.admit()
        self.assertIn('2.2', data['start'])

    def test_lost_store_reply_replans_observed_and_keeps_the_effect_once(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text),
                                 store_mode='store')
        coord.arm()
        action = coord.claim('2.1')['action']
        self.assertEqual(action['kind'], 'run')
        data = coord.admit()
        self.assertEqual(data['resume'], ['2.1'])
        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.1')
        coord.integrate('2.1')

        actions = coord.publication_next('2.1')
        self.assertEqual([a['kind'] for a in actions],
                         ['store-tick', 'collect-evidence', 'queue-done'])
        tick = actions[0]
        self.assertEqual(tick['state'], 'pending')
        self.assertEqual(tick['payload']['expected_version'], 1)
        coord.publication_record('2.1', tick, 'planned', None)
        # The store write lands at the next version; its reply is lost.
        coord.perform_effect(tick)
        self.assertEqual(coord.store.effects(), 1)
        self.assertEqual(coord.task_state('2.1'), 'integrating')

        # A fresh process re-plans from the durable intent: the landed tick is `observed`,
        # never re-applied, and the run still completes.
        report = self.fresh_recover('2.1', store_mode='store')
        self.assertEqual(report['state'], 'published')
        self.assertEqual(coord.store.effects(), 1)
        self.assertTrue(coord.store.document()['applied'])
        self.assertEqual(coord.queue.completions('2.1'), 1)
        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertEqual(self.commit_count(), 1)

    def test_lost_queue_reply_reconciles_without_repeating_the_effect(self):
        coord = self.one_task_coordinator()
        self.claim_launch_release(coord)
        coord.finish('2.1')
        coord.integrate('2.1')

        collect = coord.publication_next('2.1')[0]
        self.assertEqual(collect['kind'], 'collect-evidence')
        coord.publication_record('2.1', collect, 'planned', None)
        coord.publication_record('2.1', collect, 'applied', coord.perform_effect(collect))
        queue = coord.publication_next('2.1')[0]
        self.assertEqual(queue['kind'], 'queue-done')
        coord.publication_record('2.1', queue, 'planned', None)
        # The effect lands, the reply is lost: no receipt is recorded.
        coord.perform_effect(queue)
        self.assertEqual(coord.queue.row('2.1')['status'], 'done')
        self.assertEqual(coord.queue.completions('2.1'), 1)

        report = self.fresh_recover('2.1')
        self.assertEqual(report['state'], 'published')
        self.assertEqual(coord.queue.completions('2.1'), 1,
                         'the queue effect was repeated after the lost reply')
        self.assertEqual(coord.task_state('2.1'), 'published')

    def test_complete_record_with_a_lost_manifest_write_promotes_once(self):
        # A queue-free reservation: its single collect-evidence receipt is the LAST
        # required effect, so the receipt is what would have promoted the manifest.
        coord = self.one_task_coordinator()
        coord.transition('2.1', None, 'reserved')
        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.1')
        coord.integrate('2.1')
        actions = coord.publication_next('2.1')
        self.assertEqual([a['kind'] for a in actions], ['collect-evidence'])
        collect = actions[0]
        coord.publication_record('2.1', collect, 'planned', None)
        evidence = coord.perform_effect(collect)

        run_dir = coord.manifest_path().parent
        original = run_dir.stat().st_mode
        os.chmod(str(run_dir), stat.S_IRUSR | stat.S_IXUSR)
        try:
            transaction = coord.read_json(coord.record_path('2.1', 1))
            receipt = {'schema': 1, 'publication': transaction['publication'],
                       'action': collect, 'state': 'applied', 'evidence': evidence}
            proc, envelope = coord.cli('publication', 'record', request={
                'repo': str(coord.repo), 'transaction': transaction, 'task': '2.1',
                'action_id': collect['action_id'], 'receipt': receipt})
            self.assertEqual(proc.returncode, 2, proc.stderr or proc.stdout)
            self.assertFalse(envelope['ok'])
        finally:
            os.chmod(str(run_dir), original)
        stored = coord.read_json(coord.record_path('2.1', 1))
        self.assertTrue(stored['publication']['complete'])
        self.assertEqual(coord.task_state('2.1'), 'integrating')

        report = self.fresh_recover('2.1')
        self.assertEqual(report['state'], 'published')
        self.assertEqual(coord.task_state('2.1'), 'published')
        self.assertEqual(self.commit_count(), 1)


# ============================================================================================
# queue authority, wrong phase, the second coordinator
# ============================================================================================

class TestQueueAuthority(ExecutionCase):
    def test_wrong_phase_row_is_released_once_and_stops_admission(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        revision = coord.revision()
        intent = coord.ok('claim', 'prepare', request={
            'repo': str(coord.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': revision})['intent']
        row = coord.queue.claim('2.1', intent['actor'], phase=3)
        data = coord.ok('claim', 'record', request={
            'repo': str(coord.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': revision + 1, 'intent_id': intent['intent_id'],
            'row': row})['action']
        self.assertEqual(data['kind'], 'release')
        self.assertEqual(data['reason'], 'wrong-phase')
        manifest = coord.manifest()
        self.assertEqual(manifest['stop']['reason'], 'wrong-phase')
        self.assertEqual(manifest['claims']['intents'][0]['state'], 'wrong-phase')

        # Admission is stopped for reconciliation; nothing new is claimed.
        error = coord.refused(1, 'claim', 'prepare', request={
            'repo': str(coord.repo), 'ticket': TICKET, 'run_id': RUN_ID,
            'expected_revision': coord.revision()})
        self.assertEqual(error['kind'], 'admission_stopped')

        # A fresh reconcile with the row still held by this exact actor re-issues the one
        # release; after the release lands it is never repeated.
        reconciled = coord.reconcile()
        self.assertEqual([action['kind'] for action in reconciled['actions']], ['release'])
        coord.queue.release(row)
        reconciled = coord.reconcile()
        self.assertEqual(reconciled['actions'], [])
        self.assertEqual(coord.manifest()['claims']['intents'][0]['state'], 'wrong-phase')
        self.assertIsNotNone(coord.manifest()['stop'])

    def test_returned_row_is_authoritative_and_conflicting_reservations_serialize(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/a.txt',)),
                                  task_block('2.3', files=('src/c.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        coord.arm()
        data = coord.admit()
        # Bounded look-ahead: the incompatible head is skipped, not the whole tail.
        self.assertEqual(data['start'], ['2.1', '2.3'])
        self.assertEqual(data['claim_capacity'], 2)

        first = coord.claim('2.1')
        self.assertEqual(first['action']['kind'], 'run')
        self.assertEqual(first['action']['task'], '2.1')
        # The queue returns 2.2 for the second admission attempt; the returned row decides.
        second = coord.claim('2.3', row_task='2.2')
        self.assertEqual(second['action']['task'], '2.2')
        self.assertEqual(second['manifest']['tasks']['2.2']['state'], 'reserved')

        data = coord.admit()
        self.assertEqual(data['resume'], ['2.1'])
        self.assertNotIn('2.2', data['resume'])
        self.assertTrue(any('2.2' in reason for reason in data['reasons']), data['reasons'])
        # The run may still offer a bounded new claim (2.3); the withheld 2.2 never does.
        self.assertEqual(data['start'], ['2.3'])
        self.assertEqual(data['claim_capacity'], 1)

        handle = coord.launch('2.1', changed='src/a.txt')
        coord.wait_entered([handle])
        handle.release()
        coord.wait_exited([handle])
        coord.finish('2.1')
        coord.integrate('2.1')
        coord.publish('2.1')

        data = coord.admit()
        self.assertIn('2.2', data['resume'])
        self.assertIn('2.3', data['start'])
        row = coord.queue.row('2.2')
        self.assertEqual(row['task_id'], second['row']['task_id'])
        self.assertEqual(row['actor'], second['intent']['actor'])

    def test_a_second_coordinator_is_refused_from_the_main_and_linked_checkouts(self):
        coord = self.coordinator(seats=2, tasklist=None)
        record = coord.acquire()
        coord.create_run()
        lock_path = Path(git(self.repo, 'rev-parse', '--path-format=absolute',
                             '--git-common-dir')) / 'artel' / 'parallel' / (TICKET + '.json')
        lock_before = lock_path.read_bytes()
        manifest_before = coord.manifest_path().read_bytes()

        git(self.repo, 'worktree', 'add', '-q', '--detach', str(self.space / 'linked'),
            'HEAD')
        linked = self.space / 'linked'
        self.write_config(root=linked)
        second = px.Coordinator(linked, self.space, ticket=TICKET, run_id=OTHER_RUN,
                                phase=PHASE, seats=2, tasklist=None)
        self._coordinators.append(second)
        foreign = dict(coord.assignment('2.1', 1, 'src/a.txt'),
                       run_id=OTHER_RUN,
                       worker_root=str(self.repo / '.opencode' / 'worktrees' / 'parallel' /
                                       TICKET / OTHER_RUN / '2.1-a1'),
                       artifact_root=str(self.repo / '.opencode' / 'worktrees' / 'parallel' /
                                         TICKET / OTHER_RUN / '2.1-a1' / '.artel' /
                                         'artifacts' / '2.1-a1'))
        for root, label in ((self.repo, 'main'), (linked, 'linked')):
            with self.subTest(checkout=label):
                error = second.refused(1, 'owner', 'acquire', request={
                    'repo': str(root), 'ticket': TICKET,
                    'owner': {'schema': 1, 'ticket': TICKET, 'run_id': OTHER_RUN,
                              'phase': PHASE}})
                self.assertEqual(error['kind'], 'ticket_owned')
                error = second.refused(1, 'run', 'create', request={
                    'repo': str(root), 'ticket': TICKET, 'phase': PHASE,
                    'run_id': OTHER_RUN, 'seats': 2, 'graph_digest': 'd' * 64})
                self.assertEqual(error['kind'], 'foreign_owner')
                error = second.refused(1, 'worktree', 'create', request={
                    'repo': str(root), 'assignment': foreign})
                self.assertEqual(error['kind'], 'foreign_owner')
                error = second.refused(1, 'run', 'inspect', request={
                    'repo': str(root), 'ticket': TICKET, 'run_id': OTHER_RUN})
                self.assertEqual(error['kind'], 'missing_manifest')
        self.assertEqual(lock_path.read_bytes(), lock_before)
        self.assertEqual(coord.manifest_path().read_bytes(), manifest_before)

        # The owning run itself re-acquires its record from the linked checkout, proving
        # the lock is repository-common, not checkout-local.
        again = second.ok('owner', 'acquire', request={
            'repo': str(linked), 'ticket': TICKET,
            'owner': {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE}})
        self.assertEqual(again['token'], record['token'])


# ============================================================================================
# the durable correction budget and the final gates
# ============================================================================================

class TestBudgetAndFinalGates(ExecutionCase):
    def reserve(self, coord, **overrides):
        request = {'repo': str(coord.repo), 'ticket': TICKET, 'run_id': RUN_ID,
                   'phase': PHASE, 'kind': 'review', 'scope': '2.1', 'attempt': 1,
                   'ordinal': 1, 'expected_revision': 0}
        request.update(overrides)
        return coord.ok('budget', 'reserve', request=request)

    def state(self, coord):
        return coord.read_json(coord.repo / '.artel' / 'run' / TICKET / 'run-state.json')

    def test_budget_reservation_is_durable_before_the_fix_and_survives_restart(self):
        coord = self.coordinator(seats=2, tasklist=None)
        coord.acquire()
        state_path = self.write_run_state(run_active=True)
        before = state_path.read_bytes()

        inspect = coord.ok('budget', 'inspect', request={
            'repo': str(coord.repo), 'ticket': TICKET, 'phase': PHASE})
        self.assertEqual(inspect['cap'], 8)
        self.assertEqual(inspect['correction_rounds'], 0)
        self.assertEqual(state_path.read_bytes(), before, 'inspect wrote the run state')

        reserved = self.reserve(coord, expected_revision=0)
        self.assertFalse(reserved['replayed'])
        self.assertEqual(self.state(coord)['counters']['correction_rounds'], 1)

        # A fresh process reads the same durable ledger and reservation block.
        request_path = self.space / 'budget-request.json'
        request_path.write_text(json.dumps({'repo': str(coord.repo), 'ticket': TICKET,
                                            'phase': PHASE}), encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, str(px.CLI), 'budget', 'inspect', '--request',
             str(request_path)], cwd=str(coord.repo), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fresh = json.loads(proc.stdout.strip().splitlines()[-1])['data']
        self.assertEqual(fresh['correction_rounds'], 1)
        self.assertEqual(fresh['remaining'], 7)
        self.assertEqual(len(fresh['reservations']), 1)

        # A lost reply replays the same identity for free; a new round charges once.
        replay = self.reserve(coord, expected_revision=1)
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.state(coord)['counters']['correction_rounds'], 1)
        second = self.reserve(coord, scope='2.2', expected_revision=1)
        self.assertFalse(second['replayed'])
        self.assertEqual(self.state(coord)['counters']['correction_rounds'], 2)

        # A stale caller fails before the write; the bytes stay exactly as they were.
        snapshot = state_path.read_bytes()
        error = coord.refused(1, 'budget', 'reserve', request={
            'repo': str(coord.repo), 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE,
            'kind': 'review', 'scope': '2.1', 'attempt': 1, 'ordinal': 1,
            'expected_revision': 0})
        self.assertEqual(error['kind'], 'stale_revision')
        self.assertEqual(state_path.read_bytes(), snapshot)

        # A resume reconciles from the canonical artifact: the same identity is still free
        # to replay, and no reset verb exists.
        replay = self.reserve(coord, expected_revision=2)
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.state(coord)['counters']['correction_rounds'], 2)
        self.assertEqual(coord.refused(2, 'budget', 'reset', request={})['kind'],
                         'invalid_argument')

    def test_final_gates_and_release_wait_for_a_quiescent_published_run(self):
        text = tasklist(iteration(2, task_block('2.1', files=('src/a.txt',)),
                                  task_block('2.2', files=('src/b.txt',))))
        coord = self.coordinator(seats=2, tasklist=self.write_tasklist(text))
        lock = coord.acquire()
        coord.create_run()
        self.write_run_state(completed=True)

        def release_request(completed=True, quiescent=True):
            return {'repo': str(coord.repo), 'ticket': TICKET,
                    'owner': {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID,
                              'phase': PHASE, 'token': lock['token'],
                              'completed': completed, 'quiescent': quiescent}}

        changed = {'2.1': 'src/a.txt', '2.2': 'src/b.txt'}
        _, fresh = self.launch_all_resumable(coord, changed)
        self.assertEqual(len(fresh), 2)

        # A still-running task keeps the run non-quiescent.
        coord.workers['2.1'].release()
        coord.wait_exited([coord.workers['2.1']])
        coord.finish('2.1')
        self.assertEqual(coord.task_state('2.2'), 'running')
        error = coord.refused(1, 'owner', 'release', request=release_request())
        self.assertEqual(error['kind'], 'premature_release')

        # Both reach safe return; an integrating task still blocks the release.
        coord.workers['2.2'].release()
        coord.wait_exited([coord.workers['2.2']])
        coord.finish('2.2')
        coord.transition('2.1', 'prepared', 'integrating')
        error = coord.refused(1, 'owner', 'release', request=release_request())
        self.assertEqual(error['kind'], 'premature_release')

        coord.integrate('2.1')
        coord.publish('2.1')
        self.assertEqual(coord.task_state('2.1'), 'published')
        # A prepared, unpublished sibling still blocks the release.
        error = coord.refused(1, 'owner', 'release', request=release_request())
        self.assertEqual(error['kind'], 'premature_release')

        coord.integrate('2.2')
        actions = coord.publication_next('2.2')
        coord.publication_record('2.2', actions[0], 'planned', None)
        error = coord.refused(1, 'owner', 'release', request=release_request())
        self.assertEqual(error['kind'], 'premature_release')
        coord.publish('2.2')
        self.assertEqual(coord.task_state('2.2'), 'published')

        self.write_run_state(completed=False)
        error = coord.refused(1, 'owner', 'release', request=release_request())
        self.assertEqual(error['kind'], 'premature_release')
        self.write_run_state(completed=True, extra={'pause_reason': 'cap-escalation'})
        error = coord.refused(1, 'owner', 'release', request=release_request())
        self.assertEqual(error['kind'], 'premature_release')
        self.write_run_state(completed=True)
        receipt = coord.ok('owner', 'release', request=release_request())
        self.assertTrue(receipt['released'])
        lock_path = Path(git(self.repo, 'rev-parse', '--path-format=absolute',
                             '--git-common-dir')) / 'artel' / 'parallel' / (TICKET + '.json')
        self.assertFalse(lock_path.exists())


if __name__ == '__main__':
    unittest.main()
