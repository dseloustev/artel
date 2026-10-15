"""Task 7: the OpenCode worker-session adapter.

Two halves, one contract. `opencode/plugin/parallel.ts` is the thin native transport
(registered by the bridge `opencode/plugin/artel.ts`); `scripts/parallel_launch.py` is its
policy half, which reuses the frozen schema/lifecycle modules instead of reimplementing
them in TypeScript. Contract: docs/opencode-parallel.md §2 (adapter surface), §4-§5
(assignment/result), §6/§10 (task states, outcome layers), §9 (model resolution) and
§12 (capability failure), plus the reviewed Task 6 role boundary.

There is no TypeScript runner in this repo, so the TS side is checked at source level for
placement and call ordering; the lifecycle is exercised deterministically through the
Python policy half against a fake native service that has no event stream at all --
durable session/message state is the only recovery path under test.

The invariants worth the most: capabilities refuse an unsupported version/API before any
journal or session effect; the launch intent is durable before the session exists and the
session id before the prompt is admitted; a duplicate start adopts or reconciles instead of
creating a second session; a wrong session/root/agent/model/permissions set is refused, not
adopted; an idle or natively-succeeded session without a valid, identity-matching result is
not completion; and an interruption wins over a contradictory success result.
"""
import copy
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
sys.path.insert(0, str(ROOT / 'hooks'))
import hook_common as h  # noqa: E402
import parallel_contract  # noqa: E402
import parallel_state  # noqa: E402
import task_worktree  # noqa: E402

TICKET = 'PROJ-123'
RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
OTHER_RUN = '1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'
TASK = '3.1'
PHASE = 2
COORD_BRANCH = 'feature/PROJ-123'
MODEL = 'tokenguard/deepseek-flash'
MODEL_REF = {'providerID': 'tokenguard', 'id': 'deepseek-flash'}
AGENT = 'artel-implementer'
PROMPT_TEXT = 'Implement the pinned assignment. Reply with exactly: WORKER-DONE'
ADAPTER = ROOT / 'opencode' / 'plugin' / 'parallel.ts'
BRIDGE = ROOT / 'opencode' / 'plugin' / 'artel.ts'
HELPER = ROOT / 'scripts' / 'parallel_launch.py'
INSTALLER = ROOT / 'scripts' / 'install-opencode.sh'

try:
    import parallel_launch  # noqa: E402
except ImportError:  # the red run: the policy half does not exist yet
    parallel_launch = None


