import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common as h  # noqa: E402


class TestIsVerifiable(unittest.TestCase):
    def test_absent_surface_matches_everything(self):
        self.assertTrue(h.is_verifiable('anything/at/all.txt', {}))
        self.assertTrue(h.is_verifiable('a.py', {'verify': {}}))

    def test_positive_globs(self):
        config = {'verify': {'surface': ['src/**/*.ts']}}
        self.assertTrue(h.is_verifiable('src/deep/app.ts', config))
        self.assertFalse(h.is_verifiable('docs/readme.md', config))

    def test_negative_glob_excludes(self):
        config = {'verify': {'surface': ['lib/**/*.dart', '!*.g.dart']}}
        self.assertTrue(h.is_verifiable('lib/app/main.dart', config))
        self.assertFalse(h.is_verifiable('lib/app/main.g.dart', config))

    def test_only_negatives_implies_star_positive(self):
        config = {'verify': {'surface': ['!*.md']}}
        self.assertTrue(h.is_verifiable('src/app.py', config))
        self.assertFalse(h.is_verifiable('docs/readme.md', config))


class TestFindingKeys(unittest.TestCase):
    def test_unions_stage_keys(self):
        env = {'data': {'stages': [{'keys': ['s0:a', 's0:b']}, {'keys': ['s1:c']}]}}
        self.assertEqual(h.finding_keys(env), {'s0:a', 's0:b', 's1:c'})

    def test_tolerates_missing_shapes(self):
        self.assertEqual(h.finding_keys({}), set())
        self.assertEqual(h.finding_keys({'data': {}}), set())
        self.assertEqual(h.finding_keys({'data': {'stages': [{}]}}), set())


class TestRelpath(unittest.TestCase):
    def test_strips_cwd_prefix(self):
        data = {'tool_input': {'file_path': '/repo/src/a.py'}, 'cwd': '/repo'}
        self.assertEqual(h.relpath_from_tool_input(data), 'src/a.py')

    def test_leaves_other_paths_alone(self):
        data = {'tool_input': {'file_path': 'src/a.py'}, 'cwd': '/repo'}
        self.assertEqual(h.relpath_from_tool_input(data), 'src/a.py')


class TestResolveActiveTicket(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _write_pointer(self, content, specs_dir='specs/.current'):
        d = Path(specs_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / '.active_ticket').write_text(content, encoding='utf-8')

    def test_missing_pointer_returns_none(self):
        self.assertIsNone(h.resolve_active_ticket({}))

    def test_canonicalizes_all_accepted_forms(self):
        config = {'ticket': {'projectKey': 'PROJ'}}
        for raw in ('PROJ-123', '123', 'proj-123', 'PROJ-123-2', 'PROJ-123-p2', '123-2'):
            self._write_pointer(raw + '\n')
            self.assertEqual(h.resolve_active_ticket(config), 'PROJ-123', raw)

    def test_malformed_pointer_returns_none(self):
        self._write_pointer('not a ticket at all\n')
        self.assertIsNone(h.resolve_active_ticket({'ticket': {'projectKey': 'PROJ'}}))

    def test_respects_specs_dir_and_project_key(self):
        self._write_pointer('AW-77\n', specs_dir='my/specs')
        config = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'my/specs'}}
        self.assertEqual(h.resolve_active_ticket(config), 'AW-77')

    def test_invalid_utf8_returns_none(self):
        d = Path('specs/.current')
        d.mkdir(parents=True, exist_ok=True)
        (d / '.active_ticket').write_bytes(b'\xff\xfe garbage')
        self.assertIsNone(h.resolve_active_ticket({}))


if __name__ == '__main__':
    unittest.main()
