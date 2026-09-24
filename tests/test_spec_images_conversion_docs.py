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
SWEEP = ('python3 ${{CLAUDE_PLUGIN_ROOT}}/scripts/spec_store.py image sync <TICKET_ID> '
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


class TestCheckpointProcedure(unittest.TestCase):
    """feature-development's shared procedure -- dev's phase checkpoints run it too."""

    def setUp(self):
        self.text = skill('feature-development')

    def part(self, start, end):
        return flat(between(self.text, start, end))

    def test_the_sweep_runs_before_the_idempotence_check(self):
        step = self.part('2. **Image sweep', '3. **Quality gate (phase-end only).**')
        sweep = SWEEP.format('<skill>')
        self.assertIn(sweep, step)
        self.assertIn('`dev` when `dev` runs this procedure', step)
        self.assertIn(JOURNAL, step)
        self.assertIn('§5.6', step)
        self.assertLess(step.index(sweep), step.index('`git status --porcelain`'))

    def test_staging_excludes_every_image_extension(self):
        step = self.part('4. **Stage explicitly.**', '5. **Commit.**')
        for exclude in excludes():
            self.assertIn(exclude, step)

    def test_a_commit_of_nothing_is_skipped(self):
        self.assertIn(NOTHING_STAGED, self.part('5. **Commit.**', '6. **Push.**'))

    def test_the_planning_checkpoint_ignores_untracked_images(self):
        self.assertIn('images aside, only `.active_ticket` changed',
                      self.part('Then run the **planning checkpoint**', '### 5. Autonomous tail'))

    def test_the_final_report_sweeps_and_lists_what_is_left(self):
        report = self.part('### 9. Final report', '\n## Important')
        self.assertIn(SWEEP.format('feature-development'), report)
        self.assertIn('§5.6', report)
        self.assertIn('images left local', report)

    def test_an_unrecoverable_sweep_is_surfaced_whole_not_summarised(self):
        step = self.part('2. **Image sweep', '3. **Quality gate (phase-end only).**')
        self.assertIn('unrecoverable', step)
        self.assertIn('whole message', step)
        report = self.part('### 9. Final report', '\n## Important')
        self.assertIn('unrecoverable', report)
        self.assertIn('whole message', report)

    def test_the_run_contract_names_the_sweep(self):
        contract = flat(between(read('docs/autonomous-run.md'),
                                '## 14. Checkpoint commits & pushes', '## 15.'))
        self.assertIn('the image sweep', contract)
        self.assertIn('§4.6', contract)

    def test_the_phase_end_checkpoint_chain_starts_with_the_sweep(self):
        chain = self.part('| 10.7 | `PHASE_CHECKPOINT` |', '\n\n**Journal')
        self.assertIn('the image sweep (kartoteka path) → the `verify.commands` gate', chain)
        self.assertIn('explicit staging (no trail image on the kartoteka path)', chain)


class TestDev(unittest.TestCase):
    def setUp(self):
        self.text = skill('dev')

    def test_the_work_list_checkpoint_sweeps_then_stages_without_images(self):
        step = flat(between(self.text, '### 3. Arm the run', '### 4. Implement (autonomous)'))
        sweep = SWEEP.format('dev')
        self.assertIn(sweep, step)
        self.assertIn(JOURNAL, step)
        for exclude in excludes():
            self.assertIn(exclude, step)
        self.assertLess(step.index(sweep), step.index(excludes()[0]))
        self.assertIn('images aside, only `.active_ticket` changed', step)

    def test_the_report_sweeps_and_lists_what_is_left(self):
        report = flat(between(self.text, '### 9. Report', '\n## Important'))
        self.assertIn(SWEEP.format('dev'), report)
        self.assertIn('§5.6', report)
        self.assertIn('images left local', report)

    def test_an_unrecoverable_sweep_is_surfaced_whole_not_summarised(self):
        step = flat(between(self.text, '### 3. Arm the run', '### 4. Implement (autonomous)'))
        self.assertIn('unrecoverable', step)
        self.assertIn('whole message', step)
        report = flat(between(self.text, '### 9. Report', '\n## Important'))
        self.assertIn('unrecoverable', report)
        self.assertIn('whole message', report)

    def test_phase_checkpoints_use_the_shared_procedure(self):
        step = flat(between(self.text, '### 7.5 Phase checkpoint', '### 8. Complete'))
        self.assertIn('`feature-development` `## Checkpoint commits & pushes`', step)
        self.assertIn('the image sweep (kartoteka path) → the `verify.commands` gate', step)
        self.assertIn('explicit staging (no trail image on the kartoteka path)', step)


class TestFigmaAnalysis(unittest.TestCase):
    def test_sweeps_once_the_agent_has_returned(self):
        phase3 = flat(between(skill('figma-analysis'), '### Phase 3: Finalize', '### Completion'))
        sweep = SWEEP.format('figma-analysis')
        self.assertIn(sweep, phase3)
        self.assertIn(JOURNAL, phase3)
        # before the DESIGN_BLOCKED stop, so a parked design's screenshots are swept too
        self.assertLess(phase3.index(sweep),
                        phase3.index('`DESIGN_BLOCKED: <the parked findings>`'))

    def test_the_report_counts_the_sweep(self):
        completion = flat(between(skill('figma-analysis'), '### Completion', '## Important Rules'))
        self.assertIn('`uploaded`, `unchanged`, `failed` and `skipped` counts', completion)
        self.assertIn('each `skipped` entry with its reason', completion)

    def test_an_unrecoverable_sweep_is_surfaced_whole_not_summarised(self):
        phase3 = flat(between(skill('figma-analysis'), '### Phase 3: Finalize', '### Completion'))
        self.assertIn('unrecoverable', phase3)
        self.assertIn('whole message', phase3)


class TestPrCreate(unittest.TestCase):
    def setUp(self):
        self.step = flat(between(skill('pr-create'), '### 3. Commit & push', '### 4. PR'))

    def test_sweeps_then_stages_without_images(self):
        sweep = SWEEP.format('pr-create')
        self.assertIn(sweep, self.step)
        self.assertIn(JOURNAL, self.step)
        for exclude in excludes():
            self.assertIn(exclude, self.step)
        self.assertLess(self.step.index(sweep), self.step.index(excludes()[0]))

    def test_a_commit_of_nothing_is_skipped(self):
        self.assertIn(NOTHING_STAGED, self.step)

    def test_the_trail_holds_only_evidence_text(self):
        self.assertIn('holds only evidence text', self.step)
        self.assertNotIn('holds only evidence)', self.step)

    def test_an_unrecoverable_sweep_is_surfaced_whole_not_summarised(self):
        self.assertIn('unrecoverable', self.step)
        self.assertIn('whole message', self.step)


class TestSpecStorageUnrecoverable(unittest.TestCase):
    """docs/spec-storage.md's own wording for the `unrecoverable` ruling (P-1)."""

    def setUp(self):
        self.text = read('docs/spec-storage.md')

    def test_the_5_6_bullet_is_pinned(self):
        bullet = flat(between(self.text, "**An `unrecoverable` sweep is surfaced whole",
                              '\n- **A failed fetch'))
        self.assertIn('unrecoverable', bullet)
        self.assertIn('whole message', bullet)

    def test_the_8_image_sync_row_names_the_exit(self):
        row = flat(between(self.text, '`image sync <ticket-id> --author A`',
                           '\n\nEvery verb exits'))
        self.assertIn('`2` kind `unrecoverable`', row)


class TestRestoreContext(unittest.TestCase):
    def setUp(self):
        self.text = flat(skill('restore-context'))

    def test_the_image_excludes_join_the_document_excludes(self):
        # the Branch A sentence, and the comment above each of the two rsync commands
        self.assertEqual(self.text.count('post_feedback}.md ' + RSYNC_IMAGES), 3)

    def test_the_patterns_match_every_image_extension_in_any_case(self):
        patterns = re.findall(r"--exclude='(\*\.[^']+)'", RSYNC_IMAGES)
        for ext in kh.IMAGE_TYPES:
            for name in ('x' + ext, 'x' + ext.upper(), 'x' + ext[:2].upper() + ext[2:]):
                with self.subTest(name):
                    self.assertTrue(any(fnmatch.fnmatchcase(name, p) for p in patterns))
        for name in ('observation.md', 'findings.json', 'plan.md', 'iteration-1.json'):
            with self.subTest(name):
                self.assertFalse(any(fnmatch.fnmatchcase(name, p) for p in patterns))

    def test_the_report_names_images_too(self):
        self.assertIn('old spec copies and images stay in the context store', self.text)
