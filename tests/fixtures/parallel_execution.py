#!/usr/bin/env python3
"""Reusable stdlib harness for tests/test_parallel_execution.py.

Not a test module (unittest discovery collects `test_*.py` only). It provides the three
pieces the behavioral Task 11 scenarios need, and nothing else:

* `FakeQueue` -- the deterministic fake *external queue service*: a JSON file with the
  normalized row shape the reviewed helpers consume, plus a durable effect count so a test
  can prove an effect landed exactly once.
* `Coordinator` -- the test-side coordinator loop. Every decision and every mutation goes
  through the real `scripts/parallel.py` CLI (admission, transitions, worktrees,
  integration, publication, budget); the only fakes are the queue service, the worker
  *processes* (`tests/fixtures/parallel_worker.py`) and the fake session-lifecycle record.
  The driver never re-implements a helper's policy.
* `recover` -- the fresh-process recovery entry point. `python3
  tests/fixtures/parallel_execution.py recover ...` re-reads the durable manifest,
  records, refs, transaction WAL, publication versions and the fake lifecycle record in a
  NEW process and drives the documented recovery procedure through the same CLI.

The fake session-lifecycle boundary is a pair of durable files per task/attempt under the
test space (never inside the worker root or the artifact root): the worker writes
`<task>-a<attempt>.finished.json` as its last act, and the driver writes
`<task>-a<attempt>.interrupted.json` when it interrupts a worker. The disposition reads
interrupted first, exactly like the adapter's `interrupted_over_result` rule.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CLI = ROOT / 'scripts' / 'parallel.py'
WORKER = Path(__file__).resolve().parent / 'parallel_worker.py'
SUBJECT = 'feat: parallel execution fixture'
TICKET = 'PROJ-123'
COORDINATOR = 'ses_execution-coordinator'
MUTATING_VERBS = frozenset({
    ('owner', 'acquire'), ('owner', 'recover'), ('owner', 'release'),
    ('run', 'create'), ('run', 'transition'),
    ('claim', 'prepare'), ('claim', 'record'), ('claim', 'reconcile'),
    ('worktree', 'create'), ('worktree', 'cleanup'),
    ('integration', 'prepare'), ('integration', 'apply'), ('integration', 'commit'),
    ('integration', 'recover'),
    ('publication', 'record'), ('budget', 'reserve'),
})
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a8b-9c0d-1e2f3a4b5c6d'
PHASE = 2


class HarnessError(AssertionError):
    """An unexpected CLI result: the envelope and exit code are attached."""


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def sha256_file(path):
    with open(str(path), 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def wait_for(predicate, timeout=30.0, interval=0.02):
    """Bounded condition wait; returns the predicate's value or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


