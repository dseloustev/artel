"""`scripts/deep_review.py`: the deterministic boundary around the whole-branch review.

The deep review is one reviewer dispatch, and the orchestrator never judges its
output: `prepare` pins the reviewed branch, HEAD, base tip and merge base as SHAs
in a manifest, and `check` validates the reviewer's machine report and
`deep-review.md` against that pin -- identities, anchors, findings headings, the
`## Proposed fixes` task blocks and the completion line -- before any fix is
offered. This suite pins the boundary with real temporary repositories and
hand-derived fixtures.

Contract: skills/deep-review/SKILL.md, agents/reviewer.md.
"""
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'deep_review.py'

MANIFEST_REL = 'specs/PROJ-1/deep-review-manifest.json'
MACHINE_REL = 'specs/PROJ-1/review/machine-report.json'
REPORT_REL = 'specs/PROJ-1/deep-review.md'

TASK_DR001 = (
    '- [ ] **Task 1 (Critical): guard the changed line**\n'
    '  - Reject empty input at the changed line.\n'
    '  - Finding: DR-001\n'
    '  - Acceptance criteria:\n'
    '    - the line rejects empty input\n'
)

TASK_DR001_WARNING = (
    '- [ ] **Task 1 (Warning): guard the changed line**\n'
    '  - Reject empty input at the changed line.\n'
    '  - Finding: DR-001\n'
    '  - Acceptance criteria:\n'
    '    - the line rejects empty input\n'
)

FENCED_EXAMPLE = (
    '\n'
    '```markdown\n'
    '**Review run:** 00000000-0000-0000-0000-000000000000\n'
    '## Proposed fixes\n'
    '### Tasks (Critical)\n'
    '- [ ] **Task 9 (Critical): an example**\n'
    '  - Finding: DR-001\n'
    '```\n'
)


def finding(fid, kind='defect', scope='diff', priority='Critical', file='src/app.py',
            line=10, evidence='the changed line trusts input',
            recommendation='reject empty input'):
    item = {'id': fid, 'kind': kind, 'scope': scope, 'priority': priority, 'file': file,
            'evidence': evidence, 'recommendation': recommendation}
    if line is not None:
        item['line'] = line
    return item


class RepoCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'commit.gpgsign', 'false')
        (self.root / 'src').mkdir()
        (self.root / 'docs').mkdir()
        self.app = self.root / 'src' / 'app.py'
        self.app.write_text(''.join('line{}\n'.format(n) for n in range(1, 21)),
                            encoding='utf-8')
        (self.root / 'src' / 'other.py').write_text(
            ''.join('other{}\n'.format(n) for n in range(1, 6)), encoding='utf-8')
        (self.root / 'src' / 'gone.py').write_text('doomed\n', encoding='utf-8')
        (self.root / 'src' / 'legacy.py').write_text('legacy\n', encoding='utf-8')
        self.guide = self.root / 'docs' / 'guide.md'
        self.guide.write_text('# Guide\n\nold text\n', encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.base_sha = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('update-ref', 'refs/remotes/origin/main', self.base_sha)
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        self.git('checkout', '-q', '-b', 'feat')
        self.app.write_text(
            self.app.read_text(encoding='utf-8').replace('line10\n', 'line10 changed\n'),
            encoding='utf-8')
        self.guide.write_text('# Guide\n\nnew text\n', encoding='utf-8')
        (self.root / 'src' / 'gone.py').unlink()
        self.git('mv', 'src/legacy.py', 'src/renamed.py')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'feature')
        self.head_sha = self.git('rev-parse', 'HEAD').stdout.strip()
        self.specs = self.root / 'specs' / 'PROJ-1'
        self.specs.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True)

    def cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, input=stdin)

    def prepare(self, *extra, ticket='PROJ-1', specs='specs', output=MANIFEST_REL):
        return self.cli('prepare', '--ticket', ticket, '--specs-dir', specs,
                        '--output', output, *extra)

    def manifest(self):
        return json.loads((self.root / MANIFEST_REL).read_text(encoding='utf-8'))

    def git_state(self):
        return (
            self.git('rev-parse', 'HEAD').stdout,
            self.git('for-each-ref').stdout,
            self.git('diff').stdout,
            self.git('diff', '--cached').stdout,
            self.git('stash', 'list').stdout,
        )


