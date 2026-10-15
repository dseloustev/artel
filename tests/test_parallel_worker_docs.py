"""Task 6: pinned worker execution, role recognition and attempt-scoped evidence.

Contract: docs/opencode-parallel.md §3-§6 and §9-§12, the reviewed Task 5 marker handoff
(task-6-contract-input.md) and docs/gates.md. Every behavioral test builds a real
repository, the coordinator in a linked worktree and a real ticket lock, and creates the
worker root with the reviewed lifecycle (scripts/task_worktree.create_worker) -- the marker
under test is the one the lifecycle actually writes, never a hand-written stand-in. The
stop gate and the preserved guards are exercised as real subprocesses with the worker cwd
as their input, not as prose assertions.

The invariants worth the most: a validated worker root is allowed to stop under an armed
run without reading coordinator state; a corrupt, foreign, wrong-root or not-launch-ready
marker is not a bypass; the sensitive guard still denies in a worker root; and two attempts
never share an evidence root.
"""
import copy
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'hooks'))
import hook_common as h  # noqa: E402
import parallel_contract  # noqa: E402
import parallel_state  # noqa: E402
import task_worktree  # noqa: E402

TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
TASK = '3.1'
PHASE = 2
COORD_BRANCH = 'feature/PROJ-123'
HOOKS = ROOT / 'hooks'
STOP_GATE = HOOKS / 'stop_gate.py'
SENSITIVE_GUARD = HOOKS / 'sensitive_guard.py'


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


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
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


class WorkerCase(unittest.TestCase):
    """A real repository with the coordinator in a linked worktree, a real ticket lock and
    worker roots created by the reviewed lifecycle."""

    IGNORE = '.artel/\n'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.space = Path(os.path.realpath(self._tmp.name))
        self.main = self.space / 'main'
        git(self.space, 'init', '-q', str(self.main))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.main, 'config', key, value)
        (self.main / '.gitignore').write_text(self.IGNORE, encoding='utf-8')
        (self.main / 'app.txt').write_text('v1\n', encoding='utf-8')
        git(self.main, 'add', '.')
        git(self.main, 'commit', '-q', '-m', 'init')
        self.base = git(self.main, 'rev-parse', 'HEAD')
        self.coord = self.main / '.claude' / 'worktrees' / TICKET
        git(self.main, 'worktree', 'add', '-q', '-b', COORD_BRANCH, str(self.coord))
        self.write_config(self.coord)
        parallel_state.acquire_ticket(str(self.coord), TICKET, {
            'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE})

    def tearDown(self):
        self._tmp.cleanup()

    # --- fixture helpers ----------------------------------------------------------------

    def write_config(self, checkout, config=None):
        path = checkout / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config or {
            'specs': {'dir': 'specs/.current'}, 'ticket': {'projectKey': 'PROJ'}}),
            encoding='utf-8')
        return path

    def worker_root(self, task=TASK, attempt=1, run_id=RUN_ID):
        return (self.main / '.claude' / 'worktrees' / 'parallel tasks' / TICKET / run_id /
                '{}-a{}'.format(task, attempt))

    def artifact_root(self, task=TASK, attempt=1, run_id=RUN_ID):
        return self.worker_root(task, attempt, run_id) / '.artel' / 'run' / 'parallel' / \
            '{}-a{}'.format(task, attempt)

    def assignment(self, task=TASK, attempt=1, run_id=RUN_ID, route='light', **overrides):
        value = {
            'schema': 1, 'ticket': TICKET, 'phase': PHASE, 'task': task, 'run_id': run_id,
            'attempt': attempt, 'claim': 'artel@host', 'base_commit': self.base,
            'files': ['app.txt'], 'tests': [], 'route': route, 'model': None,
            'worker_root': str(self.worker_root(task, attempt, run_id)),
            'artifact_root': str(self.artifact_root(task, attempt, run_id)),
        }
        value.update(overrides)
        return value

    def create(self, task=TASK, attempt=1, run_id=RUN_ID, route='light', **overrides):
        assignment = self.assignment(task, attempt, run_id, route, **overrides)
        report = task_worktree.create_worker(str(self.coord), assignment)
        return assignment, report

    def result(self, assignment, **overrides):
        value = dict(assignment)
        value.update({'status': 'succeeded', 'changed_files': ['app.txt'], 'verify': [],
                      'review': None, 'report': 'report.md', 'deviations': []})
        value.update(overrides)
        return value

    def marker_path(self, root):
        return root / '.artel' / 'parallel-worker.json'

    def read_marker(self, root):
        return json.loads(self.marker_path(root).read_text(encoding='utf-8'))

    def write_marker(self, root, marker):
        path = self.marker_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marker), encoding='utf-8')

    def plant_armed_run(self, checkout):
        """An armed run-state and the active-ticket pointer in `checkout`."""
        specs = checkout / 'specs' / '.current'
        specs.mkdir(parents=True, exist_ok=True)
        (specs / '.active_ticket').write_text(TICKET + '\n', encoding='utf-8')
        run_dir = checkout / '.artel' / 'run' / TICKET
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / 'run-state.json').write_text(json.dumps({
            'run_active': True, 'completed': False, 'pause_reason': None,
            'started_at': '2999-01-01T00:00:00Z', 'effective_mode': 'yolo',
            'gates_confirmed': []}), encoding='utf-8')
        return run_dir

    def run_hook(self, script, process_cwd, payload=None):
        return subprocess.run([sys.executable, str(script)], cwd=str(process_cwd),
                              input=json.dumps(payload or {}), capture_output=True, text=True,
                              timeout=60)


