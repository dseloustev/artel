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

# Skills the router routes to that ship later in this same change. Drop this
# tuple once both skills exist on disk.
PLANNED_SKILLS = ('knowledge', 'tasks')


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
        names = set(skill_names()) | set(PLANNED_SKILLS)
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


if __name__ == '__main__':
    unittest.main()
