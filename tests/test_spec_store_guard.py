import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
sys.path.insert(0, str(HOOKS))
import spec_decision as sd  # noqa: E402
import spec_store_guard as guard  # noqa: E402

ON = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'},
      'knowledge': {'adapter': 'kartoteka', 'baseUrl': 'http://127.0.0.1:8734',
                    'project': 'adguard-wallet'}}


class GuardCase(unittest.TestCase):
    config = ON

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)
        (self.root / '.artel').mkdir()
        (self.root / '.artel' / 'config.json').write_text(json.dumps(self.config), encoding='utf-8')

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def invoke(self, file_path):
        payload = json.dumps({'tool_name': 'Write', 'tool_input': {'file_path': file_path},
                              'cwd': str(self.root)})
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(payload)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(), 0, 'the guard never blocks by exit code')
        return out.getvalue()

    def assertAllowed(self, file_path):
        self.assertEqual(self.invoke(file_path), '')

    def assertDenied(self, file_path):
        block = json.loads(self.invoke(file_path))['hookSpecificOutput']
        self.assertEqual((block['hookEventName'], block['permissionDecision']),
                         ('PreToolUse', 'deny'))
        return block['permissionDecisionReason']


class TestInert(GuardCase):
    config = dict(ON, knowledge={'adapter': 'none'})

    def test_adapter_off_allows_everything(self):
        self.assertAllowed('specs/.current/AW-12/plan.md')


class TestInertWithoutConfig(GuardCase):
    def test_no_config_allows(self):
        (self.root / '.artel' / 'config.json').unlink()
        self.assertAllowed('specs/.current/AW-12/plan.md')


class TestShortProjectKey(GuardCase):
    config = dict(ON, ticket={'projectKey': 'X'})

    def test_a_key_kartoteka_cannot_store_is_never_guarded(self):
        self.assertAllowed('specs/.current/X-12/plan.md')


class TestProjectKeyWithAnUnderscore(GuardCase):
    config = dict(ON, ticket={'projectKey': 'MY_PROJ'})

    def test_a_key_outside_the_ticket_key_grammar_is_never_guarded(self):
        self.assertAllowed('specs/.current/MY_PROJ-12/plan.md')


class TestLetterAndDigitProjectKey(GuardCase):
    config = dict(ON, ticket={'projectKey': 'A1'})

    def test_a_storable_key_is_guarded(self):
        self.assertDenied('specs/.current/A1-12/plan.md')


class TestArmed(GuardCase):
    def test_spec_documents_are_denied_by_default(self):
        reason = self.assertDenied('specs/.current/AW-12/plan.md')
        self.assertTrue(reason.startswith("kartoteka is this project's spec store:"))
        self.assertIn('plan.md', reason)
        self.assertIn('STORE_UNAVAILABLE', reason)

    def test_absolute_paths_resolve_like_relative_ones(self):
        self.assertDenied(str(self.root / 'specs/.current/AW-12/phase-2/tasks.md'))

    def test_non_spec_paths_under_specs_dir_are_allowed(self):
        for rel in ('specs/.current/.active_ticket', 'specs/.current/AW-12/runtime/observation.md',
                    'specs/.current/AW-12/review/findings.json', 'specs/.current/AW-12/pr-pending.md',
                    'lib/main.dart'):
            self.assertAllowed(rel)

    def test_a_fresh_files_decision_allows(self):
        sd.write('AW-12', sd.new_decision('files', 'local-only run requested', 'dev'))
        self.assertAllowed('specs/.current/AW-12/plan.md')

    def test_a_stale_files_decision_denies(self):
        decision = sd.new_decision('files', 'x', 'dev')
        decision['decided_at'] = '2020-01-01T00:00:00Z'
        sd.write('AW-12', decision)
        self.assertDenied('specs/.current/AW-12/plan.md')

    def test_a_pending_path_is_allowed_and_only_it(self):
        sd.write('AW-12', sd.new_decision('kartoteka', None, 'feature-development', pending=[
            {'path': 'specs/.current/AW-12/plan.md', 'base_version': 2}]))
        self.assertAllowed('specs/.current/AW-12/plan.md')
        self.assertDenied('specs/.current/AW-12/prd.md')

    def test_another_tickets_decision_does_not_leak(self):
        sd.write('AW-13', sd.new_decision('files', 'x', 'dev'))
        self.assertDenied('specs/.current/AW-12/plan.md')


class TestWiring(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_hooks_json_runs_the_guard_on_edits(self):
        hooks = json.loads((self.ROOT / 'hooks' / 'hooks.json').read_text(encoding='utf-8'))
        entry = next(e for e in hooks['hooks']['PreToolUse'] if e['matcher'] == 'Edit|Write|MultiEdit')
        commands = [h['command'] for h in entry['hooks']]
        self.assertTrue(any('spec_store_guard.py' in c for c in commands))

    def test_the_opencode_bridge_runs_the_guard(self):
        bridge = (self.ROOT / 'opencode' / 'plugin' / 'artel.ts').read_text(encoding='utf-8')
        self.assertIn('runHook("spec_store_guard.py"', bridge)
