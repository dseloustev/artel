import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import knowledge_mirror as km  # noqa: E402

CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


class TestArtifactIdentity(unittest.TestCase):
    def test_ticket_wide_path(self):
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/prd.md', CONFIG),
            ('AW-1234', 'prd', 'prd.md'),
        )

    def test_phase_scoped_path_keeps_ticket_key_canonical(self):
        # The phase belongs in `name`, never in ticket_key: related() joins
        # artifacts to Jira documents whose ticket_keys[] carry the bare key.
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/phase-2/prd.md', CONFIG),
            ('AW-1234', 'prd', 'phase-2.prd.md'),
        )

    def test_tasks_md_maps_to_the_tasklist_stage(self):
        # The one stem exception: artel calls the same document tasklist.md
        # ticket-wide and phase-<N>/tasks.md phase-scoped.
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/phase-2/tasks.md', CONFIG),
            ('AW-1234', 'tasklist', 'phase-2.tasks.md'),
        )

    def test_name_never_contains_a_slash(self):
        # GET /api/artifacts/{ticket_key}/{stage}/{name} uses a plain path
        # converter, which will not match across '/'.
        for rel in ('specs/.current/AW-1234/prd.md',
                    'specs/.current/AW-1234/phase-7/plan.md'):
            _, _, name = km.artifact_identity(rel, CONFIG)
            self.assertNotIn('/', name)

    def test_unlisted_filename_is_not_mirrored(self):
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/pr-pending.md', CONFIG))
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/change-report.html', CONFIG))

    def test_evidence_subdirectories_are_not_mirrored(self):
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/runtime/observation.md', CONFIG))
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/review/findings.json', CONFIG))

    def test_path_outside_specs_dir_is_not_mirrored(self):
        self.assertIsNone(km.artifact_identity('lib/main.dart', CONFIG))
        self.assertIsNone(km.artifact_identity('.artel/run/AW-1234/run-state.json', CONFIG))

    def test_file_directly_under_specs_dir_is_not_mirrored(self):
        # The specs root holds .active_ticket, and deep-review's standalone
        # mode writes review-claude.md there; neither is ticket-scoped.
        self.assertIsNone(km.artifact_identity('specs/.current/review-claude.md', CONFIG))

    def test_directory_not_matching_the_ticket_pattern_is_not_mirrored(self):
        self.assertIsNone(km.artifact_identity('specs/.current/scratch/prd.md', CONFIG))

    def test_non_default_specs_dir_is_honored(self):
        config = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'design/tickets'}}
        self.assertEqual(
            km.artifact_identity('design/tickets/AW-9/plan.md', config),
            ('AW-9', 'plan', 'plan.md'),
        )
        self.assertIsNone(km.artifact_identity('specs/.current/AW-9/plan.md', config))


if __name__ == '__main__':
    unittest.main()
