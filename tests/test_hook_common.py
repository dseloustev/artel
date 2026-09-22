import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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


class TestCanonicalTicket(unittest.TestCase):
    def test_default_project_key_and_pattern(self):
        config = {}
        self.assertEqual(h.canonical_ticket('PROJ-123', config), 'PROJ-123')
        self.assertEqual(h.canonical_ticket('123', config), 'PROJ-123')

    def test_phase_suffix_stripped(self):
        config = {'ticket': {'projectKey': 'AW'}}
        self.assertEqual(h.canonical_ticket('AW-12-3', config), 'AW-12')
        self.assertEqual(h.canonical_ticket('aw-12-p2', config), 'AW-12')

    def test_case_insensitive_project_key(self):
        config = {'ticket': {'projectKey': 'AW'}}
        self.assertEqual(h.canonical_ticket('aw-12', config), 'AW-12')

    def test_custom_pattern(self):
        config = {'ticket': {'projectKey': 'TST', 'pattern': r'^TSK-(\d+)$'}}
        self.assertEqual(h.canonical_ticket('TSK-99', config), 'TST-99')

    def test_broken_pattern_returns_none(self):
        config = {'ticket': {'pattern': '[invalid('}}
        self.assertIsNone(h.canonical_ticket('AW-1', config))

    def test_no_match_returns_none(self):
        config = {'ticket': {'projectKey': 'AW'}}
        self.assertIsNone(h.canonical_ticket('nonsense', config))


class TestTicketMatcher(unittest.TestCase):
    def test_returns_compiled_regex_and_project_key(self):
        config = {'ticket': {'projectKey': 'AW'}}
        compiled, project_key = h.ticket_matcher(config)
        self.assertIsNotNone(compiled)
        self.assertEqual(project_key, 'AW')

    def test_default_project_key_and_pattern(self):
        config = {}
        compiled, project_key = h.ticket_matcher(config)
        self.assertIsNotNone(compiled)
        self.assertEqual(project_key, 'PROJ')

    def test_broken_pattern_returns_none_regex(self):
        config = {'ticket': {'pattern': '[invalid('}}
        compiled, project_key = h.ticket_matcher(config)
        self.assertIsNone(compiled)
        self.assertEqual(project_key, 'PROJ')

    def test_regex_is_case_insensitive(self):
        config = {'ticket': {'projectKey': 'AW'}}
        compiled, _ = h.ticket_matcher(config)
        self.assertIsNotNone(compiled.match('aw-123'))
        self.assertIsNotNone(compiled.match('AW-123'))


def git(cwd, *args):
    subprocess.run(['git', '-C', str(cwd)] + list(args), check=True, capture_output=True, text=True)


def make_repo(path):
    path.mkdir(parents=True)
    git(path, 'init', '-q')
    git(path, 'config', 'user.email', 'test@example.com')
    git(path, 'config', 'user.name', 'Test')
    git(path, 'config', 'commit.gpgsign', 'false')
    (path / 'a.txt').write_text('a\n', encoding='utf-8')
    git(path, 'add', 'a.txt')
    git(path, 'commit', '-q', '-m', 'init')
    return path


class TestEnterSessionRoot(unittest.TestCase):
    """hooks.json starts every hook in $CLAUDE_PROJECT_DIR (the main checkout); a session
    that entered a linked worktree must be gated there instead."""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self._tmp.name))
        self.main = make_repo(base / 'main')
        self.worktree = self.main / '.claude' / 'worktrees' / 'T-1'
        git(self.main, 'worktree', 'add', '-q', '-b', 'feature/T-1', str(self.worktree))
        self.other = make_repo(base / 'other')
        os.chdir(self.main)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def here(self):
        return Path(os.getcwd()).resolve()

    def test_moves_into_a_linked_worktree(self):
        h.enter_session_root({'cwd': str(self.worktree)})
        self.assertEqual(self.here(), self.worktree.resolve())

    def test_moves_to_the_worktree_root_from_a_subdirectory(self):
        sub = self.worktree / 'deep' / 'dir'
        sub.mkdir(parents=True)
        h.enter_session_root({'cwd': str(sub)})
        self.assertEqual(self.here(), self.worktree.resolve())

    def test_stays_for_a_subdirectory_of_the_main_checkout(self):
        sub = self.main / 'src'
        sub.mkdir()
        h.enter_session_root({'cwd': str(sub)})
        self.assertEqual(self.here(), self.main.resolve())

    def test_stays_for_another_repository(self):
        h.enter_session_root({'cwd': str(self.other)})
        self.assertEqual(self.here(), self.main.resolve())

    def test_stays_without_a_usable_cwd(self):
        for data in ({}, {'cwd': ''}, {'cwd': str(self.main / 'missing')}):
            h.enter_session_root(data)
            self.assertEqual(self.here(), self.main.resolve(), data)

    def test_stays_when_the_current_directory_is_not_a_repository(self):
        os.chdir(self.main.parent)
        h.enter_session_root({'cwd': str(self.worktree)})
        self.assertEqual(self.here(), self.main.parent.resolve())

    def test_read_hook_input_enters_the_worktree(self):
        payload = '{"session_id": "s", "cwd": "%s"}' % self.worktree
        with mock.patch.object(sys, 'stdin', io.StringIO(payload)):
            data = h.read_hook_input()
        self.assertEqual(data['session_id'], 's')
        self.assertEqual(self.here(), self.worktree.resolve())

    def test_relpath_is_relative_to_the_entered_root(self):
        sub = self.worktree / 'lib'
        sub.mkdir()
        os.chdir(self.worktree)
        data = {'tool_input': {'file_path': str(self.worktree.resolve() / 'lib' / 'a.dart')},
                'cwd': str(sub)}
        self.assertEqual(h.relpath_from_tool_input(data), 'lib/a.dart')


HOOKS_DIR = Path(__file__).resolve().parent.parent / 'hooks'
# using_artel never reads its input: SessionStart fires before any worktree move, and its
# in-process test would block on a terminal's stdin.
READS_NO_INPUT = {'using_artel.py'}
# Not lifecycle hooks at all, so "has no main()" is not a violation: shared library
# modules that hooks (and scripts) import, kept in hooks/ because that is what imports
# them. kartoteka_http.py is kartoteka's HTTP client, shared by knowledge_mirror.py and,
# from later tasks, scripts/spec_store.py. spec_decision.py is the per-ticket storage
# decision module, shared by scripts/spec_store.py and hooks/spec_store_guard.py.
NOT_A_HOOK = {'hook_common.py', 'kartoteka_http.py', 'spec_decision.py'}


class TestHooksReadInputFirst(unittest.TestCase):
    """read_hook_input() moves the process into the session's worktree, so no hook may
    touch hook_common (config, run state, git) before calling it."""

    def first_h_call(self, path):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        calls = [n for n in ast.walk(main)
                 if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                 and n.value.id == 'h']
        calls.sort(key=lambda n: (n.lineno, n.col_offset))
        return calls[0].attr if calls else None

    def test_every_hook_reads_its_input_first(self):
        hooks = sorted(p for p in HOOKS_DIR.glob('*.py')
                       if p.name not in NOT_A_HOOK and p.name not in READS_NO_INPUT)
        self.assertTrue(hooks)
        for path in hooks:
            self.assertEqual(self.first_h_call(path), 'read_hook_input', path.name)


if __name__ == '__main__':
    unittest.main()