def git(cwd, *args):
    return subprocess.run(['git'] + [str(a) for a in args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


class WorkerCase(unittest.TestCase):
    """A real repository, the coordinator in a linked worktree, a real ticket lock and a
    worker root created by the reviewed lifecycle -- the marker under test is the one the
    lifecycle actually writes."""

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

    # --- fixtures ---------------------------------------------------------------------

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
            'files': ['app.txt'], 'tests': [], 'route': route, 'model': MODEL,
            'worker_root': str(self.worker_root(task, attempt, run_id)),
            'artifact_root': str(self.artifact_root(task, attempt, run_id)),
        }
        value.update(overrides)
        return value

    def create(self, task=TASK, attempt=1, run_id=RUN_ID, route='light', **overrides):
        assignment = self.assignment(task, attempt, run_id, route, **overrides)
        report = task_worktree.create_worker(str(self.coord), assignment)
        return assignment, report

    def request(self, assignment=None, agent=AGENT, text=PROMPT_TEXT, permissions=None,
                **overrides):
        value = {
            'assignment': assignment or self.assignment(),
            'agent': agent,
            'prompt': {'text': text},
            'permissions': permissions,
        }
        value.update(overrides)
        return value

    def capabilities(self, version='2.0.29', **api_overrides):
        api = {key: True for key in parallel_launch.REQUIRED_API}
        api.update(api_overrides)
        return {'version': version, 'api': api}

    def caller(self, location=None):
        return {'session_id': 'ses_coordinator', 'location': str(location or self.coord)}

    def model_facts(self, available=True, variants=()):
        return {'available': available, 'variants': list(variants)}

    def journal_path(self, assignment):
        return (self.main / '.artel' / 'run' / TICKET / 'parallel' / assignment['run_id'] /
                'launches' / '{}-a{}.json'.format(assignment['task'], assignment['attempt']))

    def read_journal(self, assignment):
        return json.loads(self.journal_path(assignment).read_text(encoding='utf-8'))

    def result(self, assignment, **overrides):
        value = dict(assignment)
        value.update({'status': 'succeeded', 'changed_files': ['app.txt'], 'verify': [],
                      'review': None, 'report': 'report.md', 'deviations': []})
        value.update(overrides)
        return value

    def write_result(self, assignment, value=None):
        root = Path(assignment['artifact_root'])
        root.mkdir(parents=True, exist_ok=True)
        (root / 'result.json').write_text(
            json.dumps(value if value is not None else self.result(assignment)), encoding='utf-8')


class FakeNative:
    """A deterministic stand-in for the native service: idempotent create by id, durable
    session info and messages, interrupt. It has no event stream at all -- every recovery
    path under test must work from durable state alone."""

    def __init__(self):
        self.sessions = {}
        self.messages = {}
        self.calls = []

    def create(self, payload):
        self.calls.append(('create', copy.deepcopy(payload)))
        sid = payload['id']
        if sid in self.sessions:
            return copy.deepcopy(self.sessions[sid])
        info = {'id': sid, 'projectID': 'proj', 'cost': 0, 'tokens': {},
                'time': {'created': 1, 'updated': 1}}
        for key in ('location', 'agent', 'model', 'metadata', 'permissions', 'parentID'):
            if payload.get(key) is not None:
                info[key] = copy.deepcopy(payload[key])
        self.sessions[sid] = info
        self.messages[sid] = []
        return copy.deepcopy(info)

    def get(self, sid):
        self.calls.append(('get', sid))
        return copy.deepcopy(self.sessions.get(sid))

    def prompt(self, sid, text, delivery=None):
        self.calls.append(('prompt', sid, text))
        self.messages.setdefault(sid, []).append(
            {'id': 'msg_%d' % len(self.messages[sid]), 'time': {'created': 1},
             'text': text, 'type': 'user'})
        return {'id': 'inbox_%d' % len(self.messages[sid]), 'sessionID': sid,
                'time': {'created': 1}, 'type': 'user',
                'payload': {'text': text}, 'delivery': delivery or 'queue'}

    def context(self, sid):
        self.calls.append(('context', sid))
        return copy.deepcopy(self.messages.get(sid, []))

    def complete(self, sid, outcome='succeeded'):
        info = self.sessions[sid]
        info['outcome'] = outcome
        info['time']['idle'] = 2
        self.messages.setdefault(sid, []).append(
            {'id': 'msg_idle', 'time': {'created': 2}, 'type': 'idle', 'outcome': outcome})

    def interrupt(self, sid, resume=False):
        self.calls.append(('interrupt', sid, resume))
        if sid in self.sessions:
            self.complete(sid, 'interrupted')
        return {'interrupted': True}

    def kinds(self):
        return [call[0] for call in self.calls]


def ts_start(helper_input, native, crash=None):
    """The TS side of startWorker, played against the policy half and the fake service.

    `crash` simulates the coordinator dying at a step boundary; the next start must
    reconcile from durable state instead of creating a second session.
    """
    plan = parallel_launch.plan_start(**helper_input)
    if plan['action'] == 'adopted':
        return plan['envelope']
    if plan['action'] == 'create':
        session = native.create(plan['create'])
        if crash == 'after_create':
            return None
        recorded = parallel_launch.record_session(helper_input['request'], session)
        if crash == 'after_record':
            return None
        prompt = recorded['prompt']
        inbox = native.prompt(plan['session_id'], prompt['text'], prompt.get('delivery'))
        if crash == 'after_prompt':
            return None
        return parallel_launch.record_prompt(helper_input['request'], inbox)['envelope']
    if plan['action'] == 'reconcile':
        session = native.get(plan['session_id'])
        messages = native.context(plan['session_id']) if session else []
        reconciled = parallel_launch.reconcile(helper_input['request'], session, messages)
        if reconciled['action'] == 'create':
            session = native.create(reconciled['create'])
            recorded = parallel_launch.record_session(helper_input['request'], session)
            inbox = native.prompt(plan['session_id'], recorded['prompt']['text'])
            return parallel_launch.record_prompt(helper_input['request'], inbox)['envelope']
        if reconciled['action'] == 'prompt':
            inbox = native.prompt(plan['session_id'], reconciled['prompt']['text'])
            return parallel_launch.record_prompt(helper_input['request'], inbox)['envelope']
        return reconciled['envelope']
    raise AssertionError('unexpected plan action: %r' % plan)


def ts_status(helper_input, native):
    plan = parallel_launch.plan_status(**helper_input)
    if plan['action'] == 'unknown':
        return plan['envelope']
    session = native.get(plan['session_id'])
    messages = native.context(plan['session_id']) if session else []
    return parallel_launch.interpret_status(helper_input['request'], session, messages)['envelope']


def ts_interrupt(helper_input, native):
    plan = parallel_launch.plan_interrupt(**helper_input)
    if plan['action'] in ('none', 'unknown'):
        return plan['envelope']
    reply = native.interrupt(plan['session_id'])
    session = native.get(plan['session_id'])
    return parallel_launch.interpret_interrupt(
        helper_input['request'], reply, session)['envelope']


def flat(rel):
    return re.sub(r'\s+', ' ', (ROOT / rel).read_text(encoding='utf-8'))


@unittest.skipUnless(parallel_launch is not None, 'the adapter policy half is not implemented yet')
class AdapterCase(WorkerCase):
    def start_input(self, assignment=None, **overrides):
        value = {
            'request': self.request(assignment=assignment),
            'capabilities': self.capabilities(),
            'caller': self.caller(),
            'model_facts': self.model_facts(),
        }
        value.update(overrides)
        return value


class TestAdapterSource(unittest.TestCase):
    """The TS half: a thin transport registered by the bridge, with the policy living in
    Python. There is no TS runner here, so placement and call ordering are asserted."""

    def setUp(self):
        self.source = ADAPTER.read_text(encoding='utf-8')
        self.bridge = BRIDGE.read_text(encoding='utf-8')

    def test_the_adapter_exists_and_registers_the_four_tools(self):
        for token in ('workerCapabilities', 'startWorker', 'workerStatus', 'interruptWorker'):
            self.assertIn(token, self.source, token)
        self.assertIn('tool.transform', self.source)
        self.assertIn('namespace', self.source)

    def test_the_adapter_exports_a_registration_function(self):
        self.assertIn('export function registerParallel', self.source)

    def test_the_bridge_registers_the_adapter(self):
        self.assertIn('registerParallel', self.bridge)
        self.assertIn('./parallel', self.bridge)

    def test_the_adapter_uses_the_installed_client_surface(self):
        for token in ('ctx.session.create', 'ctx.session.get', 'ctx.session.prompt',
                      'ctx.session.interrupt', 'ctx.session.context', 'ctx.model.list'):
            self.assertIn(token, self.source, token)

    def test_the_adapter_has_no_v1_or_imagined_surface(self):
        for token in ('seat', 'experimental.chat.messages.transform', '@opencode-ai/plugin',
                      'client.app.log', 'service.json', 'fetch(', 'http://', 'subagent',
                      'artel_implementer'):
            self.assertNotIn(token, self.source, token)

    def test_capabilities_precede_every_effect(self):
        # The helper call that validates the capability report must sit before the first
        # session call in startWorker, and the capability tool itself must not create one.
        start = self.source[self.source.index('async function startWorker'):]
        self.assertLess(start.index('plan-start'), start.index('ctx.session.create'))
        capabilities = self.source[self.source.index('async function workerCapabilities'):]
        capabilities = capabilities[:capabilities.index('\n}')]
        self.assertNotIn('ctx.session.create', capabilities)
        self.assertNotIn('ctx.session.prompt', capabilities)

    def test_the_journal_step_brackets_the_prompt(self):
        start = self.source[self.source.index('async function startWorker'):]
        self.assertLess(start.index('record-session'), start.index('promptSession('))
        self.assertLess(start.index('plan-start'), start.index('ctx.session.create'))
        self.assertIn('ctx.session.prompt', self.source)

    def test_the_adapter_never_schedules_claims_or_publishes(self):
        for token in ('task_ready', 'task_update', 'begin_claim', 'record_claim',
                      'reconcile_claims', 'publication', 'commit_cycle', 'prepare_transport'):
            self.assertNotIn(token, self.source, token)

    def test_the_not_found_check_covers_the_native_error_shape(self):
        # The installed client's Session.NotFoundError carries an empty `message` (probed
        # natively on 2.0.29), so the check must read the tag/name/string forms too.
        check = self.source[self.source.index('function isNotFound'):]
        check = check[:check.index('\n}')]
        for token in ('_tag', 'name', 'String(error)', 'not.?found'):
            self.assertIn(token, check, token)

    def test_the_adapter_refuses_a_worker_caller(self):
        # The refusal is policy: the authoritative Python marker reader decides whether the
        # caller sits in a worker root; the transport only resolves and passes the caller.
        helper = HELPER.read_text(encoding='utf-8')
        self.assertIn('caller_is_worker', helper)
        self.assertIn('caller', self.source)
        self.assertIn('resolveCaller', self.source)

    def test_the_bridge_denies_the_adapter_namespace_to_workers(self):
        # The bridge derives the denied set from the adapter's exported ids, so a renamed
        # or added adapter tool cannot silently bypass the worker role.
        for token in ('PARALLEL_TOOL_IDS', '...PARALLEL_TOOL_IDS', 'WORKER_DENIED_TOOLS'):
            self.assertIn(token, self.bridge, token)
        for tool in ('artel_parallel_workerCapabilities', 'artel_parallel_startWorker',
                     'artel_parallel_workerStatus', 'artel_parallel_interruptWorker'):
            self.assertIn(tool, self.source, tool)
        before = self.bridge[self.bridge.index('ctx.tool.hook("execute.before"'):]
        self.assertLess(before.index('WORKER_DENIED_TOOLS.has(event.tool)'),
                        before.index('runHook("vcs_guard.py"'))

    def test_the_installer_ships_the_adapter_beside_the_bridge(self):
        installer = INSTALLER.read_text(encoding='utf-8')
        self.assertIn('parallel.ts', installer)


class TestCapabilitiesPolicy(AdapterCase):
    def test_the_proven_version_and_api_are_accepted(self):
        report = parallel_launch.capability_report(self.capabilities())
        self.assertTrue(report['ok'])
        self.assertEqual(report['version'], '2.0.29')
        self.assertEqual(report['reasons'], [])

    def test_another_version_is_refused_before_any_effect(self):
        assignment, _ = self.create()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment, capabilities=self.capabilities('2.1.0')), native)
        self.assertEqual(caught.exception.kind, 'unsupported_version')
        self.assertFalse(self.journal_path(assignment).exists())
        self.assertEqual(native.calls, [])

    def test_a_missing_api_is_refused_and_named(self):
        assignment, _ = self.create()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment,
                                      capabilities=self.capabilities(**{'session.interrupt': False})),
                     native)
        self.assertEqual(caught.exception.kind, 'unsupported_api')
        self.assertIn('session.interrupt', caught.exception.message)
        self.assertFalse(self.journal_path(assignment).exists())
        self.assertEqual(native.calls, [])

    def test_capability_inspection_is_side_effect_free(self):
        assignment, _ = self.create()
        before = sorted(p.relative_to(self.main).as_posix()
                        for p in self.main.rglob('*') if p.is_file())
        report = parallel_launch.capability_report(self.capabilities())
        after = sorted(p.relative_to(self.main).as_posix()
                       for p in self.main.rglob('*') if p.is_file())
        self.assertTrue(report['ok'])
        self.assertEqual(before, after)
        self.assertFalse(self.journal_path(assignment).exists())


