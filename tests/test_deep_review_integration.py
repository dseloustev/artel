"""The deep-review protocol, integrated: the skill's documented commands run against
the real engine on a real repository, and the baseline hand-off scenarios go through
the actual CLI.

Behavioral where it can be -- the fixture builds a tiny repo, `prepare` and `check`
are executed exactly as `skills/artel-deep-review/SKILL.md` documents them (the pinned
target under `.artel/run/`, the machine report beside it, the report in the specs
dir, the kartoteka shape piped with `pipefail`), and the four pressure scenarios of
the hand-off baseline are driven through the engine: a non-current target, a
completion line whose counts disagree with the body, a partial review, and an
accepted audit. Prose pins cover only what has no code path: the step order, the two
dispatch templates' **Spec store:** line, the retry rule, and the single-home
contract doc.

Contract: skills/artel-deep-review/SKILL.md, agents/artel-reviewer.md, docs/deep-review.md,
scripts/deep_review.py.
"""
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
SCRIPT = ROOT / 'scripts' / 'deep_review.py'

SPECS = 'specs/.current'
TICKET = 'PROJ-1'
MANIFEST = '.artel/run/PROJ-1/deep-review/target.json'
MACHINE = '.artel/run/PROJ-1/deep-review/machine.json'
REPORT = 'specs/.current/PROJ-1/deep-review.md'

SKILL = 'skills/artel-deep-review/SKILL.md'
REVIEWER = 'agents/artel-reviewer.md'
CONTRACT = 'docs/deep-review.md'

TASK_DR001 = (
    '- [ ] **Task 1 (Critical): guard the changed line**\n'
    '  - Reject empty input at the changed line.\n'
    '  - Finding: DR-001\n'
    '  - Acceptance criteria:\n'
    '    - the line rejects empty input\n'
)

TASK_DR001_WARNING = (
    '- [ ] **Task 1 (Warning): update the documented timeout**\n'
    '  - Update `README.md:3` to state 60 seconds.\n'
    '  - Finding: DR-001\n'
    '  - Acceptance criteria:\n'
    '    - README.md states 60 seconds and no longer states 30 seconds.\n'
)


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


def finding(fid, kind='defect', scope='diff', priority='Critical', file='config.py',
            line=3, evidence='config.py:3 trusts the changed default',
            recommendation='pin the default'):
    item = {'id': fid, 'kind': kind, 'scope': scope, 'priority': priority, 'file': file,
            'evidence': evidence, 'recommendation': recommendation}
    if line is not None:
        item['line'] = line
    return item


