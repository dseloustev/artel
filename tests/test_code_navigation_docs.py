"""`docs/code-navigation.md` is cited, complete, and the only place naming a tool.

Not a behaviour test: these are prompts, and there is no code path to exercise.
It guards three things the repo has already got wrong once.

**The dangling pointer.** Six agents cited `orchestrator-common.md` §1 for how to
*use* an index; §1 documents only the post-implementation *refresh* hook. The
citation was live, the section was real, and the topic was absent -- which is
exactly the failure no "does the file mention X" assertion can see. So every
`code-navigation.md` §N citation in the repo is resolved against the sections
that file actually has.

**The genericization rule.** `CLAUDE.md` forbids project literals in ported
bodies; `docs/design.md` records two sanctioned exceptions, the `using-artel`
router and §2 of this contract. An agent body or a second skill body reaching for
`ast-index` by name is the drift that rule exists to stop, and it would read as
perfectly sensible in review.

**The deliberate silences.** `qa`, `validator` and `task-planner` read artifacts,
not code; `merge-conflicts` Phase 5 scans for a string literal. Each is a
considered *no*, and a considered no is indistinguishable from an oversight to
anyone who did not make the decision -- so it is asserted, not commented.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = 'docs/code-navigation.md'

# Every body that navigates the host codebase. The five that already reached for
# an index and re-pointed here, then the five wired up alongside this contract.
CITING_AGENTS = (
    'agents/analyst.md',
    'agents/researcher.md',
    'agents/planner.md',
    'agents/implementer.md',
    'agents/figma-analyst.md',
    'agents/reviewer.md',
    'agents/tech-writer.md',
    'agents/vision-writer.md',
    'agents/issue-scout.md',
)
CITING_SKILLS = (
    'skills/agents-md-generator/SKILL.md',
    'skills/merge-conflicts/SKILL.md',
)
CITING = CITING_AGENTS + CITING_SKILLS

# Artifact readers, not code readers. Guarded for the absence, not the presence.
NON_NAVIGATING = (
    'agents/qa.md',
    'agents/validator.md',
    'agents/task-planner.md',
)

# The router is the first sanctioned exception (design.md, 2026-08-25); the
# contract is the second. Nothing else under agents/ or skills/ may name a tool.
NAMING_ALLOWED = ('skills/using-artel/SKILL.md',)

# `orchestrator-common.md` §1 keeps the refresh hook and must hand off the rest.
REFRESH_HOOK_DOC = 'docs/orchestrator-common.md'

PLUGIN_ROOT = '${CLAUDE_PLUGIN_ROOT}/'


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def unwrapped(rel):
    """`read`, with newlines flattened. A phrase these files are asserted to
    carry may legitimately wrap mid-phrase; the wrap is formatting, not drift."""
    return ' '.join(read(rel).split())


def body_files():
    """Every agent and skill body in the plugin."""
    return sorted(
        [p for p in (ROOT / 'agents').glob('*.md') if p.name != 'README.md']
        + list((ROOT / 'skills').glob('*/SKILL.md'))
    )


def contract_sections():
    """The `## N. Title` headings of the contract, as ints."""
    return {int(m) for m in re.findall(r'^## (\d+)\. ', read(CONTRACT), re.M)}


def section_body(n):
    """The text of the contract's `## n. …` section, heading excluded."""
    rest = read(CONTRACT).split(f'\n## {n}. ', 1)[1]
    return rest.split('\n## ', 1)[0]


def cited_sections(text):
    """Every `§N` that follows a mention of code-navigation.md on the same line."""
    found = set()
    for line in text.splitlines():
        if 'code-navigation.md' not in line:
            continue
        after = line.split('code-navigation.md', 1)[1]
        found.update(int(n) for n in re.findall(r'§(\d+)', after))
    return found


