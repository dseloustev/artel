import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fake_kartoteka import FakeKartoteka

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'spec_store.py'
PROJECT = 'adguard-wallet'


class StoreCase(unittest.TestCase):
    """A host repo whose config points at a fresh FakeKartoteka."""

    knowledge_extra = {}

    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / '.artel').mkdir()
        knowledge = {'adapter': 'kartoteka', 'baseUrl': self.fake.base_url, 'project': PROJECT}
        knowledge.update(self.knowledge_extra)
        self.config = {'version': 1, 'ticket': {'projectKey': 'AW'},
                       'specs': {'dir': 'specs/.current'}, 'knowledge': knowledge}
        self.write_config()

    def write_config(self):
        (self.repo / '.artel' / 'config.json').write_text(json.dumps(self.config), encoding='utf-8')

    def run_cli(self, *args, stdin=None, env=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo, input=stdin,
                              capture_output=True, text=True, env=full_env)

    def error_of(self, proc):
        return json.loads(proc.stderr)['error']


class TestGet(StoreCase):
    def test_prints_the_document_verbatim(self):
        body = '# Plan\n\n```\ncode\n```\n'
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', body)
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual((proc.returncode, proc.stdout), (0, body))

    def test_phase_path_addresses_the_phase_artifact(self):
        self.fake.seed(PROJECT, 'AW-12', 'tasklist', 'phase-2.tasks.md', '- [ ] a\n')
        proc = self.run_cli('get', 'specs/.current/AW-12-2/../AW-12/phase-2/tasks.md')
        # A non-canonical path is not a spec address; the canonical one is:
        self.assertEqual(proc.returncode, 2)
        proc = self.run_cli('get', 'specs/.current/AW-12/phase-2/tasks.md')
        self.assertEqual((proc.returncode, proc.stdout), (0, '- [ ] a\n'))

    def test_a_pinned_version(self):
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'one')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'two')
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md', '--version', '1')
        self.assertEqual(proc.stdout, 'one')

    def test_absent_exits_3(self):
        self.assertEqual(self.run_cli('get', 'specs/.current/AW-12/plan.md').returncode, 3)

    def test_not_a_spec_document_exits_2(self):
        proc = self.run_cli('get', 'specs/.current/AW-12/runtime/observation.md')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.error_of(proc)['kind'], 'not_a_spec_document')

    def test_workspace_off_is_an_error_not_absence(self):
        self.fake.mode = 'workspace_off'
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.error_of(proc)['kind'], 'store_off')


class TestExistsListVersions(StoreCase):
    def test_exists(self):
        self.assertEqual(self.run_cli('exists', 'specs/.current/AW-12/prd.md').returncode, 3)
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'x')
        self.assertEqual(self.run_cli('exists', 'specs/.current/AW-12/prd.md').returncode, 0)

    def test_list_is_scoped_to_project_and_ticket(self):
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'x')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'y')
        self.fake.seed(PROJECT, 'AW-13', 'prd', 'prd.md', 'z')
        self.fake.seed('vpn', 'AW-12', 'plan', 'plan.md', 'z')
        proc = self.run_cli('list', 'AW-12-2')
        self.assertEqual(proc.returncode, 0)
        rows = json.loads(proc.stdout)
        self.assertEqual([(r['name'], r['stage'], r['version'], r['redacted']) for r in rows],
                         [('prd.md', 'prd', 2, False)])

    def test_versions_newest_first_with_hashes(self):
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'one', author_agent=None)
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'two', author_agent='artel:planner')
        rows = json.loads(self.run_cli('versions', 'specs/.current/AW-12/plan.md').stdout)
        self.assertEqual([r['version'] for r in rows], [2, 1])
        self.assertEqual(rows[0]['author_agent'], 'artel:planner')
        self.assertEqual(len(rows[1]['content_hash']), 64)

    def test_versions_of_nothing_exits_3(self):
        self.assertEqual(self.run_cli('versions', 'specs/.current/AW-12/plan.md').returncode, 3)


class TestPut(StoreCase):
    def test_put_creates_and_reports_the_version(self):
        proc = self.run_cli('put', 'specs/.current/AW-12/prd.md', '--expected-version', '0',
                            '--author', 'artel:migrate-specs', stdin='# PRD\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['version'], 1)
        stored = self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')
        self.assertEqual((stored['content'], stored['author_agent']),
                         ('# PRD\n', 'artel:migrate-specs'))

    def test_a_stale_expected_version_exits_4_with_the_current_version(self):
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'x')
        proc = self.run_cli('put', 'specs/.current/AW-12/prd.md', '--expected-version', '0',
                            stdin='y')
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(json.loads(proc.stdout), {'current_version': 1})


class TestTokenHandling(StoreCase):
    knowledge_extra = {'tokenEnv': 'ARTEL_TEST_TOKEN'}

    def test_the_token_is_sent_and_never_printed(self):
        self.fake.mode = 'unauthorized'
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md',
                            env={'ARTEL_TEST_TOKEN': 'ktk_secret'})
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.error_of(proc)['kind'], 'unauthorized')
        self.assertNotIn('ktk_secret', proc.stdout + proc.stderr)
        self.assertEqual(self.fake.requests[-1][4], 'Bearer ktk_secret')

    def test_plaintext_off_loopback_with_a_token_sends_nothing(self):
        self.config['knowledge']['baseUrl'] = 'http://kartoteka.example.com'
        self.write_config()
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md',
                            env={'ARTEL_TEST_TOKEN': 'ktk_secret'})
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.error_of(proc)['kind'], 'misconfigured')
        self.assertNotIn('ktk_secret', proc.stderr)


