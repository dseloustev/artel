import hashlib
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
SPECS = Path('specs/.current')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class MigrateCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKartoteka().start()
        self.addCleanup(self.fake.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / '.artel').mkdir()
        (self.repo / '.artel' / 'config.json').write_text(json.dumps({
            'version': 1, 'ticket': {'projectKey': 'AW'}, 'specs': {'dir': str(SPECS)},
            'knowledge': {'adapter': 'kartoteka', 'baseUrl': self.fake.base_url,
                          'project': PROJECT}}), encoding='utf-8')
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True,
                              text=True)

    def local(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return str(rel)

    def commit_all(self):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'seed')

    def decision(self, ticket, **fields):
        run = self.repo / '.artel' / 'run' / ticket
        run.mkdir(parents=True, exist_ok=True)
        body = {'store': 'kartoteka', 'reason': None, 'decided_by': 'test',
                'decided_at': '2026-09-22T10:00:00Z', 'versions': {}, 'pending': []}
        body.update(fields)
        (run / 'spec-store.json').write_text(json.dumps(body), encoding='utf-8')

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo,
                              capture_output=True, text=True)

    def plan(self, *args):
        proc = self.cli('migrate', 'plan', *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return {i['logical']: i for i in json.loads(proc.stdout)['items']}


class TestClassify(MigrateCase):
    def test_absent(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'absent')

    def test_current(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'current')

    def test_stale_when_the_store_moved_on(self):
        self.local(SPECS / 'AW-12/prd.md', 'old')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'old')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'new', author_agent='artel:analyst')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual((item['class'], item['newest_version']), ('stale', 2))

    def test_successor_over_mirror_only_history(self):
        self.local(SPECS / 'AW-12/prd.md', 'edited after the last mirror')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1')  # author_agent None: the mirror
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v2')
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'successor')

    def test_conflict_over_store_mode_writes_carries_a_diff(self):
        self.local(SPECS / 'AW-12/prd.md', 'line\nlocal\n')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'line\nstored\n',
                       author_agent='artel:analyst')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual(item['class'], 'conflict')
        self.assertIn('-stored', item['diff'])
        self.assertIn('+local', item['diff'])

    def test_pending_base_decides_successor_or_conflict(self):
        self.local(SPECS / 'AW-12/plan.md', 'saved during the outage')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'v1', author_agent='artel:planner')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'v2', author_agent='artel:planner')
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/plan.md', 'base_version': 2}])
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/plan.md']['class'], 'successor')
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/plan.md', 'base_version': 1}])
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/plan.md']['class'], 'conflict')

    def test_a_files_decision_is_the_base(self):
        self.local(SPECS / 'AW-12/prd.md', 'worked locally')
        self.local(SPECS / 'AW-12/plan.md', 'new locally')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1', author_agent='artel:analyst')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'someone else', author_agent='x')
        self.decision('AW-12', store='files', reason='r', versions={'prd.md': 1})
        items = self.plan('AW-12')
        self.assertEqual(items['specs/.current/AW-12/prd.md']['class'], 'successor')
        # plan.md was not stored when the run went local, and is now: someone else made it.
        self.assertEqual(items['specs/.current/AW-12/plan.md']['class'], 'conflict')

    def test_a_redacted_newest_version_is_a_conflict(self):
        self.local(SPECS / 'AW-12/prd.md', 'secret')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'secret', redacted=True)
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'conflict')

    def test_the_context_copy_is_a_source_of_the_same_address(self):
        self.local(SPECS / 'AW-12/prd.md', 'same')
        self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'same')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual(len(item['sources']), 2)
        self.assertEqual(item['class'], 'absent')

    def test_differing_local_copies_are_a_conflict(self):
        self.local(SPECS / 'AW-12/prd.md', 'one')
        self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'two')
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'conflict')

    def test_oversized_is_skipped(self):
        self.local(SPECS / 'AW-12/prd.md', 'x' * (1048576 + 1))
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'skipped')

    def test_evidence_and_the_pointer_are_not_candidates(self):
        self.local(SPECS / '.active_ticket', 'AW-12\n')
        self.local(SPECS / 'AW-12/runtime/observation.md', 'RUNTIME_OK')
        self.local('specs/releases/2026.10.md', 'release')
        self.assertEqual(self.plan('AW-12'), {})

    def test_all_finds_every_ticket_directory(self):
        self.local(SPECS / 'AW-12/prd.md', 'a')
        self.local('.artel/context/tickets/AW-13/spec-trail/prd.md', 'b')
        proc = self.cli('migrate', 'plan', '--all')
        self.assertEqual(json.loads(proc.stdout)['tickets'], ['AW-12', 'AW-13'])

    def test_pending_only_restricts_to_pending_paths(self):
        self.local(SPECS / 'AW-12/prd.md', 'a')
        self.local(SPECS / 'AW-12/plan.md', 'b')
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/plan.md', 'base_version': 0}])
        self.assertEqual(list(self.plan('AW-12', '--pending-only')), ['specs/.current/AW-12/plan.md'])

    def test_a_corrupt_pending_field_does_not_crash_the_plan(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.decision('AW-12', pending=5)
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'absent')

    def test_unavailable_exits_5(self):
        self.local(SPECS / 'AW-12/prd.md', 'a')
        self.fake.stop()
        self.assertEqual(self.cli('migrate', 'plan', 'AW-12').returncode, 5)


