"""The PreToolUse sensitive-path guard: inert in normal sessions, and during an armed
autonomous run it denies writes whose category floor the effective mode does not satisfy.

Contract: docs/autonomous-run.md sections 2 and 10.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
sys.path.insert(0, str(HOOKS))
import sensitive_guard as sg  # noqa: E402

TICKET = 'AW-1234'
CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


def iso(delta_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


class GuardCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)
        (self.root / '.artel').mkdir()
        (self.root / '.artel' / 'config.json').write_text(json.dumps(CONFIG), encoding='utf-8')

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def arm(self, effective_mode='full-gates', gates_confirmed=('TASKLIST_READY',), **overrides):
        specs = self.root / 'specs' / '.current'
        specs.mkdir(parents=True, exist_ok=True)
        (specs / '.active_ticket').write_text(TICKET + '\n', encoding='utf-8')
        state = {
            'run_active': True,
            'completed': False,
            'started_at': iso(),
            'effective_mode': effective_mode,
            'gates_confirmed': list(gates_confirmed),
        }
        state.update(overrides)
        run = self.root / '.artel' / 'run' / TICKET
        run.mkdir(parents=True, exist_ok=True)
        (run / 'run-state.json').write_text(json.dumps(state), encoding='utf-8')

    def invoke(self, file_path):
        payload = json.dumps({'tool_input': {'file_path': file_path}, 'cwd': str(self.root)})
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(payload)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sg.main(), 0, 'the guard never blocks by exit code')
        return out.getvalue()

    def assertAllowed(self, file_path):
        self.assertEqual(self.invoke(file_path), '', 'an allow prints nothing')

    def assertDenied(self, file_path):
        output = self.invoke(file_path)
        self.assertNotEqual(output, '', 'expected a deny for ' + file_path)
        block = json.loads(output)['hookSpecificOutput']
        self.assertEqual(block['hookEventName'], 'PreToolUse')
        self.assertEqual(block['permissionDecision'], 'deny')
        return block['permissionDecisionReason']


class TestDisarmed(GuardCase):
    """No autonomous run means no guard — an interactive session is unaffected."""

    def test_no_active_ticket(self):
        self.assertAllowed('.env')

    def test_run_not_active(self):
        self.arm(run_active=False)
        self.assertAllowed('.env')

    def test_completed_run(self):
        self.arm(completed=True)
        self.assertAllowed('.env')

    def test_stale_run_never_arms_guards_forever(self):
        self.arm(started_at=iso(-sg.WALL_CLOCK_HOURS - 1))
        self.assertAllowed('.env')

    def test_unparseable_timestamp_disarms(self):
        self.arm(started_at='whenever')
        self.assertAllowed('.env')

    def test_missing_file_path_is_allowed(self):
        self.arm()
        self.assertAllowed('')


class TestModeFloor(GuardCase):
    def test_yolo_cannot_touch_a_full_gates_path(self):
        self.arm(effective_mode='yolo')
        reason = self.assertDenied('.env')
        self.assertIn('secrets', reason)
        self.assertIn('full-gates', reason)
        self.assertIn('yolo', reason)

    def test_yolo_cannot_touch_a_plan_gate_path(self):
        self.arm(effective_mode='yolo')
        self.assertIn('ci-cd', self.assertDenied('.github/workflows/tests.yml'))

    def test_plan_gate_satisfies_its_own_floor_once_the_pause_is_confirmed(self):
        self.arm(effective_mode='plan-gate')
        self.assertAllowed('.github/workflows/tests.yml')

    def test_plan_gate_still_cannot_touch_a_full_gates_path(self):
        self.arm(effective_mode='plan-gate')
        self.assertIn('full-gates', self.assertDenied('.env'))

    def test_absent_mode_is_treated_as_yolo(self):
        """The lowest rank is the safe default for a malformed run-state."""
        self.arm(effective_mode=None)
        self.assertIn('secrets', self.assertDenied('.env'))


class TestPauseGate(GuardCase):
    def test_unconfirmed_pause_denies_even_at_the_floor(self):
        self.arm(effective_mode='full-gates', gates_confirmed=())
        reason = self.assertDenied('.env')
        self.assertIn(sg.FLOOR_PAUSE_GATE, reason)

    def test_confirmed_pause_allows_at_the_floor(self):
        self.arm(effective_mode='full-gates', gates_confirmed=('TASKLIST_READY',))
        self.assertAllowed('.env')


class TestShippedPolicy(GuardCase):
    """The default categories must actually match the paths they name."""

    def setUp(self):
        super().setUp()
        self.arm(effective_mode='yolo')

    def test_secret_shapes(self):
        for path in ('.env', '.env.local', 'app/.env', 'server.pem', 'deploy.key',
                     'cert.p12', 'src/my_secret_store.py', 'aws_credentials.json',
                     'keys/id_rsa'):
            self.assertIn('secrets', self.assertDenied(path), path)

    def test_gate_config_shapes(self):
        for path in ('.claude/settings.json', '.artel/config.json',
                     '.artel/sensitive-paths.json'):
            self.assertIn('gate-config', self.assertDenied(path), path)

    def test_ci_cd_shapes(self):
        for path in ('.github/workflows/tests.yml', '.gitlab-ci.yml', 'Jenkinsfile',
                     '.circleci/config.yml', 'azure-pipelines.yml',
                     'bitbucket-pipelines.yml'):
            self.assertIn('ci-cd', self.assertDenied(path), path)

    def test_ordinary_source_is_untouched(self):
        for path in ('src/main.py', 'lib/app/widget.dart', 'README.md',
                     'specs/.current/AW-1234/plan.md'):
            self.assertAllowed(path)


class TestHostOverride(GuardCase):
    def write_host_rules(self, rules):
        (self.root / '.artel' / 'sensitive-paths.json').write_text(
            json.dumps(rules), encoding='utf-8')

    def test_host_policy_replaces_the_default_wholesale(self):
        """Not a merge: the effective policy is always exactly one file."""
        self.write_host_rules({'categories': [
            {'name': 'house-rules', 'floor': 'full-gates', 'globs': ['infra/*.tf']}]})
        self.arm(effective_mode='yolo')
        self.assertIn('house-rules', self.assertDenied('infra/main.tf'))
        self.assertAllowed('.env')  # the shipped default no longer applies

    def test_empty_host_policy_guards_nothing(self):
        self.write_host_rules({'categories': []})
        self.arm(effective_mode='yolo')
        self.assertAllowed('.env')

    def test_category_without_a_floor_defaults_to_full_gates(self):
        self.write_host_rules({'categories': [
            {'name': 'unlabelled', 'globs': ['danger/*']}]})
        self.arm(effective_mode='plan-gate')
        self.assertIn('full-gates', self.assertDenied('danger/thing.txt'))


class TestFailsOpen(GuardCase):
    def test_unreadable_rules_do_not_block_the_write(self):
        """A broken policy file must not wedge every edit in the repo."""
        (self.root / '.artel' / 'sensitive-paths.json').write_text('{not json', encoding='utf-8')
        self.arm(effective_mode='yolo')
        with mock.patch.object(sys, 'stdin', io.StringIO(
                json.dumps({'tool_input': {'file_path': '.env'}, 'cwd': str(self.root)}))):
            with self.assertRaises(ValueError):
                sg.main()  # the __main__ guard converts this to a silent exit 0


if __name__ == '__main__':
    unittest.main()