class TestPrepare(RepoCase):

    def test_writes_a_pinned_manifest(self):
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual(manifest['ticket'], 'PROJ-1')
        self.assertEqual(manifest['branch'], 'feat')
        self.assertEqual(manifest['specs_dir'], 'specs')
        self.assertEqual(manifest['base'], self.base_sha)
        self.assertEqual(manifest['head'], self.head_sha)
        self.assertEqual(manifest['merge_base'], self.base_sha)
        self.assertEqual(str(uuid.UUID(manifest['run_id'])), manifest['run_id'])

    def test_run_ids_are_fresh_per_prepare(self):
        self.assertEqual(self.prepare().returncode, 0)
        first = self.manifest()['run_id']
        self.assertEqual(self.prepare().returncode, 0)
        self.assertNotEqual(first, self.manifest()['run_id'])

    def test_origin_head_wins_over_a_moved_local_main(self):
        tree = self.git('rev-parse', 'HEAD^{tree}').stdout.strip()
        moved = self.git('commit-tree', tree, '-p', self.base_sha, '-m', 'moved on').stdout.strip()
        self.git('update-ref', 'refs/heads/main', moved)
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.manifest()['base'], self.base_sha)

    def test_falls_back_to_local_main_without_origin_head(self):
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.manifest()['base'], self.base_sha)

    def test_ambiguous_fallback_refs_fail(self):
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        tree = self.git('rev-parse', 'HEAD^{tree}').stdout.strip()
        other = self.git('commit-tree', tree, '-p', self.base_sha, '-m', 'other base').stdout.strip()
        self.git('update-ref', 'refs/remotes/origin/master', other)
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('ambiguous', proc.stderr)

    def test_missing_base_refs_fail(self):
        self.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.git('branch', '-D', 'main')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--base', proc.stderr)

    def test_explicit_branch_must_be_the_checked_out_one(self):
        self.git('branch', 'other', self.head_sha)
        proc = self.prepare('--branch', 'other')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('worktree', proc.stderr)

    def test_explicit_current_branch_is_accepted(self):
        for branch in ('feat', 'refs/heads/feat'):
            with self.subTest(branch=branch):
                proc = self.prepare('--branch', branch)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_rename_into_the_specs_dir_is_still_a_source_change(self):
        self.git('mv', 'src/other.py', 'specs/PROJ-1/other.py')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('src/other.py', proc.stderr)

    def test_explicit_base_is_pinned(self):
        proc = self.prepare('--base', 'main')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.manifest()['base'], self.base_sha)

    def test_base_that_does_not_resolve_is_rejected(self):
        proc = self.prepare('--base', 'no-such-ref')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--base', proc.stderr)

    def test_detached_head_is_rejected(self):
        self.git('checkout', '-q', '--detach')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('detached', proc.stderr)

    def test_empty_ticket_is_rejected(self):
        proc = self.prepare(ticket='  ')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--ticket', proc.stderr)

    def test_unstaged_source_change_is_rejected(self):
        self.app.write_text(self.app.read_text(encoding='utf-8') + 'extra\n', encoding='utf-8')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('src/app.py', proc.stderr)

    def test_staged_source_change_is_rejected(self):
        (self.root / 'src' / 'new.py').write_text('new\n', encoding='utf-8')
        self.git('add', 'src/new.py')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('src/new.py', proc.stderr)

    def test_untracked_source_change_is_rejected(self):
        (self.root / 'src' / 'new.py').write_text('new\n', encoding='utf-8')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('src/new.py', proc.stderr)

    def test_artifacts_under_specs_and_artel_are_allowed(self):
        (self.specs / 'tasklist.md').write_text('# Tasklist\n', encoding='utf-8')
        (self.root / '.artel').mkdir()
        (self.root / '.artel' / 'run-state.json').write_text('{}\n', encoding='utf-8')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_bad_specs_dirs_are_rejected(self):
        for specs in ('.', str(self.root), '../outside', 'src/../..'):
            with self.subTest(specs=specs):
                proc = self.prepare(specs=specs)
                self.assertEqual(proc.returncode, 2)
                self.assertIn('--specs-dir', proc.stderr)

    def test_output_inside_the_checkout_outside_artifacts_is_rejected(self):
        proc = self.prepare(output='src/manifest.json')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--output', proc.stderr)

    def test_output_may_live_outside_the_checkout(self):
        with tempfile.TemporaryDirectory() as other:
            out = Path(other) / 'manifest.json'
            proc = self.prepare(output=str(out))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.exists())

    def test_prepare_does_not_mutate_git_state(self):
        before = self.git_state()
        self.assertEqual(self.prepare().returncode, 0)
        self.assertEqual(self.git_state(), before)


