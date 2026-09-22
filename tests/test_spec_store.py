import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fake_kartoteka import FakeKartoteka

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import kartoteka_http as kh  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'spec_store.py'
PROJECT = 'adguard-wallet'
NOT_SET = 'kartoteka is configured for this project but {} is not set'


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

    def test_a_reader_that_stops_early_is_not_a_failure(self):
        # `set -o pipefail; get <path> | grep -q <pattern>` (docs/spec-storage.md
        # §4.2) must answer grep's match: a document bigger than the pipe buffer
        # is still being written when grep -q exits on its first match.
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'needle\n' + 'x' * 400000 + '\n')
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), 'get', 'specs/.current/AW-12/plan.md'],
            cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.stdout.read(7), b'needle\n')
        proc.stdout.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        self.assertEqual((proc.wait(timeout=30), stderr), (0, b''))

    def test_a_redacted_newest_version_is_an_error_not_the_document(self):
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'secret', redacted=True)
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual((proc.returncode, proc.stdout), (2, ''))
        error = self.error_of(proc)
        self.assertEqual(error['kind'], 'redacted')
        self.assertIn('v1', error['message'])

    def test_a_redacted_pinned_version_is_an_error_too(self):
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'secret', redacted=True)
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'clean')
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md', '--version', '1')
        self.assertEqual((proc.returncode, proc.stdout, self.error_of(proc)['kind']),
                         (2, '', 'redacted'))
        self.assertIn('v1', self.error_of(proc)['message'])
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual((proc.returncode, proc.stdout), (0, 'clean'))

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

    def test_a_document_over_max_bytes_is_refused_and_nothing_is_sent(self):
        proc = self.run_cli('put', 'specs/.current/AW-12/prd.md',
                            stdin='x' * (kh.MAX_BYTES + 1))
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'too_large'))
        self.assertEqual(self.fake.requests, [])

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

    def test_an_unexpected_status_is_the_one_answered_record(self):
        self.fake.forced['GET'] = (500, 'Internal Server Error')
        proc = self.run_cli('get', 'specs/.current/AW-12/plan.md')
        self.assertEqual((proc.returncode, self.error_of(proc)),
                         (2, {'kind': 'rejected',
                              'message': 'kartoteka answered HTTP 500: no error text'}))


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
        self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '0')
        self.fake.stop()
        code, out = self.decide('--files', 'kartoteka unavailable; working locally at the '
                                           "user's request -- kartoteka is unreachable")
        self.assertEqual((code, out['store'], out['versions']), (0, 'files', {'prd.md': 1}))
        self.assertEqual(out['pending'], [{'path': 'specs/.current/AW-12/plan.md',
                                           'base_version': 0}])
        self.assertEqual(self.stored()['pending'], out['pending'])

    def test_a_project_key_outside_kartotekas_grammar_is_files(self):
        # kartoteka's PATCH never checks the key, so the probe alone would call
        # MY_PROJ ready and every later put would be refused.
        for key in ('X', 'MY_PROJ'):
            self.config['ticket']['projectKey'] = key
            self.write_config()
            proc = self.run_cli('decide', key + '-12', '--decided-by', 'dev')
            out = json.loads(proc.stdout)
            self.assertEqual((proc.returncode, out['store']), (0, 'files'), key)
            self.assertEqual(out['reason'], (
                'kartoteka cannot store tickets keyed {}-…: its ticket-key grammar needs a '
                'project key of two or more letters or digits, starting with a letter').format(key))
        self.assertEqual(self.fake.requests, [])

    def test_a_letter_and_a_digit_is_a_storable_key(self):
        self.config['ticket']['projectKey'] = 'A1'
        self.write_config()
        proc = self.run_cli('decide', 'A1-12', '--decided-by', 'dev')
        self.assertEqual((proc.returncode, json.loads(proc.stdout)['store']), (0, 'kartoteka'))

    def assertUnavailable(self, fragment):
        code, out = self.decide()
        self.assertEqual((code, out['store']), (5, None))
        self.assertIn(fragment, out['reason'])
        self.assertIsNone(self.stored())

    def test_unreachable(self):
        self.fake.stop()
        code, out = self.decide()
        self.assertEqual(code, 5)
        prefix = 'kartoteka is unreachable at {}: '.format(self.fake.base_url)
        self.assertTrue(out['reason'].startswith(prefix), out['reason'])
        self.assertNotIn('unreachable', out['reason'][len(prefix):])

    def test_old_daemon(self):
        self.fake.mode = 'old'
        self.assertUnavailable('predates artifact_patch (0.43.0)')

    def test_workspace_off(self):
        self.fake.mode = 'workspace_off'
        self.assertUnavailable('[workspace] enabled = false')

    def test_the_probe_never_writes_even_where_its_address_exists(self):
        # expected_version 0: a missing address answers 404, an existing one
        # 409 -- both "ready", and neither writes.
        self.fake.seed(PROJECT, 'AW-12', 'artel-probe', 'probe.md', 'x')
        code, out = self.decide()
        self.assertEqual((code, out['store']), (0, 'kartoteka'))
        self.assertEqual(len(self.fake.artifacts[(PROJECT, 'AW-12', 'artel-probe', 'probe.md')]), 1)
        probe = next(r for r in self.fake.requests if r[0] == 'PATCH')
        self.assertEqual(probe[3]['expected_version'], 0)

    def test_a_405_on_the_probe_is_a_missing_route(self):
        self.fake.forced['PATCH'] = (405, {'detail': 'Method Not Allowed'})
        self.assertUnavailable('the kartoteka daemon predates artifact_patch (0.43.0); upgrade it')

    def test_a_405_with_no_listing_either_is_a_disabled_store(self):
        self.fake.mode = 'workspace_off'
        self.fake.forced['PATCH'] = (405, 'Method Not Allowed')
        self.assertUnavailable('[workspace] enabled = false')

    def test_a_failing_follow_up_listing_is_not_an_old_daemon(self):
        self.fake.mode = 'old'
        self.fake.forced['GET'] = (500, 'Internal Server Error')
        code, out = self.decide()
        self.assertEqual(code, 5)
        self.assertEqual(out['reason'], 'kartoteka answered HTTP 500: no error text')

    def test_unregistered_project(self):
        self.fake.mode = 'unregistered'
        self.assertUnavailable('kartoteka project add ' + PROJECT)

    def test_unauthorized(self):
        self.fake.mode = 'unauthorized'
        self.assertUnavailable('HTTP 401')

    def assertRecord(self, record, env=None):
        proc = self.run_cli('decide', 'AW-12', '--decided-by', 'dev', env=env)
        self.assertEqual((proc.returncode, json.loads(proc.stdout)['reason']), (5, record))
        self.assertIsNone(self.stored())

    def test_missing_project_is_unavailable_naming_the_key(self):
        self.config['knowledge']['project'] = ''
        self.write_config()
        self.assertRecord(NOT_SET.format('knowledge.project'))

    def test_a_project_outside_its_grammar_is_named_as_not_set(self):
        self.config['knowledge']['project'] = 'AdGuard_Wallet'
        self.write_config()
        self.assertRecord(NOT_SET.format('knowledge.project'))

    def test_missing_base_url_is_unavailable_naming_the_key(self):
        self.config['knowledge']['baseUrl'] = ''
        self.write_config()
        self.assertRecord(NOT_SET.format('knowledge.baseUrl'))

    def test_plaintext_off_loopback_is_its_own_record_not_an_unset_key(self):
        # The message names knowledge.baseUrl; it is not "not set".
        self.config['knowledge'].update(baseUrl='http://kartoteka.example.com',
                                        tokenEnv='ARTEL_TEST_TOKEN')
        self.write_config()
        self.assertRecord(
            'knowledge.baseUrl http://kartoteka.example.com is plaintext http:// off loopback and '
            "a bearer token would cross the network in the clear; use the daemon's https:// "
            'origin', env={'ARTEL_TEST_TOKEN': 'ktk_secret'})

    def test_a_misspelled_adapter_is_its_own_record(self):
        self.config['knowledge']['adapter'] = 'kartoteca'
        self.write_config()
        self.assertRecord('knowledge.adapter must be "none" or "kartoteka", got \'kartoteca\'')

    def test_an_unexpected_probe_answer_is_the_one_answered_record(self):
        self.fake.forced['PATCH'] = (500, 'Internal Server Error')
        self.assertRecord('kartoteka answered HTTP 500: no error text')

    def test_the_listing_after_the_probe_fails_with_the_same_record(self):
        self.fake.forced['GET'] = (500, 'Internal Server Error')
        self.assertRecord('kartoteka answered HTTP 500: no error text')

    def test_an_echoed_request_body_is_cut_to_the_error_body_limit(self):
        # FastAPI's 422 echoes the request, which can be a whole document.
        self.fake.forced['PATCH'] = (422, {'detail': [{'type': 'missing', 'input': 'x' * 5000}]})
        code, out = self.decide()
        prefix = 'kartoteka answered HTTP 422: '
        self.assertEqual(code, 5)
        self.assertTrue(out['reason'].startswith(prefix), out['reason'][:80])
        self.assertEqual(len(out['reason']),
                         len(prefix) + kh.ERROR_BODY_LIMIT + len('...(truncated)'))


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

    def test_pending_add_stores_the_path_normalised_and_the_guard_admits_it(self):
        self.run_cli('decide', 'AW-12', '--decided-by', 'feature-development')
        proc = self.run_cli('pending', 'add', './specs/.current/AW-12/plan.md',
                            '--base-version', '2')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '3')
        out = json.loads(self.run_cli('decision', 'AW-12').stdout)
        self.assertEqual(out['pending'], [{'path': 'specs/.current/AW-12/plan.md',
                                           'base_version': 3}])
        guard = subprocess.run(
            [sys.executable, str(SCRIPT.parent.parent / 'hooks' / 'spec_store_guard.py')],
            cwd=self.repo, capture_output=True, text=True, input=json.dumps({
                'tool_name': 'Write', 'cwd': str(self.repo),
                'tool_input': {'file_path': 'specs/.current/AW-12/plan.md'}}))
        self.assertEqual((guard.returncode, guard.stdout), (0, ''))

    def test_pending_without_a_decision_is_an_error(self):
        proc = self.run_cli('pending', 'add', 'specs/.current/AW-12/plan.md', '--base-version', '2')
        self.assertEqual((proc.returncode, self.error_of(proc)['kind']), (2, 'no_decision'))
