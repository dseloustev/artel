"""kartoteka's project namespacing (E4, kartoteka 0.31.0) is spelled identically
everywhere artel names a kartoteka call.

Not a behaviour test: these are prompts, and there is no code path to exercise
(the hook's half is tests/test_knowledge_mirror.py). It guards the shape of every
call artel's agents and skills make -- `related` takes the project first, every
write names it, every read scopes to it -- and the record lines the three
contracts must spell the same way, because a paraphrase of a gate message is
what makes two documents disagree about whether consultation happened. Prose is
deliberately not asserted on; only the spellings are.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every live file that may name a kartoteka tool. CHANGELOG.md is history and
# docs/superpowers/ is untracked design history; neither is a live instruction.
LIVE_FILES = tuple(sorted(
    str(p.relative_to(ROOT)) for p in
    list(ROOT.glob('agents/*.md')) + list(ROOT.glob('skills/*/SKILL.md')) +
    list(ROOT.glob('docs/*.md')) + [ROOT / 'README.md', ROOT / 'hooks/README.md',
                                     ROOT / 'skills/README.md']
))

CONFIG = 'docs/config.md'
CONSULTATION = 'docs/knowledge-consultation.md'
QUEUE = 'docs/task-queue.md'
FORECAST = 'docs/review-forecast.md'
SETUP = 'skills/setup/SKILL.md'
HOOKS_README = 'hooks/README.md'
OPENCODE = 'docs/opencode.md'
REQUIREMENTS = 'docs/kartoteka-requirements.md'
KNOWLEDGE_SKILL = 'skills/knowledge/SKILL.md'
TASKS_SKILL = 'skills/tasks/SKILL.md'
DEEP_REVIEW_SKILL = 'skills/deep-review/SKILL.md'

# The one record line for "adapter on, project missing", inherited byte for
# byte by the forecast's `off:` mode, the same way the tools-absent reason is.
PROJECT_UNSET = 'kartoteka is configured for this project but knowledge.project is not set'
# What the implementer records when the daemon refuses the project it names.
PROJECT_REFUSED = 'kartoteka refused knowledge.project as unregistered; continued from tasklist.md'
REGISTER_COMMAND = 'kartoteka project add'
GRAMMAR = '^[a-z0-9][a-z0-9-]*$'

# A `related(` with arguments must open with the project. Bare `related()` is
# the tool's name used as a noun and stays legal.
RELATED_WITHOUT_PROJECT = re.compile(r'related\((?!\)|<project>|project=)')
# A write with arguments must name its project somewhere before the call closes.
WRITE_CALL = re.compile(r'\b(task_ready|task_create|artifact_put)\(([^)]*)\)')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


class TestConfigKey(unittest.TestCase):

    def setUp(self):
        self.text = read(CONFIG)

    def test_documents_the_key_its_grammar_and_the_register_command(self):
        self.assertIn('`knowledge.project`', self.text)
        self.assertIn(GRAMMAR, self.text)
        self.assertIn(REGISTER_COMMAND, self.text)

    def test_both_example_configs_carry_the_key(self):
        # The default block (adapter none, empty value) and the filled example.
        self.assertEqual(2, self.text.count('"project":'),
                         'the default and the filled example each spell the key once')

    def test_no_longer_calls_kartoteka_single_project(self):
        self.assertNotIn('single-project', self.text)


class TestEveryCallNamesTheProject(unittest.TestCase):

    def test_related_always_takes_the_project_first(self):
        for rel in LIVE_FILES:
            with self.subTest(rel):
                self.assertIsNone(RELATED_WITHOUT_PROJECT.search(read(rel)),
                                  rel + ' spells related() without the project first')

    def test_every_write_call_with_arguments_names_the_project(self):
        for rel in LIVE_FILES:
            with self.subTest(rel):
                for match in WRITE_CALL.finditer(read(rel)):
                    args = match.group(2)
                    if not args.strip():
                        continue  # the tool's name as a noun
                    self.assertIn('project=', args,
                                  '{}: {}({}) names no project'.format(rel, match.group(1), args))

    def test_no_live_file_calls_kartoteka_single_project(self):
        for rel in LIVE_FILES:
            with self.subTest(rel):
                self.assertNotIn('single-project', read(rel))


class TestGateMessages(unittest.TestCase):

    def test_the_three_contracts_spell_the_unset_line_identically(self):
        for rel in (CONSULTATION, QUEUE, FORECAST):
            with self.subTest(rel):
                self.assertIn(PROJECT_UNSET, read(rel))

    def test_the_forecast_has_an_off_mode_for_it(self):
        self.assertIn('`off: ' + PROJECT_UNSET + '`', read(FORECAST))
        self.assertIn('off: ' + PROJECT_UNSET, read(DEEP_REVIEW_SKILL))

    def test_the_queue_doc_records_a_refused_project_distinctly(self):
        self.assertIn(PROJECT_REFUSED, read(QUEUE))
        self.assertIn(REGISTER_COMMAND, read(QUEUE))

    def test_the_consultation_doc_uses_the_unscoped_probe_as_the_registry_check(self):
        # Reads for an unregistered project answer with silent zeros; only an
        # unscoped index_status() walks the registry, so the availability probe
        # is also the one place the agent can learn the project is unknown.
        self.assertIn(REGISTER_COMMAND, read(CONSULTATION))

    def test_the_conversational_skills_gate_on_the_key(self):
        for rel in (KNOWLEDGE_SKILL, TASKS_SKILL):
            with self.subTest(rel):
                self.assertIn('knowledge.project', read(rel))


class TestSetupInterview(unittest.TestCase):

    def test_asks_for_the_project_and_names_the_register_command(self):
        text = read(SETUP)
        self.assertIn('knowledge.project', text)
        self.assertIn(REGISTER_COMMAND, text)


class TestTokenEnv(unittest.TestCase):
    """kartoteka 0.32.0 (E3 phase 1): a daemon with [auth] on refuses every
    request without a bearer token. artel names the variable holding it as
    `knowledge.tokenEnv` -- the name, never the value, because the config is
    committed -- and the same variable feeds the MCP registration through the
    client's own expansion syntax. Spelled once here so the six documents that
    mention it cannot drift."""

    TOKEN_KEY = '`knowledge.tokenEnv`'
    VARIABLE = 'KARTOTEKA_TOKEN'

    def test_config_documents_the_key_and_the_conventional_variable(self):
        text = read(CONFIG)
        self.assertIn(self.TOKEN_KEY, text)
        self.assertIn(self.VARIABLE, text)
        self.assertIn('Authorization: Bearer', text)

    def test_both_example_configs_carry_the_key(self):
        # The default block (empty) and the filled example (KARTOTEKA_TOKEN).
        self.assertEqual(2, read(CONFIG).count('"tokenEnv":'),
                         'the default and the filled example each spell the key once')

    def test_config_wires_the_mcp_session_with_the_same_variable(self):
        # Claude Code expands ${VAR} inside .mcp.json headers; the hook reads
        # the variable directly. One export serves both.
        text = read(CONFIG)
        self.assertIn('--header', text)
        self.assertIn('${' + self.VARIABLE + '}', text)

    def test_opencode_names_its_own_expansion_syntax(self):
        self.assertIn('{env:' + self.VARIABLE + '}', read(OPENCODE))

    def test_hooks_readme_names_the_key_and_the_401(self):
        text = read(HOOKS_README)
        self.assertIn(self.TOKEN_KEY, text)
        self.assertIn('401', text)

    def test_setup_asks_for_the_name_never_the_value(self):
        text = read(SETUP)
        self.assertIn('knowledge.tokenEnv', text)
        self.assertIn(self.VARIABLE, text)
        self.assertIn('never the value', text)
        self.assertIn('--header', text)

    def test_the_read_contracts_explain_an_auth_on_daemon_as_tools_absent(self):
        # Without the header Claude Code cannot connect, so the session sees
        # no tools: row 5 of both gating tables, with a fix that is host-side
        # wiring rather than an artel setting.
        for rel in (CONSULTATION, QUEUE):
            with self.subTest(rel):
                text = read(rel)
                self.assertIn('[auth]', text)
                self.assertIn('--header', text)

    def test_the_requirements_record_that_kartoteka_shipped_it(self):
        text = read(REQUIREMENTS)
        self.assertIn('0.32.0', text)
        self.assertIn('knowledge.tokenEnv', text)


if __name__ == '__main__':
    unittest.main()