class TestWorkerRoleRecognition(WorkerCase):
    def test_a_lifecycle_worker_root_is_recognized(self):
        assignment, _ = self.create()
        root = self.worker_root()
        context = h.worker_context(root=str(root))
        self.assertIsNotNone(context)
        self.assertEqual(context['role'], 'worker')
        self.assertEqual(context['assignment'],
                         parallel_contract.validate_assignment(assignment))
        self.assertEqual(context['assignment']['worker_root'], str(root))
        self.assertEqual(context['assignment']['artifact_root'], str(self.artifact_root()))
        self.assertEqual(context['assignment']['task'], TASK)
        self.assertEqual(context['assignment']['attempt'], 1)
        self.assertEqual(context['assignment']['run_id'], RUN_ID)
        self.assertEqual(context['repository']['common_dir'],
                         git(self.coord, 'rev-parse', '--path-format=absolute',
                             '--git-common-dir'))
        self.assertEqual(context['base_ref'],
                         'refs/artel/parallel/{}/{}/1/base'.format(RUN_ID, TASK))
        self.assertEqual(context['result_ref'],
                         'refs/artel/parallel/{}/{}/1/result'.format(RUN_ID, TASK))
        self.assertEqual(context['setup']['status'], 'complete')

    def test_the_reader_defaults_to_the_session_root(self):
        self.create()
        root = self.worker_root()
        old = os.getcwd()
        try:
            os.chdir(str(root))
            context = h.worker_context()
        finally:
            os.chdir(old)
        self.assertIsNotNone(context)
        self.assertEqual(context['assignment']['worker_root'], str(root))

    def test_two_attempts_keep_their_own_identity_and_evidence_root(self):
        self.create(attempt=1)
        self.create(attempt=2)
        first = h.worker_context(root=str(self.worker_root(attempt=1)))
        second = h.worker_context(root=str(self.worker_root(attempt=2)))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first['assignment']['attempt'], 1)
        self.assertEqual(second['assignment']['attempt'], 2)
        self.assertNotEqual(first['assignment']['worker_root'], second['assignment']['worker_root'])
        self.assertNotEqual(first['assignment']['artifact_root'],
                            second['assignment']['artifact_root'])
        for context, root in ((first, self.worker_root(attempt=1)),
                              (second, self.worker_root(attempt=2))):
            self.assertEqual(context['assignment']['worker_root'], str(root))
            self.assertTrue(context['assignment']['artifact_root'].startswith(
                str(root) + os.sep))

    def test_a_marker_from_another_attempt_is_not_a_worker(self):
        self.create(attempt=1)
        self.create(attempt=2)
        first = self.read_marker(self.worker_root(attempt=1))
        self.write_marker(self.worker_root(attempt=2), first)
        self.assertIsNone(h.worker_context(root=str(self.worker_root(attempt=2))))
        self.assertIsNotNone(h.worker_context(root=str(self.worker_root(attempt=1))))

    def test_a_foreign_repository_marker_is_not_a_worker(self):
        self.create()
        root = self.worker_root()
        other = self.space / 'other'
        git(self.space, 'init', '-q', str(other))
        marker = self.read_marker(root)
        marker['repository']['common_dir'] = git(
            other, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        self.write_marker(root, marker)
        self.assertIsNone(h.worker_context(root=str(root)))

    def test_malformed_markers_are_not_a_bypass(self):
        self.create()
        root = self.worker_root()
        valid = self.read_marker(root)
        extra = copy.deepcopy(valid)
        extra['assignment']['notes'] = 'extra'
        moved = copy.deepcopy(valid)
        moved['assignment']['worker_root'] = str(self.worker_root(attempt=2))
        variants = {
            'not json': 'not json at all',
            'empty object': json.dumps({}),
            'role only': json.dumps({'schema': 1, 'role': 'worker'}),
            'coordinator role': json.dumps(dict(valid, role='coordinator')),
            'schema two': json.dumps(dict(valid, schema=2)),
            'schema boolean': json.dumps(dict(valid, schema=True)),
            'setup pending': json.dumps(dict(valid, setup=dict(valid['setup'], status='pending'))),
            'setup failed': json.dumps(dict(valid, setup=dict(valid['setup'], status='failed'))),
            'no assignment': json.dumps({k: v for k, v in valid.items() if k != 'assignment'}),
            'no repository': json.dumps({k: v for k, v in valid.items() if k != 'repository'}),
            'assignment extra key': json.dumps(extra),
            'wrong root': json.dumps(moved),
        }
        for name, text in variants.items():
            with self.subTest(name):
                self.marker_path(root).write_text(text, encoding='utf-8')
                self.assertIsNone(h.worker_context(root=str(root)))
        self.marker_path(root).unlink()
        self.assertIsNone(h.worker_context(root=str(root)))

    def test_a_hand_written_marker_outside_a_worker_repository_is_not_a_worker(self):
        self.create()
        marker = self.read_marker(self.worker_root())
        plain = self.space / 'plain'
        plain.mkdir()
        marker['assignment']['worker_root'] = str(plain)
        marker['repository']['main'] = str(plain)
        self.write_marker(plain, marker)
        self.assertIsNone(h.worker_context(root=str(plain)))

    def test_a_marker_outside_the_worktree_area_is_not_a_worker(self):
        # The same repository, a valid assignment and a real common dir — but the root is
        # not in the lifecycle's `.claude/worktrees/` area, so it is not a worker root.
        self.create()
        marker = self.read_marker(self.worker_root())
        elsewhere = self.main / 'elsewhere'
        elsewhere.mkdir()
        marker['assignment']['worker_root'] = str(elsewhere)
        self.write_marker(elsewhere, marker)
        self.assertIsNone(h.worker_context(root=str(elsewhere)))

    def test_the_reader_reads_no_run_state(self):
        source = inspect.getsource(h.worker_context)
        for token in ('run-state', 'verify-baseline', 'ticket_run_dir', 'stop-gate-blocks'):
            self.assertNotIn(token, source, token)


class TestWorkerStopGate(WorkerCase):
    def test_an_armed_worker_root_is_allowed_without_touching_worker_state(self):
        self.create()
        root = self.worker_root()
        self.plant_armed_run(root)
        before = digest_tree(root / '.artel' / 'run')
        result = self.run_hook(STOP_GATE, root, {'session_id': 'sess-w', 'cwd': str(root)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '', 'a validated worker root is allowed silently')
        self.assertEqual(digest_tree(root / '.artel' / 'run'), before,
                         'the worker branch returns before any counter or run-state logic')

    def test_the_worker_check_precedes_the_coordinators_state(self):
        self.create()
        root = self.worker_root()
        self.plant_armed_run(self.coord)
        baseline = self.coord / '.artel' / 'run' / TICKET / 'verify-baseline.json'
        baseline.write_text(json.dumps({'recorded_at': 'x', 'stages': []}), encoding='utf-8')
        coord_before = digest_tree(self.coord / '.artel' / 'run')
        # hooks.json starts hooks in the coordinator checkout; the payload cwd is the worker.
        result = self.run_hook(STOP_GATE, self.coord, {'session_id': 'sess-w', 'cwd': str(root)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '',
                         'the hook moved into the worker root and never consumed the '
                         'coordinator state')
        self.assertEqual(digest_tree(self.coord / '.artel' / 'run'), coord_before,
                         'the coordinator run state and checkpoint baseline are untouched')

    def test_corrupt_markers_do_not_bypass_the_armed_gate(self):
        self.create()
        root = self.worker_root()
        marker = self.read_marker(root)
        self.plant_armed_run(root)
        moved = copy.deepcopy(marker)
        moved['assignment']['worker_root'] = str(self.worker_root(attempt=2))
        variants = {
            'role only': {'schema': 1, 'role': 'worker'},
            'coordinator role': dict(marker, role='coordinator'),
            'incomplete setup': dict(marker, setup=dict(marker['setup'], status='pending')),
            'missing assignment': {k: v for k, v in marker.items() if k != 'assignment'},
            'wrong root': moved,
        }
        for name, value in variants.items():
            with self.subTest(name):
                self.write_marker(root, value)
                result = self.run_hook(STOP_GATE, root,
                                       {'session_id': 'sess-w', 'cwd': str(root)})
                self.assertEqual(result.returncode, 0)
                payload = json.loads(result.stdout)
                self.assertEqual(payload['decision'], 'block')
        self.assertTrue((root / '.artel' / 'run' / TICKET / '.stop-gate-blocks').is_file(),
                        'the serial gate really ran for every corrupt marker')

    def test_an_unmarked_root_keeps_the_serial_gate(self):
        self.plant_armed_run(self.coord)
        result = self.run_hook(STOP_GATE, self.coord,
                               {'session_id': 'sess-c', 'cwd': str(self.coord)})
        self.assertEqual(json.loads(result.stdout)['decision'], 'block')


class TestPreservedGuards(WorkerCase):
    def test_the_sensitive_guard_still_denies_in_a_worker_root(self):
        self.create()
        root = self.worker_root()
        self.plant_armed_run(root)
        self.assertTrue((root / '.artel' / 'config.json').is_file(),
                        'the lifecycle copied the config the guard reads')
        payload = {'session_id': 'sess-w', 'cwd': str(root), 'tool_name': 'Write',
                   'tool_input': {'file_path': str(root / '.env.local')}}
        result = self.run_hook(SENSITIVE_GUARD, root, payload)
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(decision['permissionDecision'], 'deny')
        self.assertIn('sensitive path', decision['permissionDecisionReason'])


class TestAttemptEvidenceIsolation(WorkerCase):
    def test_an_attempt_cannot_claim_another_attempts_artifact_root(self):
        first, _ = self.create(attempt=1)
        with self.assertRaises(task_worktree.WorktreeError) as caught:
            self.create(attempt=2, artifact_root=first['artifact_root'])
        self.assertEqual(caught.exception.kind, 'invalid_root')
        self.assertFalse(self.worker_root(attempt=2).exists())

    def test_a_result_cannot_swap_attempt_identity(self):
        assignment, _ = self.create(attempt=1)
        result = self.result(assignment)
        swapped_roots = {'artifact_root': str(self.artifact_root(attempt=2))}
        for name, overrides in (('attempt', {'attempt': 2}),
                                ('artifact root', swapped_roots)):
            with self.subTest(name):
                swapped = dict(result)
                swapped.update(overrides)
                with self.assertRaises(parallel_contract.ContractError) as caught:
                    parallel_contract.validate_result(swapped, assignment)
                self.assertEqual(caught.exception.kind, 'invalid_result')

    def test_a_task_cannot_claim_another_tasks_artifact_root(self):
        first, _ = self.create(task='3.1')
        with self.assertRaises(task_worktree.WorktreeError) as caught:
            self.create(task='3.2', artifact_root=first['artifact_root'])
        self.assertEqual(caught.exception.kind, 'invalid_root')
        self.assertFalse(self.worker_root(task='3.2').exists())

    def test_a_worker_result_keeps_review_null_and_verify_output_verbatim(self):
        assignment, _ = self.create(route='full')
        output = 'line one\n  indented\r\nline three\n\n'
        result = self.result(assignment, verify=[
            {'command': 'python3 -m unittest tests.test_app', 'ok': False, 'output': output}])
        validated = parallel_contract.validate_result(result, assignment)
        self.assertIsNone(validated['review'])
        self.assertEqual(validated['verify'][0]['output'], output)


def flat(rel):
    return re.sub(r'\s+', ' ', (ROOT / rel).read_text(encoding='utf-8'))


class TestWorkerProcedureDocs(unittest.TestCase):
    def test_the_implementer_agent_defines_worker_mode(self):
        text = flat('agents/implementer.md')
        self.assertIn('## Worker mode', text)
        for phrase in ('one pinned assignment',
                       '`worker_root`', '`artifact_root`', '`run_id`', '`attempt`',
                       'Never call `task_ready`', '`task_update`', 'read-only input',
                       'not a second orchestrator',
                       '`<artifact_root>/report.md`', '`<artifact_root>/verify/`',
                       '`<artifact_root>/result.json`',
                       '`review` is always `null`', 'verbatim', 'No extra keys',
                       "against this worker's diff and attempt",
                       'never ask the user', '`AskUserQuestion`',
                       'never dispatch a subagent', 'structured proposal',
                       '`verify-baseline.json`', 'cleanup', 'transport'):
            self.assertIn(phrase, text, phrase)

    def test_the_implementer_skill_keeps_worker_mode_out_of_the_serial_flow(self):
        text = flat('skills/implementer/SKILL.md')
        self.assertIn('## Worker mode', text)
        self.assertIn('agents/implementer.md', text)
        self.assertIn('`artifact_root`', text)
        self.assertIn('never loads this skill', text)
        self.assertIn('serial procedure below is unchanged', text)
        # the serial flow is untouched
        self.assertIn('Implement the next incomplete task now', text)
        self.assertIn('task_ready', text)

    def test_the_inner_loop_takes_the_worker_evidence_root(self):
        text = flat('skills/inner-loop/SKILL.md')
        self.assertIn('Worker mode', text)
        self.assertIn('`<artifact_root>/verify/`', text)
        self.assertIn('`verify-baseline.json`', text)

    def test_gates_document_the_worker_evidence_root(self):
        text = flat('docs/gates.md')
        self.assertIn('**Worker mode.**', text)
        self.assertIn('`<artifact_root>/verify/`', text)
        self.assertIn('`verify-baseline.json`', text)
        self.assertIn('never reads', text)


if __name__ == '__main__':
    unittest.main()
