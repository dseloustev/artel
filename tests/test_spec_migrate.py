import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fake_kartoteka import FakeKartoteka

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import spec_store  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'spec_store.py'
PROJECT = 'adguard-wallet'
SPECS = Path('specs/.current')
ATTACHMENTS_MISSING = 'the kartoteka daemon predates attachments (0.44.0); upgrade it'


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

    def items(self, *args):
        proc = self.cli('migrate', 'plan', *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)['items']

    def plan(self, *args):
        """{logical: item} -- for addresses with one distinct local copy."""
        return {i['logical']: i for i in self.items(*args)}

    def by_source(self, *args):
        """{source: its item} -- every local copy, for addresses whose copies differ."""
        return {s: i for i in self.items(*args) for s in i['sources']}


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

    def test_a_redacted_newest_version_is_a_conflict_without_a_diff(self):
        self.local(SPECS / 'AW-12/prd.md', 'SECRET token=abc\n')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'SECRET token=abc\n', redacted=True)
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual(item['class'], 'conflict')
        self.assertIsNone(item['diff'])  # never print a copy that may hold the removed text

    def test_a_redaction_blocks_an_automatic_upload(self):
        # C2: a redacted version's hash is the marker's, so the removed text never reads
        # stale; over mirror-only history it used to be a successor and was re-uploaded.
        self.local(SPECS / 'AW-12/prd.md', 'SECRET token=abc')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'SECRET token=abc', redacted=True)
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'cleaned')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual(item['class'], 'conflict')
        self.assertEqual(item['reason'], 'kartoteka redacted v1 of this document; this copy may '
                                         'carry the removed content — review it before choosing')
        self.assertIsNone(item['diff'])
        proc = self.cli('migrate', 'apply', 'AW-12')
        self.assertEqual(json.loads(proc.stdout)['uploaded'], [])
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'], 'cleaned')

    def test_stale_matching_ignores_redacted_versions(self):
        # The marker's own hash must never read as "kartoteka holds this copy".
        self.local(SPECS / 'AW-12/prd.md', '[redacted]')
        self.local(SPECS / 'AW-12/plan.md', 'old')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'secret', redacted=True)
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'cleaned')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'old')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'newer', author_agent='a')
        items = self.plan('AW-12')
        self.assertEqual(items['specs/.current/AW-12/prd.md']['class'], 'conflict')
        # a copy kartoteka does hold, in a live older version, is still stale
        self.assertEqual(items['specs/.current/AW-12/plan.md']['class'], 'stale')

    def test_a_known_base_still_uploads_over_an_older_redaction(self):
        self.local(SPECS / 'AW-12/plan.md', 'saved during the outage')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'secret', redacted=True)
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'cleaned', author_agent='artel:planner')
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/plan.md', 'base_version': 2}])
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/plan.md']['class'], 'successor')

    def test_the_context_copy_is_a_source_of_the_same_address(self):
        self.local(SPECS / 'AW-12/prd.md', 'same')
        self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'same')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual(len(item['sources']), 2)
        self.assertEqual(item['class'], 'absent')

    def test_differing_local_copies_are_each_a_conflict(self):
        tree = self.local(SPECS / 'AW-12/prd.md', 'line\ntree\n')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'line\ncontext\n')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'line\nstored\n', author_agent='a')
        items = self.items('AW-12')
        self.assertEqual([(i['sources'], i['class']) for i in items],
                         [([tree], 'conflict'), ([context], 'conflict')])
        for item in items:
            self.assertTrue(item['reason'].startswith('the local copies differ'), item['reason'])
        tree_diff = items[0]['diff']
        self.assertIn('-context', tree_diff)   # local against local
        self.assertIn('+tree', tree_diff)
        self.assertIn('-stored', tree_diff)    # and local against the stored newest
        self.assertEqual((items[0]['sha256'], items[1]['sha256']),
                         (sha('line\ntree\n'), sha('line\ncontext\n')))

    def test_one_unread_copy_is_the_only_one_offered(self):
        # One readable copy beside one that was never read (unreadable bytes here,
        # a symlink would do too): nothing established a *difference*, so the
        # reason must not claim "the local copies differ".
        tree = self.local(SPECS / 'AW-12/prd.md', 'line\nlocal\n')
        context = '.artel/context/tickets/AW-12/spec-trail/prd.md'
        path = self.repo / context
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\xff\xfe not valid utf-8')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'line\nstored\n', author_agent='a')
        item = self.by_source('AW-12')[tree]
        self.assertEqual(item['class'], 'conflict')
        self.assertEqual(item['reason'],
                         'another copy of this document was not read: {}'.format(context))
        self.assertIn('-stored', item['diff'])   # local against the stored newest
        self.assertIn('+local', item['diff'])

    def test_a_third_unread_copy_is_named_beside_the_differing_ones(self):
        # candidates() only ever finds two local copies of one address -- the
        # working tree and the .artel/context snapshot are trail_roots' whole
        # "two trail roots" -- so classify() never actually sees three sources
        # for one address from the CLI today. This calls it directly, the way a
        # future third trail root would reach the same branch.
        tree = self.local(SPECS / 'AW-12/prd.md', 'line\ntree\n')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'line\ncontext\n')
        link = str(SPECS / 'AW-12/extra.md')
        os.symlink(self.repo / 'nowhere.md', self.repo / link)
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'line\nstored\n', author_agent='a')
        config = json.loads((self.repo / '.artel' / 'config.json').read_text(encoding='utf-8'))
        logical = str(SPECS / 'AW-12/prd.md')
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            items = spec_store.classify(spec_store.Store(config), config, 'AW-12', logical,
                                        [tree, context, link], {}, {})
        finally:
            os.chdir(cwd)
        by_source = {i['source']: i for i in items}
        for source in (tree, context):
            reason = by_source[source]['reason']
            self.assertTrue(reason.startswith('the local copies differ: '), reason)
            self.assertTrue(reason.endswith('; another copy was not read: {}'.format(link)),
                            reason)
            self.assertEqual(by_source[source]['class'], 'conflict')
        self.assertEqual(by_source[link]['class'], 'skipped')

    def test_each_distinct_copy_is_classified_on_its_own(self):
        tree = self.local(SPECS / 'AW-12/prd.md', 'stored text')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'other text')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored text', author_agent='a')
        items = self.by_source('AW-12')
        self.assertEqual(items[tree]['class'], 'current')
        self.assertEqual(items[context]['class'], 'conflict')
        self.assertIsNot(items[tree], items[context])

    def test_a_context_copy_with_no_known_base_is_a_conflict(self):
        # I1: .artel/context copies are newer-wins snapshots shared across worktrees;
        # the mirror-only rule (which can only lag) is the working tree's alone.
        context = self.local('.artel/context/tickets/AW-12/spec-trail/plan.md', 'old snapshot')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'orig')        # mirror v1
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'newer edit')  # mirror v2
        item = self.plan('AW-12')['specs/.current/AW-12/plan.md']
        self.assertEqual((item['class'], item['sources']), ('conflict', [context]))
        self.assertEqual(item['reason'], 'a saved context copy with no known base; it may be '
                                         'older than what kartoteka holds')
        proc = self.cli('migrate', 'apply', 'AW-12')
        self.assertEqual(json.loads(proc.stdout)['uploaded'], [])
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'plan', 'plan.md')['content'],
                         'newer edit')

    def test_a_working_copy_older_than_the_newest_version_is_a_conflict(self):
        tree = self.local(SPECS / 'AW-12/prd.md', 'edited long ago')
        os.utime(self.repo / tree, (1577880000, 1577880000))  # 2020-01-01, before v2
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v2')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual((item['class'], item['reason']),
                         ('conflict', "this copy is older than kartoteka's v2"))

    def test_an_unreadable_created_at_leaves_the_age_check_out(self):
        self.local(SPECS / 'AW-12/prd.md', 'edited after the last mirror')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v2')['created_at'] = 'whenever'
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'successor')

    def test_a_symlinked_candidate_is_never_read(self):
        # I3: a committed symlink in the trail used to be read and uploaded.
        outside = Path(self._tmp.name).parent / 'outside-the-repo.md'
        outside.write_text('-----BEGIN PRIVATE KEY----- not really', encoding='utf-8')
        self.addCleanup(outside.unlink)
        link = self.repo / SPECS / 'AW-12' / 'prd.md'
        link.parent.mkdir(parents=True)
        os.symlink(outside, link)
        self.commit_all()
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual((item['class'], item['reason']),
                         ('skipped', 'a symbolic link or a path outside the trail; not read'))
        self.assertEqual(json.loads(self.cli('migrate', 'apply', 'AW-12').stdout)['uploaded'], [])
        self.assertEqual(self.fake.artifacts, {})
        self.assertEqual(json.loads(self.cli('migrate', 'delete', 'AW-12').stdout)['removed'], [])
        self.assertTrue(outside.exists())
        self.assertTrue(link.is_symlink())

    def test_a_symlinked_ticket_directory_is_never_read(self):
        outside = Path(self._tmp.name).parent / 'outside-trail-dir'
        (outside).mkdir(exist_ok=True)
        (outside / 'prd.md').write_text('not this ticket\'s', encoding='utf-8')
        self.addCleanup(lambda: [(outside / 'prd.md').unlink(), outside.rmdir()])
        (self.repo / SPECS).mkdir(parents=True)
        os.symlink(outside, self.repo / SPECS / 'AW-12')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual(item['class'], 'skipped')
        self.assertEqual(self.fake.artifacts, {})

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

    def test_an_outage_after_the_probe_exits_5(self):
        self.local(SPECS / 'AW-12/prd.md', 'a')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'a')

        def go_down(method, path, query, body):
            if method == 'GET':
                self.fake.on_request = None
                self.fake.mode = 'unauthorized'
        for verb in ('plan', 'delete'):
            self.fake.mode = 'ok'
            self.fake.on_request = go_down
            proc = self.cli('migrate', verb, 'AW-12')
            self.assertEqual(proc.returncode, 5, (verb, proc.stdout))
            self.assertEqual(json.loads(proc.stderr)['error']['kind'], 'unavailable')
        self.assertTrue((self.repo / SPECS / 'AW-12/prd.md').exists())