class TestConfigErrors(StoreCase):
    def test_adapter_off(self):
        self.config['knowledge'] = {'adapter': 'none'}
        self.write_config()
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'adapter_off'))

    def test_unreachable(self):
        self.fake.stop()
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'unreachable'))


class TestDecide(StoreCase):
    def decide(self, *extra):
        proc = self.run_cli('decide', 'AW-12-1', '--decided-by', 'feature-development', *extra)
        return proc.returncode, json.loads(proc.stdout)

    def stored(self):
        path = self.repo / '.artel' / 'run' / 'AW-12' / 'spec-store.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def test_available_writes_a_kartoteka_decision_with_versions(self):
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'a')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'b')
        code, out = self.decide()
        self.assertEqual(code, 0)
        self.assertEqual((out['store'], out['ticket'], out['versions']),
                         ('kartoteka', 'AW-12', {'prd.md': 2}))
        self.assertEqual(self.stored()['decided_by'], 'feature-development')
        # The probe wrote nothing.
        self.assertNotIn((PROJECT, 'AW-12', 'artel-probe', 'probe.md'), self.fake.artifacts)

    def test_available_reports_the_local_trail(self):
        (self.repo / 'specs' / '.current' / 'AW-12').mkdir(parents=True)
        (self.repo / 'specs' / '.current' / 'AW-12' / 'plan.md').write_text('x', encoding='utf-8')
        _, out = self.decide()
        self.assertEqual(out['local_trail'], ['specs/.current/AW-12/plan.md'])

    def test_adapter_none_is_files_and_writes_nothing(self):
        self.config['knowledge'] = {'adapter': 'none'}
        self.write_config()
        code, out = self.decide()
        self.assertEqual((code, out), (0, {'store': 'files', 'reason': None, 'written': False}))
        self.assertIsNone(self.stored())

    def test_local_flag_writes_a_files_decision(self):
        code, out = self.decide('--local')
        self.assertEqual((code, out['store'], out['reason']),
                         (0, 'files', 'local-only run requested'))
        self.assertEqual(self.stored()['store'], 'files')

    def test_files_reason_carries_versions_and_pending_forward(self):
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'a')
        self.decide()
        self.fake.stop()
        code, out = self.decide('--files', 'kartoteka unavailable; working locally at the '
                                           "user's request -- kartoteka is unreachable")
        self.assertEqual((code, out['store'], out['versions']), (0, 'files', {'prd.md': 1}))

    def test_one_character_project_key_is_files(self):
        self.config['ticket']['projectKey'] = 'X'
        self.write_config()
        proc = self.run_cli('decide', 'X-12', '--decided-by', 'dev')
        out = json.loads(proc.stdout)
        self.assertEqual((proc.returncode, out['store']), (0, 'files'))
        self.assertIn('two or more characters', out['reason'])

    def assertUnavailable(self, fragment):
        code, out = self.decide()
        self.assertEqual((code, out['store']), (5, None))
        self.assertIn(fragment, out['reason'])
        self.assertIsNone(self.stored())

    def test_unreachable(self):
        self.fake.stop()
        self.assertUnavailable('kartoteka is unreachable')

    def test_old_daemon(self):
        self.fake.mode = 'old'
        self.assertUnavailable('predates artifact_patch (0.43.0)')

    def test_workspace_off(self):
        self.fake.mode = 'workspace_off'
        self.assertUnavailable('[workspace] enabled = false')

    def test_unregistered_project(self):
        self.fake.mode = 'unregistered'
        self.assertUnavailable('kartoteka project add ' + PROJECT)

    def test_unauthorized(self):
        self.fake.mode = 'unauthorized'
        self.assertUnavailable('HTTP 401')

    def test_missing_project_is_unavailable_naming_the_key(self):
        self.config['knowledge']['project'] = ''
        self.write_config()
        self.assertUnavailable('knowledge.project')


class TestDecisionAndPending(StoreCase):
    def test_decision_reads_back_with_freshness(self):
        self.assertEqual(self.run_cli('decision', 'AW-12').returncode, 3)
        self.run_cli('decide', 'AW-12', '--decided-by', 'dev')
        proc = self.run_cli('decision', 'AW-12-3')
        out = json.loads(proc.stdout)
        self.assertEqual((proc.returncode, out['store'], out['fresh']), (0, 'kartoteka', True))

    def test_pending_add_records_path_and_base(self):
        self.run_cli('decide', 'AW-12', '--decided-by', 'feature-development')
        proc = self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '2')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(self.run_cli('decision', 'AW-12').stdout)
        self.assertEqual(out['pending'], [{'path': 'specs/.current/AW-12/plan.md',
                                           'base_version': 2}])
        # Idempotent: the same path is recorded once, with the latest base.
        self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '3')
        out = json.loads(self.run_cli('decision', 'AW-12').stdout)
        self.assertEqual(out['pending'], [{'path': 'specs/.current/AW-12/plan.md',
                                           'base_version': 3}])

    def test_pending_survives_a_new_decide(self):
        self.run_cli('decide', 'AW-12', '--decided-by', 'feature-development')
        self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '2')
        self.run_cli('decide', 'AW-12', '--decided-by', 'feature-development')
        out = json.loads(self.run_cli('decision', 'AW-12').stdout)
        self.assertEqual(len(out['pending']), 1)

    def test_pending_without_a_decision_is_an_error(self):
        proc = self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '2')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'no_decision'))
