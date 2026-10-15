#!/usr/bin/env python3
"""scripts/parallel_publication.py: restartable completion publication and promotion.

Contract: docs/opencode-parallel.md §13.9 (publication actions, receipts and the durable
record), §13.7 (the cleanup publication proof), §13.8 (the integration transaction Task 9
consumes), §6 (task states) and the approved serialized integration/publication clauses.

Every fixture is a real repository with a real Task 8 integration commit: the files-path
documents are committed with the code by the real transaction, and the store path commits
the code first. The artifact store and the queue are deterministic fakes behind the effect
boundaries the coordinator owns, so every crash window -- before the intent, before the
effect, after the effect, and a lost reply -- is executed against fresh durable state and
the exactly-once result is asserted, not described.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_integration  # noqa: E402
import parallel_publication  # noqa: E402
import parallel_queue  # noqa: E402
import parallel_scheduler  # noqa: E402
import parallel_state  # noqa: E402
import task_grammar  # noqa: E402
import task_worktree  # noqa: E402

TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
TASK = '3.1'
DEPENDENT = '3.2'
ATTEMPT = 1
PHASE = 2
COORD_BRANCH = 'feature/PROJ-123'
TASKLIST = 'specs/.current/PROJ-123/tasklist.md'
NOTES = 'specs/.current/PROJ-123/implementation-notes.md'
SUBJECT = 'feat: do a thing'
PROPOSALS = (('3.1-a1#1f0c2a', 'the worker deviated on app.txt'),
             ('3.1-a1#77b31d', 'a second accepted deviation'))

TASKLIST_TEXT = (
    '## Iteration 3: first\n'
    '\n'
    '### Task 3.1: do a thing\n'
    '- **Files:** `app.txt`\n'
    '- **Depends on:** none\n'
    '- **Route:** light\n'
    '- **Test:** none — nothing to test\n'
    '- [ ] do a thing\n'
    '\n'
    '### Task 3.2: dependent thing\n'
    '- **Files:** `other.txt`\n'
    '- **Depends on:** 3.1\n'
    '- **Route:** light\n'
    '- **Test:** none — nothing to test\n'
    '- [ ] do the dependent work\n'
)
TICK_CHECKBOX = '- [ ] do a thing'
TICKED_CHECKBOX = '- [x] do a thing'
SEEDED_NOTES = '# Implementation notes\n\n## 1. earlier-work\n\nsomething earlier\n'
NOTE_RE = re.compile(r'^## (\d+)\. (\S+)$', re.MULTILINE)
ACTION_KEYS = ('schema', 'action_id', 'kind', 'effect', 'state', 'ticket', 'phase', 'task',
               'run_id', 'attempt', 'mode', 'transaction_id', 'cycle_commit', 'cycle_ref',
               'result_commit', 'result_ref', 'documents', 'plan', 'index', 'payload')
PLAN_KINDS = ('committed-documents', 'store-tick', 'note-append', 'collect-evidence',
              'queue-done')


def git(cwd, *args, check=True):
    proc = subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=False,
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError('git {} failed in {}: {}'.format(
            ' '.join(str(a) for a in args), cwd, proc.stderr or proc.stdout))
    return proc.stdout.strip()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def rejects(test, kind, call):
    """Assert `call` raises PublicationError of `kind`; return the exception."""
    with test.assertRaises(parallel_publication.PublicationError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind, str(caught.exception))
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


def rejects_worktree(test, kind, call):
    """Assert `call` raises WorktreeError of `kind`; return the exception."""
    with test.assertRaises(task_worktree.WorktreeError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind, str(caught.exception))
    return caught.exception


class StoreDown(Exception):
    """The configured store is unreachable: the effect did not run."""


class VersionConflict(Exception):
    """The stored version moved past the expected one: the effect did not run."""


class LostReply(Exception):
    """The effect landed; the reply never reached the coordinator."""


class FakeStore:
    """A deterministic versioned artifact store behind the coordinator's effect boundary.

    `patch` is the expected-version write the configured store client performs; `put` is
    any other writer (a concurrent edit, a seed bump). `down` refuses before the effect;
    `lose_reply` applies the effect and loses the answer.
    """

    def __init__(self, seed=None):
        self.seed = {path: dict(doc) for path, doc in (seed or {}).items()}
        self.reset()

    def reset(self):
        self.docs = {path: dict(doc) for path, doc in self.seed.items()}
        self.patches = []
        self.down = False
        self.lose_reply = False

    def get(self, path):
        return self.docs.get(path)

    def put(self, path, content):
        current = self.docs.get(path)
        version = (current['version'] if current else 0) + 1
        self.docs[path] = {'version': version, 'content': content}
        return self.docs[path]

    def patch(self, path, expected_version, content):
        if self.down:
            raise StoreDown('the store is unreachable')
        current = self.docs.get(path)
        version = current['version'] if current else 0
        if version != expected_version:
            raise VersionConflict('the store holds {} at version {}, not {}'.format(
                path, version, expected_version))
        result = self.put(path, content)
        self.patches.append({'path': path, 'expected_version': expected_version,
                             'version': result['version']})
        if self.lose_reply:
            self.lose_reply = False
            raise LostReply('the store applied {} v{}; the reply was lost'.format(
                path, result['version']))
        return result


class FakeQueue:
    """The configured queue's owned row behind the coordinator's effect boundary."""

    def __init__(self, row):
        self.initial = dict(row)
        self.reset()

    def reset(self):
        self.row = dict(self.initial)
        self.completions = 0
        self.down = False

    def observe(self):
        return dict(self.row)

    def complete(self, task_id, actor):
        if self.down:
            raise QueueDown('the queue is unreachable')
        if (self.row['task_id'] != task_id or self.row['actor'] != actor
                or self.row['status'] != 'in_progress'):
            raise AssertionError('the fake queue does not hold that row')
        self.row['status'] = 'done'
        self.completions += 1
        return dict(self.row)


class QueueDown(Exception):
    """The configured queue is unreachable: the completion did not run."""


class PublicationCase(unittest.TestCase):
    """A real repository, a real integration commit and fake store/queue boundaries."""

    IGNORE = '.artel/\nbuild/\n'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.space = Path(os.path.realpath(self._tmp.name))
        self.main = self.space / 'host repo'
        git(self.space, 'init', '-q', str(self.main))
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            git(self.main, 'config', key, value)
        self.write('.gitignore', self.IGNORE)
        self.write('app.txt', 'v1\n')
        self.write('other.txt', 'unrelated\n')
        self.write(TASKLIST, TASKLIST_TEXT)
        self.write(NOTES, SEEDED_NOTES)
        self.write('.artel/config.json', '{}\n')
        git(self.main, 'add', '-A')
        git(self.main, 'commit', '-q', '-m', 'init')
        git(self.main, 'branch', '-M', 'trunk')
        self.base = git(self.main, 'rev-parse', 'HEAD')
        self.coord = self.main / '.claude' / 'worktrees' / TICKET
        git(self.main, 'worktree', 'add', '-q', '-b', COORD_BRANCH, str(self.coord))
        parallel_state.acquire_ticket(
            str(self.coord), TICKET,
            {'schema': 1, 'ticket': TICKET, 'run_id': RUN_ID, 'phase': PHASE})
        self.manifest_path = (self.coord / '.artel' / 'run' / TICKET / 'parallel' / RUN_ID /
                              'manifest.json')
        self.worker = self.worker_root()
        task_worktree.create_worker(str(self.coord), self.assignment())
        self.write_evidence()
        self.mode = 'files'
        self.proposals = ()
        self.proposal_text = {}
        self.store = FakeStore()
        self.queue = None
        self.manifest = None
        self.armed = None
        self.transaction = None
        self.effects = []

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

    def worker_root(self):
        return (self.main / '.claude' / 'worktrees' / 'parallel tasks' / TICKET / RUN_ID /
                '{}-a{}'.format(TASK, ATTEMPT))

    def artifact_root(self):
        return self.worker_root() / '.artel' / 'run' / 'parallel' / '{}-a{}'.format(
            TASK, ATTEMPT)

    def collected_root(self):
        return (self.coord / '.artel' / 'run' / TICKET / 'parallel' / RUN_ID /
                '{}-a{}'.format(TASK, ATTEMPT))

    def result_ref(self):
        return 'refs/artel/parallel/{}/{}/{}/result'.format(RUN_ID, TASK, ATTEMPT)

    def assignment(self):
        return {
            'schema': 1, 'ticket': TICKET, 'phase': PHASE, 'task': TASK, 'run_id': RUN_ID,
            'attempt': ATTEMPT, 'claim': 'artel@host', 'base_commit': self.base,
            'files': ['app.txt'], 'tests': [], 'route': 'light', 'model': None,
            'worker_root': str(self.worker_root()), 'artifact_root': str(self.artifact_root()),
        }

    def result(self):
        value = dict(self.assignment())
        value.update({
            'status': 'succeeded', 'changed_files': ['app.txt'],
            'verify': [{'command': 'true', 'ok': True, 'output': ''}],
            'review': {'required': False, 'route': 'light', 'verdict': 'skipped',
                       'rounds': 0},
            'report': 'report.md', 'deviations': [],
        })
        return value

    def record_path(self):
        return parallel_integration.record_path(str(self.coord), self.assignment())

    def stored_transaction(self):
        return json.loads(self.record_path().read_text(encoding='utf-8'))

    def write_transaction(self, record):
        self.record_path().write_text(json.dumps(record, indent=2, sort_keys=True) + '\n',
                                      encoding='utf-8')

    def publication(self):
        return self.stored_transaction().get('publication')

    def write_evidence(self):
        root = self.artifact_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / 'result.json').write_text('{"status": "succeeded"}\n', encoding='utf-8')
        (root / 'report.md').write_text('# report\n', encoding='utf-8')
        (root / 'verify').mkdir(exist_ok=True)
        (root / 'verify' / 'iteration-1.json').write_text('{"ok": true}\n', encoding='utf-8')

    def tree_entries(self, root):
        if not root.is_dir():
            return None
        found = []
        for dirpath, _dirnames, filenames in os.walk(str(root)):
            for name in filenames:
                path = Path(dirpath) / name
                found.append({'path': path.relative_to(root).as_posix(),
                              'sha256': sha256_text(path.read_text(encoding='utf-8'))})
        return sorted(found, key=lambda entry: entry['path'])

    # --- the run ----------------------------------------------------------------------

    def walk(self, manifest, task, state):
        expected = None
        for target in ('reserved', 'preparing', 'running', 'verifying/reviewing', 'prepared',
                       'integrating', 'published'):
            manifest = parallel_state.transition_task(manifest, task, expected, target,
                                                      {'decision': None})
            expected = target
            if target == state:
                return manifest
        raise AssertionError('unknown state: {}'.format(state))

    def queue_row(self, **overrides):
        value = {'task_id': 41, 'title': 'I2 · 3.1 · do a thing', 'status': 'in_progress',
                 'actor': None, 'phase': PHASE, 'task': TASK}
        value.update(overrides)
        return value

    def arm(self, queue=True, dependent=True):
        """The manifest the coordinator persists before integrating: 3.1 at `integrating`."""
        manifest = parallel_state.new_run(TICKET, PHASE, RUN_ID, 2, 'digest')
        manifest = self.walk(manifest, TASK, 'integrating')
        if dependent:
            manifest = self.walk(manifest, DEPENDENT, 'reserved')
        if queue:
            prepared = parallel_queue.begin_claim(manifest)
            row = self.queue_row(actor=prepared['intent']['actor'])
            recorded = parallel_queue.record_claim(prepared['manifest'],
                                                   prepared['intent']['intent_id'], row)
            manifest = recorded['manifest']
            self.queue = FakeQueue(row)
        else:
            self.queue = None
        self.armed = manifest
        current = (parallel_state.read_manifest(str(self.manifest_path))['revision']
                   if self.manifest_path.exists() else 0)
        self.manifest = parallel_state.write_manifest(
            str(self.manifest_path), dict(manifest, revision=current), current)
        return self.manifest

    def integrate(self, documents=()):
        """The real Task 8 transaction: transport, apply, documents, committed cycle."""
        assignment = self.assignment()
        self.write('app.txt', 'v2\n', base=self.worker)
        prepared = parallel_integration.prepare_transport(str(self.coord), assignment,
                                                          self.result())
        applied = parallel_integration.apply_transport(str(self.coord), assignment, prepared)
        for path in documents:
            self.write(path, self.document_text(path), base=self.coord)
        applied = dict(applied, documents=list(documents))
        self.transaction = parallel_integration.commit_cycle(str(self.coord), applied, SUBJECT)
        self.effects.append(('commit', self.transaction['cycle_commit']))
        return self.transaction

    def document_text(self, path):
        if path == TASKLIST:
            return self.tick(self.read(TASKLIST))
        if path == NOTES:
            return self.append_note(self.read(NOTES), 1, '3.1-a1#files', 'the files note')
        raise AssertionError('unknown document: {}'.format(path))

    # --- the store and the notes ------------------------------------------------------

    def seed_store(self, tasklist=TASKLIST_TEXT, notes=SEEDED_NOTES, proposals=PROPOSALS):
        """The store path's configured store: the tasklist and the notes at their versions."""
        self.mode = 'store'
        self.proposals = tuple(key for key, _text in proposals)
        self.proposal_text = dict(proposals)
        self.store = FakeStore({TASKLIST: {'version': 1, 'content': tasklist},
                                NOTES: {'version': 1, 'content': notes}})

    def tick(self, content):
        return content.replace(TICK_CHECKBOX, TICKED_CHECKBOX, 1)

    def append_note(self, content, number, key, text=None):
        text = text if text is not None else self.proposal_text.get(key, 'a note')
        return content + '\n## {}. {}\n\n{}\n'.format(number, key, text)

    def note_entries(self, content):
        return [{'number': int(number), 'key': key}
                for number, key in NOTE_RE.findall(content)]

    def store_observation(self):
        if self.mode == 'files':
            return {'mode': 'files', 'documents': [], 'notes': None}
        tasklist = self.store.get(TASKLIST)
        notes = self.store.get(NOTES)
        return {
            'mode': 'store',
            'documents': [{
                'path': TASKLIST, 'version': tasklist['version'],
                'content_sha256': sha256_text(tasklist['content']),
                'applied': TICKED_CHECKBOX in tasklist['content'],
            }],
            'notes': {
                'path': NOTES, 'version': notes['version'],
                'content_sha256': sha256_text(notes['content']),
                'entries': self.note_entries(notes['content']),
                'proposals': [{'key': key} for key in self.proposals],
            },
        }

    # --- the observed bundle ----------------------------------------------------------

    def observe(self, lifecycle='succeeded', store=None, queue=None, evidence=None,
                transaction=None):
        if queue is None and self.queue is not None:
            queue = self.queue.observe()
        return {
            'schema': 1,
            'transaction': transaction if transaction is not None else self.stored_transaction(),
            'lifecycle': {'disposition': lifecycle},
            'store': store if store is not None else self.store_observation(),
            'queue': {'row': queue},
            'evidence': evidence if evidence is not None else self.evidence_observation(),
        }

    def evidence_observation(self):
        return {'source_root': str(self.artifact_root()),
                'collected_root': str(self.collected_root()),
                'entries': self.tree_entries(self.artifact_root()),
                'collected': self.tree_entries(self.collected_root())}

    def plan(self, **kwargs):
        return parallel_publication.next_publication_actions(self.manifest, TASK,
                                                             self.observe(**kwargs))

    # --- the coordinator's effect boundaries ------------------------------------------

    def envelope(self, action, state, evidence):
        return {'schema': 1, 'publication': self.publication(), 'action': action,
                'state': state, 'evidence': evidence}

    def record_intent(self, action):
        return self.record(action, 'planned', None)

    def record(self, action, state, evidence, persist_manifest=True):
        result = parallel_publication.record_publication(
            self.manifest, TASK, action['action_id'],
            self.envelope(action, state, evidence))
        self.persist(result, persist_manifest=persist_manifest)
        return result

    def persist(self, result, persist_manifest=True):
        record = self.stored_transaction()
        if record.get('publication') != result['publication']:
            record['publication'] = result['publication']
            self.write_transaction(record)
        manifest = result['manifest']
        if persist_manifest and manifest != self.manifest:
            current = parallel_state.read_manifest(str(self.manifest_path))['revision']
            self.manifest = parallel_state.write_manifest(str(self.manifest_path), manifest,
                                                          current)
        else:
            self.manifest = manifest

    def observed_receipt(self, action):
        """The receipt the coordinator derives from fresh observation of a landed effect."""
        kind = action['kind']
        payload = action['payload']
        if kind == 'committed-documents':
            return {'paths': list(payload['paths'])}
        if kind == 'store-tick':
            doc = self.store_observation()['documents'][0]
            return {'version': doc['version'], 'content_sha256': doc['content_sha256']}
        if kind == 'note-append':
            notes = self.store_observation()['notes']
            entry = next(entry for entry in notes['entries']
                         if entry['key'] == payload['key'])
            return {'version': notes['version'], 'content_sha256': notes['content_sha256'],
                    'number': entry['number'], 'key': entry['key']}
        if kind == 'collect-evidence':
            return {'collected_root': payload['collected_root'],
                    'entries': [dict(entry) for entry in payload['entries']]}
        if kind == 'queue-done':
            row = self.queue.observe()
            return {'task_id': row['task_id'], 'actor': row['actor'],
                    'status': row['status']}
        raise AssertionError('no observed receipt for {}'.format(kind))

    def execute(self, action):
        """Perform one action's effect through the configured fake boundary."""
        kind = action['kind']
        payload = action['payload']
        if kind == 'committed-documents':
            return self.envelope(action, 'observed', {'paths': list(payload['paths'])})
        if kind == 'store-tick':
            result = self.store.patch(payload['path'], payload['expected_version'],
                                      self.tick(self.store.get(payload['path'])['content']))
            self.effects.append(('store-tick', result['version']))
            return self.envelope(action, 'applied', {
                'version': result['version'],
                'content_sha256': sha256_text(result['content'])})
        if kind == 'note-append':
            content = self.append_note(self.store.get(payload['path'])['content'],
                                       payload['number'], payload['key'])
            result = self.store.patch(payload['path'], payload['expected_version'], content)
            self.effects.append(('note-append', payload['number']))
            return self.envelope(action, 'applied', {
                'version': result['version'],
                'content_sha256': sha256_text(result['content']),
                'number': payload['number'], 'key': payload['key']})
        if kind == 'collect-evidence':
            for entry in payload['entries']:
                source = Path(payload['source_root']) / entry['path']
                destination = Path(payload['collected_root']) / entry['path']
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(destination))
            self.effects.append(('collect', len(payload['entries'])))
            return self.envelope(action, 'applied', {
                'collected_root': payload['collected_root'],
                'entries': [dict(entry) for entry in payload['entries']]})
        if kind == 'queue-done':
            row = self.queue.complete(payload['task_id'], payload['actor'])
            self.effects.append(('queue-done', row['task_id']))
            return self.envelope(action, 'applied', {
                'task_id': row['task_id'], 'actor': row['actor'], 'status': row['status']})
        raise AssertionError('no effect boundary for {}'.format(kind))

    def publish(self, crash=None, lose=None):
        """The coordinator loop: plan, persist the intent, execute, record the receipt.

        `crash` is an `(action_id, 'before-intent'|'before-effect'|'after-effect')` window;
        `lose` an action id whose effect lands but whose reply is lost. Both return with the
        durable state exactly as a restarted coordinator would read it.
        """
        while True:
            actions = parallel_publication.next_publication_actions(self.manifest, TASK,
                                                                    self.observe())
            if not actions:
                return
            action = actions[0]
            action_id = action['action_id']
            if crash == (action_id, 'before-intent'):
                return
            self.record_intent(action)
            if crash == (action_id, 'before-effect'):
                return
            if action['state'] == 'observed':
                self.record(action, 'observed', self.observed_receipt(action))
                continue
            if lose == action_id:
                self.store.lose_reply = True
            try:
                receipt = self.execute(action)
            except LostReply:
                return
            if crash == (action_id, 'after-effect'):
                return
            self.record(action, receipt['state'], receipt['evidence'])

    def restart(self):
        self.manifest = parallel_state.read_manifest(str(self.manifest_path))
        return self.manifest

    # --- assertions -------------------------------------------------------------------

    def manifest_state(self, task=TASK):
        return parallel_state.read_manifest(str(self.manifest_path))['tasks'][task]['state']

    def commit_count(self):
        return int(git(self.coord, 'rev-list', '--count', '{}..{}'.format(
            self.base, COORD_BRANCH)))

    def assert_complete(self):
        self.assertEqual(self.manifest_state(), 'published')
        record = self.publication()
        self.assertIsNotNone(record)
        self.assertTrue(record['complete'])
        self.assertIsNotNone(record['proof'])
        return record

    def admissions(self, manifest=None):
        iterations, problems, warnings = task_grammar.parse(TASKLIST_TEXT)
        self.assertFalse(problems, problems)
        self.assertFalse(warnings, warnings)
        return parallel_scheduler.select_admissions(
            iterations, manifest if manifest is not None else self.manifest, 2, True)

    def observed_with_evidence(self, evidence):
        observed = self.observe()
        observed['evidence'] = evidence
        return observed


