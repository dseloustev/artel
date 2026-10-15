"""What OpenCode loads out of the plugin directory, and what it must not.

OpenCode registers EVERY `.md` directly under `agents/` as an agent, named by its
file stem. A stray `README.md` there becomes an agent called `README` — no
frontmatter, so it reaches the picker with an empty description and "All tools",
offered to the model as a real dispatch target. Prose about the crew lives in
docs/agents.md; this guards the directory against the next one.

Same family as tests/test_build_opencode.py: a surface defect nobody sees until a
session picks the wrong thing.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


def frontmatter(path):
    """The `key: value` pairs of a leading `---` block, or {} if there is none."""
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---\n'):
        return {}
    _, _, rest = text.partition('---\n')
    block, sep, _ = rest.partition('\n---')
    if not sep:
        return {}
    fields = {}
    for line in block.splitlines():
        key, colon, value = line.partition(':')
        if colon and not key.startswith((' ', '\t', '#')):
            fields[key.strip()] = value.strip().strip('"\'')
    return fields


class TestAgentsDirectory(unittest.TestCase):
    """Every .md in agents/ is loaded as an agent — so every .md must be one."""

    def agent_files(self):
        return sorted((ROOT / 'agents').glob('*.md'))

    def test_directory_is_not_empty(self):
        self.assertTrue(self.agent_files(), 'agents/ has no agent definitions')

    def test_no_readme_or_other_prose(self):
        for path in self.agent_files():
            self.assertNotEqual(
                path.name.lower(), 'readme.md',
                'agents/README.md loads as an agent named README — put prose in docs/agents.md')

    def test_every_file_has_agent_frontmatter(self):
        for path in self.agent_files():
            fields = frontmatter(path)
            self.assertIn('description', fields, path.name + ' has no frontmatter description')
            self.assertTrue(fields['description'], path.name + ' has an empty description')
            self.assertEqual(fields.get('mode'), 'subagent',
                             path.name + ' is not declared `mode: subagent`')

    def test_the_filename_is_the_agent_name(self):
        for path in self.agent_files():
            self.assertRegex(path.stem, NAME)


class TestSkillsDirectory(unittest.TestCase):
    """Skills are discovered as skills/<name>/SKILL.md, so a loose .md is inert —
    but a skill folder without a SKILL.md is a silently missing skill."""

    def test_every_skill_folder_has_a_skill_md(self):
        for path in sorted((ROOT / 'skills').iterdir()):
            if path.is_dir():
                self.assertTrue((path / 'SKILL.md').is_file(), 'missing ' + str(path / 'SKILL.md'))

    def test_every_skill_declares_a_matching_name(self):
        for path in sorted((ROOT / 'skills').glob('*/SKILL.md')):
            fields = frontmatter(path)
            self.assertEqual(fields.get('name', ''), path.parent.name, str(path))
            self.assertTrue(fields.get('description'), str(path) + ' has no description')


if __name__ == '__main__':
    unittest.main()