class TestFilesBase(MigrateCase):
    """I9: the base a locally edited document was made from outlives `decide`."""

    def stored_decision(self, ticket='AW-12'):
        return json.loads((self.repo / '.artel/run' / ticket / 'spec-store.json').read_text())

    def test_the_files_era_base_survives_a_new_decide(self):
        self.local(SPECS / 'AW-12/prd.md', 'edited while working locally')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1', author_agent='artel:analyst')
        self.decision('AW-12', store='files', reason='kartoteka unavailable; working locally',
                      versions={'prd.md': 1})
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'successor')
        proc = self.cli('decide', 'AW-12', '--decided-by', 'feature-development')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['store'], 'kartoteka')
        self.assertEqual(self.stored_decision()['files_base'], {'prd.md': 1})
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'successor')

    def test_the_frozen_base_beats_a_listing_taken_later(self):
        self.local(SPECS / 'AW-12/prd.md', 'edited from v1')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1', author_agent='artel:analyst')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'teammate v2', author_agent='b')
        self.decision('AW-12', store='files', reason='kept working locally',
                      versions={'prd.md': 2}, files_base={'prd.md': 1})
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual((item['class'], item['base_version']), ('conflict', 1))

    def test_an_existing_files_base_is_carried_on(self):
        self.local(SPECS / 'AW-12/prd.md', 'edited from v1')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1', author_agent='artel:analyst')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'teammate v2', author_agent='b')
        self.decision('AW-12', store='files', reason='kept working locally',
                      versions={'prd.md': 2}, files_base={'prd.md': 1})
        self.cli('decide', 'AW-12', '--decided-by', 'feature-development')
        self.assertEqual(self.stored_decision()['files_base'], {'prd.md': 1})
        self.cli('decide', 'AW-12', '--decided-by', 'dev', '--files', 'kept working locally')
        self.assertEqual(self.stored_decision()['files_base'], {'prd.md': 1})
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'conflict')

    def test_a_corrupt_versions_or_files_base_is_no_base_at_all(self):
        self.local(SPECS / 'AW-12/prd.md', 'x')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1', author_agent='a')
        self.decision('AW-12', store='files', reason='r', versions=['prd.md'],
                      files_base='v1')
        item = self.plan('AW-12')['specs/.current/AW-12/prd.md']
        self.assertEqual((item['class'], item['base_version']), ('conflict', None))

    def test_the_flip_drops_the_base_once_no_copy_rests_on_it(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')
        self.decision('AW-12', store='files', reason='worked locally', versions={'prd.md': 1})
        out = json.loads(self.cli('migrate', 'apply', 'AW-12').stdout)
        self.assertEqual(out['flipped'], ['AW-12'])
        self.assertNotIn('files_base', self.stored_decision())

    def test_the_flip_keeps_the_base_a_kept_stored_copy_still_rests_on(self):
        self.local(SPECS / 'AW-12/prd.md', 'local edit from v1')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1')        # mirror-only history
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v2 from another checkout')
        self.decision('AW-12', store='files', reason='worked locally', versions={'prd.md': 1})
        resolve = ('--resolve', 'specs/.current/AW-12/prd.md=keep-stored')
        out = json.loads(self.cli('migrate', 'apply', 'AW-12', *resolve).stdout)
        self.assertEqual((out['uploaded'], out['flipped']), ([], ['AW-12']))
        self.assertEqual(self.stored_decision()['files_base'], {'prd.md': 1})
        # and the copy the user called obsolete is still a conflict, never a successor
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'conflict')
        self.assertEqual(json.loads(self.cli('migrate', 'apply', 'AW-12').stdout)['uploaded'], [])

    def test_delete_drops_the_base_with_the_last_local_copy(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')
        self.decision('AW-12', store='kartoteka', versions={'prd.md': 1},
                      files_base={'prd.md': 1})
        out = json.loads(self.cli('migrate', 'delete', 'AW-12').stdout)
        self.assertEqual(out['removed'], ['specs/.current/AW-12/prd.md'])
        self.assertNotIn('files_base', self.stored_decision())


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

    def test_a_named_copy_settles_the_address_and_the_other_copy_goes(self):
        # I5: the named copy used to upload but leave the address a conflict forever.
        tree = self.local(SPECS / 'AW-12/prd.md', 'tree')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'context')
        self.decision('AW-12', store='files', reason='worked locally')
        resolve = ('--resolve', 'specs/.current/AW-12/prd.md=keep-local:' + tree)
        out = self.apply('AW-12', *resolve)
        self.assertEqual(out['uploaded'], [{'logical': 'specs/.current/AW-12/prd.md', 'version': 1}])
        self.assertEqual(out['deletable'], sorted([tree, context]))
        self.assertEqual(out['flipped'], ['AW-12'])
        again = self.apply('AW-12', *resolve)
        self.assertEqual(again['uploaded'], [])
        self.assertEqual(len(self.fake.artifacts[(PROJECT, 'AW-12', 'prd', 'prd.md')]), 1)
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'], 'tree')

    def test_a_plain_keep_local_is_refused_when_the_copies_differ(self):
        tree = self.local(SPECS / 'AW-12/prd.md', 'tree')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'context')
        proc = self.cli('migrate', 'apply', 'AW-12',
                        '--resolve', 'specs/.current/AW-12/prd.md=keep-local')
        self.assertEqual(proc.returncode, 2, proc.stderr)
        error = json.loads(proc.stderr)['error']
        self.assertEqual(error['kind'], 'invalid_argument')
        self.assertIn(tree, error['message'])
        self.assertIn(context, error['message'])
        self.assertEqual(self.fake.artifacts, {})

    def test_a_ticket_with_nothing_on_disk_is_never_flipped(self):
        self.local(SPECS / 'AW-12/runtime/observation.md', 'RUNTIME_OK')  # evidence, not a trail
        self.decision('AW-12', store='files', reason='local-only run requested')
        out = self.apply('--all')
        self.assertEqual(out['flipped'], [])
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual(decision['store'], 'files')

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

    def test_apply_prunes_orphan_pending_entries_and_counts_what_is_left(self):
        self.local(SPECS / 'AW-12/prd.md', 'pending doc')
        self.decision('AW-12', pending=[
            {'path': 'specs/.current/AW-12/prd.md', 'base_version': 0},
            {'path': 'specs/.current/AW-12/plan.md', 'base_version': 0}])  # never written
        out = self.apply('AW-12', '--pending-only')
        self.assertEqual(out['pending_left'], {'AW-12': 1})
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual([p['path'] for p in decision['pending']], ['specs/.current/AW-12/prd.md'])

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

    def test_keep_stored_without_a_stored_version_is_refused_before_any_upload(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.local(SPECS / 'AW-12/plan.md', 'never stored either')
        proc = self.cli('migrate', 'apply', 'AW-12',
                        '--resolve', 'specs/.current/AW-12/prd.md=keep-stored')
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn('kartoteka holds no version of it', json.loads(proc.stderr)['error']['message'])
        self.assertEqual(self.fake.artifacts, {})

    def test_a_rejected_upload_does_not_abort_the_run(self):
        # I7: one non-409 rejection used to raise out of apply, exit 2, the rest unreported.
        self.local(SPECS / 'AW-12/adr.md', 'A')
        self.local(SPECS / 'AW-12/prd.md', 'P')

        puts = []

        def reject_every_put_after_the_first(method, path, query, body):
            if method == 'POST':
                puts.append(path)
                if len(puts) > 1:
                    self.fake.forced['POST'] = (400, {'error': 'content is over the '
                                                               'max_artifact_bytes limit'})
        self.fake.on_request = reject_every_put_after_the_first
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'], [{'logical': 'specs/.current/AW-12/adr.md', 'version': 1}])
        self.assertEqual(out['failed'][0]['logical'], 'specs/.current/AW-12/prd.md')
        self.assertIn('kartoteka answered HTTP 400', out['failed'][0]['reason'])
        self.assertEqual(out['deletable'], ['specs/.current/AW-12/adr.md'])

    def test_an_outage_mid_apply_exits_5(self):
        self.local(SPECS / 'AW-12/adr.md', 'A')
        self.local(SPECS / 'AW-12/prd.md', 'P')

        def go_down(method, path, query, body):
            if method == 'POST':
                self.fake.on_request = None
                self.fake.mode = 'unauthorized'
        self.fake.on_request = go_down
        proc = self.cli('migrate', 'apply', 'AW-12')
        self.assertEqual(proc.returncode, 5, proc.stdout)
        error = json.loads(proc.stderr)['error']
        self.assertEqual(error['kind'], 'unavailable')
        self.assertIn('HTTP 401', error['message'])

    def test_an_upload_that_cannot_be_verified_is_reported_and_kept(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')

        def store_something_else(method, path, query, body):
            if method == 'POST':
                self.fake.on_request = None
                body['content'] = 'not what was sent'
        self.fake.on_request = store_something_else
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'], [])
        self.assertEqual(out['failed'], [{'logical': 'specs/.current/AW-12/prd.md', 'reason': (
            'the upload could not be verified; the local copy is kept')}])
        self.assertEqual(out['deletable'], [])
        self.assertTrue((self.repo / SPECS / 'AW-12/prd.md').exists())

    def test_a_version_written_during_the_run_is_reported_not_overwritten(self):
        self.local(SPECS / 'AW-12/prd.md', 'newer')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'v1')  # mirror: a successor upload

        def someone_else_writes(method, path, query, body):
            if method == 'POST':
                self.fake.on_request = None
                self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'teammate v2', author_agent='b')
        self.fake.on_request = someone_else_writes
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'], [])
        self.assertEqual(out['failed'], [{'logical': 'specs/.current/AW-12/prd.md', 'reason': (
            'kartoteka moved to v2 during the migration; run it again')}])
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'],
                         'teammate v2')

    def test_a_crlf_document_uploads_byte_exact(self):
        # I8: apply read with newline translation, so what it verified was not what it sent.
        path = self.repo / SPECS / 'AW-12/prd.md'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'line one\r\nline two\r\n')
        out = self.apply('AW-12')
        self.assertEqual(out['uploaded'], [{'logical': 'specs/.current/AW-12/prd.md', 'version': 1}])
        stored = self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')
        self.assertEqual(stored['content'], 'line one\r\nline two\r\n')
        self.assertEqual(stored['content_hash'], sha('line one\r\nline two\r\n'))
        self.assertEqual(self.plan('AW-12')['specs/.current/AW-12/prd.md']['class'], 'current')

    def test_keep_local_at_the_version_the_user_saw_refuses_a_store_that_moved(self):
        # I2: the answer is tied to the version the conflict was shown against.
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored v1', author_agent='a')
        seen = self.plan('AW-12')['specs/.current/AW-12/prd.md']['newest_version']
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'teammate v2, never shown',
                       author_agent='b')
        out = self.apply('AW-12', '--resolve',
                         'specs/.current/AW-12/prd.md=keep-local@{}'.format(seen))
        self.assertEqual(out['uploaded'], [])
        self.assertEqual(out['failed'], [{'logical': 'specs/.current/AW-12/prd.md', 'reason': (
            'kartoteka moved from v1 to v2 since you decided; run migrate-specs again')}])
        self.assertEqual(self.fake.newest(PROJECT, 'AW-12', 'prd', 'prd.md')['content'],
                         'teammate v2, never shown')

    def test_keep_local_at_the_current_version_uploads(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored v1', author_agent='a')
        out = self.apply('AW-12', '--resolve', 'specs/.current/AW-12/prd.md=keep-local@1')
        self.assertEqual(out['uploaded'], [{'logical': 'specs/.current/AW-12/prd.md', 'version': 2}])

    def test_a_source_path_is_never_read_as_a_version(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        proc = self.cli('migrate', 'apply', 'AW-12', '--resolve',
                        'specs/.current/AW-12/prd.md=keep-local:specs/x@y/prd.md')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('specs/x@y/prd.md', json.loads(proc.stderr)['error']['message'])

    def test_a_version_suffix_is_only_for_keep_local(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        proc = self.cli('migrate', 'apply', 'AW-12',
                        '--resolve', 'specs/.current/AW-12/prd.md=keep-stored@1')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)['error']['kind'], 'invalid_argument')

    def test_malformed_resolve_value_is_rejected(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        proc = self.cli('migrate', 'apply', 'AW-12', '--resolve', 'foo=bogus')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)['error']['kind'], 'invalid_argument')


