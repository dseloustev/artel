"""The using-artel router names every skill, and only skills that exist; the two
kartoteka front doors spell the tool names exactly.

Not a behaviour test: these are prompts, and there is no code path to exercise.
Same guard as tests/test_knowledge_consultation_docs.py and
tests/test_task_queue_docs.py -- a routing row pointing at a renamed skill, or a
tool name one underscore off, fails in a session nobody is watching.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROUTER = 'skills/using-artel/SKILL.md'
KNOWLEDGE = 'skills/knowledge/SKILL.md'
TASKS = 'skills/tasks/SKILL.md'

# Every session pays for the router; the spec caps it.
ROUTER_MAX_BYTES = 10240

SKILL_REF = re.compile(r'/artel:([a-z0-9-]+)')

def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def skill_names():
    """Every skill shipped under skills/, by folder name."""
    return sorted(p.parent.name for p in (ROOT / 'skills').glob('*/SKILL.md'))


class TestRouter(unittest.TestCase):
    def test_router_exists(self):
        self.assertTrue((ROOT / ROUTER).is_file(), ROUTER + ' is missing')

    def test_router_names_every_skill(self):
        text = read(ROUTER)
        for name in skill_names():
            if name == 'using-artel':
                continue
            self.assertIn('/artel:' + name, text,
                          ROUTER + ' has no route to /artel:' + name)

    def test_router_names_only_skills_that_exist(self):
        names = set(skill_names())
        for ref in sorted(set(SKILL_REF.findall(read(ROUTER)))):
            self.assertIn(ref, names,
                          ROUTER + ' routes to /artel:' + ref + ', which does not exist')

    def test_router_fits_the_injection_budget(self):
        size = (ROOT / ROUTER).stat().st_size
        self.assertLessEqual(size, ROUTER_MAX_BYTES,
                             '{} is {} bytes; the cap is {}'.format(ROUTER, size, ROUTER_MAX_BYTES))

    def test_router_guards_subagents(self):
        self.assertIn('<SUBAGENT-STOP>', read(ROUTER))


class TestHookRegistration(unittest.TestCase):
    def test_hooks_json_registers_the_router_hook_on_session_start(self):
        groups = json.loads(read('hooks/hooks.json'))['hooks']['SessionStart']
        matchers = [group.get('matcher')
                    for group in groups
                    for hook in group['hooks']
                    if 'hooks/using_artel.py' in hook['command']]
        self.assertEqual(matchers, ['startup|clear|compact'])

    def test_router_hook_has_a_short_timeout(self):
        groups = json.loads(read('hooks/hooks.json'))['hooks']['SessionStart']
        timeouts = [hook['timeout']
                    for group in groups
                    for hook in group['hooks']
                    if 'hooks/using_artel.py' in hook['command']]
        self.assertEqual(timeouts, [10])


# The read-side kartoteka tools the knowledge skill calls. Exact spellings.
READ_TOOLS = ('search_knowledge', 'related', 'index_status', 'artifact_list', 'artifact_get')
READ_NEAR_MISSES = ('knowledge_search', 'search_knowledge_base', 'related_docs',
                    'index_state', 'artifacts_list', 'artifact_read')


class TestKnowledgeSkill(unittest.TestCase):
    def test_skill_exists(self):
        self.assertTrue((ROOT / KNOWLEDGE).is_file(), KNOWLEDGE + ' is missing')

    def test_spells_every_read_tool(self):
        text = read(KNOWLEDGE)
        for name in READ_TOOLS:
            self.assertIn(name, text, KNOWLEDGE + ' never mentions ' + name)

    def test_uses_no_near_miss_spelling(self):
        text = read(KNOWLEDGE)
        for wrong in READ_NEAR_MISSES:
            self.assertNotIn(wrong, text, KNOWLEDGE + ' uses ' + wrong)

    def test_never_writes(self):
        text = read(KNOWLEDGE)
        self.assertIn('**writes nothing**', text)
        for name in ('task_create', 'task_update', 'task_ready'):
            self.assertNotIn(name, text, KNOWLEDGE + ' mentions the write tool ' + name)

    def test_carries_the_gate_messages(self):
        text = read(KNOWLEDGE)
        self.assertIn('declare it with `/artel:setup`', text)
        self.assertIn('kartoteka is configured for this project but its MCP tools are not '
                      'available in this session', text)
        self.assertNotIn('--force', text)

    def test_keeps_the_search_budget_and_the_marker(self):
        text = read(KNOWLEDGE)
        self.assertIn('At most four `search_knowledge` calls', text)
        self.assertIn('⚠ NON-CURRENT', text)


# The write-side kartoteka tools the tasks skill calls. Exact spellings.
WRITE_TOOLS = ('task_create', 'task_update', 'task_list')
# Same near-miss list as tests/test_task_queue_docs.py.
WRITE_NEAR_MISSES = ('task_claim', 'task_next', 'tasks_ready', 'task_get',
                     'task_ready_claim', 'tasks_create')


class TestTasksSkill(unittest.TestCase):
    def test_skill_exists(self):
        self.assertTrue((ROOT / TASKS).is_file(), TASKS + ' is missing')

    def test_spells_every_queue_tool(self):
        text = read(TASKS)
        for name in WRITE_TOOLS:
            self.assertIn(name, text, TASKS + ' never mentions ' + name)

    def test_uses_no_near_miss_spelling(self):
        text = read(TASKS)
        for wrong in WRITE_NEAR_MISSES:
            self.assertNotIn(wrong, text, TASKS + ' uses ' + wrong)

    def test_never_claims(self):
        # task_ready may be named only to say it is never called.
        lines = [line for line in read(TASKS).splitlines() if 'task_ready' in line]
        self.assertTrue(lines, TASKS + ' should state that it never calls task_ready')
        for line in lines:
            self.assertIn('never', line, TASKS + ' names task_ready outside a never-clause: ' + line)

    def test_add_goes_through_the_mirror_script(self):
        text = read(TASKS)
        self.assertIn('scripts/tasklist_tasks.py', text)
        self.assertIn('--raw', text)

    def test_carries_the_gate_messages(self):
        text = read(TASKS)
        self.assertIn('declare it with `/artel:setup`', text)
        self.assertIn('kartoteka is configured for this project but its MCP tools are not '
                      'available in this session', text)
        self.assertNotIn('--force', text)

    def test_release_confirms(self):
        text = read(TASKS)
        self.assertIn('AskUserQuestion', text)

    def test_release_never_touches_an_iteration_parent(self):
        text = read(TASKS)
        release = text[text.index('### `release <task-id>`'):]
        self.assertIn('parents are never released', release)

    def test_add_can_target_a_fix_section(self):
        text = read(TASKS)
        self.assertIn('--fix CRF|RTF|VF|FV', argument_hint(TASKS))
        self.assertIn('### `add <ticket> "<title>" --fix CRF|RTF|VF|FV [--hitl <reason>]`', text)
        self.assertIn('### manual-<YYYY-MM-DD>', text)

    def test_list_keeps_fix_rows_out_of_promotion_and_drained(self):
        text = read(TASKS)
        listing = text[text.index('### `list'):text.index('### `add')]
        self.assertIn('never for **promotion pending** or **drained**', listing)
        self.assertIn('**fix work open**', listing)

    def test_release_refuses_a_fix_row(self):
        text = read(TASKS)
        release = text[text.index('### `release <task-id>`'):]
        self.assertIn('is a fix-section row', release)


REFERENCE = 'docs/skills-reference.md'
GUIDE = 'docs/workflow-guide.md'
NEW_SKILLS = ('using-artel', 'knowledge', 'tasks')


def argument_hint(rel):
    """The skill's frontmatter `argument-hint`, unquoted (either quote style)."""
    for line in read(rel).splitlines():
        if line.startswith('argument-hint:'):
            return line.split(':', 1)[1].strip().strip('"\'')
    return None