class TestApply(MigrateCase):
    def apply(self, *args):
        proc = self.cli('migrate', 'apply', *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_absent_is_uploaded_verified_and_becomes_deletable(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'], [{'logical': 'specs/.current/AW-12/prd.md', 'version': 1}])
        stored = self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')
        self.assertEqual((stored['content'], stored['author_agent']), ('P', 'artel:migrate-specs'))
        self.assertEqual(out['deletable'], ['specs/.current/AW-12/prd.md'])

    def test_a_successor_is_uploaded_against_the_newest_version(self):
        self.local(SPECS / 'AW-12/prd.md', 'newer')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1')
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'][0]['version'], 2)
        put = [r for r in self.fake.requests if r[0] == 'POST'][-1]
        self.assertEqual(put[3]['expected_version'], 1)

    def test_stale_and_current_upload_nothing_and_are_deletable(self):
        self.local(SPECS / 'AW-12/prd.md', 'old')
        self.local(SPECS / 'AW-12/plan.md', 'same')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'old')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'new', author_agent='a')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'same')
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'], [])
        self.assertEqual(sorted(out['deletable']),
                         ['specs/.current/AW-12/plan.md', 'specs/.current/AW-12/prd.md'])
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'], 'new')

    def test_an_unresolved_conflict_is_kept(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        out = self.apply('AW-12')
        self.assertEqual((out['uploaded'], out['deletable']), ([], []))
        self.assertEqual(out['kept'][0]['class'], 'conflict')

    def test_keep_local_uploads_and_keep_stored_discards(self):
        self.local(SPECS / 'AW-12/prd.md', 'local prd')
        self.local(SPECS / 'AW-12/plan.md', 'local plan')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored prd', author_agent='a')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'stored plan', author_agent='a')
        out = self.apply('AW-12', '--resolve', 'specs/.current/AW-12/prd.md=keep-local',
                         '--resolve', 'specs/.current/AW-12/plan.md=keep-stored')
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'], 'local prd')
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'plan', 'plan.md')['content'],
                         'stored plan')
        self.assertEqual(sorted(out['deletable']),
                         ['specs/.current/AW-12/plan.md', 'specs/.current/AW-12/prd.md'])

    def test_keep_local_can_name_which_local_copy(self):
        self.local(SPECS / 'AW-12/prd.md', 'tree')
        self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'context')
        self.apply('AW-12', '--resolve', 'specs/.current/AW-12/prd.md=keep-local:'
                   '.artel/context/tickets/AW-12/spec-trail/prd.md')
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'], 'context')

    def test_the_decision_is_flipped_to_kartoteka(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.decision('AW-12', store='files', reason='worked locally', versions={})
        out = self.apply('AW-12')
        self.assertEqual(out['flipped'], ['AW-12'])
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual((decision['store'], decision['decided_by'], decision['versions']),
                         ('kartoteka', 'migrate-specs', {'prd.md': 1}))

    def test_skipped_is_kept_and_never_uploaded(self):
        self.local(SPECS / 'AW-12/prd.md', 'x' * (1048576 + 1))
        out = self.apply('AW-12')
        self.assertEqual((out['uploaded'], out['kept'][0]['class']), ([], 'skipped'))

    def test_an_unresolved_conflict_keeps_the_previous_decision_and_does_not_flip(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        self.decision('AW-12', store='files', reason='worked locally')
        out = self.apply('AW-12')
        self.assertEqual(out['flipped'], [])
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual(decision['store'], 'files')

    def test_pending_only_apply_does_not_flip_while_other_docs_remain(self):
        self.local(SPECS / 'AW-12/prd.md', 'pending doc')
        self.local(SPECS / 'AW-12/plan.md', 'not pending yet')
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/prd.md', 'base_version': 0}])
        out = self.apply('AW-12', '--pending-only')
        self.assertEqual(out['uploaded'], [{'logical': 'specs/.current/AW-12/prd.md', 'version': 1}])
        self.assertEqual(out['flipped'], [])

    def test_keep_local_source_outside_the_trail_is_rejected(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        outside = self.local('elsewhere/not-a-copy.md', 'not a copy')
        proc = self.cli('migrate', 'apply', 'AW-12',
                        '--resolve', 'specs/.current/AW-12/prd.md=keep-local:' + outside)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)['error']['kind'], 'invalid_argument')
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'], 'stored')

    def test_malformed_resolve_value_is_rejected(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        proc = self.cli('migrate', 'apply', 'AW-12', '--resolve', 'foo=bogus')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)['error']['kind'], 'invalid_argument')