class FakeQueue:
    """The deterministic fake external queue service, durable across processes.

    Rows are the coordinator's normalized observation (`task_id`, `title`, `status`,
    `actor`, `phase`, `task`). `claim` marks a row in progress under the given actor,
    `release` frees it, `complete` marks it done and counts the completion effect, so a
    test can prove an effect landed exactly once after a lost reply.
    """

    def __init__(self, space, phase=PHASE):
        self.path = Path(space) / 'fake-queue.json'
        self.phase = phase
        if not self.path.is_file():
            _write_json(self.path, {'next_task_id': 41, 'rows': {}, 'completions': {}})

    def _load(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def _save(self, value):
        _write_json(self.path, value)

    def ensure(self, task):
        value = self._load()
        if task not in value['rows']:
            task_id = value['next_task_id']
            value['next_task_id'] += 1
            value['rows'][task] = {'task_id': task_id, 'title': 'I2 · {} · work'.format(task),
                                   'status': 'todo', 'actor': None, 'phase': self.phase,
                                   'task': task}
            self._save(value)
        return value['rows'][task]

    def claim(self, task, actor, phase=None, task_id=None):
        """The atomic claim: the row must be free; it returns in progress under `actor`."""
        value = self._load()
        row = value['rows'].get(task)
        if row is None:
            row = self.ensure(task)
            value = self._load()
            row = value['rows'][task]
        if row['status'] != 'todo':
            raise HarnessError('fake queue: row {} is {}'.format(row['task_id'],
                                                                 row['status']))
        row = dict(row, status='in_progress', actor=actor,
                   phase=phase if phase is not None else row['phase'])
        if task_id is not None:
            row['task_id'] = task_id
        value['rows'][task] = row
        self._save(value)
        return row

    def row(self, task):
        return self._load()['rows'].get(task)

    def rows(self):
        return list(self._load()['rows'].values())

    def release(self, row):
        value = self._load()
        current = value['rows'][row['task']]
        current = dict(current, status='todo', actor=None)
        value['rows'][row['task']] = current
        self._save(value)

    def complete(self, row):
        value = self._load()
        current = dict(value['rows'][row['task']], status='done')
        value['rows'][row['task']] = current
        value['completions'][row['task']] = value['completions'].get(row['task'], 0) + 1
        self._save(value)
        return current

    def completions(self, task):
        return self._load()['completions'].get(task, 0)


class FakeStore:
    """The deterministic fake artifact store for the store publication path.

    One document (the tasklist) with a durable version, its content digest, and whether
    the completion tick is present in the newest version. `apply_tick` performs the
    coordinator's expected-version write and counts the effect.
    """

    def __init__(self, space, document='specs/.current/PROJ-123/tasklist.md'):
        self.path = Path(space) / 'fake-store.json'
        self.document_path = document
        if not self.path.is_file():
            _write_json(self.path, {'version': 1, 'applied': False,
                                    'content': '# Tasklist\n', 'effects': 0})

    def _load(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def document(self):
        value = self._load()
        return {'path': self.document_path, 'version': value['version'],
                'content_sha256': hashlib.sha256(value['content'].encode('utf-8')).hexdigest(),
                'applied': value['applied']}

    def apply_tick(self, expected_version):
        value = self._load()
        if value['version'] != expected_version:
            raise HarnessError('fake store: version {} is not the expected {}'.format(
                value['version'], expected_version))
        value['version'] += 1
        value['applied'] = True
        value['content'] += '- [x] ticked\n'
        value['effects'] += 1
        _write_json(self.path, value)
        return {'version': value['version'],
                'content_sha256': hashlib.sha256(value['content'].encode('utf-8')).hexdigest()}

    def effects(self):
        return self._load()['effects']


class WorkerHandle:
    """One fake worker process and its assignment."""

    def __init__(self, task, attempt, process, assignment, paths):
        self.task = task
        self.attempt = attempt
        self.process = process
        self.assignment = assignment
        self.paths = paths

    def alive(self):
        return self.process.poll() is None

    def entered(self):
        return os.path.exists(self.paths['entered'])

    def result_exists(self):
        return os.path.exists(self.paths['result'])

    def release(self):
        with open(self.paths['release'], 'w', encoding='utf-8') as handle:
            handle.write('release\n')

    def wait(self, timeout=60.0):
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stderr_tail(self):
        try:
            return (self.process.stderr.read() or '')[-500:]
        except (ValueError, OSError):
            return ''

    def close_pipes(self):
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def terminate(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.close_pipes()


class Coordinator:
    """The test-side coordinator: every decision and mutation through the real CLI."""

    def __init__(self, repo, space, ticket=TICKET, run_id=RUN_ID, phase=PHASE,
                 seats=2, tasklist=None, python=None, store_mode='files'):
        self.repo = Path(os.path.realpath(str(repo)))
        self.space = Path(os.path.realpath(str(space)))
        self.ticket = ticket
        self.run_id = run_id
        self.phase = phase
        self.seats = seats
        self.tasklist = str(tasklist) if tasklist is not None else None
        self.python = python or sys.executable
        self.store_mode = store_mode
        self.queue = FakeQueue(self.space, phase=phase)
        self.store = FakeStore(self.space)
        self.workers = {}
        self.coordinator_id = COORDINATOR
        self._request_seq = 0

    # --- CLI plumbing ------------------------------------------------------------------

    def cli(self, *argv, request=None, cwd=None):
        key = tuple(argv)
        if key in MUTATING_VERBS and self.coordinator_id and isinstance(request, dict):
            request = json.loads(json.dumps(request))
            if key in (('owner', 'acquire'), ('owner', 'release')):
                request.setdefault('owner', {}).setdefault('coordinator_id',
                                                           self.coordinator_id)
            else:
                request.setdefault('coordinator_id', self.coordinator_id)
        command = [self.python, str(CLI)] + [str(a) for a in argv]
        if request is not None:
            self._request_seq += 1
            path = self.space / 'request-{:04d}.json'.format(self._request_seq)
            path.write_text(json.dumps(request), encoding='utf-8')
            command += ['--request', str(path)]
        proc = subprocess.run(command, cwd=str(cwd or self.repo), capture_output=True,
                              text=True, timeout=180)
        envelope = None
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                try:
                    envelope = json.loads(line)
                except ValueError:
                    envelope = None
                break
        return proc, envelope

    def ok(self, *argv, **kwargs):
        proc, envelope = self.cli(*argv, **kwargs)
        if proc.returncode != 0 or not isinstance(envelope, dict) or not envelope.get('ok'):
            raise HarnessError('{} failed ({}) for {}: {}'.format(
                ' '.join(str(a) for a in argv), proc.returncode, kwargs.get('request'),
                proc.stderr or proc.stdout))
        return envelope['data']

    def refused(self, code, *argv, **kwargs):
        proc, envelope = self.cli(*argv, **kwargs)
        if proc.returncode != code or not isinstance(envelope, dict) or envelope.get('ok'):
            raise HarnessError('expected refusal {} from {}: rc={} out={}'.format(
                code, ' '.join(str(a) for a in argv), proc.returncode,
                proc.stdout or proc.stderr))
        return envelope['error']

    # --- durable paths -----------------------------------------------------------------

    def manifest_path(self):
        return (self.repo / '.artel' / 'run' / self.ticket / 'parallel' / self.run_id /
                'manifest.json')

    def record_path(self, task, attempt):
        return (self.repo / '.artel' / 'run' / self.ticket / 'parallel' / self.run_id /
                'integration' / '{}-a{}.json'.format(task, attempt))

    def collected_root(self, task, attempt):
        return (self.repo / '.artel' / 'run' / self.ticket / 'parallel' / self.run_id /
                '{}-a{}'.format(task, attempt))

    def worker_root(self, task, attempt):
        return (self.repo / '.claude' / 'worktrees' / 'parallel' / self.ticket /
                self.run_id / '{}-a{}'.format(task, attempt))

    def artifact_root(self, task, attempt):
        return self.worker_root(task, attempt) / '.artel' / 'artifacts' / \
            '{}-a{}'.format(task, attempt)

    def lifecycle_paths(self, task, attempt):
        name = self.repo.name
        base = self.space / 'lifecycle' / name / '{}-a{}'.format(task, attempt)
        barrier = self.space / 'barrier' / name / '{}-a{}'.format(task, attempt)
        return {
            'finished': str(base) + '.finished.json',
            'interrupted': str(base) + '.interrupted.json',
            'entered': str(barrier) + '.entered',
            'release': str(barrier) + '.release',
            'result': str(self.artifact_root(task, attempt) / 'result.json'),
            'plan': str(self.space / 'plans' / '{}-a{}.json'.format(task, attempt)),
        }

    def read_json(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def manifest(self):
        return self.read_json(self.manifest_path())

    def revision(self):
        return self.manifest()['revision']

    def task_state(self, task):
        record = self.manifest()['tasks'].get(task)
        return None if record is None else record['state']

    def attempt(self, task):
        record = self.manifest()['tasks'].get(task)
        return None if record is None else record['attempt']

    # --- run state ---------------------------------------------------------------------

    def acquire(self, run_id=None, phase=None):
        return self.ok('owner', 'acquire', request={
            'repo': str(self.repo), 'ticket': self.ticket,
            'owner': {'schema': 1, 'ticket': self.ticket,
                      'run_id': run_id or self.run_id,
                      'phase': self.phase if phase is None else phase}})

    def create_run(self, seats=None):
        return self.ok('run', 'create', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'phase': self.phase,
            'run_id': self.run_id, 'seats': self.seats if seats is None else seats,
            'graph_digest': 'd' * 64})

    def arm(self, seats=None):
        self.acquire()
        return self.create_run(seats=seats)

    def transition(self, task, expected, target, fields=None, run_id=None):
        request = {'repo': str(self.repo), 'ticket': self.ticket,
                   'run_id': run_id or self.run_id, 'expected_revision': self.revision(),
                   'task': task, 'expected': expected, 'target': target}
        if fields is not None:
            request['fields'] = fields
        return self.ok('run', 'transition', request=request)

    def admit(self):
        return self.ok('admit', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'run_id': self.run_id,
            'tasklist': self.tasklist})

    def inspect(self):
        return self.ok('run', 'inspect', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'run_id': self.run_id})

    # --- claims ------------------------------------------------------------------------

    def claim(self, candidate, row_task=None):
        """One real claim: intent persisted, fake queue call, recorded row, persisted,
        and the returned mapping reserved through the real transition.

        `row_task` lets the fake queue return a *different* row than the admitted
        candidate -- the returned mapping is authoritative.
        """
        revision = self.revision()
        intent = self.ok('claim', 'prepare', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'run_id': self.run_id,
            'expected_revision': revision})['intent']
        row = self.queue.claim(row_task or candidate, intent['actor'], phase=self.phase)
        data = self.ok('claim', 'record', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'run_id': self.run_id,
            'expected_revision': revision + 1, 'intent_id': intent['intent_id'],
            'row': row})
        action = data['action']
        if action['kind'] == 'run':
            self.transition(action['task'], None, 'reserved')
        return {'intent': intent, 'row': row, 'action': action,
                'manifest': self.manifest()}

    def reconcile(self):
        return self.ok('claim', 'reconcile', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'run_id': self.run_id,
            'expected_revision': self.revision(), 'rows': self.queue.rows()})

    # --- worker lifecycle --------------------------------------------------------------

    def assignment(self, task, attempt, changed, base_commit=None):
        base = base_commit or git(self.repo, 'rev-parse', 'HEAD')
        return {'schema': 1, 'ticket': self.ticket, 'phase': self.phase, 'task': task,
                'run_id': self.run_id, 'attempt': attempt, 'claim': 'artel@fake-queue',
                'base_commit': base, 'files': [changed], 'tests': [], 'route': 'light',
                'model': 'provider/model',
                'worker_root': str(self.worker_root(task, attempt)),
                'artifact_root': str(self.artifact_root(task, attempt))}

    def result_template(self, assignment, changed):
        value = dict(assignment)
        value.update({'status': 'succeeded', 'changed_files': [changed], 'verify': [],
                      'review': None, 'report': None, 'deviations': []})
        return value

    def launch(self, task, changed, attempt=None, base_commit=None, result_first=False,
               content=None):
        """Persist `preparing`, create the real worktree, spawn the fake worker, persist
        `running`. Returns the handle."""
        attempt = attempt or self.attempt(task) or 1
        assignment = self.assignment(task, attempt, changed, base_commit=base_commit)
        result = self.result_template(assignment, changed)
        paths = self.lifecycle_paths(task, attempt)
        _write_json(paths['plan'], result)
        self.transition(task, 'reserved', 'preparing')
        self.ok('worktree', 'create', request={'repo': str(self.repo),
                                               'assignment': assignment})
        command = [self.python, str(WORKER), '--root', assignment['worker_root'],
                   '--artifact', assignment['artifact_root'], '--result', paths['plan'],
                   '--changed', changed, '--entered', paths['entered'],
                   '--release', paths['release'], '--finished', paths['finished']]
        if content is not None:
            command += ['--content', content]
        if result_first:
            command += ['--result-first']
        process = subprocess.Popen(command, cwd=assignment['worker_root'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True)
        handle = WorkerHandle(task, attempt, process, assignment, paths)
        self.workers[task] = handle
        self.transition(task, 'preparing', 'running')
        return handle

    def wait_entered(self, handles, timeout=30.0):
        for handle in handles:
            if not wait_for(handle.entered, timeout=timeout):
                raise HarnessError('worker {} never entered its barrier'.format(handle.task))

    def wait_exited(self, handles, timeout=60.0):
        for handle in handles:
            code = handle.wait(timeout=timeout)
            if code is None:
                raise HarnessError('worker {} did not exit'.format(handle.task))
            if code != 0:
                tail = handle.stderr_tail()
                handle.close_pipes()
                raise HarnessError('worker {} exited {}: {}'.format(handle.task, code, tail))
            handle.close_pipes()

    def alive_count(self):
        return sum(1 for handle in self.workers.values() if handle.alive())

    def disposition(self, task, attempt=None):
        """The fake session-lifecycle disposition: interrupted wins over finished."""
        attempt = attempt or self.attempt(task) or 1
        paths = self.lifecycle_paths(task, attempt)
        if os.path.exists(paths['interrupted']):
            return 'interrupted'
        if os.path.exists(paths['finished']):
            return 'succeeded'
        return None

    def interrupt(self, task):
        handle = self.workers[task]
        handle.terminate()
        _write_json(handle.paths['interrupted'],
                    {'schema': 1, 'disposition': 'interrupted',
                     'session': 'fake-session-{}'.format(task)})
        return handle

    def fill_review(self, task, attempt=None):
        attempt = attempt or self.attempt(task) or 1
        path = Path(self.artifact_root(task, attempt)) / 'result.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['review'] = {'required': False, 'route': 'light', 'verdict': 'passed',
                           'rounds': 0}
        _write_json(path, value)
        return value

    def finish(self, task, attempt=None):
        """Observe a completed worker: verify the disposition, fill the review, walk
        `running -> verifying/reviewing -> prepared` through the real CLI."""
        attempt = attempt or self.attempt(task) or 1
        disposition = self.disposition(task, attempt)
        if disposition != 'succeeded':
            raise HarnessError('worker {} is {}; it never reached a succeeded '
                               'disposition'.format(task, disposition))
        state = self.task_state(task)
        if state == 'running':
            self.transition(task, 'running', 'verifying/reviewing')
        elif state != 'verifying/reviewing':
            raise HarnessError('task {} is {}; finish needs running'.format(task, state))
        self.fill_review(task, attempt)
        self.transition(task, 'verifying/reviewing', 'prepared')
        return self.task_state(task)

    # --- integration -------------------------------------------------------------------

    def integrate(self, task, attempt=None):
        attempt = attempt or self.attempt(task) or 1
        state = self.task_state(task)
        if state == 'prepared':
            self.transition(task, 'prepared', 'integrating')
        elif state != 'integrating':
            raise HarnessError('task {} is {}; integrate needs prepared'.format(task, state))
        assignment = self.workers[task].assignment if task in self.workers else \
            self.marker_assignment(task, attempt)
        result = self.read_json(self.artifact_root(task, attempt) / 'result.json')
        transaction = self.ok('integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result})
        transaction = self.ok('integration', 'apply', request={
            'repo': str(self.repo), 'assignment': assignment, 'prepared': transaction})
        transaction = self.ok('integration', 'commit', request={
            'repo': str(self.repo), 'transaction': transaction, 'subject': SUBJECT})
        return transaction

    def marker_assignment(self, task, attempt):
        marker = self.read_json(self.worker_root(task, attempt) /
                                '.artel' / 'parallel-worker.json')
        return marker['assignment']

    def integrate_recover(self, task, attempt=None):
        """The recovery path for a durable transaction record (fresh process safe)."""
        attempt = attempt or self.attempt(task) or 1
        record_path = self.record_path(task, attempt)
        if record_path.is_file():
            transaction = self.read_json(record_path)
            if transaction['stage'] in ('committed', 'recovered'):
                return transaction
            if transaction['stage'] in ('applied', 'failed', 'conflicted',
                                        'commit_refused', 'committing', 'applying'):
                transaction = self.ok('integration', 'recover', request={
                    'repo': str(self.repo), 'transaction': transaction})
                if transaction.get('cycle_commit'):
                    return transaction
                return self.ok('integration', 'commit', request={
                    'repo': str(self.repo), 'transaction': transaction,
                    'subject': SUBJECT})
            if transaction['stage'] == 'prepared':
                assignment = self.marker_assignment(task, attempt)
                transaction = self.ok('integration', 'apply', request={
                    'repo': str(self.repo), 'assignment': assignment,
                    'prepared': transaction})
                return self.ok('integration', 'commit', request={
                    'repo': str(self.repo), 'transaction': transaction,
                    'subject': SUBJECT})
            raise HarnessError('cannot recover transaction stage {}'.format(
                transaction['stage']))
        assignment = self.marker_assignment(task, attempt)
        result = self.read_json(self.artifact_root(task, attempt) / 'result.json')
        transaction = self.ok('integration', 'prepare', request={
            'repo': str(self.repo), 'assignment': assignment, 'result': result})
        transaction = self.ok('integration', 'apply', request={
            'repo': str(self.repo), 'assignment': assignment, 'prepared': transaction})
        return self.ok('integration', 'commit', request={
            'repo': str(self.repo), 'transaction': transaction, 'subject': SUBJECT})

    # --- publication -------------------------------------------------------------------

    def mapped_row(self, task):
        """The queue row only when the manifest records this run's mapped claim."""
        for intent in self.manifest()['claims']['intents']:
            if intent['state'] == 'mapped' and intent['row']['task'] == task:
                return intent['row']
        return None

    def collected_entries(self, task, attempt=None):
        attempt = attempt or self.attempt(task) or 1
        root = self.collected_root(task, attempt)
        if not root.is_dir():
            return None
        entries = []
        for path in sorted(root.rglob('*')):
            if path.is_file():
                entries.append({'path': path.relative_to(root).as_posix(),
                                'sha256': sha256_file(path)})
        return entries

    def observed(self, task, disposition=None, attempt=None):
        attempt = attempt or self.attempt(task) or 1
        transaction = self.read_json(self.record_path(task, attempt))
        source = self.artifact_root(task, attempt)
        entries = [{'path': 'result.json', 'sha256': sha256_file(source / 'result.json')}]
        if self.store_mode == 'store':
            store = {'mode': 'store', 'documents': [self.store.document()], 'notes': None}
        else:
            store = {'mode': 'files', 'documents': [], 'notes': None}
        return {'schema': 1, 'transaction': transaction,
                'lifecycle': {'disposition': disposition or self.disposition(task, attempt)},
                'store': store,
                'queue': {'row': self.queue.row(task) if self.mapped_row(task) else None},
                'evidence': {'source_root': str(source),
                             'collected_root': str(self.collected_root(task, attempt)),
                             'entries': entries,
                             'collected': self.collected_entries(task, attempt)}}

    def publication_next(self, task, **kwargs):
        return self.ok('publication', 'next', request={
            'repo': str(self.repo), 'ticket': self.ticket, 'run_id': self.run_id,
            'task': task, 'observed': self.observed(task, **kwargs)})

    def publication_record(self, task, action, state, evidence=None):
        attempt = self.attempt(task)
        transaction = self.read_json(self.record_path(task, attempt))
        receipt = {'schema': 1, 'publication': transaction.get('publication'),
                   'action': action, 'state': state, 'evidence': evidence}
        return self.ok('publication', 'record', request={
            'repo': str(self.repo), 'transaction': transaction, 'task': task,
            'action_id': action['action_id'], 'receipt': receipt})

    def perform_effect(self, action):
        """Perform one planned external effect; returns the receipt evidence."""
        payload = action['payload']
        if action['kind'] == 'collect-evidence':
            destination = Path(payload['collected_root'])
            destination.mkdir(parents=True, exist_ok=True)
            for entry in payload['entries']:
                source = Path(payload['source_root']) / entry['path']
                target = destination / entry['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(target))
            return {'collected_root': payload['collected_root'],
                    'entries': [dict(entry) for entry in payload['entries']]}
        if action['kind'] == 'queue-done':
            row = self.queue.complete(payload['row'])
            return {'task_id': row['task_id'], 'actor': row['actor'], 'status': row['status']}
        if action['kind'] == 'store-tick':
            return self.store.apply_tick(payload['expected_version'])
        if action['kind'] == 'committed-documents':
            return {'paths': payload['paths']}
        raise HarnessError('no fake effect for action kind {}'.format(action['kind']))

    def observed_evidence(self, action):
        payload = action['payload']
        if action['kind'] == 'collect-evidence':
            return {'collected_root': payload['collected_root'],
                    'entries': [dict(entry) for entry in payload['entries']]}
        if action['kind'] == 'queue-done':
            row = self.queue.row(payload['row']['task'])
            return {'task_id': row['task_id'], 'actor': row['actor'], 'status': row['status']}
        if action['kind'] == 'store-tick':
            document = self.store.document()
            return {'version': document['version'],
                    'content_sha256': document['content_sha256']}
        if action['kind'] == 'committed-documents':
            return {'paths': payload['paths']}
        raise HarnessError('no observed evidence for action kind {}'.format(action['kind']))

    def publish(self, task, disposition=None):
        """Run the publication loop until the task is durably published."""
        steps = []
        while True:
            actions = self.publication_next(task, disposition=disposition)
            if not actions:
                return {'task': task, 'steps': steps, 'published': True}
            action = actions[0]
            steps.append({'action_id': action['action_id'], 'kind': action['kind'],
                          'state': action['state']})
            if action['state'] == 'observed':
                evidence = None if action['kind'] == 'promote' \
                    else self.observed_evidence(action)
                self.publication_record(task, action, 'observed', evidence)
                continue
            self.publication_record(task, action, 'planned', None)
            evidence = self.perform_effect(action)
            self.publication_record(task, action, 'applied', evidence)


def recover(coord, task, disposition=None):
    """The documented recovery procedure from durable state only (fresh process safe)."""
    report = {'task': task, 'steps': []}
    record = coord.manifest()['tasks'].get(task)
    if record is None:
        report['blocked'] = 'no manifest record for {}'.format(task)
        return report
    state = record['state']
    attempt = record['attempt']
    if state in ('preparing', 'running', 'verifying/reviewing'):
        disposition = disposition or coord.disposition(task, attempt)
        report['disposition'] = disposition
        if disposition != 'succeeded':
            report['blocked'] = 'worker disposition is {!r}; never integrated'.format(
                disposition)
            return report
        if state == 'running':
            coord.transition(task, 'running', 'verifying/reviewing')
            report['steps'].append('running->verifying/reviewing')
        coord.fill_review(task, attempt)
        coord.transition(task, 'verifying/reviewing', 'prepared')
        report['steps'].append('verifying/reviewing->prepared')
        state = 'prepared'
    if state == 'prepared':
        coord.transition(task, 'prepared', 'integrating')
        report['steps'].append('prepared->integrating')
        state = 'integrating'
    if state == 'integrating':
        transaction = coord.integrate_recover(task, attempt)
        report['steps'].append('integration:{}'.format(transaction['stage']))
        report['cycle_commit'] = transaction.get('cycle_commit')
        publication = coord.publish(task, disposition=disposition)
        report['steps'].append('publication:{}'.format(publication['steps']))
        report['state'] = coord.task_state(task)
    else:
        report['state'] = state
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Task 11 parallel execution fixture')
    subparsers = parser.add_subparsers(dest='command', required=True)
    recover_parser = subparsers.add_parser('recover')
    recover_parser.add_argument('--repo', required=True)
    recover_parser.add_argument('--space', required=True)
    recover_parser.add_argument('--ticket', default=TICKET)
    recover_parser.add_argument('--run-id', default=RUN_ID)
    recover_parser.add_argument('--phase', type=int, default=PHASE)
    recover_parser.add_argument('--task', required=True)
    recover_parser.add_argument('--store-mode', default='files',
                                choices=('files', 'store'))
    recover_parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    coord = Coordinator(args.repo, args.space, ticket=args.ticket, run_id=args.run_id,
                        phase=args.phase, store_mode=args.store_mode)
    report = recover(coord, args.task)
    _write_json(args.out, report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