class DeepReviewCase(unittest.TestCase):
    """A tiny repo with one operator-facing default changed on a feature branch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'commit.gpgsign', 'false')
        (self.root / 'config.py').write_text(
            'import os\n\nREQUEST_TIMEOUT_SECONDS = 30\nDEFAULT_RETRIES = 3\n\n\n'
            'def request_timeout():\n    return REQUEST_TIMEOUT_SECONDS\n', encoding='utf-8')
        (self.root / 'README.md').write_text(
            '# Demo\n\nRequests time out after 30 seconds.\n', encoding='utf-8')
        (self.root / 'AGENTS.md').write_text(
            '# AGENTS\n\nChanging an operator-facing default requires an Unreleased CHANGELOG '
            'entry.\n', encoding='utf-8')
        (self.root / 'CHANGELOG.md').write_text(
            '# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n', encoding='utf-8')
        tests = self.root / 'tests'
        tests.mkdir()
        (tests / 'test_config.py').write_text(
            'from config import REQUEST_TIMEOUT_SECONDS\n\n\n'
            'def test_default_timeout():\n    assert REQUEST_TIMEOUT_SECONDS == 30\n',
            encoding='utf-8')
        (self.root / '.artel').mkdir()
        (self.root / '.artel' / 'config.json').write_text(
            json.dumps({'specs': {'dir': SPECS}}) + '\n', encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.base_sha = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('update-ref', 'refs/remotes/origin/main', self.base_sha)
        self.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        self.git('checkout', '-q', '-b', 'feat/timeout')
        (self.root / 'config.py').write_text(
            'import os\n\nREQUEST_TIMEOUT_SECONDS = 60\nDEFAULT_RETRIES = 3\n\n\n'
            'def request_timeout():\n    return REQUEST_TIMEOUT_SECONDS\n', encoding='utf-8')
        (tests / 'test_config.py').write_text(
            'from config import REQUEST_TIMEOUT_SECONDS\n\n\n'
            'def test_default_timeout():\n    assert REQUEST_TIMEOUT_SECONDS == 60\n',
            encoding='utf-8')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'raise the default timeout')
        self.head_sha = self.git('rev-parse', 'HEAD').stdout.strip()
        specs = self.root / SPECS / TICKET
        specs.mkdir(parents=True)
        (specs / 'idea.md').write_text('# Idea\n', encoding='utf-8')
        (self.root / SPECS / '.active_ticket').write_text(TICKET + '\n', encoding='utf-8')

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True)

    def cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, input=stdin)

    def prepare(self, *extra):
        return self.cli('prepare', '--ticket', TICKET, '--specs-dir', SPECS,
                        '--output', MANIFEST, *extra)

    def prepared(self):
        proc = self.prepare()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads((self.root / MANIFEST).read_text(encoding='utf-8'))

    def summary(self, critical=0, warning=0, suggestion=0):
        return 'Deep review: {} Critical, {} Warning, {} Suggestion — {}'.format(
            critical, warning, suggestion, REPORT)

    def write_machine(self, findings, completeness='complete', gaps=(),
                      verification=None):
        report = {
            'schema': 1,
            'ticket': TICKET,
            'run_id': self.manifest['run_id'],
            'base': self.manifest['base'],
            'head': self.manifest['head'],
            'completeness': completeness,
            'gaps': list(gaps),
            'verification': verification if verification is not None else [
                {'kind': 'command', 'command': 'python3 -m unittest tests.test_config',
                 'result': '1 passed'},
                {'kind': 'evidence',
                 'evidence': 'verify.commands not configured — the quality gate is skipped, '
                             'not green'},
            ],
            'findings': findings,
        }
        path = self.root / MACHINE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return MACHINE

    def write_report(self, text):
        path = self.root / REPORT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return REPORT

    def check(self, summary=None):
        return self.cli('check', '--target', MANIFEST, '--findings', MACHINE,
                        '--report', REPORT,
                        '--summary', summary if summary is not None else self.summary())

    def envelope(self, proc):
        self.assertTrue(proc.stdout.strip(), 'check printed no envelope: {}'.format(proc.stderr))
        return json.loads(proc.stdout)

    def errors(self, proc):
        return '\n'.join(self.envelope(proc).get('errors', []))

    def report_text(self, **overrides):
        values = {
            'run_id': self.manifest['run_id'],
            'base': self.manifest['base'],
            'head': self.manifest['head'],
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
            'ticket: {ticket}\n'
            'produced_by: artel:reviewer\n'
            '---\n'
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
        ).format(ticket=TICKET, **values)


class TestDocumentedCommands(DeepReviewCase):
    """The layout and commands the skill documents, executed as documented."""

    def setUp(self):
        super().setUp()
        self.manifest = self.prepared()

    def test_the_documented_layout_pins_the_target_under_artel_run(self):
        self.assertTrue((self.root / MANIFEST).is_file())
        self.assertEqual(self.manifest['ticket'], TICKET)
        self.assertEqual(self.manifest['branch'], 'feat/timeout')
        self.assertEqual(self.manifest['base'], self.base_sha)
        self.assertEqual(self.manifest['head'], self.head_sha)
        self.assertEqual(self.manifest['specs_dir'], SPECS)

    def test_the_documented_check_validates_the_documentation_review(self):
        self.write_machine([
            finding('DR-001', priority='Warning',
                    evidence='config.py:3 now defaults to 60 seconds; README.md:3 still '
                             'documents 30.',
                    recommendation='Update README.md:3 to 60 seconds.'),
        ])
        self.write_report(self.report_text(
            warnings='### DR-001 (Warning): README.md documents the old timeout',
            tasks_warning=TASK_DR001_WARNING))
        proc = self.check(summary=self.summary(warning=1))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = self.envelope(proc)['data']
        self.assertEqual(data['counts'], {'Critical': 0, 'Warning': 1, 'Suggestion': 0})
        self.assertEqual(data['tasks'], {'Critical': 0, 'Warning': 1})
        self.assertEqual(data['findings'],
                         {'by_kind': {'defect': 1, 'audit': 0},
                          'by_scope': {'diff': 1, 'repository': 0}})
        self.assertEqual(data['completeness'], 'complete')
        self.assertEqual(data['report'], REPORT)

    def test_a_documentation_finding_anchored_on_the_stale_doc_is_refused(self):
        # The docs pass anchors on the changed code that makes the statement false;
        # the unchanged README line is evidence, never the anchor.
        self.write_machine([
            finding('DR-001', priority='Warning', file='README.md', line=3,
                    evidence='README.md:3 documents 30 seconds.',
                    recommendation='Update it to 60.'),
        ])
        self.write_report(self.report_text(
            warnings='### DR-001 (Warning): README.md documents the old timeout',
            tasks_warning=TASK_DR001_WARNING))
        proc = self.check(summary=self.summary(warning=1))
        self.assertEqual(proc.returncode, 1)
        self.assertIn('DR-001: README.md:3 [diff] — file is not in the diff',
                      self.errors(proc))

    def test_a_documentation_finding_guessed_outside_the_hunks_is_refused(self):
        # A changed file is not enough: the anchor line must fall in a new-side hunk.
        self.write_machine([
            finding('DR-001', priority='Warning', file='config.py', line=8,
                    evidence='config.py:8 is unchanged.', recommendation='Anchor the cause.'),
        ])
        self.write_report(self.report_text(
            warnings='### DR-001 (Warning): guessed anchor',
            tasks_warning=TASK_DR001_WARNING))
        proc = self.check(summary=self.summary(warning=1))
        self.assertEqual(proc.returncode, 1)
        self.assertIn('not in a new-side hunk', self.errors(proc))

    @unittest.skipUnless(shutil.which('bash'), 'the kartoteka pipe needs a shell')
    def test_the_kartoteka_shape_pipes_the_stored_report_into_the_checker(self):
        self.write_machine([
            finding('DR-001', priority='Warning',
                    evidence='config.py:3 now defaults to 60 seconds; README.md:3 still '
                             'documents 30.',
                    recommendation='Update README.md:3 to 60 seconds.'),
        ])
        report = self.report_text(
            warnings='### DR-001 (Warning): README.md documents the old timeout',
            tasks_warning=TASK_DR001_WARNING)
        command = (
            'set -o pipefail; printf %s "$DOC" | '
            '{} {} check --target {} --findings {} --report - --summary "$SUMMARY"'
        ).format(sys.executable, SCRIPT, MANIFEST, MACHINE)
        env = dict(os.environ)
        env['DOC'] = report
        env['SUMMARY'] = self.summary(warning=1)
        proc = subprocess.run(['bash', '-c', command], cwd=self.root, env=env,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(json.loads(proc.stdout)['ok'])

    @unittest.skipUnless(shutil.which('bash'), 'the kartoteka pipe needs a shell')
    def test_a_failing_store_producer_cannot_enter_the_correction_path(self):
        # A failed `spec_store.py get` is a real producer that exits non-zero with
        # empty stdout. Under `set -o pipefail` the pipeline answers with the
        # checker's own exit: 2 with `empty_input`, never an exit-1
        # malformed-header error the bounded artifact retry would try to correct.
        self.write_machine([])
        command = (
            'set -o pipefail; {} -c "import sys; sys.exit(3)" | '
            '{} {} check --target {} --findings {} --report - --summary "$SUMMARY"'
        ).format(sys.executable, sys.executable, SCRIPT, MANIFEST, MACHINE)
        env = dict(os.environ)
        env['SUMMARY'] = self.summary()
        proc = subprocess.run(['bash', '-c', command], cwd=self.root, env=env,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope['ok'])
        self.assertTrue(any('empty_input' in error for error in envelope['errors']),
                        envelope['errors'])
        self.assertNotIn('no document header block', proc.stdout)


class TestHandOffScenarios(DeepReviewCase):
    """The baseline's hand-off failures, driven through the real engine."""

    def test_a_requested_branch_that_is_not_checked_out_is_refused(self):
        self.git('branch', 'other', self.head_sha)
        proc = self.prepare('--branch', 'other')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('worktree', proc.stderr)
        self.assertFalse((self.root / MANIFEST).exists())  # nothing was pinned

    def test_counts_that_disagree_with_the_body_never_reach_the_offer(self):
        self.manifest = self.prepared()
        self.write_machine([finding('DR-001', priority='Critical')])
        self.write_report(self.report_text(
            critical='### DR-001 (Critical): trusts the changed line',
            tasks_critical=TASK_DR001))
        # The old contract trusted this line and would have printed "Nothing to apply."
        proc = self.check(summary=self.summary())
        self.assertEqual(proc.returncode, 1)
        self.assertIn('summary says 0 Critical but the validated diff defects are 1',
                      self.errors(proc))
        data = self.envelope(proc)['data']
        self.assertEqual(data['counts']['Critical'], 1)
        self.assertEqual(data['tasks']['Critical'], 1)
        # The validated counts are the source the skill offers from.
        again = self.check(summary=self.summary(critical=1))
        self.assertEqual(again.returncode, 0, again.stderr + again.stdout)

    def test_a_partial_review_is_refused_and_its_gaps_are_exposed(self):
        self.manifest = self.prepared()
        self.write_machine([], completeness='partial',
                           gaps=['the runtime paths were not read (review timed out)'],
                           verification=[{'kind': 'evidence',
                                          'evidence': 'the review timed out before the runtime '
                                                      'paths were read'}])
        self.write_report(self.report_text(completeness='partial'))
        proc = self.check(summary=self.summary())
        self.assertEqual(proc.returncode, 1)
        self.assertIn('the review is partial', self.errors(proc))
        self.assertIn('the runtime paths were not read', self.errors(proc))
        data = self.envelope(proc)['data']
        self.assertEqual(data['completeness'], 'partial')
        self.assertEqual(data['gaps'], ['the runtime paths were not read (review timed out)'])

    def test_an_accepted_audit_is_neither_counted_nor_a_task(self):
        self.manifest = self.prepared()
        self.write_machine([
            finding('DR-001', kind='audit', scope='diff', priority='Critical',
                    evidence='The sensitive surface touched is reviewed; the change is '
                             'acceptable.',
                    recommendation='none — audit only.'),
        ])
        self.write_report(self.report_text(
            audits='### DR-001 (audit): reviewed the sensitive surface'))
        proc = self.check(summary=self.summary())
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        data = self.envelope(proc)['data']
        self.assertEqual(data['counts'], {'Critical': 0, 'Warning': 0, 'Suggestion': 0})
        self.assertEqual(data['tasks'], {'Critical': 0, 'Warning': 0})
        self.assertEqual(data['findings']['by_kind'], {'defect': 0, 'audit': 1})