class TestLaunchLifecycle(AdapterCase):
    def test_the_intent_is_durable_before_the_session_exists(self):
        assignment, _ = self.create()
        native = FakeNative()
        seen = {}
        original = native.create

        def create(payload):
            seen['journal'] = self.journal_path(assignment).exists()
            seen['state'] = self.read_journal(assignment)['state'] if seen['journal'] else None
            return original(payload)

        native.create = create
        envelope = ts_start(self.start_input(assignment), native)
        self.assertTrue(seen['journal'], 'the launch intent must be durable before create')
        self.assertEqual(seen['state'], 'intent')
        self.assertEqual(envelope['status'], 'started')
        self.assertEqual(self.read_journal(assignment)['state'], 'prompted')

    def test_the_session_id_is_durable_before_the_prompt_is_admitted(self):
        assignment, _ = self.create()
        native = FakeNative()
        seen = {}
        original = native.prompt

        def prompt(sid, text, delivery=None):
            seen['journal'] = self.read_journal(assignment)
            return original(sid, text, delivery)

        native.prompt = prompt
        ts_start(self.start_input(assignment), native)
        self.assertEqual(seen['journal']['state'], 'created')
        self.assertEqual(seen['journal']['session_id'], native.calls[0][1]['id'])

    def test_the_created_session_carries_the_pinned_identity(self):
        assignment, _ = self.create()
        native = FakeNative()
        envelope = ts_start(self.start_input(assignment), native)
        payload = native.calls[0][1]
        self.assertEqual(payload['id'], envelope['session_id'])
        self.assertEqual(payload['location'], {'directory': assignment['worker_root']})
        self.assertEqual(payload['agent'], AGENT)
        self.assertEqual(payload['model'], MODEL_REF)
        self.assertEqual(payload['metadata'], {
            'schema': 1, 'ticket': TICKET, 'phase': PHASE, 'task': TASK,
            'run_id': RUN_ID, 'attempt': 1, 'claim': 'artel@host'})
        self.assertNotIn('parentID', payload)
        self.assertNotIn('permissions', payload)
        self.assertIn('title', payload)

    def test_the_prompt_is_the_pinned_text(self):
        assignment, _ = self.create()
        native = FakeNative()
        ts_start(self.start_input(assignment), native)
        self.assertEqual(native.calls[1][0], 'prompt')
        self.assertEqual(native.calls[1][2], PROMPT_TEXT)

    def test_a_duplicate_start_adopts_the_prompted_session(self):
        assignment, _ = self.create()
        native = FakeNative()
        first = ts_start(self.start_input(assignment), native)
        creates = native.kinds().count('create')
        second = ts_start(self.start_input(assignment), native)
        self.assertEqual(second['session_id'], first['session_id'])
        self.assertTrue(second['adopted'])
        self.assertEqual(native.kinds().count('create'), creates)
        self.assertEqual(native.kinds().count('prompt'), 1)

    def test_a_crash_after_create_reconciles_instead_of_creating_a_second_session(self):
        assignment, _ = self.create()
        native = FakeNative()
        self.assertIsNone(ts_start(self.start_input(assignment), native, crash='after_create'))
        self.assertEqual(self.read_journal(assignment)['state'], 'intent')
        envelope = ts_start(self.start_input(assignment), native)
        self.assertEqual(envelope['status'], 'started')
        self.assertEqual(native.kinds().count('create'), 1,
                         'the idempotent create must adopt the session the crashed start made')

    def test_a_crash_after_record_reconciles_and_prompts_once(self):
        assignment, _ = self.create()
        native = FakeNative()
        self.assertIsNone(ts_start(self.start_input(assignment), native, crash='after_record'))
        self.assertEqual(self.read_journal(assignment)['state'], 'created')
        envelope = ts_start(self.start_input(assignment), native)
        self.assertEqual(envelope['status'], 'started')
        self.assertEqual(native.kinds().count('prompt'), 1)
        self.assertEqual(self.read_journal(assignment)['state'], 'prompted')

    def test_a_crash_after_prompt_adoption_is_reconciled_from_messages(self):
        assignment, _ = self.create()
        native = FakeNative()
        self.assertIsNone(ts_start(self.start_input(assignment), native, crash='after_prompt'))
        self.assertEqual(self.read_journal(assignment)['state'], 'created')
        envelope = ts_start(self.start_input(assignment), native)
        self.assertEqual(envelope['status'], 'started')
        self.assertTrue(envelope['adopted'])
        self.assertEqual(native.kinds().count('prompt'), 1,
                         'the already-admitted prompt must not be sent twice')

    def test_a_wrong_session_is_refused_not_adopted(self):
        assignment, _ = self.create()
        native = FakeNative()
        ts_start(self.start_input(assignment), native, crash='after_create')
        sid = native.calls[0][1]['id']
        native.sessions[sid]['metadata'] = {'schema': 1, 'ticket': 'OTHER-1'}
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment), native)
        self.assertEqual(caught.exception.kind, 'wrong_session')
        self.assertNotIn('prompt', native.kinds())

    def test_a_wrong_root_is_refused_not_adopted(self):
        assignment, _ = self.create()
        native = FakeNative()
        ts_start(self.start_input(assignment), native, crash='after_create')
        sid = native.calls[0][1]['id']
        native.sessions[sid]['location'] = {'directory': str(self.worker_root(task='3.2'))}
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment), native)
        self.assertEqual(caught.exception.kind, 'wrong_root')
        self.assertNotIn('prompt', native.kinds())

    def test_a_switched_agent_or_model_is_refused_not_adopted(self):
        for index, (field, value, kind) in enumerate(
                (('agent', 'build', 'wrong_agent'),
                 ('model', {'providerID': 'tokenguard', 'id': 'other'}, 'wrong_model'))):
            with self.subTest(field):
                assignment, _ = self.create(task='3.%d' % (index + 2))
                native = FakeNative()
                ts_start(self.start_input(assignment), native, crash='after_create')
                sid = native.calls[0][1]['id']
                native.sessions[sid][field] = value
                with self.assertRaises(parallel_launch.LaunchError) as caught:
                    ts_start(self.start_input(assignment), native)
                self.assertEqual(caught.exception.kind, kind)

    def test_a_child_session_is_refused(self):
        assignment, _ = self.create()
        native = FakeNative()
        ts_start(self.start_input(assignment), native, crash='after_create')
        sid = native.calls[0][1]['id']
        native.sessions[sid]['parentID'] = 'ses_parent'
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment), native)
        self.assertEqual(caught.exception.kind, 'wrong_session')

    def test_permissions_are_applied_and_verified(self):
        rules = [{'action': 'subagent', 'resource': '*', 'effect': 'deny'}]
        assignment, _ = self.create()
        native = FakeNative()
        envelope = ts_start(self.start_input(
            assignment, request=self.request(assignment, permissions=rules)), native)
        self.assertEqual(native.calls[0][1]['permissions'], rules)
        self.assertEqual(envelope['status'], 'started')
        second, _ = self.create(task='3.2')
        native2 = FakeNative()
        ts_start(self.start_input(
            second, request=self.request(second, permissions=rules)), native2,
            crash='after_create')
        sid = native2.calls[0][1]['id']
        native2.sessions[sid]['permissions'] = []
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(
                second, request=self.request(second, permissions=rules)), native2)
        self.assertEqual(caught.exception.kind, 'permissions_mismatch')

    def test_a_non_worker_root_is_refused_before_any_effect(self):
        assignment = self.assignment()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment), native)
        self.assertEqual(caught.exception.kind, 'not_worker_root')
        self.assertEqual(native.calls, [])
        self.assertFalse(self.journal_path(assignment).exists())

    def test_an_assignment_that_is_not_the_marker_identity_is_refused(self):
        assignment, _ = self.create()
        other = dict(assignment, claim='artel@other-host')
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(other), native)
        self.assertEqual(caught.exception.kind, 'worker_identity_mismatch')

    def test_first_consumed_marker_refs_are_validated(self):
        for name, value in (('base_ref', 'refs/heads/nope'),
                            ('result_ref', 'refs/artel/parallel/other/3.1/1/result')):
            with self.subTest(name):
                assignment, _ = self.create()
                marker_path = Path(assignment['worker_root']) / '.artel' / 'parallel-worker.json'
                marker = json.loads(marker_path.read_text(encoding='utf-8'))
                marker[name] = value
                marker_path.write_text(json.dumps(marker), encoding='utf-8')
                native = FakeNative()
                with self.assertRaises(parallel_launch.LaunchError) as caught:
                    ts_start(self.start_input(assignment), native)
                self.assertEqual(caught.exception.kind, 'invalid_worker_marker')
                self.assertEqual(native.calls, [])

    def test_a_null_model_is_refused_never_inherited(self):
        assignment, _ = self.create(model=None)
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment), native)
        self.assertEqual(caught.exception.kind, 'unresolved_model')
        self.assertEqual(native.calls, [])

    def test_an_unavailable_model_or_unknown_variant_is_refused(self):
        assignment, _ = self.create()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment, model_facts=self.model_facts(available=False)),
                     native)
        self.assertEqual(caught.exception.kind, 'unresolved_model')
        self.assertEqual(native.calls, [])
        variant_assignment, _ = self.create(task='3.2', model=MODEL + '#beta')
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(variant_assignment,
                                      model_facts=self.model_facts(variants=['default'])), native)
        self.assertEqual(caught.exception.kind, 'unresolved_model')

    def test_a_worker_caller_is_refused_before_any_effect(self):
        assignment, _ = self.create()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment,
                                      caller=self.caller(location=assignment['worker_root'])),
                     native)
        self.assertEqual(caught.exception.kind, 'caller_is_worker')
        self.assertEqual(native.calls, [])
        self.assertFalse(self.journal_path(assignment).exists())

    def test_an_unresolvable_caller_is_refused(self):
        assignment, _ = self.create()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment, caller={'session_id': 'ses_x'}), native)
        self.assertEqual(caught.exception.kind, 'invalid_request')

    def test_a_malformed_journal_is_refused_never_overwritten(self):
        assignment, _ = self.create()
        path = self.journal_path(assignment)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{not json', encoding='utf-8')
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment), native)
        self.assertEqual(caught.exception.kind, 'invalid_journal')
        self.assertEqual(path.read_text(encoding='utf-8'), '{not json')
        self.assertEqual(native.calls, [])

    def test_two_attempts_keep_separate_journals_and_sessions(self):
        first, _ = self.create(attempt=1)
        second, _ = self.create(attempt=2)
        native = FakeNative()
        envelope_a = ts_start(self.start_input(first), native)
        envelope_b = ts_start(self.start_input(second), native)
        self.assertNotEqual(envelope_a['session_id'], envelope_b['session_id'])
        self.assertNotEqual(self.journal_path(first), self.journal_path(second))
        self.assertEqual(self.read_journal(first)['attempt'], 1)
        self.assertEqual(self.read_journal(second)['attempt'], 2)


