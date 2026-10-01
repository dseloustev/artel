"""The OpenCode 2.x bridge source: shape and bindings asserted against the plugin
source. There is no TypeScript runner in this repo, so source-level checks are the
only ones available -- which is why they assert placement and casing rather than
mere presence.

Companion to tests/test_build_opencode.py: that file covers the generator, this one
the hand-written bridge.
"""
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
        self.assertLess(self.source.index('vcs_guard.py'),
                        self.source.index('MUTATION_TOOLS.has(event.tool)'))

    def test_binding_is_not_limited_to_the_mcp_prefix(self):
        # `mcp__` is a Claude Code convention; OpenCode names MCP tools without it, so a
        # prefix test would leave Bitbucket MCP writes unguarded here.
        self.assertNotIn('startsWith("mcp__")', self.source)
        self.assertIn('PLATFORM_TOKENS', self.source)
        for token in ('"bitbucket"', '"github"', '"jira"'):
            self.assertIn(token, self.source)
        self.assertIn('isPlatformTool(event.tool)', self.source)
        self.assertLess(self.source.index('isPlatformTool(event.tool)'),
                        self.source.index('MUTATION_TOOLS.has(event.tool)'))

    def test_payload_carries_claude_code_tool_casing(self):
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

    def test_uses_console_not_a_client_logger(self):
        self.assertIn('console.warn', self.source)


if __name__ == '__main__':
    unittest.main()