class TestProtocolWiring(unittest.TestCase):
    """The skill's order and dispatch, pinned where no code path can run."""

    def setUp(self):
        self.skill = read(SKILL)
        self.flat = flat(self.skill)

    def test_the_target_is_pinned_before_the_quality_gate(self):
        self.assertLess(self.flat.index('deep_review.py prepare'),
                        self.flat.index('Running quality gate...'))

    def test_the_quality_gate_runs_against_the_pinned_checkout(self):
        gate = self.skill.split('### 2d: Quality gate')[1]
        self.assertIn('pinned', gate)

    def test_both_dispatch_templates_carry_the_spec_store_line(self):
        without = self.skill.split('**Without PR context**')[1].split('**With PR context**')[0]
        with_pr = self.skill.split('**With PR context**')[1]
        for template in (without, with_pr):
            self.assertIn('**Spec store:**', template)

    def test_the_dispatch_carries_the_pins_the_paths_and_the_contract(self):
        for phrase in ('.artel/run/<TICKET_ID>/deep-review/target.json',
                       'git diff <MERGE_BASE> <HEAD>',
                       'git diff <BASE> <HEAD>',
                       '.artel/run/<TICKET_ID>/deep-review/machine.json',
                       '${ARTEL_ROOT}/docs/deep-review.md'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_the_kartoteka_check_pipes_the_stored_report_with_pipefail(self):
        self.assertIn(
            'set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get '
            '<specs.dir>/<TICKET_ID>/deep-review.md | python3 '
            '${ARTEL_ROOT}/scripts/deep_review.py check', self.flat)
        self.assertIn('--report -', self.flat)

    def test_the_counts_come_from_the_validated_envelope(self):
        for phrase in ('deep_review.py check', 'data.counts', 'data.tasks',
                       'data.completeness', 'data.gaps'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)
        self.assertNotIn("Read the counts from the agent's completion line", self.flat)

    def test_a_partial_review_stops_before_any_fix(self):
        self.assertIn('never `Nothing to apply.`', self.flat)
        self.assertIn('no copy, no implementer', self.flat)

    def test_invalid_artifacts_get_one_bounded_retry_and_never_inline_correction(self):
        for phrase in ('one bounded retry', 'same prompt and model',
                       'edit the report or the machine report yourself'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_one_shared_artifact_retry_budget_across_both_branches(self):
        self.assertIn('ARTIFACT_RETRY_USED', self.flat)
        self.assertIn('at most two reviewer attempts', self.flat)
        dispatch = self.skill.split('### 3a: Dispatch')[1].split('### 3b: Validate')[0]
        validate = self.skill.split('### 3b: Validate')[1].split('## Step 4:')[0]
        for branch, name in ((dispatch, '3a'), (validate, '3b')):
            with self.subTest(branch=name):
                self.assertIn('ARTIFACT_RETRY_USED', flat(branch))

    def test_checkout_drift_stops_before_the_artifact_retry(self):
        validate = flat(self.skill.split('### 3b: Validate')[1].split('## Step 4:')[0])
        self.assertLess(validate.index('left the pinned target'),
                        validate.index('ARTIFACT_RETRY_USED'))

    def test_an_empty_report_is_unprocessable_input_and_never_retried(self):
        validate = flat(self.skill.split('### 3b: Validate')[1].split('## Step 4:')[0])
        self.assertIn('empty_input', validate)
        self.assertLess(validate.index('- `2` —'), validate.index('empty_input'))
        self.assertIn('never re-dispatch the reviewer for it', validate)

    def test_the_checker_is_not_semantic_truth(self):
        self.assertIn('never that a finding is true', self.flat)


class TestReviewerWiring(unittest.TestCase):
    """The reviewer's bounded documentation pass and the deep-review output."""

    def setUp(self):
        self.text = read(REVIEWER)
        self.flat = flat(self.text)

    def test_the_documentation_pass_is_bounded(self):
        for phrase in ('### Documentation pass (standalone)',
                       'Only statements the change invalidates',
                       'no generic documentation wishlist'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_documentation_findings_anchor_on_the_causing_code(self):
        for phrase in ('Anchor on the causing code',
                       'is never the anchor'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_the_deep_review_output_points_at_the_single_home(self):
        for phrase in ('docs/deep-review.md', 'machine report', 'DR-NNN',
                       'Finding: DR-NNN', 'Accepted audits'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_an_accepted_audit_never_counts_or_becomes_a_task(self):
        self.assertIn('never counts and never becomes a task', self.flat)

    def test_a_documentation_only_fix_carries_no_behavior_marker(self):
        self.assertIn('documentation-only fix carries no `behavior` marker', self.flat)


class TestContractDoc(unittest.TestCase):
    """`docs/deep-review.md` is the single home of the schema and the report contract."""

    def setUp(self):
        self.text = read(CONTRACT)
        self.flat = flat(self.text)

    def test_it_owns_the_machine_report_schema(self):
        for phrase in ('"schema": 1', '"run_id"', '"merge_base"', '"completeness"',
                       '"gaps"', '"verification"', '"findings"', '"kind"', '"scope"',
                       '"evidence"', '"recommendation"'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.text)

    def test_every_fenced_json_example_parses(self):
        # The examples are the schema's single home: a copy of either must be
        # loadable as JSON, so adjacent-string concatenation is not permitted.
        blocks = re.findall(r'^```json\n(.*?)^```', self.text, re.MULTILINE | re.DOTALL)
        self.assertTrue(blocks, 'docs/deep-review.md has no fenced json example')
        for index, block in enumerate(blocks):
            with self.subTest(example=index):
                json.loads(block)

    def test_it_owns_the_report_contract(self):
        for phrase in ('type: deep-review', '**Review run:**', '**Reviewed base:**',
                       '**Reviewed head:**', '**Review completeness:**',
                       '### DR-NNN (<Priority>): <title>', '### DR-NNN (audit): <title>',
                       '### Tasks (Critical)', '### Tasks (Warning)',
                       'Finding: DR-NNN', 'Acceptance criteria:'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_it_owns_the_check_contract(self):
        for phrase in ('deep_review.py check', 'exit `0`', 'exit `1`', 'exit `2`',
                       'by_kind', 'by_scope'):
            with self.subTest(phrase):
                self.assertIn(phrase, self.flat)

    def test_it_says_what_the_check_does_not_prove(self):
        self.assertIn('never that a finding is true', self.flat)

    def test_the_skill_and_the_reviewer_point_at_it(self):
        for rel in (SKILL, REVIEWER):
            with self.subTest(rel):
                self.assertIn('docs/deep-review.md', read(rel))


class TestOpenCodeBuild(unittest.TestCase):
    """The baked install copy keeps the checker contract."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.root = tmp / 'artel'
        cls.out = tmp / 'dist'
        cls.root.mkdir()
        # The installer's own flow: the canonical trees copied under the install root,
        # then the builder bakes that copy in place.
        for tree in ('skills', 'agents', 'docs'):
            shutil.copytree(ROOT / tree, cls.root / tree)
        proc = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'build_opencode.py'),
             '--root', str(cls.root), '--out', str(cls.out)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError('build failed: ' + proc.stderr)
        cls.proc = proc

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_baked_skill_carries_the_checker(self):
        text = flat((self.root / 'skills' / 'artel-deep-review' / 'SKILL.md')
                    .read_text(encoding='utf-8'))
        self.assertIn('/scripts/deep_review.py check', text)
        self.assertIn('/scripts/deep_review.py prepare', text)
        self.assertIn('/docs/deep-review.md', text)
        self.assertNotIn('${ARTEL_ROOT}', text)

    def test_the_baked_skill_resolves_the_deep_review_model_site(self):
        """The installed artifact is the only thing that pins the resolver instruction."""
        text = flat((self.root / 'skills' / 'artel-deep-review' / 'SKILL.md')
                    .read_text(encoding='utf-8'))
        self.assertIn('/scripts/models.py resolve --site reviewer.deepReview', text)


if __name__ == '__main__':
    unittest.main()
