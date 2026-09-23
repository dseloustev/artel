"""Images in the kartoteka spec store reach every agent, skill and orchestrator.

Prompts, not code: this pins the spellings docs/spec-storage.md §4.6 depends on --
the agents' stock paragraph, the sweep command at every sweep point, the staging
exclude at every staging site, the journal line and the restore excludes -- the
way test_spec_store_conversion_docs.py pins the document store's. Prose is not
asserted on beyond those spellings. Whitespace is normalised, so rewrapping a
paragraph never breaks a pin.
"""
import fnmatch
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'hooks'))
import kartoteka_http as kh  # noqa: E402

AGENTS = sorted(p.stem for p in (ROOT / 'agents').glob('*.md') if p.name != 'README.md')

STOCK = ('Evidence text (`review/findings.json`, `verify/`, `runtime/*.md`) and `.active_ticket` '
         'stay files on both paths. On the kartoteka path, images under the trail (`design/`, '
         '`runtime/`, any `*.png|jpg|jpeg|gif|webp`) are stored in kartoteka. To view one, run '
         '`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image fetch <logical path>` and '
         'Read the path it prints. Save a new image to its logical path as usual; your '
         'orchestrator sweeps it in (spec-storage.md §4.6).')
OLD_STOCK = 'Evidence (`review/findings.json`, `verify/`, `runtime/`, `design/`)'
FIGMA_STEP_7 = ('On the kartoteka path `design-analysis.md` is stored (spec-storage.md §4.1). '
                'The `figma-analysis` skill sweeps the `design/` screenshots into kartoteka after '
                "you return, and the document's links to them are unchanged.")
SWEEP = ('python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py image sync <TICKET_ID> '
         '--author artel:{}')
JOURNAL = 'image-sync: <n> left local — <first error line>'
NOTHING_STAGED = '`git diff --cached --quiet`'
RSYNC_IMAGES = ("--exclude='*.[pP][nN][gG]' --exclude='*.[jJ][pP][gG]' "
                "--exclude='*.[jJ][pP][eE][gG]' --exclude='*.[gG][iI][fF]' "
                "--exclude='*.[wW][eE][bB][pP]'")


def excludes():
    return ["':(exclude,icase,glob)<specs.dir>/<TICKET_ID>/**/*{}'".format(ext)
            for ext in kh.IMAGE_TYPES]


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def skill(name):
    return read('skills/{}/SKILL.md'.format(name))


def flat(text):
    return ' '.join(text.split())


def between(text, start, end):
    """From `start` up to the first `end` after it."""
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


class TestAgents(unittest.TestCase):
    def test_there_are_thirteen_agents(self):
        self.assertEqual(len(AGENTS), 13, AGENTS)

    def test_every_spec_store_section_carries_the_image_paragraph(self):
        for name in AGENTS:
            with self.subTest(name):
                text = read('agents/{}.md'.format(name))
                section = flat(between(text, '\n## Spec store\n', '\n## '))
                self.assertIn(flat(STOCK), section)
                self.assertNotIn(OLD_STOCK, flat(text))

    def test_figma_analyst_hands_its_screenshots_to_the_sweep(self):
        step = flat(between(read('agents/figma-analyst.md'),
                            '### 7. Write the artifact + evidence', '\n## '))
        self.assertIn(flat(FIGMA_STEP_7), step)
        self.assertNotIn('screenshots stay files on both paths', step)