def invocation_line(skill_name):
    """The Invocation line under skills-reference.md's `### <skill_name>` entry."""
    entry = None
    for line in read(REFERENCE).splitlines():
        if line.startswith('### '):
            entry = line[4:].strip()
        elif entry == skill_name and line.startswith('- **Invocation:**'):
            return line
    return None


class TestDocs(unittest.TestCase):
    def test_reference_has_an_entry_per_new_skill(self):
        text = read(REFERENCE)
        for name in NEW_SKILLS:
            self.assertIn('\n### ' + name + '\n', text, REFERENCE + ' has no entry for ' + name)

    def test_reference_invocation_quotes_the_frontmatter_hint(self):
        for name in ('knowledge', 'tasks'):
            hint = argument_hint('skills/' + name + '/SKILL.md')
            line = invocation_line(name)
            self.assertIsNotNone(line, REFERENCE + ' has no Invocation line for ' + name)
            self.assertIn('/artel:' + name + ' ' + hint, line)

    def test_reference_router_invocation_names_the_skill(self):
        self.assertIn('/artel:using-artel', invocation_line('using-artel') or '')

    def test_guide_quickstart_routes_to_both_front_doors(self):
        text = read(GUIDE)
        self.assertIn('/artel:knowledge', text)
        self.assertIn('/artel:tasks', text)
        self.assertIn('## Turn one: the router', text)

    def test_config_doc_names_the_front_doors_under_the_knowledge_key(self):
        text = read('docs/config.md')
        self.assertIn('#### The conversational front doors', text)
        self.assertIn('using_artel', text)

    def test_changelog_records_the_feature(self):
        text = read('CHANGELOG.md')
        self.assertIn('`using-artel`', text)
        self.assertIn('/artel:knowledge', text)
        self.assertIn('/artel:tasks', text)


class TestRouterCodeNavigation(unittest.TestCase):
    def test_router_routes_code_navigation_to_the_ast_index(self):
        text = read(ROUTER)
        self.assertIn('/ast-index:ast-index', text)
        self.assertIn('/ast-index:initialize', text)

    def test_router_gates_the_group_on_the_status_line(self):
        self.assertIn('ast-index: on PATH', read(ROUTER))


if __name__ == '__main__':
    unittest.main()
