"""The OpenCode 2.x bridge source: shape and bindings asserted against the plugin
source. Most checks are source-level -- placement and casing rather than mere presence --
because the bridge's hook bindings have no seam to inject. The session-baseline capture is
the exception: it is driven end to end through the real TypeScript (Node's type stripping)
against a scratch host, since a missed `session.created` is a timing race whose consequence
is a silently open gate.

Companion to tests/test_build_opencode.py: that file covers the generator, this one
the hand-written bridge.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / 'opencode' / 'plugin' / 'artel.ts'


class TestBridgeSource(unittest.TestCase):
    def setUp(self):
        self.source = BRIDGE.read_text(encoding='utf-8')

    # --- v2 plugin shape ---

    def test_default_export_is_a_v2_plugin_definition(self):
        self.assertIn('export default', self.source)
        self.assertIn('id: "artel"', self.source)
        self.assertIn('setup', self.source)

    def test_no_v1_plugin_api_surface(self):
        for token in ('experimental.chat.messages.transform',
                      'client.app.log',
                      '@opencode-ai/plugin',
                      '"tool.execute.before":'):
            self.assertNotIn(token, self.source, token)

    def test_uses_v2_tool_hooks(self):
        self.assertIn('ctx.tool.hook("execute.before"', self.source)
        self.assertIn('ctx.tool.hook("execute.after"', self.source)
        self.assertIn('event.tool', self.source)

    def test_router_uses_the_context_hook(self):
        self.assertIn('ctx.session.hook("context"', self.source)
        self.assertIn('event.messages', self.source)

    def test_stop_gate_uses_the_execution_terminal_event_not_idle(self):
        self.assertIn('session.execution.succeeded', self.source)
        self.assertNotIn('session.idle', self.source)
        self.assertIn('ctx.session.prompt', self.source)

    def test_cwd_comes_from_the_location(self):
        self.assertIn('ctx.location.directory', self.source)

    def test_returns_an_abort_cleanup(self):
        self.assertIn('AbortController', self.source)
        self.assertIn('return () => controller.abort()', self.source)

    # --- bindings carried over from v1 (still required on v2) ---

    def test_inert_without_artel_config(self):
        self.assertIn('hasArtelConfig', self.source)
        self.assertIn('.artel', self.source)
        self.assertIn('config.json', self.source)

    def test_install_root_fallback(self):
        self.assertIn('ARTEL_ROOT', self.source)
        self.assertIn('opencode", "artel"', self.source)

    def test_binds_the_vcs_guard(self):
        self.assertIn('vcs_guard.py', self.source)
        self.assertIn('artel vcs guard', self.source)

    def test_binding_precedes_the_mutation_tools_early_return(self):
        # A binding placed after the mutation-tool branch never sees a shell or MCP call,
        # so the guard would look installed and enforce nothing.
        self.assertLess(self.source.index('runHook("vcs_guard.py"'),
                        self.source.index('MUTATION_TOOLS.has(event.tool)'))

    def test_binding_is_not_limited_to_the_mcp_prefix(self):
        # The ABI tolerates legacy `mcp__`-prefixed names; OpenCode names MCP tools without
        # the prefix, so a prefix test would leave Bitbucket MCP writes unguarded here.
        self.assertNotIn('startsWith("mcp__")', self.source)
        self.assertIn('PLATFORM_TOKENS', self.source)
        for token in ('"bitbucket"', '"github"', '"jira"'):
            self.assertIn(token, self.source)
        self.assertIn('isPlatformTool(event.tool)', self.source)
        self.assertLess(self.source.index('isPlatformTool(event.tool)'),
                        self.source.index('MUTATION_TOOLS.has(event.tool)'))

    def test_payload_carries_the_abi_tool_casing(self):
        # hooks/vcs_guard.py matches `tool == 'Bash'` exactly; OpenCode's tool is `shell`.
        self.assertIn('"Bash"', self.source)
        self.assertIn('"Write"', self.source)
        self.assertIn('"Read"', self.source)

    def test_covers_v2_mutation_tool_names(self):
        for tool in ('"edit"', '"write"', '"patch"'):
            self.assertIn(tool, self.source)

    def test_patch_targets_come_from_the_patch_grammar(self):
        # `patch` carries no path; its targets are the grammar's file headers.
        self.assertIn('patchText', self.source)
        self.assertIn('Add|Delete|Update', self.source)
        self.assertIn('Move to', self.source)

    def test_findings_mutate_the_tool_result(self):
        self.assertIn('appendFindings', self.source)
        self.assertIn('result.content', self.source)
        self.assertIn('artel_findings', self.source)

    def test_after_hook_order_verifies_before_mirroring(self):
        # The verify gate must run before the side-effect mirror.
        self.assertLess(self.source.index('runHook("fast_verify_post_edit.py"'),
                        self.source.index('runHook("knowledge_mirror.py"'))

    def test_uses_console_not_a_client_logger(self):
        self.assertIn('console.warn', self.source)


class TestLocationScoping(unittest.TestCase):
    """The event stream is server-wide, but the hooks it drives are not. OpenCode's docs
    say `ctx.location` is "not the location of every session it can access or event it
    receives", so every event-driven hook must check the session's own location first —
    otherwise a location with an active run baselines and stop-gates sessions in other
    checkouts (observed 2026-10-08: a wallet run re-prompted a session in the artel
    checkout, and the shared block counter ate the run's own budget)."""

    def setUp(self):
        self.source = BRIDGE.read_text(encoding='utf-8')

    def test_the_location_guard_exists(self):
        self.assertIn('sessionLocations', self.source)
        self.assertIn('sessionDirectory', self.source)
        self.assertIn('ctx.session.get', self.source)

    def test_created_sessions_cache_and_check_their_location(self):
        # Placement, not presence: the location check must sit before the baseline hook,
        # and the cached value is what execution events — which carry no location — read.
        created = self.source[self.source.index('event.type === "session.created"'):]
        self.assertIn('data.location?.directory', created)
        self.assertLess(created.index('data.location?.directory'),
                        created.index('runHook("session_baseline.py"'))

    def test_the_stop_gate_resolves_the_location_before_any_hook(self):
        gate = self.source[self.source.index('const runStopGate'):]
        self.assertLess(gate.index('sessionDirectory(sessionID)'),
                        gate.index('runHook(script'))

    def test_moved_sessions_refresh_the_cache_and_deletes_clear_it(self):
        moved = self.source[self.source.index('event.type === "session.moved"'):]
        self.assertIn('sessionLocations.set(sessionID', moved)
        deleted = self.source[self.source.index('event.type === "session.deleted"'):]
        self.assertIn('sessionLocations.delete(sessionID)', deleted)


class TestWorkerRole(unittest.TestCase):
    """A pinned worker location is recognized from the reviewed lifecycle marker: the
    router is skipped, orchestration and user questions are denied, and the marker's role
    string alone is never enough — schema, complete setup, root equality and the worktree
    area are all checked. The Python hooks re-validate authoritatively, and the bridge never
    skips the stop-gate hook itself."""

    def setUp(self):
        self.source = BRIDGE.read_text(encoding='utf-8')

    def test_the_role_check_reads_the_lifecycle_marker(self):
        self.assertIn('isWorkerLocation', self.source)
        self.assertIn('parallel-worker.json', self.source)

    def test_the_role_string_alone_is_not_enough(self):
        check = self.source[self.source.index('function isWorkerLocation'):]
        check = check[:check.index('\n}')]
        self.assertIn('marker.schema !== 1', check)
        self.assertIn('marker.role !== "worker"', check)
        self.assertIn('setup.status !== "complete"', check)
        self.assertIn('path.resolve(workerRoot) !== path.resolve(directory)', check)
        self.assertIn('.opencode', check)
        self.assertIn('worktrees', check)

    def test_the_router_is_skipped_for_worker_locations(self):
        context = self.source[self.source.index('ctx.session.hook("context"'):]
        self.assertIn('isWorker()', context)
        self.assertLess(context.index('isWorker()'),
                        context.index('runHook("using_artel.py"'))

    def test_orchestration_and_user_questions_are_denied_for_workers(self):
        for token in ('WORKER_DENIED_TOOLS', '"subagent"', '"question"'):
            self.assertIn(token, self.source, token)
        before = self.source[self.source.index('ctx.tool.hook("execute.before"'):]
        self.assertIn('WORKER_DENIED_TOOLS.has(event.tool)', before)
        self.assertIn('artel worker role', before)
        self.assertLess(before.index('WORKER_DENIED_TOOLS.has(event.tool)'),
                        before.index('runHook("vcs_guard.py"'))
        self.assertLess(before.index('WORKER_DENIED_TOOLS.has(event.tool)'),
                        before.index('MUTATION_TOOLS.has(event.tool)'))

    def test_the_stop_gate_still_runs_for_worker_locations(self):
        # The armed stop gate is skipped inside hooks/stop_gate.py on a validated worker
        # root — the authoritative Python validation always runs; the bridge itself never
        # skips the hook.
        gate = self.source[self.source.index('const runStopGate'):]
        self.assertNotIn('isWorker()', gate)


class TestParallelAdapterRegistration(unittest.TestCase):
    """The bridge registers the parallel worker-session adapter (Task 7) and a worker
    location can never reach it: the adapter namespace is denied beside `subagent` and
    `question`, so a worker gains no session-creation authority by naming a different
    tool."""

    ADAPTER_TOOLS = ('artel_parallel_workerCapabilities', 'artel_parallel_startWorker',
                     'artel_parallel_workerStatus', 'artel_parallel_interruptWorker')

    def setUp(self):
        self.source = BRIDGE.read_text(encoding='utf-8')
        self.adapter = (BRIDGE.parent / 'parallel.ts').read_text(encoding='utf-8')

    def test_the_bridge_imports_and_registers_the_adapter(self):
        self.assertIn('registerParallel', self.source)
        self.assertIn('"./parallel"', self.source)

    def test_the_adapter_is_registered_inside_setup(self):
        setup = self.source[self.source.index('const setup: Plugin["setup"]'):]
        self.assertIn('registerParallel(ctx, { root })', setup)

    def test_the_adapter_namespace_is_denied_for_workers(self):
        # The denial derives from the adapter's exported ids, so a renamed or added adapter
        # tool cannot silently bypass the worker role.
        self.assertIn('PARALLEL_TOOL_IDS', self.source)
        for tool in self.ADAPTER_TOOLS:
            self.assertIn(tool, self.adapter, tool)
        before = self.source[self.source.index('ctx.tool.hook("execute.before"'):]
        self.assertIn('WORKER_DENIED_TOOLS', before)
        self.assertIn('...PARALLEL_TOOL_IDS', self.source)

    def test_the_adapter_registration_carries_no_hooks(self):
        # Loading the adapter must never double the bridge's hook bindings: it registers
        # tools only.
        for token in ('ctx.tool.hook', 'ctx.session.hook', 'ctx.event.subscribe',
                      'session_baseline.py', 'stop_gate.py', 'using_artel.py'):
            self.assertNotIn(token, self.adapter, token)


class TestPerInstanceFactory(unittest.TestCase):
    """Task 11 fix: the bridge is constructible per instance with an explicit install root
    and no shared/global mutation. The default export stays the same plugin with the
    `ARTEL_ROOT` environment fallback; a scratch proof wrapper builds its own instance
    bound to its own checkout (the Task 11 native proof), which is impossible with the
    prior temporary `process.env.ARTEL_ROOT` mutation."""

    def setUp(self):
        self.source = BRIDGE.read_text(encoding='utf-8')

    def factory_source(self):
        start = self.source.index('export function createArtelPlugin')
        end = self.source.index('const plugin: Plugin = createArtelPlugin()')
        return self.source[start:end]

    def test_the_bridge_exports_a_per_instance_factory(self):
        self.assertIn('export function createArtelPlugin', self.source)

    def test_the_default_export_is_still_the_default_instance(self):
        self.assertIn('const plugin: Plugin = createArtelPlugin()', self.source)
        self.assertIn('export default plugin', self.source)
        self.assertIn('id: "artel", setup', self.source)

    def test_the_factory_binds_its_root_to_every_hook_spawn(self):
        factory = self.factory_source()
        self.assertIn('const root = options.root || ARTEL_ROOT', factory)
        self.assertIn('const runHook = (script', factory)
        self.assertIn('spawnHook(root, script', factory)
        self.assertIn('path.join(root, "hooks", script)', self.source)
        self.assertIn('registerParallel(ctx, { root })', factory)

    def test_the_factory_root_is_a_local_binding_not_shared_state(self):
        self.assertNotIn('let ARTEL_ROOT', self.source)
        self.assertNotIn('export let', self.source)
        self.assertNotIn('globalThis', self.source)

    def test_the_factory_never_mutates_the_process_environment(self):
        # The prior scratch stand-in assigned process.env.ARTEL_ROOT; the seam must not.
        self.assertNotIn('process.env.ARTEL_ROOT =', self.source)
        self.assertNotIn('setArtelRoot', self.source)
        self.assertIn('process.env.ARTEL_ROOT ||', self.source)

    def test_two_instances_cannot_change_each_other_or_the_default(self):
        # The only root input is the construction-time option; nothing exports a setter.
        factory = self.factory_source()
        self.assertIn('options: { root?: string } = {}', factory)
        self.assertNotIn('export function set', self.source)
        self.assertNotIn('module.exports', self.source)


class TestHookReplyShape(unittest.TestCase):
    """hooks/README.md's ABI list is what the bridge answers: the retained
    `hookSpecificOutput.hookEventName` is documented, and the hooks that emit it keep their
    historical event names."""

    EVENTS = {
        'hooks/sensitive_guard.py': 'PreToolUse',
        'hooks/spec_store_guard.py': 'PreToolUse',
        'hooks/vcs_guard.py': 'PreToolUse',
        'hooks/fast_verify_post_edit.py': 'PostToolUse',
        'hooks/using_artel.py': 'SessionStart',
    }

    def test_the_readme_documents_the_field(self):
        readme = (ROOT / 'hooks' / 'README.md').read_text(encoding='utf-8')
        self.assertIn('hookSpecificOutput.hookEventName', readme)
        for event in ('`PreToolUse`', '`PostToolUse`', '`SessionStart`'):
            self.assertIn(event, readme)

    def test_the_hooks_keep_their_historical_event_names(self):
        for rel, event in self.EVENTS.items():
            with self.subTest(rel=rel):
                text = (ROOT / rel).read_text(encoding='utf-8')
                self.assertIn("'hookEventName': '{}'".format(event), text)


class TestFirstSessionBaseline(unittest.TestCase):
    """A session whose `session.created` was missed still gets its findings baseline.

    On a freshly started server the bridge's event subscription can come up after the first
    `session.created` (docs/opencode.md, `Headless runs`). The only other capture point was
    the verify stop gate's first-sight branch, which records the session's own findings as
    pre-existing and so fails open for that whole session. The session context hook always
    runs before the model's first tool call, so the same idempotent baseline hook belongs
    there too. Driven through the real TypeScript bridge; only the native client is fake."""

    HARNESS = ROOT / 'tests' / 'fixtures' / 'bridge_ts_harness.mjs'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.host = Path(self._tmp.name)
        (self.host / '.artel').mkdir(parents=True)
        (self.host / '.artel' / 'config.json').write_text(json.dumps({
            'version': 1,
            'ticket': {'projectKey': 'BASE'},
            'tracker': {'adapter': 'none'},
            'vcs': {'adapter': 'none'},
            'verify': {'commands': [], 'fast': 'sh -c "exit 0"'},
            'knowledge': {'adapter': 'none'},
        }), encoding='utf-8')

    def tearDown(self):
        self._tmp.cleanup()

    def run_harness(self, mode):
        proc = subprocess.run(
            ['node', str(self.HARNESS), str(BRIDGE), str(ROOT), str(self.host), mode,
             'ses_first'],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_a_missed_created_event_still_baselines_on_the_first_context_call(self):
        report = self.run_harness('missed')
        self.assertTrue(report['baseline'], report)
        self.assertEqual(report['baseline_keys'], [])

    def test_the_created_event_path_still_baselines_and_routes(self):
        report = self.run_harness('created')
        self.assertTrue(report['created_baseline'], report)
        self.assertTrue(report['context_injected'], report)

    def test_the_context_capture_precedes_the_router_and_runs_once_per_session(self):
        source = BRIDGE.read_text(encoding='utf-8')
        context = source[source.index('ctx.session.hook("context"'):]
        self.assertIn('runHook("session_baseline.py"', context)
        self.assertLess(context.index('runHook("session_baseline.py"'),
                        context.index('runHook("using_artel.py"'))
        self.assertIn('baselined.has(sessionID)', context)
        created = source[source.index('event.type === "session.created"'):]
        self.assertLess(created.index('baselined.add(sessionID)'),
                        created.index('runHook("session_baseline.py"'))
        deleted = source[source.index('event.type === "session.deleted"'):]
        self.assertIn('baselined.delete(sessionID)', deleted)


class TestReadHookCost(unittest.TestCase):
    """A Read that can never produce the spec-store hint does not start `python3`.

    `spec_store_guard.py` sees every Read and returns immediately unless the name ends in an
    image extension, but the process spawn itself costs about 30 ms. The bridge now makes the
    same extension test before spawning. The Python set stays the authority: the bridge's set
    must cover it, or an image read would silently lose the fetch hint.

    Proved with a PATH shim in front of `python3`: the harness records every hook spawn, so
    the test observes the absence of a process, not an internal branch."""

    HARNESS = ROOT / 'tests' / 'fixtures' / 'bridge_ts_harness.mjs'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.host = self.root / 'host'
        self.host.mkdir()
        (self.host / '.artel').mkdir()
        (self.host / '.artel' / 'config.json').write_text(json.dumps({
            'version': 1,
            'ticket': {'projectKey': 'BASE'},
            'tracker': {'adapter': 'none'},
            'vcs': {'adapter': 'none'},
            'verify': {'commands': [], 'fast': 'sh -c "exit 0"'},
            'knowledge': {'adapter': 'kartoteka', 'baseUrl': 'http://127.0.0.1:1',
                          'project': 'base'},
        }), encoding='utf-8')
        self.shim = self.root / 'bin'
        self.shim.mkdir()
        self.log = self.root / 'hooks.log'
        fake = self.shim / 'python3'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$ARTEL_HOOK_LOG"\nexit 0\n',
                        encoding='utf-8')
        fake.chmod(0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def run_read(self, read_path):
        env = dict(os.environ)
        env['PATH'] = str(self.shim) + os.pathsep + env.get('PATH', '')
        env['ARTEL_HOOK_LOG'] = str(self.log)
        proc = subprocess.run(
            ['node', str(self.HARNESS), str(BRIDGE), str(ROOT), str(self.host), 'missed',
             'ses_read', read_path],
            capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def spawned(self):
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text(encoding='utf-8').splitlines() if line.strip()]

    def test_a_non_image_read_spawns_no_hook(self):
        self.run_read('notes.md')
        self.assertEqual(self.spawned(), [])

    def test_an_image_read_still_runs_the_guard(self):
        self.run_read('screens/shot.PNG')
        self.assertTrue(any('spec_store_guard.py' in line for line in self.spawned()),
                        self.spawned())

    def test_the_bridge_extension_set_covers_the_python_one(self):
        sys.path.insert(0, str(ROOT / 'hooks'))
        import kartoteka_http as kh
        source = BRIDGE.read_text(encoding='utf-8')
        match = re.search(r'const IMAGE_EXTENSIONS = new Set\(\[(.*?)\]\)', source, re.S)
        self.assertIsNotNone(match, 'the bridge names its image extensions in IMAGE_EXTENSIONS')
        bridge_extensions = set(re.findall(r'"(\.[a-z]+)"', match.group(1)))
        self.assertTrue(set(kh.IMAGE_TYPES) <= bridge_extensions,
                        'the bridge would skip a read the Python guard still hints: {}'
                        .format(set(kh.IMAGE_TYPES) - bridge_extensions))

    def test_the_extension_test_precedes_the_spawn(self):
        source = BRIDGE.read_text(encoding='utf-8')
        branch = source[source.index('if (event.tool === "read")'):]
        self.assertIn('IMAGE_EXTENSIONS.has(', branch)
        self.assertLess(branch.index('IMAGE_EXTENSIONS.has('), branch.index('runHook('))


if __name__ == '__main__':
    unittest.main()
