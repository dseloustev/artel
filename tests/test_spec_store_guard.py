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

    def invoke(self, file_path, tool='Write'):
        payload = json.dumps({'tool_name': tool, 'tool_input': {'file_path': file_path},
                              'cwd': str(self.root)})
        out = io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO(payload)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(), 0, 'the guard never blocks by exit code')
        return out.getvalue()

    def assertAllowed(self, file_path, tool='Write'):
        self.assertEqual(self.invoke(file_path, tool), '')

    def assertDenied(self, file_path, tool='Write'):
        block = json.loads(self.invoke(file_path, tool))['hookSpecificOutput']
        self.assertEqual((block['hookEventName'], block['permissionDecision']),
                         ('PreToolUse', 'deny'))
        return block['permissionDecisionReason']


class TestInert(GuardCase):
    config = dict(ON, knowledge={'adapter': 'none'})

    def test_adapter_off_allows_everything(self):
        self.assertAllowed('specs/.current/AW-12/plan.md')

    def test_reads_are_never_hinted(self):
        self.assertAllowed('specs/.current/AW-12/design/overview.png', tool='Read')


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

    def test_a_dot_dot_segment_does_not_bypass_the_guard(self):
        self.assertDenied('specs/x/../.current/AW-12/plan.md')
        self.assertDenied('./specs/.current/AW-12/plan.md')

    def test_non_spec_paths_under_specs_dir_are_allowed(self):
        for rel in ('specs/.current/.active_ticket', 'specs/.current/AW-12/runtime/observation.md',
                    'specs/.current/AW-12/review/findings.json', 'specs/.current/AW-12/pr-pending.md',
                    'lib/main.dart'):
            self.assertAllowed(rel)

    def test_a_fresh_files_decision_allows(self):
        sd.write('AW-12', sd.new_decision('files', 'local-only run requested', 'dev'))
        self.assertAllowed('specs/.current/AW-12/plan.md')

    def test_a_stale_files_decision_denies_naming_the_staleness(self):
        decision = sd.new_decision('files', 'x', 'dev')
        decision['decided_at'] = '2020-01-01T00:00:00Z'
        sd.write('AW-12', decision)
        self.assertEqual(self.assertDenied('specs/.current/AW-12/plan.md'), (
            'the storage decision for AW-12 is stale (older than 3 hours): re-resolve it '
            '(docs/spec-storage.md §2) before writing plan.md'))

    def test_a_stale_kartoteka_decision_keeps_the_store_reason(self):
        decision = sd.new_decision('kartoteka', None, 'dev')
        decision['decided_at'] = '2020-01-01T00:00:00Z'
        sd.write('AW-12', decision)
        self.assertTrue(self.assertDenied('specs/.current/AW-12/plan.md').startswith(
            "kartoteka is this project's spec store:"))

    def test_a_pending_path_is_allowed_and_only_it(self):
        sd.write('AW-12', sd.new_decision('kartoteka', None, 'feature-development', pending=[
            {'path': 'specs/.current/AW-12/plan.md', 'base_version': 2}]))
        self.assertAllowed('specs/.current/AW-12/plan.md')
        self.assertDenied('specs/.current/AW-12/prd.md')

    def test_another_tickets_decision_does_not_leak(self):
        sd.write('AW-13', sd.new_decision('files', 'x', 'dev'))
        self.assertDenied('specs/.current/AW-12/plan.md')

    def test_writing_an_image_is_always_allowed(self):
        sd.write('AW-12', sd.new_decision('kartoteka', None, 'dev'))
        self.assertAllowed('specs/.current/AW-12/design/overview.png')
        self.assertAllowed('specs/.current/AW-12/runtime/login.PNG')


class TestReadHint(GuardCase):
    IMAGE = 'specs/.current/AW-12/design/overview.png'

    @staticmethod
    def hint(path):
        return ('images are stored in kartoteka: run spec_store.py image fetch {} and Read '
                'the path it prints').format(path)

    def fresh(self, store='kartoteka'):
        sd.write('AW-12', sd.new_decision(store, None if store == 'kartoteka' else 'x', 'dev'))

    def test_a_swept_image_names_the_fetch(self):
        self.fresh()
        self.assertEqual(self.assertDenied(self.IMAGE, tool='Read'), self.hint(self.IMAGE))

    def test_the_message_is_the_module_constant(self):
        self.assertEqual(guard.READ_HINT.format(path=self.IMAGE), self.hint(self.IMAGE))

    def test_an_absolute_path_is_named_repo_relative(self):
        self.fresh()
        rel = 'specs/.current/AW-12/phase-2/runtime/settings.PNG'
        self.assertEqual(self.assertDenied(str(self.root / rel), tool='Read'), self.hint(rel))

    def test_opencode_sends_the_tool_name_lowercase(self):
        self.fresh()
        self.assertDenied(self.IMAGE, tool='read')

    def test_an_unswept_image_reads_as_usual(self):
        self.fresh()
        path = self.root / self.IMAGE
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\x89PNG\r\n\x1a\n')
        self.assertAllowed(self.IMAGE, tool='Read')

    def test_no_hint_without_a_fresh_kartoteka_decision(self):
        self.assertAllowed(self.IMAGE, tool='Read')  # no decision at all
        stale = sd.new_decision('kartoteka', None, 'dev')
        stale['decided_at'] = '2020-01-01T00:00:00Z'
        sd.write('AW-12', stale)
        self.assertAllowed(self.IMAGE, tool='Read')
        self.fresh('files')
        self.assertAllowed(self.IMAGE, tool='Read')

    def test_documents_and_other_paths_read_as_usual(self):
        self.fresh()
        for rel in ('specs/.current/AW-12/plan.md', 'specs/.current/AW-12/runtime/observation.md',
                    'specs/.current/AW-12/design/Screen Shot.png', 'assets/icon.png',
                    'lib/main.dart'):
            with self.subTest(rel):
                self.assertAllowed(rel, tool='Read')


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

    def test_hooks_json_runs_only_the_guard_on_reads(self):
        hooks = json.loads((self.ROOT / 'hooks' / 'hooks.json').read_text(encoding='utf-8'))
        entry = next(e for e in hooks['hooks']['PreToolUse'] if e.get('matcher') == 'Read')
        commands = [h['command'] for h in entry['hooks']]
        self.assertEqual(len(commands), 1, 'sensitive_guard.py must not run on Read')
        self.assertIn('spec_store_guard.py', commands[0])
        self.assertTrue(commands[0].startswith('cd "$CLAUDE_PROJECT_DIR" && '))

    def test_the_opencode_bridge_runs_the_hint_on_reads(self):
        # scripts/build_opencode.py never reads hooks.json: the bridge binds each hook by hand.
        bridge = (self.ROOT / 'opencode' / 'plugin' / 'artel.ts').read_text(encoding='utf-8')
        self.assertIn('input.tool === "read"', bridge)
        # before the edit-tools early return, or a read never reaches it
        self.assertLess(bridge.index('input.tool === "read"'),
                        bridge.index('EDIT_TOOLS.has(input.tool)'))
        # Claude Code's casing, as the VCS guard's payload carries "Bash"
        self.assertIn('claudeEditPayload(input.sessionID, directory, "Read", output.args)', bridge)