class ContractIsComplete(unittest.TestCase):

    def test_the_contract_exists(self):
        self.assertTrue((ROOT / CONTRACT).is_file())

    def test_it_has_the_five_numbered_sections(self):
        self.assertEqual({1, 2, 3, 4, 5}, contract_sections())

    def test_every_section_citation_in_the_repo_resolves(self):
        """The defect this contract was written to fix: a live citation into a
        section that does not cover the topic. A missing section is the case a
        test can catch."""
        sections = contract_sections()
        for rel in CITING + (REFRESH_HOOK_DOC, 'docs/skills-reference.md'):
            with self.subTest(rel):
                for n in cited_sections(read(rel)):
                    self.assertIn(n, sections, f'{rel} cites §{n}')


class EveryNavigatingBodyCitesIt(unittest.TestCase):

    def test_each_citing_body_points_at_the_contract(self):
        for rel in CITING:
            with self.subTest(rel):
                self.assertIn('docs/code-navigation.md', read(rel))

    def test_agents_resolve_it_through_the_plugin_root(self):
        """Agent bodies run from the install dir; a repo-relative path would not
        resolve there. Skills carry prose links too, so only agents are asserted."""
        for rel in CITING_AGENTS:
            with self.subTest(rel):
                self.assertIn(PLUGIN_ROOT + 'docs/code-navigation.md', read(rel))

    def test_no_body_still_points_at_the_refresh_hook_for_search(self):
        """`orchestrator-common.md` §1 owns the refresh hook only. Until 0.18.0
        `tasklist-writer` legitimately cited it -- its Final Verification step *was* a
        refresh; that step is the orchestrator's now (docs/gates.md §1), so no body
        cites the refresh hook at all."""
        for rel in CITING + ('agents/tasklist-writer.md',):
            with self.subTest(rel):
                self.assertNotIn('orchestrator-common.md` §1', read(rel))

    def test_the_refresh_hook_hands_off_the_query_side(self):
        self.assertIn('code-navigation.md', read(REFRESH_HOOK_DOC))


class DeliberateSilences(unittest.TestCase):

    def test_artifact_readers_are_left_alone(self):
        for rel in NON_NAVIGATING:
            with self.subTest(rel):
                self.assertNotIn('code-navigation.md', read(rel))

    def test_merge_conflicts_keeps_grep_for_the_conflict_marker(self):
        """A conflict marker is a string literal -- §3's worked example of when
        not to reach for the index. The Phase 5 scan stays Grep."""
        text = read('skills/merge-conflicts/SKILL.md')
        self.assertIn('Pattern: `<<<<<<<`', text)
        self.assertIn('Grep is correct here', text)


class GenericizationHolds(unittest.TestCase):

    def test_only_the_router_names_the_tool_in_a_body(self):
        """`CLAUDE.md`: never copy project literals. The contract carries the
        concrete names so the bodies do not have to."""
        for path in body_files():
            rel = path.relative_to(ROOT).as_posix()
            if rel in NAMING_ALLOWED:
                continue
            with self.subTest(rel):
                self.assertNotIn('ast-index', path.read_text(encoding='utf-8'))

    def test_the_router_still_names_it(self):
        self.assertIn('ast-index', read(NAMING_ALLOWED[0]))

    def test_the_mechanics_sections_name_the_tool(self):
        """§1 is the probe and §2 the command table; neither is actionable
        without a name."""
        for n in (1, 2):
            with self.subTest(n):
                self.assertIn('ast-index', section_body(n))

    def test_the_rules_sections_name_no_tool(self):
        """The line that keeps this portable: a host with a different index
        rewrites §§1-2 and keeps §§3-5 verbatim. A command name drifting into a
        rule is how that stops being true."""
        for n in (3, 4, 5):
            with self.subTest(n):
                self.assertNotIn('ast-index', section_body(n))

    def test_the_bodies_use_the_generic_phrase(self):
        for rel in CITING:
            with self.subTest(rel):
                self.assertIn('code-symbol index', unwrapped(rel))


if __name__ == '__main__':
    unittest.main()