class TestPlanShape(PublicationCase):
    """The deterministic plan: ordering, stable identities, mode boundaries."""

    def test_the_files_plan_recognizes_the_commit_then_collects_then_completes(self):
        self.arm()
        self.integrate(documents=[TASKLIST, NOTES])
        actions = self.plan()
        self.assertEqual([action['kind'] for action in actions],
                         ['committed-documents', 'collect-evidence', 'queue-done'])
        self.assertEqual([action['state'] for action in actions],
                         ['observed', 'pending', 'pending'])
        self.assertEqual([action['index'] for action in actions], [0, 1, 2])
        self.assertEqual(len({action['action_id'] for action in actions}), 3)
        for action in actions:
            self.assertEqual(sorted(action), sorted(ACTION_KEYS))
            self.assertIn(action['kind'], PLAN_KINDS)
            self.assertEqual(action['plan'], [entry['action_id'] for entry in actions])
            self.assertEqual(action['transaction_id'], self.transaction['transaction_id'])
            self.assertEqual(action['cycle_commit'], self.transaction['cycle_commit'])
            self.assertEqual(action['cycle_ref'], 'refs/heads/' + COORD_BRANCH)
            self.assertEqual(action['result_commit'], self.transaction['transport_commit'])
            self.assertEqual(action['result_ref'], self.result_ref())
            self.assertEqual(action['mode'], 'files')

    def test_the_files_plan_never_emits_a_file_tick_or_another_commit(self):
        self.arm()
        self.integrate(documents=[TASKLIST, NOTES])
        seen = set()
        while True:
            actions = self.plan()
            if not actions:
                break
            for action in actions:
                self.assertNotIn(action['kind'],
                                 ('file-tick', 'commit', 'store-tick', 'note-append'))
                seen.add(action['kind'])
            action = actions[0]
            self.record_intent(action)
            receipt = self.execute(action)
            self.record(action, receipt['state'], receipt['evidence'])
        self.assertEqual(seen, {'committed-documents', 'collect-evidence', 'queue-done'})
        self.assertEqual(self.commit_count(), 1)
        self.assertEqual(self.publication()['documents'], sorted([TASKLIST, NOTES]))

    def test_the_store_plan_publishes_the_tick_and_numbered_notes_after_the_commit(self):
        self.arm()
        self.seed_store()
        self.integrate()
        actions = self.plan()
        self.assertEqual([action['kind'] for action in actions],
                         ['store-tick', 'note-append', 'note-append', 'collect-evidence',
                          'queue-done'])
        notes = [action for action in actions if action['kind'] == 'note-append']
        self.assertEqual([action['payload']['number'] for action in notes], [2, 3])
        self.assertEqual([action['payload']['key'] for action in notes],
                         [key for key, _text in PROPOSALS])
        self.assertEqual([action['payload']['expected_version'] for action in notes], [1, 2])
        self.assertEqual(actions[0]['payload']['expected_version'], 1)
        self.assertEqual(actions[0]['payload']['path'], TASKLIST)
        self.assertEqual(actions[0]['mode'], 'store')
        self.assertEqual(self.transaction['documents'], [])

    def test_the_plan_is_stable_across_identical_calls(self):
        self.arm()
        self.seed_store()
        self.integrate()
        self.assertEqual(self.plan(), self.plan())

    def test_a_worker_completion_or_a_code_commit_alone_is_not_published(self):
        self.arm()
        self.integrate()
        self.assertEqual(self.manifest_state(), 'integrating')
        self.assertIsNone(self.publication())
        committing = dict(self.stored_transaction(), stage='committing', cycle_commit=None)
        rejects(self, 'not_committed', lambda: self.plan(transaction=committing))
        rejects(self, 'worker_not_succeeded', lambda: self.plan(lifecycle='failed'))

    def test_a_local_run_plans_the_same_lifecycle_without_queue_calls(self):
        self.arm(queue=False)
        self.integrate(documents=[TASKLIST])
        actions = self.plan()
        self.assertEqual([action['kind'] for action in actions],
                         ['committed-documents', 'collect-evidence'])
        self.assertEqual(self.observe()['queue'], {'row': None})

    def test_a_published_task_plans_nothing(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.publish()
        self.assertEqual(self.manifest_state(), 'published')
        self.assertEqual(self.plan(), [])

    def test_the_reducers_never_mutate_their_inputs(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        manifest = json.loads(json.dumps(self.manifest))
        observed = self.observe()
        before = json.dumps(observed, sort_keys=True)
        actions = parallel_publication.next_publication_actions(manifest, TASK, observed)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(json.dumps(observed, sort_keys=True), before)
        action = actions[0]
        state = parallel_publication.record_publication(
            manifest, TASK, action['action_id'],
            {'schema': 1, 'publication': None, 'action': action, 'state': 'planned',
             'evidence': None})
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(state['manifest'], manifest)


class TestPublicationExecution(PublicationCase):
    """The whole publication on both storage paths, and the dependent's promotion."""

    def test_the_files_publication_publishes_once_with_the_documents_committed(self):
        self.arm()
        self.integrate(documents=[TASKLIST, NOTES])
        self.publish()
        record = self.assert_complete()
        self.assertEqual(self.commit_count(), 1)
        committed = git(self.coord, 'diff-tree', '-r', '--no-renames', '--name-only', '-z',
                        self.transaction['cycle_commit'] + '^',
                        self.transaction['cycle_commit'])
        committed = sorted(name for name in committed.split('\0') if name)
        self.assertIn(TASKLIST, committed)
        self.assertIn(NOTES, committed)
        self.assertEqual(record['documents'], sorted([TASKLIST, NOTES]))
        self.assertEqual(self.store.patches, [])
        self.assertEqual([entry['kind'] for entry in record['receipts']],
                         ['committed-documents', 'collect-evidence', 'queue-done'])
        self.assertEqual(self.queue.completions, 1)
        self.assertEqual(self.queue.observe()['status'], 'done')
        self.assertEqual(record['mode'], 'files')

    def test_the_store_publication_ticks_and_numbers_the_notes_after_the_commit(self):
        self.arm()
        self.seed_store()
        self.integrate()
        self.publish()
        record = self.assert_complete()
        self.assertEqual(self.commit_count(), 1)
        self.assertEqual(self.transaction['documents'], [])
        self.assertEqual([entry['kind'] for entry in record['receipts']],
                         ['store-tick', 'note-append', 'note-append', 'collect-evidence',
                          'queue-done'])
        self.assertEqual(self.store.patches,
                         [{'path': TASKLIST, 'expected_version': 1, 'version': 2},
                          {'path': NOTES, 'expected_version': 1, 'version': 2},
                          {'path': NOTES, 'expected_version': 2, 'version': 3}])
        self.assertIn(TICKED_CHECKBOX, self.store.get(TASKLIST)['content'])
        entries = self.note_entries(self.store.get(NOTES)['content'])
        self.assertEqual(entries, [{'number': 1, 'key': 'earlier-work'},
                                   {'number': 2, 'key': PROPOSALS[0][0]},
                                   {'number': 3, 'key': PROPOSALS[1][0]}])
        self.assertEqual(self.queue.completions, 1)
        self.assertLess(self.effects.index(('commit', self.transaction['cycle_commit'])),
                        min(index for index, effect in enumerate(self.effects)
                            if effect[0] != 'commit'))

    def test_the_cleanup_proof_removes_the_clean_worker_and_retains_the_refs(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.publish()
        record = self.assert_complete()
        report = task_worktree.cleanup_worker(str(self.coord), self.assignment(),
                                              record['proof'])
        self.assertTrue(report['removed'])
        self.assertFalse(self.worker.exists())
        self.assertEqual(git(self.coord, 'rev-parse', self.result_ref()),
                         self.transaction['transport_commit'])
        self.assertEqual(record['proof']['cycle_ref'], 'refs/heads/' + COORD_BRANCH)
        self.assertEqual(record['proof']['cycle_commit'], self.transaction['cycle_commit'])
        self.assertEqual(record['proof']['result_commit'],
                         self.transaction['transport_commit'])
        self.assertEqual(record['proof']['collected_root'], str(self.collected_root()))
        self.assertEqual(record['proof']['evidence'],
                         self.tree_entries(self.collected_root()))

    def test_a_dependent_is_held_until_every_publication_receipt_is_durable(self):
        self.arm()
        self.seed_store()
        self.integrate()
        held = self.admissions()
        self.assertEqual(held['start'], [])
        self.assertEqual(held['resume'], [])
        self.assertTrue(held['drain'])
        while True:
            actions = self.plan()
            if not actions:
                break
            action = actions[0]
            if action['kind'] != 'promote':
                admitted = self.admissions()
                self.assertNotIn(DEPENDENT, admitted['resume'])
                self.assertNotIn(DEPENDENT, admitted['start'])
            self.record_intent(action)
            if action['state'] == 'observed':
                receipt = self.observed_receipt(action)
                self.record(action, 'observed', receipt)
                continue
            receipt = self.execute(action)
            self.record(action, receipt['state'], receipt['evidence'])
        self.assertEqual(self.manifest_state(), 'published')
        promoted = self.admissions()
        self.assertIn(DEPENDENT, promoted['resume'])
        self.assertEqual(promoted['drain'], False)


class TestCrashRecovery(PublicationCase):
    """Every durable/external effect, crashed around, executed exactly once on resume."""

    CRASHES = ('before-intent', 'before-effect', 'after-effect')

    def crash_actions(self):
        return [action['action_id'] for action in self.plan()]

    def assert_exactly_once(self, record):
        kinds = [entry['kind'] for entry in record['receipts']]
        self.assertEqual(kinds.count('collect-evidence'), 1)
        self.assertEqual(kinds.count('queue-done'), 1)
        self.assertEqual(self.queue.completions, 1)
        self.assertEqual(sum(1 for effect in self.effects if effect[0] == 'collect'), 1)
        self.assertEqual(sum(1 for effect in self.effects if effect[0] == 'queue-done'), 1)
        self.assertEqual(len(record['receipts']), len(record['plan']))

    def test_every_files_action_crashes_and_resumes_exactly_once(self):
        self.arm()
        self.integrate(documents=[TASKLIST, NOTES])
        for action_id in self.crash_actions():
            for window in self.CRASHES:
                with self.subTest(action=action_id, window=window):
                    self.reset_publication()
                    self.publish(crash=(action_id, window))
                    self.restart()
                    self.publish()
                    record = self.assert_complete()
                    self.assert_exactly_once(record)
                    self.assertEqual(self.commit_count(), 1)
                    self.assertEqual([entry['kind'] for entry in record['receipts']],
                                     ['committed-documents', 'collect-evidence',
                                      'queue-done'])

    def test_every_store_action_crashes_and_resumes_exactly_once(self):
        self.arm()
        self.seed_store()
        self.integrate()
        for action_id in self.crash_actions():
            for window in self.CRASHES:
                with self.subTest(action=action_id, window=window):
                    self.reset_publication()
                    self.publish(crash=(action_id, window))
                    self.restart()
                    self.publish()
                    record = self.assert_complete()
                    self.assert_exactly_once(record)
                    self.assertEqual([entry['kind'] for entry in record['receipts']],
                                     ['store-tick', 'note-append', 'note-append',
                                      'collect-evidence', 'queue-done'])
                    self.assertEqual(len(self.store.patches), 3)
                    self.assertEqual([patch['version'] for patch in self.store.patches],
                                     [2, 2, 3])
                    self.assertEqual(self.note_entries(self.store.get(NOTES)['content'])[-2:],
                                     [{'number': 2, 'key': PROPOSALS[0][0]},
                                      {'number': 3, 'key': PROPOSALS[1][0]}])

    def test_a_lost_reply_is_reconciled_from_the_observed_effect(self):
        self.arm()
        self.seed_store()
        self.integrate()
        first = self.plan()[0]
        self.publish(lose=first['action_id'])
        self.assertEqual(self.publication()['receipts'], [])
        self.assertEqual(len(self.publication()['intents']), 1)
        self.assertEqual(len(self.store.patches), 1)
        resumed = self.plan()
        self.assertEqual(resumed[0]['action_id'], first['action_id'])
        self.assertEqual(resumed[0]['state'], 'observed')
        self.publish()
        self.assert_complete()
        self.assertEqual(len(self.store.patches), 3)
        self.assertEqual(self.queue.completions, 1)

    def test_a_lost_queue_reply_recovers_the_completion_once(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        queue_id = 'artel-publish-{}-{}-a{}-queue'.format(RUN_ID, TASK, ATTEMPT)
        self.publish(crash=(queue_id, 'before-effect'))
        self.queue.row['status'] = 'done'  # the effect landed; the reply was lost
        self.restart()
        actions = self.plan()
        self.assertEqual([action['kind'] for action in actions], ['queue-done'])
        self.assertEqual(actions[0]['state'], 'observed')
        self.publish()
        self.assert_complete()
        self.assertEqual(self.queue.completions, 0)

    def test_a_store_outage_before_the_effect_leaves_the_publication_resumable(self):
        self.arm()
        self.seed_store()
        self.integrate()
        tick = self.plan()[0]
        self.record_intent(tick)
        self.store.down = True
        with self.assertRaises(StoreDown):
            self.execute(tick)
        self.assertEqual(self.store.patches, [])
        self.assertEqual(self.manifest_state(), 'integrating')
        self.store.down = False
        self.publish()
        self.assert_complete()
        self.assertEqual(len(self.store.patches), 3)
        self.assertEqual(self.queue.completions, 1)

    def test_a_queue_outage_blocks_the_completion_until_it_returns(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.queue.down = True
        with self.assertRaises(QueueDown):
            self.publish()
        self.assertEqual(self.queue.completions, 0)
        self.assertEqual(self.queue.observe()['status'], 'in_progress')
        self.assertEqual(self.manifest_state(), 'integrating')
        self.queue.down = False
        self.publish()
        self.assert_complete()
        self.assertEqual(self.queue.completions, 1)
        self.assertEqual(sum(1 for effect in self.effects if effect[0] == 'collect'), 1)

    def test_a_malformed_receipt_is_refused(self):
        self.arm()
        self.seed_store()
        self.integrate()
        action = self.plan()[0]
        self.record_intent(action)
        receipt = self.execute(action)
        bad = dict(receipt['evidence'], unknown='not part of the schema')
        rejects(self, 'invalid_receipt',
                lambda: self.record(action, receipt['state'], bad))

    def test_a_complete_publication_promotes_after_a_lost_manifest_write(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.publish()
        self.assertEqual(self.manifest_state(), 'published')
        self.rewrite_manifest_integrating()
        actions = self.plan()
        self.assertEqual([action['kind'] for action in actions], ['promote'])
        self.assertEqual(actions[0]['state'], 'observed')
        self.record_intent(actions[0])
        self.record(actions[0], 'observed', None)
        self.assertEqual(self.manifest_state(), 'published')
        self.assertEqual(self.plan(), [])

    def test_a_duplicate_receipt_is_idempotent(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        action = self.plan()[0]
        self.record_intent(action)
        receipt = self.execute(action)
        first = self.record(action, receipt['state'], receipt['evidence'])
        second = self.record(action, receipt['state'], receipt['evidence'])
        self.assertEqual(first['publication'], second['publication'])
        self.assertEqual(len(second['publication']['receipts']), 1)

    def test_a_conflicting_receipt_for_the_same_action_is_refused(self):
        self.arm()
        self.seed_store()
        self.integrate()
        action = self.plan()[0]
        self.record_intent(action)
        receipt = self.execute(action)
        self.record(action, receipt['state'], receipt['evidence'])
        conflicting = dict(receipt['evidence'], version=99)
        rejects(self, 'conflicting_receipt',
                lambda: self.record(action, receipt['state'], conflicting))

    def test_an_out_of_order_intent_or_receipt_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        actions = self.plan()
        queue = actions[-1]
        rejects(self, 'out_of_order', lambda: self.record_intent(queue))
        self.record_intent(actions[0])
        rejects(self, 'missing_intent', lambda: self.record(queue, 'applied', {
            'task_id': queue['payload']['task_id'], 'actor': queue['payload']['actor'],
            'status': 'done'}))

    def test_a_receipt_without_its_intent_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        action = self.plan()[0]
        rejects(self, 'missing_intent', lambda: self.record(
            action, 'observed', {'paths': action['payload']['paths']}))

    def test_the_publication_record_passes_the_strict_transaction_validator(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        action = self.plan()[0]
        self.record_intent(action)
        receipt = self.execute(action)
        self.record(action, receipt['state'], receipt['evidence'])
        stored = self.stored_transaction()
        self.assertIsNotNone(stored['publication'])
        recovered = parallel_integration.recover_cycle(str(self.coord), stored)
        self.assertEqual(recovered['publication'], stored['publication'])
        self.assertEqual(self.publication(), stored['publication'])

    # --- helpers ----------------------------------------------------------------------

    def reset_publication(self):
        """Back to the durable state a restarted coordinator would read before publication."""
        record = self.stored_transaction()
        record['publication'] = None
        self.write_transaction(record)
        self.store.reset()
        if self.queue is not None:
            self.queue.reset()
        shutil.rmtree(str(self.collected_root()), ignore_errors=True)
        self.effects = [('commit', self.transaction['cycle_commit'])]
        self.rewrite_manifest_integrating()

    def rewrite_manifest_integrating(self):
        stored = parallel_state.read_manifest(str(self.manifest_path))
        self.manifest = parallel_state.write_manifest(
            str(self.manifest_path), dict(self.armed, revision=stored['revision']),
            stored['revision'])


class TestRefusals(PublicationCase):
    """Expected-version, holder, identity and evidence conflicts block instead of merging."""

    def test_a_recorded_intent_rebases_on_the_fresh_version_and_keeps_the_concurrent_edit(self):
        self.arm()
        self.seed_store()
        self.integrate()
        tick = self.plan()[0]
        self.record_intent(tick)
        self.store.put(TASKLIST, TASKLIST_TEXT + '\n- [ ] 9.9 unrelated edit\n')
        rebased = self.plan()[0]
        self.assertEqual(rebased['action_id'], tick['action_id'])
        self.assertEqual(rebased['payload']['expected_version'], 2)
        self.assertEqual(rebased['state'], 'pending')
        self.record_intent(rebased)
        self.publish()
        self.assert_complete()
        content = self.store.get(TASKLIST)['content']
        self.assertIn(TICKED_CHECKBOX, content)
        self.assertIn('- [ ] 9.9 unrelated edit', content)
        self.assertEqual(self.store.patches[0],
                         {'path': TASKLIST, 'expected_version': 2, 'version': 3})

    def test_a_rebase_never_moves_the_expected_version_backwards(self):
        self.arm()
        self.seed_store()
        self.integrate()
        tick = self.plan()[0]
        self.record_intent(tick)
        stale = dict(tick, payload=dict(tick['payload'], expected_version=0))
        rejects(self, 'intent_conflict', lambda: self.record_intent(stale))

    def test_a_concurrent_note_append_is_never_dropped_or_renumbered_over(self):
        self.arm()
        self.seed_store()
        self.integrate()
        tick = self.plan()[0]
        self.record_intent(tick)
        first = self.plan()[1]
        self.record_intent(first)
        self.store.put(NOTES, self.append_note(SEEDED_NOTES, 2, 'other-agent',
                                               'someone else'))
        rebased = self.plan()[1]
        self.assertEqual(rebased['action_id'], first['action_id'])
        self.assertEqual(rebased['payload']['number'], 3)
        self.assertEqual(rebased['payload']['expected_version'], 2)
        self.publish()
        self.assert_complete()
        entries = self.note_entries(self.store.get(NOTES)['content'])
        self.assertIn({'number': 2, 'key': 'other-agent'}, entries)
        self.assertEqual([entry['number'] for entry in entries[-2:]], [3, 4])

    def test_a_foreign_queue_holder_is_never_completed(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.queue.row['actor'] = 'artel@someone-else'
        rejects(self, 'foreign_row', lambda: self.plan())
        self.assertEqual(self.queue.observe()['status'], 'in_progress')
        self.assertEqual(self.manifest_state(), 'integrating')

    def test_a_reacquired_or_changed_row_is_never_completed(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.queue.row['task_id'] = 99
        rejects(self, 'row_changed', lambda: self.plan())
        self.queue.reset()
        self.queue.row['task'] = DEPENDENT
        rejects(self, 'row_changed', lambda: self.plan())

    def test_a_missing_or_released_row_blocks(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        rejects(self, 'row_missing',
                lambda: parallel_publication.next_publication_actions(
                    self.manifest, TASK, self.observe_with_row(None)))
        self.queue.row['status'] = 'ready'
        rejects(self, 'row_not_in_progress', lambda: self.plan())

    def observe_with_row(self, row):
        observed = self.observe()
        observed['queue'] = {'row': row}
        return observed

    def test_a_conflicting_identity_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        actions = self.plan()
        self.record_intent(actions[0])
        mutated = dict(actions[1], cycle_commit='1' * 40)
        rejects(self, 'identity_conflict', lambda: self.record_intent(mutated))

    def test_a_receipt_for_another_task_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        action = self.plan()[0]
        mutated = dict(action, task=DEPENDENT,
                       action_id=action['action_id'].replace(TASK, DEPENDENT))
        rejects(self, 'invalid_action', lambda: self.record(
            mutated, 'observed', {'paths': action['payload']['paths']}))

    def test_a_receipt_for_a_mismatched_action_id_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        action = self.plan()[0]
        rejects(self, 'invalid_receipt', lambda: parallel_publication.record_publication(
            self.manifest, TASK, 'artel-publish-not-this-action',
            self.envelope(action, 'planned', None)))

    def test_a_collection_receipt_that_does_not_match_the_plan_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        actions = self.plan()
        collect = next(action for action in actions if action['kind'] == 'collect-evidence')
        self.record_intent(actions[0])
        receipt = self.execute(actions[0])
        self.record(actions[0], receipt['state'], receipt['evidence'])
        self.record_intent(collect)
        evidence = {'collected_root': collect['payload']['collected_root'],
                    'entries': [{'path': 'other.txt', 'sha256': '0' * 64}]}
        rejects(self, 'invalid_receipt', lambda: self.record(collect, 'applied', evidence))

    def test_a_lost_collected_copy_refuses_before_the_publication_completes(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        queue_id = 'artel-publish-{}-{}-a{}-queue'.format(RUN_ID, TASK, ATTEMPT)
        self.publish(crash=(queue_id, 'before-intent'))
        self.assertEqual(self.manifest_state(), 'integrating')
        (self.collected_root() / 'result.json').write_text('tampered\n', encoding='utf-8')
        rejects(self, 'collection_lost', lambda: self.plan())

    def test_a_corrupt_prior_publication_record_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        record = self.stored_transaction()
        record['publication'] = {'schema': 1}
        self.write_transaction(record)
        rejects(self, 'invalid_publication', lambda: self.plan())

    def test_a_stale_base_observation_is_refused(self):
        self.arm()
        self.seed_store()
        self.integrate()
        tick = self.plan()[0]
        self.record_intent(tick)
        self.store.docs[TASKLIST] = {'version': 0, 'content': TASKLIST_TEXT}
        rejects(self, 'invalid_observation', lambda: self.plan())

    def test_evidence_that_escapes_the_collected_root_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        observation = self.evidence_observation()
        observation['collected_root'] = str(self.main / 'outside')
        rejects(self, 'invalid_evidence', lambda: parallel_publication.next_publication_actions(
            self.manifest, TASK, self.observed_with_evidence(observation)))

    def test_the_collected_root_may_not_be_the_worker_artifact_root(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        observation = self.evidence_observation()
        observation['collected_root'] = str(self.artifact_root())
        rejects(self, 'invalid_evidence', lambda: parallel_publication.next_publication_actions(
            self.manifest, TASK, self.observed_with_evidence(observation)))


class TestLocalAndFilesBoundaries(PublicationCase):
    """The local, queue-free lifecycle and the files/store mode boundaries."""

    def test_the_local_lifecycle_publishes_without_any_queue_call(self):
        self.arm(queue=False)
        self.integrate(documents=[TASKLIST])
        self.publish()
        record = self.assert_complete()
        self.assertNotIn('queue-done', [entry['kind'] for entry in record['receipts']])
        self.assertEqual(record['mode'], 'files')

    def test_a_store_run_carrying_committed_documents_is_refused(self):
        self.arm()
        self.seed_store()
        self.integrate(documents=[TASKLIST])
        rejects(self, 'mode_conflict', lambda: self.plan())

    def test_a_files_run_with_store_patches_is_refused(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        store = {'mode': 'files',
                 'documents': [{'path': TASKLIST, 'version': 1,
                                'content_sha256': '0' * 64, 'applied': False}],
                 'notes': None}
        rejects(self, 'mode_conflict', lambda: self.plan(store=store))

    def test_a_store_run_without_configured_documents_plans_only_the_rest(self):
        self.arm()
        self.seed_store()
        self.integrate()
        store = self.store_observation()
        store['documents'] = []
        store['notes'] = None
        actions = self.plan(store=store)
        self.assertEqual([action['kind'] for action in actions],
                         ['collect-evidence', 'queue-done'])

    def test_a_passive_prepared_sibling_is_left_untouched(self):
        self.arm()
        manifest = self.walk(self.manifest, '3.3', 'prepared')
        current = parallel_state.read_manifest(str(self.manifest_path))['revision']
        self.manifest = parallel_state.write_manifest(
            str(self.manifest_path), dict(manifest, revision=current), current)
        self.integrate(documents=[TASKLIST])
        self.publish()
        stored = parallel_state.read_manifest(str(self.manifest_path))
        self.assertEqual(stored['tasks']['3.3']['state'], 'prepared')
        self.assertEqual(stored['tasks'][TASK]['state'], 'published')
        self.assertTrue(self.worker.exists())

    def test_a_cleanup_refused_dirty_root_stays_published_and_retained(self):
        self.arm()
        self.integrate(documents=[TASKLIST])
        self.publish()
        record = self.assert_complete()
        self.write('build/leftover.txt', 'ignored but real\n', base=self.worker)
        rejects_worktree(self, 'dirty_worktree', lambda: task_worktree.cleanup_worker(
            str(self.coord), self.assignment(), record['proof']))
        self.assertTrue(self.worker.exists())
        self.assertEqual(self.manifest_state(), 'published')
        self.assertEqual(self.publication()['complete'], True)


class TestPublicationDocs(unittest.TestCase):
    """docs/opencode-parallel.md §13.9 is the contract Task 10's loop consumes."""

    def test_the_section_documents_the_publication_contract(self):
        text = (ROOT / 'docs' / 'opencode-parallel.md').read_text(encoding='utf-8')
        section = text[text.index('\n### 13.9 '):]
        for phrase in ('next_publication_actions', 'record_publication', 'intents', 'receipts',
                       'collect-evidence', 'queue done is last', 'promote',
                       'expected_version', 'files path', 'artifact-store path',
                       'never a second completion commit', 'collected_root'):
            self.assertIn(phrase, section, phrase)


if __name__ == '__main__':
    unittest.main()