class TestWorkerStatus(AdapterCase):
    def started(self, **overrides):
        assignment, _ = self.create(**overrides)
        native = FakeNative()
        envelope = ts_start(self.start_input(assignment), native)
        return assignment, native, envelope

    def test_the_status_request_is_assignment_only(self):
        assignment, _ = self.create()
        native = FakeNative()
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            parallel_launch.plan_status(self.request(assignment=assignment), self.caller())
        self.assertEqual(caught.exception.kind, 'invalid_request')
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_start(self.start_input(assignment, request={'assignment': assignment}), native)
        self.assertEqual(caught.exception.kind, 'invalid_request')

    def test_a_worker_caller_is_refused_for_status_and_interrupt(self):
        assignment, _ = self.create()
        native = FakeNative()
        worker_caller = self.caller(location=assignment['worker_root'])
        for call in (lambda: parallel_launch.plan_status({'assignment': assignment},
                                                         worker_caller),
                     lambda: parallel_launch.plan_interrupt({'assignment': assignment},
                                                            worker_caller)):
            with self.assertRaises(parallel_launch.LaunchError) as caught:
                call()
            self.assertEqual(caught.exception.kind, 'caller_is_worker')
        self.assertEqual(native.calls, [])

    def test_an_unknown_assignment_has_no_status(self):
        assignment, _ = self.create()
        native = FakeNative()
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertFalse(status['known'])
        self.assertFalse(status['success'])

    def test_a_running_session_is_not_completion(self):
        assignment, native, envelope = self.started()
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertTrue(status['known'])
        self.assertTrue(status['present'])
        self.assertIsNone(status['outcome'])
        self.assertIn(status['state'], ('running', 'idle'))
        self.assertFalse(status['success'])
        self.assertEqual(status['session_id'], envelope['session_id'])

    def test_idle_without_a_result_is_not_success(self):
        assignment, native, envelope = self.started()
        native.sessions[envelope['session_id']]['time']['idle'] = 2
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertEqual(status['state'], 'idle')
        self.assertFalse(status['success'])
        self.assertEqual(status['result_error'], 'missing')

    def test_native_success_without_a_result_is_not_success(self):
        assignment, native, envelope = self.started()
        native.complete(envelope['session_id'], 'succeeded')
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertEqual(status['state'], 'awaiting_result')
        self.assertFalse(status['success'])
        self.assertEqual(status['result_error'], 'missing')

    def test_a_valid_result_recovers_without_any_event(self):
        assignment, native, envelope = self.started()
        native.complete(envelope['session_id'], 'succeeded')
        self.write_result(assignment)
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertEqual(status['state'], 'completed')
        self.assertTrue(status['success'])
        self.assertIsNone(status['result_error'])
        self.assertEqual(status['result']['status'], 'succeeded')
        self.assertEqual(status['result']['task'], TASK)

    def test_a_malformed_result_is_not_success(self):
        assignment, native, envelope = self.started()
        native.complete(envelope['session_id'], 'succeeded')
        Path(assignment['artifact_root']).mkdir(parents=True, exist_ok=True)
        (Path(assignment['artifact_root']) / 'result.json').write_text('{nope', encoding='utf-8')
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertFalse(status['success'])
        self.assertEqual(status['result_error'], 'malformed')

    def test_a_result_with_the_wrong_identity_is_not_success(self):
        assignment, native, envelope = self.started()
        native.complete(envelope['session_id'], 'succeeded')
        self.write_result(assignment, value=self.result(assignment, attempt=2))
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertFalse(status['success'])
        self.assertEqual(status['result_error'], 'identity_mismatch')

    def test_a_result_escaping_the_root_is_not_success(self):
        assignment, native, envelope = self.started()
        native.complete(envelope['session_id'], 'succeeded')
        self.write_result(assignment, value=self.result(assignment, changed_files=['../evil']))
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertFalse(status['success'])
        self.assertEqual(status['result_error'], 'identity_mismatch')

    def test_an_interruption_wins_over_a_contradictory_success_result(self):
        assignment, native, envelope = self.started()
        self.write_result(assignment)
        native.interrupt(envelope['session_id'])
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertEqual(status['state'], 'interrupted')
        self.assertEqual(status['outcome'], 'interrupted')
        self.assertFalse(status['success'])
        self.assertTrue(status['interrupted_over_result'])
        self.assertIsNotNone(status['result'])

    def test_a_wrong_session_object_is_refused(self):
        assignment, native, envelope = self.started()
        wrong = dict(native.sessions[envelope['session_id']], id='ses_other')
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            parallel_launch.interpret_status({'assignment': assignment}, wrong, [])
        self.assertEqual(caught.exception.kind, 'wrong_session')

    def test_a_vanished_session_reports_missing_not_adopted(self):
        assignment, native, envelope = self.started()
        journal = self.read_journal(assignment)
        journal['session_id'] = 'ses_someone_else'
        self.journal_path(assignment).write_text(json.dumps(journal), encoding='utf-8')
        status = ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertTrue(status['known'])
        self.assertFalse(status['present'])
        self.assertEqual(status['state'], 'missing')
        self.assertFalse(status['success'])

    def test_a_session_that_moved_root_is_refused(self):
        assignment, native, envelope = self.started()
        native.sessions[envelope['session_id']]['location'] = {'directory': str(self.coord)}
        with self.assertRaises(parallel_launch.LaunchError) as caught:
            ts_status({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertEqual(caught.exception.kind, 'wrong_root')

    def test_status_reads_no_event_stream(self):
        source = Path(parallel_launch.__file__).read_text(encoding='utf-8')
        for token in ('session.execution', 'session.created', 'api/event', 'subscribe'):
            self.assertNotIn(token, source, token)


class TestInterrupt(AdapterCase):
    def test_interrupt_reports_the_durable_outcome(self):
        assignment, _ = self.create()
        native = FakeNative()
        ts_start(self.start_input(assignment), native)
        envelope = ts_interrupt({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertTrue(envelope['interrupted'])
        self.assertEqual(envelope['state'], 'interrupted')
        self.assertFalse(envelope['success'])
        self.assertEqual(self.read_journal(assignment)['state'], 'interrupted')
        self.assertEqual(native.kinds()[-2:], ['interrupt', 'get'])

    def test_a_second_interrupt_is_a_no_op(self):
        assignment, _ = self.create()
        native = FakeNative()
        ts_start(self.start_input(assignment), native)
        ts_interrupt({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        interrupts = native.kinds().count('interrupt')
        envelope = ts_interrupt({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertTrue(envelope['already_interrupted'])
        self.assertEqual(native.kinds().count('interrupt'), interrupts)

    def test_interrupting_an_unknown_assignment_changes_nothing(self):
        assignment, _ = self.create()
        native = FakeNative()
        envelope = ts_interrupt({'request': {'assignment': assignment}, 'caller': self.caller()}, native)
        self.assertFalse(envelope['known'])
        self.assertEqual(native.calls, [])


class TestAdapterDocs(unittest.TestCase):
    def test_the_contract_documents_the_adapter_tools(self):
        text = flat('docs/opencode-parallel.md')
        for phrase in ('`workerCapabilities`', '`startWorker`', '`workerStatus`',
                       '`interruptWorker`', 'launch journal', 'never schedules',
                       'not a second orchestrator'):
            self.assertIn(phrase, text, phrase)


@unittest.skipUnless(shutil.which('bash'), 'the installer is a bash script')
class TestInstallerPlacesTheAdapter(unittest.TestCase):
    """The bridge imports `./parallel`, so an install that copies only artel.ts leaves the
    adapter tools unregistered (and the capability preflight refusing parallel mode)."""

    def test_the_installer_places_the_adapter_beside_the_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'))
            proc = subprocess.run(['bash', str(INSTALLER)], capture_output=True, text=True,
                                  env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plugins = home / 'config' / 'opencode' / 'plugins'
            self.assertTrue((plugins / 'artel.ts').is_file())
            self.assertTrue((plugins / 'parallel.ts').is_file())
            manifest = (home / 'config' / 'opencode' / '.artel-install-manifest').read_text(
                encoding='utf-8')
            self.assertIn('plugins/artel.ts', manifest)
            self.assertIn('plugins/parallel.ts', manifest)
            installed = home / 'config' / 'opencode' / 'artel' / 'scripts' / 'parallel_launch.py'
            self.assertTrue(installed.is_file(),
                            'the adapter policy half must ship with the plugin copy')


BUNDLE = ROOT / '.artel' / 'run' / 'repro' / 'opencode-parallel-proof'
PROBE = BUNDLE / 'bin' / 'probe_gap.py'


@unittest.skipUnless(PROBE.is_file(), 'the Task 1 scratch bundle is not present')
class TestProbeGapPredicate(unittest.TestCase):
    """The corrected missed-event predicate of the scratch probe: identity-based replay
    detection (original `created`/`durable.seq`), never receive time; a missing identity is
    inconclusive; a legitimate later completion does not fail the check."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import warnings
        spec = importlib.util.spec_from_file_location('probe_gap_task7', str(PROBE))
        module = importlib.util.module_from_spec(spec)
        sys.modules['probe_gap_task7'] = module
        with warnings.catch_warnings():
            # oclib.py (Task 1 scratch instrumentation, retained verbatim) leaks one file
            # handle at import; that is not this suite's contract.
            warnings.simplefilter('ignore', ResourceWarning)
            spec.loader.exec_module(module)
        cls.probe = module

    def test_an_old_event_with_a_fresh_receive_time_is_a_replay(self):
        after = [{'recv': 1792038798.5, 'event': {
            'type': 'session.execution.succeeded', 'created': 1792038784686,
            'data': {'sessionID': 'ses_proof'},
            'durable': {'aggregateID': 'ses_proof', 'seq': 12, 'version': 1}}}]
        verdict = self.probe.classify_replay(
            after_events=after, session_id='ses_proof', gap_end=1792038797.130879,
            gap_end_ms=1792038797130.879, max_disconnected_seq=12,
            after_reconnect_turns=1)
        self.assertFalse(verdict['no_replay'])
        self.assertFalse(verdict['inconclusive'])

    def test_a_legitimate_later_completion_is_not_a_replay(self):
        after = [{'recv': 1792038799.276, 'event': {
            'type': 'session.execution.succeeded', 'created': 1792038799272,
            'data': {'sessionID': 'ses_proof'},
            'durable': {'aggregateID': 'ses_proof', 'seq': 21, 'version': 1}}}]
        verdict = self.probe.classify_replay(
            after_events=after, session_id='ses_proof', gap_end=1792038797.130879,
            gap_end_ms=1792038797130.879, max_disconnected_seq=12,
            after_reconnect_turns=1)
        self.assertTrue(verdict['no_replay'])
        self.assertFalse(verdict['inconclusive'])
        self.assertEqual(verdict['legitimate_successes'], 1)

    def test_a_missing_identity_is_inconclusive_not_a_pass(self):
        after = [{'recv': 1792038798.5, 'event': {
            'type': 'session.execution.succeeded',
            'data': {'sessionID': 'ses_proof'}}}]
        verdict = self.probe.classify_replay(
            after_events=after, session_id='ses_proof', gap_end=1792038797.130879,
            gap_end_ms=1792038797130.879, max_disconnected_seq=12,
            after_reconnect_turns=1)
        self.assertFalse(verdict['no_replay'])
        self.assertTrue(verdict['inconclusive'])

    def test_the_retained_capture_classifies_as_no_replay(self):
        gap = json.loads((BUNDLE / 'phase2_gap.json').read_text(encoding='utf-8'))
        window = gap['gap_window']
        after = [json.loads(line) for line in
                 (BUNDLE / 'gap-after.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        global_events = [json.loads(line) for line in
                         (BUNDLE / 'sse-all.jsonl').read_text(encoding='utf-8').splitlines()
                         if line.strip()]
        sid = 'ses_ec22a7227ffe3U2j2alqC3zP36'
        max_seq = self.probe.max_disconnected_seq(global_events, sid, window['end'] * 1000)
        self.assertEqual(max_seq, 12)
        verdict = self.probe.classify_replay(
            after_events=after, session_id=sid, gap_end=window['end'],
            gap_end_ms=window['end'] * 1000, max_disconnected_seq=max_seq,
            after_reconnect_turns=1)
        self.assertTrue(verdict['no_replay'], verdict)
        self.assertFalse(verdict['inconclusive'])
        self.assertEqual(verdict['legitimate_successes'], 1)


if __name__ == '__main__':
    unittest.main()