class PreparedCase(RepoCase):

    def setUp(self):
        super().setUp()
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.manifest_data = self.manifest()
        self.run_id = self.manifest_data['run_id']

    def write_machine(self, findings, **overrides):
        report = {
            'schema': 1,
            'ticket': 'PROJ-1',
            'run_id': self.run_id,
            'base': self.base_sha,
            'head': self.head_sha,
            'completeness': 'complete',
            'gaps': [],
            'verification': [{'kind': 'command', 'command': 'python3 -m unittest',
                              'result': 'ok'}],
            'findings': findings,
        }
        report.update(overrides)
        path = self.root / MACHINE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report), encoding='utf-8')
        return MACHINE_REL

    def write_report(self, text):
        path = self.root / REPORT_REL
        path.write_text(text, encoding='utf-8')
        return REPORT_REL

    def check(self, findings=MACHINE_REL, report=REPORT_REL, summary=None, report_path=None,
              stdin=None):
        if summary is None:
            summary = self.summary()
        args = ['check', '--target', MANIFEST_REL, '--findings', findings,
                '--report', report, '--summary', summary]
        if report_path is not None:
            args += ['--report-path', report_path]
        return self.cli(*args, stdin=stdin)

    def summary(self, critical=0, warning=0, suggestion=0, path=REPORT_REL):
        return 'Deep review: {} Critical, {} Warning, {} Suggestion — {}'.format(
            critical, warning, suggestion, path)

    def report_text(self, **overrides):
        values = {
            'run_id': self.run_id,
            'base': self.base_sha,
            'head': self.head_sha,
            'completeness': 'complete',
            'critical': 'none',
            'warnings': 'none',
            'suggestions': 'none',
            'pre_existing': 'none',
            'audits': 'none',
            'tasks_critical': '- none',
            'tasks_warning': '- none',
        }
        values.update(overrides)
        return (
            '---\n'
            'type: deep-review\n'
            'ticket: PROJ-1\n'
            'produced_by: artel:reviewer\n'
            '---\n'
            '\n'
            '# Deep review — PROJ-1\n'
            '\n'
            '**Review run:** {run_id}\n'
            '**Reviewed base:** {base}\n'
            '**Reviewed head:** {head}\n'
            '**Review completeness:** {completeness}\n'
            '\n'
            '## Critical Issues (must fix)\n'
            '{critical}\n'
            '\n'
            '## Warnings (should fix)\n'
            '{warnings}\n'
            '\n'
            '## Suggestions (consider improving)\n'
            '{suggestions}\n'
            '\n'
            '## Pre-existing issues (out of diff)\n'
            '{pre_existing}\n'
            '\n'
            '## Accepted audits\n'
            '{audits}\n'
            '\n'
            '## Proposed fixes\n'
            '\n'
            '### Tasks (Critical)\n'
            '{tasks_critical}\n'
            '\n'
            '### Tasks (Warning)\n'
            '{tasks_warning}\n'
        ).format(**values)

    def envelope(self, proc):
        self.assertTrue(proc.stdout.strip(), 'check printed no envelope: {}'.format(proc.stderr))
        return json.loads(proc.stdout)

    def canonical_findings(self):
        return [
            finding('DR-001', priority='Critical', file='src/app.py', line=10),
            finding('DR-002', priority='Suggestion', file='docs/guide.md', line=3),
            finding('DR-003', kind='audit', priority='Warning', file='src/app.py', line=10),
            finding('DR-004', scope='repository', priority='Warning', file='src/other.py', line=3),
        ]

    def canonical_report(self, **overrides):
        values = {
            'critical': '### DR-001 (Critical): trusts the changed line',
            'suggestions': '### DR-002 (Suggestion): stale guide text',
            'pre_existing': '### DR-004 (Warning): old bug in other.py',
            'audits': '### DR-003 (audit): reviewed the input surface',
            'tasks_critical': TASK_DR001,
        }
        values.update(overrides)
        return self.report_text(**values)


