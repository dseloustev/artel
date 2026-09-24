"""The 0.17.0 release record: images in the kartoteka spec store.

Pins the spellings an operator upgrading from 0.16.0 relies on -- the kartoteka
floor, the verbs, the migration path -- and that the design history records the
decisions. Prose is not asserted on beyond those spellings.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def since_0_16_0():
    # Everything since 0.16.0: [Unreleased] before the release is cut, [0.17.0] after.
    return read('CHANGELOG.md').split('## [Unreleased]', 1)[1].split('\n## [0.16.0]', 1)[0]


class TestChangelog(unittest.TestCase):
    def test_entry_names_the_kartoteka_floor(self):
        self.assertIn('**Requires kartoteka 0.44.0**', since_0_16_0())

    def test_entry_names_the_verbs_and_the_migration(self):
        text = since_0_16_0()
        for phrase in ('image sync', 'image fetch', '/artel:migrate-specs',
                       '.artel/run/<TICKET_ID>/images/', 'docs/spec-storage.md',
                       'the kartoteka daemon predates attachments (0.44.0); upgrade it',
                       'kartoteka migrate', 'unrecoverable', '.unverified'):
            self.assertIn(phrase, text)

    def test_entry_has_an_upgrading_section(self):
        self.assertIn('### Upgrading', since_0_16_0())


class TestDesignHistory(unittest.TestCase):
    def test_decision_log_records_the_decisions(self):
        text = read('docs/design.md')
        for heading in ('2026-09-23 — Images are inputs, not evidence',
                        '2026-09-23 — A separate attachment store, not binary artifacts',
                        '2026-09-23 — A sweep at fixed points instead of per-producer uploads',
                        '2026-09-23 — Hard-require kartoteka 0.44.0',
                        "2026-09-23 — A tracked image's age is its last commit's author time",
                        '2026-09-23 — An unrecoverable sweep is surfaced whole'):
            self.assertIn(heading, text)

    def test_follow_ups_name_the_owed_smoke_test_and_the_parked_items(self):
        text = read('docs/design.md')
        self.assertIn('**Store mode and spec images: the live smoke test is still owed.**', text)
        self.assertNotIn("**Store mode's live smoke test is still owed.**", text)
        self.assertIn('**Spec images: parked follow-ups.**', text)


class TestKartotekaRequirements(unittest.TestCase):
    def test_k3_is_recorded_as_shipped(self):
        text = read('docs/kartoteka-requirements.md')
        self.assertIn('- **K3 attachment store** — **Shipped 0.44.0.**', text)
        self.assertIn('| 6.3 | Attachment store (K3) | Spec images | Medium | **Shipped 0.44.0** |',
                      text)


if __name__ == '__main__':
    unittest.main()
