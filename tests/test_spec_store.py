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