class TestCheck(PreparedCase):

    def test_complete_review_validates(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        envelope = self.envelope(proc)
        self.assertTrue(envelope['ok'])
        data = envelope['data']
        self.assertEqual(data['counts'], {'Critical': 1, 'Warning': 0, 'Suggestion': 1})
        self.assertEqual(data['tasks'], {'Critical': 1, 'Warning': 0})
        self.assertEqual(data['findings'],
                         {'by_kind': {'defect': 3, 'audit': 1},
                          'by_scope': {'diff': 3, 'repository': 1}})
        self.assertEqual(data['completeness'], 'complete')
        self.assertEqual(data['gaps'], [])
        self.assertEqual(data['report'], REPORT_REL)
        self.assertEqual(data['verification'], 1)

    def test_clean_report_validates(self):
        self.write_machine([])
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.envelope(proc)['data']['counts'],
                         {'Critical': 0, 'Warning': 0, 'Suggestion': 0})

    def test_a_clean_report_without_findings_sections_validates(self):
        self.write_machine([])
        text = (
            '---\ntype: deep-review\nticket: PROJ-1\nproduced_by: artel:reviewer\n---\n\n'
            '**Review run:** {}\n**Reviewed base:** {}\n**Reviewed head:** {}\n'
            '**Review completeness:** complete\n\n'
            '## Proposed fixes\n\n### Tasks (Critical)\n- none\n\n### Tasks (Warning)\n- none\n'
        ).format(self.run_id, self.base_sha, self.head_sha)
        self.write_report(text)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_summary_count_mismatch_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        proc = self.check(summary=self.summary(critical=2, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        envelope = self.envelope(proc)
        self.assertFalse(envelope['ok'])
        self.assertTrue(any('Critical' in error for error in envelope['errors']))

    def test_malformed_summary_is_rejected(self):
        self.write_machine([])
        self.write_report(self.report_text())
        proc = self.check(summary='Deep review: some counts')
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('summary line does not match' in error
                            for error in self.envelope(proc)['errors']))

    def test_wrong_target_identity_is_rejected(self):
        self.write_report(self.canonical_report())
        for key, value in (('ticket', 'OTHER-1'), ('run_id', str(uuid.uuid4())),
                           ('base', self.head_sha), ('head', self.base_sha)):
            with self.subTest(key=key):
                self.write_machine(self.canonical_findings(), **{key: value})
                proc = self.check(summary=self.summary(critical=1, suggestion=1))
                self.assertEqual(proc.returncode, 1)
                self.assertTrue(any(key in error for error in self.envelope(proc)['errors']))

    def test_moved_head_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        self.app.write_text(self.app.read_text(encoding='utf-8') + 'later\n', encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'later')
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('HEAD' in error for error in self.envelope(proc)['errors']))

    def test_changed_branch_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        self.git('checkout', '-q', '-b', 'other')
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('branch' in error for error in self.envelope(proc)['errors']))

    def test_changed_source_checkout_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        self.app.write_text(self.app.read_text(encoding='utf-8') + 'dirty\n', encoding='utf-8')
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('pending changes' in error for error in self.envelope(proc)['errors']))

    def test_untracked_source_file_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        (self.root / 'src' / 'extra.py').write_text('extra\n', encoding='utf-8')
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('src/extra.py' in error for error in self.envelope(proc)['errors']))

    def test_guessed_line_is_rejected(self):
        self.write_machine([finding('DR-002', priority='Suggestion', file='src/app.py', line=5)])
        self.write_report(self.report_text(
            suggestions='### DR-002 (Suggestion): guessed anchor'))
        proc = self.check(summary=self.summary(suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('hunk' in error for error in self.envelope(proc)['errors']))

    def test_deleted_file_level_observation_is_accepted(self):
        self.write_machine([finding('DR-002', priority='Suggestion', file='src/gone.py',
                                    line=None)])
        self.write_report(self.report_text(
            suggestions='### DR-002 (Suggestion): the file is gone'))
        proc = self.check(summary=self.summary(suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_deleted_file_with_a_line_is_rejected(self):
        self.write_machine([finding('DR-002', priority='Suggestion', file='src/gone.py',
                                    line=1)])
        self.write_report(self.report_text(
            suggestions='### DR-002 (Suggestion): the file is gone'))
        proc = self.check(summary=self.summary(suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('src/gone.py' in error
                            for error in self.envelope(proc)['errors']))

    def test_file_level_observation_on_the_old_path_of_a_rename_is_accepted(self):
        self.write_machine([finding('DR-002', priority='Suggestion', file='src/legacy.py',
                                    line=None)])
        self.write_report(self.report_text(
            suggestions='### DR-002 (Suggestion): the old name is gone'))
        proc = self.check(summary=self.summary(suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_file_level_observation_on_the_new_path_of_a_rename_is_accepted(self):
        self.write_machine([finding('DR-002', priority='Suggestion', file='src/renamed.py',
                                    line=None)])
        self.write_report(self.report_text(
            suggestions='### DR-002 (Suggestion): the new name is here'))
        proc = self.check(summary=self.summary(suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_line_anchor_on_the_old_path_of_a_rename_is_rejected(self):
        self.write_machine([finding('DR-002', priority='Suggestion', file='src/legacy.py',
                                    line=1)])
        self.write_report(self.report_text(
            suggestions='### DR-002 (Suggestion): guessed anchor'))
        proc = self.check(summary=self.summary(suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('src/legacy.py' in error
                            for error in self.envelope(proc)['errors']))

    def test_audit_and_repository_findings_are_excluded_from_counts(self):
        self.write_machine([
            finding('DR-003', kind='audit', priority='Critical', file='src/app.py', line=10),
            finding('DR-004', scope='repository', priority='Critical', file='src/other.py',
                    line=3),
        ])
        self.write_report(self.report_text(
            pre_existing='### DR-004 (Critical): old bug',
            audits='### DR-003 (audit): reviewed'))
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = self.envelope(proc)['data']
        self.assertEqual(data['counts'], {'Critical': 0, 'Warning': 0, 'Suggestion': 0})
        self.assertEqual(data['tasks'], {'Critical': 0, 'Warning': 0})

    def test_partial_review_is_rejected_even_with_zero_counts(self):
        self.write_machine([], completeness='partial', gaps=['runtime evidence not gathered'])
        self.write_report(self.report_text(completeness='partial'))
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        envelope = self.envelope(proc)
        self.assertFalse(envelope['ok'])
        self.assertTrue(any('partial' in error for error in envelope['errors']))
        self.assertEqual(envelope['data']['completeness'], 'partial')
        self.assertEqual(envelope['data']['gaps'], ['runtime evidence not gathered'])

    def test_partial_without_gaps_is_rejected(self):
        self.write_machine([], completeness='partial', gaps=[])
        self.write_report(self.report_text(completeness='partial'))
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('must list its gaps' in error
                            for error in self.envelope(proc)['errors']))

    def test_complete_review_with_gaps_is_rejected(self):
        self.write_machine([], gaps=['something'])
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('must not list gaps' in error
                            for error in self.envelope(proc)['errors']))

    def test_invalid_machine_json_is_exit_two(self):
        path = self.root / MACHINE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{not json', encoding='utf-8')
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('JSON', proc.stderr)
        self.assertFalse(self.envelope(proc)['ok'])

    def test_missing_machine_report_is_exit_two(self):
        self.write_report(self.report_text())
        proc = self.check(findings='specs/PROJ-1/review/absent.json')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('cannot read the machine report', proc.stderr)

    def test_unresolvable_target_is_exit_two(self):
        manifest = self.manifest()
        manifest['head'] = '0' * 40
        (self.root / MANIFEST_REL).write_text(json.dumps(manifest), encoding='utf-8')
        self.write_machine([])
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(any('does not resolve to a commit' in error
                            for error in self.envelope(proc)['errors']))

    def test_wrong_manifest_schema_is_exit_two(self):
        manifest = self.manifest()
        manifest['schema'] = 2
        (self.root / MANIFEST_REL).write_text(json.dumps(manifest), encoding='utf-8')
        self.write_machine([])
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(any('schema must be 1' in error
                            for error in self.envelope(proc)['errors']))

    def test_invalid_finding_types_are_rejected(self):
        cases = (
            (finding('DR-001', line=True), 'line must be a positive integer'),
            (finding('DR-001', file='/etc/passwd'), 'safe repo-relative path'),
            (finding('DR-001', file='../escape.py'), 'safe repo-relative path'),
            (finding('DR-001', evidence='   '), 'evidence must be a non-empty string'),
            (finding('DR-001', priority='High'), 'priority must be one of'),
            (finding('DR-001', kind='nit'), 'kind must be one of'),
            (dict(finding('DR-001'), id='DR-1'), 'id must be DR-NNN'),
        )
        self.write_report(self.report_text())
        for item, fragment in cases:
            with self.subTest(fragment=fragment):
                self.write_machine([item])
                proc = self.check()
                self.assertEqual(proc.returncode, 1)
                errors = self.envelope(proc)['errors']
                self.assertTrue(any(fragment in error for error in errors),
                                'no error matched {!r}: {}'.format(fragment, errors))

    def test_findings_not_a_list_is_rejected(self):
        self.write_machine([])
        (self.root / MACHINE_REL).write_text(
            json.dumps(dict(self.machine_payload(), findings='nope')), encoding='utf-8')
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('findings must be a list' in error
                            for error in self.envelope(proc)['errors']))

    def machine_payload(self):
        return {
            'schema': 1, 'ticket': 'PROJ-1', 'run_id': self.run_id, 'base': self.base_sha,
            'head': self.head_sha, 'completeness': 'complete', 'gaps': [],
            'verification': [{'kind': 'evidence', 'evidence': 'read the diff'}],
        }

    def test_verification_confidence_is_rejected(self):
        self.write_machine([], verification=[
            {'kind': 'evidence', 'evidence': 'read the diff', 'confidence': 0.9}])
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('confidence' in error for error in self.envelope(proc)['errors']))

    def test_empty_verification_is_rejected(self):
        self.write_machine([], verification=[])
        self.write_report(self.report_text())
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('must record at least one' in error
                            for error in self.envelope(proc)['errors']))

    def test_duplicate_machine_ids_are_rejected(self):
        self.write_machine([finding('DR-001'), finding('DR-001', file='docs/guide.md', line=3)])
        self.write_report(self.canonical_report())
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any("'DR-001' is a duplicate" in error
                            for error in self.envelope(proc)['errors']))

    def test_wrong_report_header_is_rejected(self):
        cases = {
            'type': self.canonical_report().replace('type: deep-review', 'type: review'),
            'produced_by': self.canonical_report().replace(
                'produced_by: artel:reviewer\n', ''),
            'ticket': self.canonical_report().replace('ticket: PROJ-1', 'ticket: OTHER-1'),
        }
        self.write_machine(self.canonical_findings())
        for key, text in cases.items():
            with self.subTest(key=key):
                self.write_report(text)
                proc = self.check(summary=self.summary(critical=1, suggestion=1))
                self.assertEqual(proc.returncode, 1)
                self.assertTrue(any(key in error for error in self.envelope(proc)['errors']))

    def test_wrong_review_run_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(run_id=str(uuid.uuid4())))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('Review run' in error for error in self.envelope(proc)['errors']))

    def test_duplicate_metadata_is_rejected(self):
        self.write_machine(self.canonical_findings())
        text = self.canonical_report().replace(
            '**Reviewed head:** {}\n'.format(self.head_sha),
            '**Reviewed head:** {}\n**Reviewed head:** {}\n'.format(self.head_sha, self.head_sha))
        self.write_report(text)
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('Reviewed head' in error for error in self.envelope(proc)['errors']))

    def test_missing_metadata_is_rejected(self):
        self.write_machine(self.canonical_findings())
        text = self.canonical_report().replace(
            '**Reviewed head:** {}\n'.format(self.head_sha), '')
        self.write_report(text)
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('Reviewed head' in error for error in self.envelope(proc)['errors']))

    def test_finding_without_a_heading_is_rejected(self):
        self.write_machine([finding('DR-001')])
        self.write_report(self.report_text())
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-001' in error for error in self.envelope(proc)['errors']))

    def test_heading_for_an_unknown_finding_is_rejected(self):
        self.write_machine([finding('DR-001')])
        self.write_report(self.report_text(
            critical='### DR-001 (Critical): real',
            suggestions='### DR-999 (Suggestion): ghost'))
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-999' in error for error in self.envelope(proc)['errors']))

    def test_heading_priority_mismatch_is_rejected(self):
        self.write_machine([finding('DR-001')])
        self.write_report(self.report_text(
            critical='### DR-001 (Warning): wrong priority'))
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-001' in error for error in self.envelope(proc)['errors']))

    def test_audit_heading_in_a_defect_section_is_rejected(self):
        self.write_machine([finding('DR-003', kind='audit', priority='Warning')])
        self.write_report(self.report_text(
            critical='### DR-003 (audit): misplaced audit'))
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-003' in error for error in self.envelope(proc)['errors']))

    def test_repository_heading_in_a_defect_section_is_rejected(self):
        self.write_machine([finding('DR-004', scope='repository', file='src/other.py', line=3)])
        self.write_report(self.report_text(
            critical='### DR-004 (Critical): misplaced repository finding'))
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-004' in error for error in self.envelope(proc)['errors']))

    def test_duplicate_task_block_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical=TASK_DR001 + '\n### Tasks (Critical)\n- none\n'))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('Tasks (Critical)' in error for error in self.envelope(proc)['errors']))

    def test_missing_task_block_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report().replace('### Tasks (Warning)\n- none\n', ''))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('Tasks (Warning)' in error for error in self.envelope(proc)['errors']))

    def test_checked_task_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical=TASK_DR001.replace('- [ ]', '- [x]')))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('has a checked task' in error
                            for error in self.envelope(proc)['errors']))

    def test_malformed_task_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical=TASK_DR001.replace('**Task 1 (Critical): guard the changed line**',
                                              '**Task one (Critical): guard the changed line**')))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('malformed' in error for error in self.envelope(proc)['errors']))

    def test_task_without_a_finding_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical=TASK_DR001.replace('  - Finding: DR-001\n', '')))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('must carry exactly one Finding' in error
                            for error in self.envelope(proc)['errors']))

    def test_duplicate_task_for_one_finding_is_rejected(self):
        self.write_machine(self.canonical_findings())
        second = TASK_DR001.replace('**Task 1 (Critical)', '**Task 2 (Critical)')
        self.write_report(self.canonical_report(tasks_critical=TASK_DR001 + second))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-001' in error for error in self.envelope(proc)['errors']))

    def test_task_referencing_a_non_actionable_finding_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical=TASK_DR001.replace('Finding: DR-001', 'Finding: DR-004')))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-004' in error for error in self.envelope(proc)['errors']))

    def test_task_in_the_wrong_priority_block_is_rejected(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical='- none', tasks_warning=TASK_DR001))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('DR-001' in error for error in self.envelope(proc)['errors']))

    def test_a_task_title_contradicting_its_warning_finding_is_rejected(self):
        # The finding, its heading and the enclosing block all say Warning; only
        # the copied task title claims Critical. The title's own priority must be
        # validated too.
        self.write_machine([finding('DR-001', priority='Warning')])
        self.write_report(self.report_text(
            warnings='### DR-001 (Warning): trusts the changed line',
            tasks_warning=TASK_DR001_WARNING.replace('**Task 1 (Warning)',
                                                     '**Task 1 (Critical)')))
        proc = self.check(summary=self.summary(warning=1))
        self.assertEqual(proc.returncode, 1)
        errors = self.envelope(proc)['errors']
        self.assertTrue(any('says (Critical) in its title' in error and 'DR-001' in error
                            for error in errors), errors)

    def test_a_task_title_contradicting_its_critical_finding_is_rejected(self):
        self.write_machine([finding('DR-001', priority='Critical')])
        self.write_report(self.report_text(
            critical='### DR-001 (Critical): trusts the changed line',
            tasks_critical=TASK_DR001.replace('**Task 1 (Critical)', '**Task 1 (Warning)')))
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        errors = self.envelope(proc)['errors']
        self.assertTrue(any('says (Warning) in its title' in error and 'DR-001' in error
                            for error in errors), errors)

    def test_fenced_examples_are_ignored(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report() + FENCED_EXAMPLE)
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_checkboxes_outside_the_task_blocks_are_rejected(self):
        hidden = ('- [ ] **Task 1 (Critical): hidden task**\n'
                  '  - Finding: DR-001\n'
                  '  - Acceptance criteria:\n'
                  '    - hidden\n')
        self.write_machine(self.canonical_findings())
        cases = {
            'preamble': self.canonical_report().replace(
                '## Critical Issues (must fix)',
                hidden + '\n## Critical Issues (must fix)'),
            'suggestions': self.canonical_report().replace(
                '### DR-002 (Suggestion): stale guide text',
                '### DR-002 (Suggestion): stale guide text\n\n' + hidden),
            'warnings': self.canonical_report().replace(
                '## Warnings (should fix)',
                '## Warnings (should fix)\n\n' + hidden),
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                self.write_report(text)
                proc = self.check(summary=self.summary(critical=1, suggestion=1))
                self.assertEqual(proc.returncode, 1)
                self.assertTrue(any('outside a ### Tasks block' in error
                                    for error in self.envelope(proc)['errors']))

    def test_a_prefix_but_non_canonical_section_is_rejected(self):
        self.write_machine([finding('DR-001', priority='Warning')])
        text = self.report_text(warnings='### DR-001 (Warning): the changed line',
                                tasks_warning=TASK_DR001_WARNING).replace(
            '## Warnings (should fix)', '## Warningsomething')
        self.write_report(text)
        proc = self.check(summary=self.summary(warning=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('unrecognized section' in error
                            for error in self.envelope(proc)['errors']))

    def test_bare_canonical_section_titles_are_accepted(self):
        self.write_machine([finding('DR-001', priority='Warning')])
        text = self.report_text(warnings='### DR-001 (Warning): the changed line',
                                tasks_warning=TASK_DR001_WARNING)
        for canonical, bare in (('## Critical Issues (must fix)', '## Critical Issues'),
                                ('## Warnings (should fix)', '## Warnings'),
                                ('## Suggestions (consider improving)', '## Suggestions'),
                                ('## Pre-existing issues (out of diff)',
                                 '## Pre-existing issues')):
            text = text.replace(canonical, bare)
        self.write_report(text)
        proc = self.check(summary=self.summary(warning=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_stdin_report_validates(self):
        self.write_machine(self.canonical_findings())
        text = self.canonical_report()
        proc = self.check(report='-', stdin=text, summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.envelope(proc)['data']['report'], REPORT_REL)

    def test_stdin_report_with_a_wrong_summary_path_is_rejected(self):
        self.write_machine(self.canonical_findings())
        proc = self.check(report='-', stdin=self.canonical_report(),
                          summary=self.summary(critical=1, suggestion=1,
                                               path='specs/PROJ-1/other.md'))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('other.md' in error for error in self.envelope(proc)['errors']))

    def test_stdin_report_path_override_matches_an_absolute_summary(self):
        self.write_machine(self.canonical_findings())
        absolute = str(self.root / REPORT_REL)
        proc = self.check(report='-', stdin=self.canonical_report(),
                          report_path=REPORT_REL,
                          summary=self.summary(critical=1, suggestion=1, path=absolute))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_missing_report_file_is_exit_two(self):
        self.write_machine([])
        proc = self.check(report='specs/PROJ-1/absent.md')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('cannot read the report', proc.stderr)

    def test_empty_stdin_report_is_exit_two_empty_input(self):
        # An empty pipe is a failed producer, not a malformed artifact: it is
        # unprocessable input (exit 2), never an exit-1 header error the skill's
        # bounded retry could try to correct.
        self.write_machine([])
        proc = self.check(report='-', stdin='')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('empty_input', proc.stderr)
        envelope = self.envelope(proc)
        self.assertFalse(envelope['ok'])
        self.assertTrue(any('empty_input' in error for error in envelope['errors']))

    def test_whitespace_stdin_report_is_exit_two_empty_input(self):
        self.write_machine([])
        proc = self.check(report='-', stdin=' \n\t\n')
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(any('empty_input' in error
                            for error in self.envelope(proc)['errors']))

    def test_empty_report_file_is_exit_two_empty_input(self):
        self.write_machine([])
        self.write_report('')
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('empty_input', proc.stderr)
        self.assertIn(REPORT_REL, proc.stderr)

    def test_whitespace_report_file_is_exit_two_empty_input(self):
        self.write_machine([])
        self.write_report('  \n\t\n')
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(any('empty_input' in error
                            for error in self.envelope(proc)['errors']))

    def test_a_nonempty_report_missing_its_header_stays_exit_one(self):
        # Only empty input is unprocessable. A nonempty report that lacks the
        # header is a malformed artifact — exit 1, correctable by the reviewer.
        self.write_machine([])
        self.write_report('# A report without a header block\n\nSome prose.\n')
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('no document header block' in error
                            for error in self.envelope(proc)['errors']))

    def test_a_section_after_proposed_fixes_is_rejected(self):
        self.write_machine([])
        self.write_report(self.report_text() + '\n## Trailing notes\nmore\n')
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('Proposed fixes' in error
                            for error in self.envelope(proc)['errors']))

    def test_duplicate_finding_heading_is_rejected(self):
        self.write_machine([finding('DR-001')])
        self.write_report(self.report_text(
            critical='### DR-001 (Critical): one\n\n### DR-001 (Critical): again'))
        proc = self.check(summary=self.summary(critical=1))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(any('more than once' in error
                            for error in self.envelope(proc)['errors']))

    def test_the_behavior_marker_is_accepted(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report(
            tasks_critical=TASK_DR001.replace('**Task 1 (Critical)',
                                              '**Task 1 (Critical, behavior)')))
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_check_does_not_mutate_git_state(self):
        self.write_machine(self.canonical_findings())
        self.write_report(self.canonical_report())
        before = self.git_state() + (self.git('status', '--porcelain').stdout,)
        proc = self.check(summary=self.summary(critical=1, suggestion=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.git_state() + (self.git('status', '--porcelain').stdout,), before)


class TestSubmoduleSourceCleanliness(unittest.TestCase):
    """A real local submodule dependency and the source-cleanliness guard.

    `git diff` honours submodule-ignore preferences (`diff.ignoreSubmodules`, a
    per-submodule `submodule.<name>.ignore`), which can hide a dirty dependency
    from the pending-path inventory. The guard must ask for
    `--ignore-submodules=none` explicitly and never touch the user's Git
    configuration.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.root = tmp / 'super'
        self.dep = tmp / 'dep'
        for repo in (self.dep, self.root):
            repo.mkdir()
            self.git(repo, 'init', '-q', '-b', 'main')
            self.git(repo, 'config', 'user.email', 'test@example.com')
            self.git(repo, 'config', 'user.name', 'Test')
            self.git(repo, 'config', 'commit.gpgsign', 'false')
        (self.dep / 'dep.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.commit(self.dep, 'dep1')
        (self.root / 'root.py').write_text('root\n', encoding='utf-8')
        self.commit(self.root, 'base')
        # A real local submodule; the file protocol is allowed per command only.
        added = self.git(self.root, '-c', 'protocol.file.allow=always',
                         'submodule', 'add', '-q', '../dep', 'vendor/dep')
        self.assertEqual(added.returncode, 0, added.stderr)
        self.commit(self.root, 'add the dependency')
        base = self.rev(self.root)
        self.git(self.root, 'update-ref', 'refs/remotes/origin/main', base)
        self.git(self.root, 'symbolic-ref', 'refs/remotes/origin/HEAD',
                 'refs/remotes/origin/main')
        self.git(self.root, 'checkout', '-q', '-b', 'feat')
        self.dependency = self.root / 'vendor' / 'dep'
        for key, value in (('user.email', 'test@example.com'), ('user.name', 'Test'),
                           ('commit.gpgsign', 'false')):
            self.git(self.dependency, 'config', key, value)
        # A clean, committed gitlink change: the feature branch advances the pin.
        (self.dependency / 'dep.py').write_text('VALUE = 2\n', encoding='utf-8')
        self.commit(self.dependency, 'dep2')
        self.git(self.root, 'add', 'vendor/dep')
        self.commit(self.root, 'advance the dependency')
        (self.root / 'specs' / 'PROJ-1').mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, cwd, *args):
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)

    def commit(self, cwd, message):
        self.git(cwd, 'add', '-A')
        proc = self.git(cwd, 'commit', '-q', '-m', message)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def rev(self, cwd):
        return self.git(cwd, 'rev-parse', 'HEAD').stdout.strip()

    def cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, input=stdin)

    def prepare(self, *extra):
        return self.cli('prepare', '--ticket', 'PROJ-1', '--specs-dir', 'specs',
                        '--output', MANIFEST_REL, *extra)

    def manifest(self):
        return json.loads((self.root / MANIFEST_REL).read_text(encoding='utf-8'))

    def write_artifacts(self, findings=(), suggestions='- none'):
        manifest = self.manifest()
        machine = {
            'schema': 1, 'ticket': 'PROJ-1', 'run_id': manifest['run_id'],
            'base': manifest['base'], 'head': manifest['head'],
            'completeness': 'complete', 'gaps': [],
            'verification': [{'kind': 'evidence', 'evidence': 'read the pinned diff'}],
            'findings': list(findings),
        }
        machine_path = self.root / MACHINE_REL
        machine_path.parent.mkdir(parents=True, exist_ok=True)
        machine_path.write_text(json.dumps(machine), encoding='utf-8')
        report = (
            '---\ntype: deep-review\nticket: PROJ-1\nproduced_by: artel:reviewer\n---\n\n'
            '**Review run:** {}\n**Reviewed base:** {}\n**Reviewed head:** {}\n'
            '**Review completeness:** complete\n\n'
            '## Suggestions (consider improving)\n\n{}\n\n'
            '## Proposed fixes\n\n### Tasks (Critical)\n- none\n\n### Tasks (Warning)\n- none\n'
        ).format(manifest['run_id'], manifest['base'], manifest['head'], suggestions)
        report_path = self.root / REPORT_REL
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding='utf-8')

    def check(self, critical=0, warning=0, suggestion=0):
        summary = 'Deep review: {} Critical, {} Warning, {} Suggestion — {}'.format(
            critical, warning, suggestion, REPORT_REL)
        return self.cli('check', '--target', MANIFEST_REL, '--findings', MACHINE_REL,
                        '--report', REPORT_REL, '--summary', summary)

    def envelope(self, proc):
        self.assertTrue(proc.stdout.strip(), 'check printed no envelope: {}'.format(proc.stderr))
        return json.loads(proc.stdout)

    def dirty_dependency(self):
        (self.dependency / 'dep.py').write_text('VALUE = 999\n', encoding='utf-8')

    def test_a_clean_dependency_checkout_prepares(self):
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_dirty_dependency_is_rejected_without_ignore_settings(self):
        self.dirty_dependency()
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('vendor/dep', proc.stderr)

    def test_a_dirty_dependency_is_rejected_under_a_global_ignore_setting(self):
        self.git(self.root, 'config', 'diff.ignoreSubmodules', 'all')
        self.dirty_dependency()
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('vendor/dep', proc.stderr)

    def test_a_dirty_dependency_is_rejected_under_a_per_submodule_ignore_setting(self):
        self.git(self.root, 'config', 'submodule.vendor/dep.ignore', 'all')
        self.dirty_dependency()
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('vendor/dep', proc.stderr)

    def test_staged_dependency_source_is_rejected_under_a_global_ignore_setting(self):
        self.git(self.root, 'config', 'diff.ignoreSubmodules', 'all')
        self.dirty_dependency()
        self.git(self.dependency, 'add', 'dep.py')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('vendor/dep', proc.stderr)

    def test_untracked_dependency_source_is_rejected_under_a_global_ignore_setting(self):
        self.git(self.root, 'config', 'diff.ignoreSubmodules', 'all')
        (self.dependency / 'junk.txt').write_text('junk\n', encoding='utf-8')
        proc = self.prepare()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('vendor/dep', proc.stderr)

    def test_dependency_drift_after_prepare_fails_the_check(self):
        self.git(self.root, 'config', 'diff.ignoreSubmodules', 'all')
        self.assertEqual(self.prepare().returncode, 0)
        self.write_artifacts()
        clean = self.check()
        self.assertEqual(clean.returncode, 0, clean.stderr + clean.stdout)
        self.dirty_dependency()
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn('vendor/dep', '\n'.join(self.envelope(proc)['errors']))

    def test_dependency_drift_fails_under_a_per_submodule_ignore_setting(self):
        self.git(self.root, 'config', 'submodule.vendor/dep.ignore', 'all')
        self.assertEqual(self.prepare().returncode, 0)
        self.write_artifacts()
        self.dirty_dependency()
        proc = self.check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn('vendor/dep', '\n'.join(self.envelope(proc)['errors']))

    def test_a_clean_committed_gitlink_stays_a_file_level_anchor(self):
        self.git(self.root, 'config', 'diff.ignoreSubmodules', 'all')
        self.assertEqual(self.prepare().returncode, 0)
        self.write_artifacts(
            findings=[{'id': 'DR-001', 'kind': 'defect', 'scope': 'diff',
                       'priority': 'Suggestion', 'file': 'vendor/dep',
                       'evidence': 'the dependency pin advanced',
                       'recommendation': 'review the new pin'}],
            suggestions='### DR-001 (Suggestion): the dependency pin advanced\n\n'
                        'The gitlink advanced; keep the new pin reviewed.')
        proc = self.check(suggestion=1)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)


if __name__ == '__main__':
    unittest.main()