class TestDelete(MigrateCase):
    def delete(self, *args):
        proc = self.cli('migrate', 'delete', *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_tracked_files_are_git_rm_and_committed_alone(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.local(SPECS / 'AW-12/runtime/observation.md', 'RUNTIME_OK')
        self.local(SPECS / '.active_ticket', 'AW-12\n')
        self.commit_all()
        self.local('unrelated.txt', 'staged work')
        self.git('add', 'unrelated.txt')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')
        out = self.delete('AW-12', '--commit')
        self.assertEqual(out['removed'], ['specs/.current/AW-12/prd.md'])
        self.assertFalse((self.repo / SPECS / 'AW-12/prd.md').exists())
        self.assertTrue((self.repo / SPECS / 'AW-12/runtime/observation.md').exists())
        self.assertTrue((self.repo / SPECS / '.active_ticket').exists())
        log = self.git('log', '-1', '--name-status', '--format=%s').stdout
        self.assertIn('chore: move AW-12 spec trail to kartoteka', log)
        self.assertIn('D\tspecs/.current/AW-12/prd.md', log)
        self.assertNotIn('unrelated.txt', log)
        self.assertIn('A  unrelated.txt', self.git('status', '--porcelain').stdout)

    def test_a_tracked_file_edited_since_the_last_commit_is_removed(self):
        # I6: git rm refused it, though the working-tree content is verified in kartoteka.
        self.local(SPECS / 'AW-12/prd.md', 'committed')
        self.commit_all()
        self.local(SPECS / 'AW-12/prd.md', 'edited after the checkpoint')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'edited after the checkpoint')
        out = self.delete('AW-12', '--commit')
        self.assertEqual((out['removed'], out['kept']), (['specs/.current/AW-12/prd.md'], []))
        self.assertFalse((self.repo / SPECS / 'AW-12/prd.md').exists())
        self.assertIn('D\tspecs/.current/AW-12/prd.md',
                      self.git('log', '-1', '--name-status', '--format=%s').stdout)

    def test_a_tracked_file_with_staged_content_kartoteka_lacks_is_kept(self):
        self.local(SPECS / 'AW-12/prd.md', 'committed')
        self.commit_all()
        self.local(SPECS / 'AW-12/prd.md', 'staged, stored nowhere')
        self.git('add', str(SPECS / 'AW-12/prd.md'))
        self.local(SPECS / 'AW-12/prd.md', 'working tree, verified')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'working tree, verified')
        out = self.delete('AW-12')
        self.assertEqual(out['removed'], [])
        self.assertEqual(out['kept'], [{'logical': 'specs/.current/AW-12/prd.md',
                                        'source': 'specs/.current/AW-12/prd.md', 'class': 'error',
                                        'reason': 'has staged changes kartoteka does not hold; '
                                                  'commit or unstage them first'}])
        self.assertTrue((self.repo / SPECS / 'AW-12/prd.md').exists())

    def test_a_file_that_changed_since_it_was_classified_is_kept(self):
        tree = self.local(SPECS / 'AW-12/prd.md', 'P')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')

        def rewrite(method, path, query, body):
            if method == 'GET' and path.endswith('/versions'):
                self.fake.on_request = None      # after the plan read it, before it is deleted
                (self.repo / tree).write_text('written again meanwhile', encoding='utf-8')
        self.fake.on_request = rewrite
        out = self.delete('AW-12')
        self.assertEqual(out['removed'], [])
        self.assertEqual(out['kept'][0]['reason'],
                         'it changed since it was classified; run migrate-specs again')
        self.assertEqual((self.repo / tree).read_text(), 'written again meanwhile')

    def test_without_commit_the_removal_is_left_staged(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.commit_all()
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')
        out = self.delete('AW-12')
        self.assertIsNone(out['commit'])
        self.assertIn('D  specs/.current/AW-12/prd.md', self.git('status', '--porcelain').stdout)

    def test_untracked_and_context_copies_are_unlinked_and_empty_dirs_pruned(self):
        self.local(SPECS / 'AW-12/phase-2/tasks.md', 'T')
        self.local('.artel/context/tickets/AW-12/spec-trail/phase-2/tasks.md', 'T')
        self.fake.seed(PROJECT, 'AW-12', 'tasklist', 'phase-2.tasks.md', 'T')
        out = self.delete('AW-12')
        self.assertEqual(len(out['removed']), 2)
        self.assertFalse((self.repo / SPECS / 'AW-12').exists())
        self.assertFalse((self.repo / '.artel/context/tickets/AW-12/spec-trail').exists())

    def test_pruning_stops_at_the_trail_root(self):
        self.local('.artel/context/tickets/AW-12/spec-trail/phase-2/tasks.md', 'T')
        self.fake.seed(PROJECT, 'AW-12', 'tasklist', 'phase-2.tasks.md', 'T')
        self.delete('AW-12')
        self.assertFalse((self.repo / '.artel/context/tickets/AW-12/spec-trail').exists())
        self.assertTrue((self.repo / '.artel/context/tickets/AW-12').is_dir())

    def test_nothing_unverified_is_deleted(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')        # conflict
        self.local(SPECS / 'AW-12/plan.md', 'never stored')  # absent
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        out = self.delete('AW-12')
        self.assertEqual(out['removed'], [])
        self.assertEqual({k['class'] for k in out['kept']}, {'conflict', 'absent'})
        self.assertTrue((self.repo / SPECS / 'AW-12/prd.md').exists())

    def test_a_held_copy_is_deleted_while_a_differing_copy_stays(self):
        tree = self.local(SPECS / 'AW-12/prd.md', 'stored text')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'other text')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored text', author_agent='a')
        out = self.delete('AW-12')
        self.assertEqual(out['removed'], [tree])
        self.assertEqual([(k['sources'], k['class']) for k in out['kept']], [([context], 'conflict')])
        self.assertTrue((self.repo / context).exists())

    def test_keep_stored_deletes_the_local_copy(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        out = self.delete('AW-12', '--resolve', 'specs/.current/AW-12/prd.md=keep-stored')
        self.assertEqual(out['removed'], ['specs/.current/AW-12/prd.md'])

    def test_pending_entries_are_pruned(self):
        self.local(SPECS / 'AW-12/plan.md', 'P')
        self.fake.seed(PROJECT, 'AW-12', 'plan', 'plan.md', 'P')
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/plan.md', 'base_version': 0}])
        out = self.delete('AW-12', '--pending-only')
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual(decision['pending'], [])
        self.assertEqual(out['pending_left'], {'AW-12': 0})

    def test_an_orphan_pending_entry_is_pruned(self):
        # I4: a pending entry whose file is gone kept the guard open for that path forever.
        self.decision('AW-12', pending=[{'path': 'specs/.current/AW-12/plan.md', 'base_version': 0}])
        out = self.delete('AW-12', '--pending-only')
        self.assertEqual((out['removed'], out['pending_left']), ([], {'AW-12': 0}))
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual(decision['pending'], [])

    def test_pending_left_counts_the_entries_still_on_disk(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        self.decision('AW-12', pending=[
            {'path': 'specs/.current/AW-12/prd.md', 'base_version': 0},
            {'path': 'specs/.current/AW-12/plan.md', 'base_version': 0}])  # never written
        out = self.delete('AW-12', '--pending-only')
        self.assertEqual(out['pending_left'], {'AW-12': 1})
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual([p['path'] for p in decision['pending']], ['specs/.current/AW-12/prd.md'])

    def test_many_tickets_get_a_counted_subject(self):
        for n in (1, 2, 3, 4):
            self.local(SPECS / 'AW-{}/prd.md'.format(n), 'P')
            self.fake.seed(PROJECT, 'AW-{}'.format(n), 'prd', 'prd.md', 'P')
        self.commit_all()
        self.delete('--all', '--commit')
        self.assertEqual(self.git('log', '-1', '--format=%s').stdout.strip(),
                         "chore: move 4 tickets' spec trails to kartoteka")

    def test_keep_stored_without_a_stored_version_is_refused_and_deletes_nothing(self):
        # C1: nothing stored, two differing copies -- keep-stored used to delete both.
        tree = self.local(SPECS / 'AW-12/prd.md', 'tree copy')
        context = self.local('.artel/context/tickets/AW-12/spec-trail/prd.md', 'context copy')
        proc = self.cli('migrate', 'delete', 'AW-12',
                        '--resolve', 'specs/.current/AW-12/prd.md=keep-stored')
        self.assertEqual(proc.returncode, 2, proc.stderr)
        error = json.loads(proc.stderr)['error']
        self.assertEqual(error['kind'], 'invalid_argument')
        self.assertIn('keep-stored for specs/.current/AW-12/prd.md: kartoteka holds no version '
                      'of it', error['message'])
        self.assertTrue((self.repo / tree).exists())
        self.assertTrue((self.repo / context).exists())

    def test_delete_validates_keep_local_sources_too(self):
        self.local(SPECS / 'AW-12/prd.md', 'local')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'stored', author_agent='a')
        outside = self.local('elsewhere/not-a-copy.md', 'not a copy')
        proc = self.cli('migrate', 'delete', 'AW-12',
                        '--resolve', 'specs/.current/AW-12/prd.md=keep-local:' + outside)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)['error']['kind'], 'invalid_argument')
        self.assertTrue((self.repo / SPECS / 'AW-12/prd.md').exists())

    def test_a_corrupt_pending_field_survives_deletion_unchanged(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.fake.seed(PROJECT, 'AW-12', 'prd', 'prd.md', 'P')
        self.decision('AW-12', pending=5)
        out = self.delete('AW-12')
        self.assertEqual(out['removed'], ['specs/.current/AW-12/prd.md'])
        self.assertEqual(out['pending_left'], {'AW-12': 0})
        decision = json.loads((self.repo / '.artel/run/AW-12/spec-store.json').read_text())
        self.assertEqual(decision['pending'], 5)


class TestAttachmentProbe(MigrateCase):
    def test_a_daemon_before_attachments_stops_every_migrate_verb(self):
        self.local(SPECS / 'AW-12/prd.md', 'P')
        self.fake.mode = 'pre_attachments'
        for verb in ('plan', 'apply', 'delete'):
            proc = self.cli('migrate', verb, 'AW-12')
            self.assertEqual(proc.returncode, 5, (verb, proc.stdout, proc.stderr))
            self.assertEqual(json.loads(proc.stderr)['error'],
                             {'kind': 'unavailable', 'message': ATTACHMENTS_MISSING})
        self.assertEqual(self.fake.artifacts, {})
        self.assertTrue((self.repo / SPECS / 'AW-12/prd.md').exists())
