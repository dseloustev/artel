"""The arm-time sensitive-policy check: one warning line per run-owned file a host policy
floors above the effective mode, and silence otherwise. Advisory only — it never refuses an
arm, because the guard exempts those paths (hooks/README.md).

Contract: docs/autonomous-run.md §10.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import sensitive_policy_check as spc  # noqa: E402

TICKET = 'AW-1234'
CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}
RUN_GLOBS = ['.artel/run/*/run-state.json', '.artel/run/*/run-journal.md',
             '.artel/run/*/open-questions.md', '.artel/run/*/runtime-observation.md',
             '.artel/run/*/.stop-gate-blocks']


class CheckCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)
        (self.root / '.artel').mkdir()
        (self.root / '.artel' / 'config.json').write_text(json.dumps(CONFIG), encoding='utf-8')

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def write_host(self, value):
        (self.root / '.artel' / 'sensitive-paths.json').write_text(
            value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

    def run_check(self, ticket=TICKET, mode='plan-gate'):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = spc.main(['--ticket', ticket, '--mode', mode])
        return code, out.getvalue(), err.getvalue()


class TestWarns(CheckCase):
    def test_each_floored_run_owned_file_gets_one_line(self):
        self.write_host({'categories': [
            {'name': 'run-state', 'floor': 'full-gates', 'globs': RUN_GLOBS}]})
        code, out, _ = self.run_check()
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 5, out)
        for name in ('run-state.json', 'run-journal.md', 'open-questions.md',
                     'runtime-observation.md', '.stop-gate-blocks'):
            self.assertIn('.artel/run/{}/{}'.format(TICKET, name), out)
        self.assertIn('full-gates', lines[0])
        self.assertIn('effective plan-gate', lines[0])

    def test_a_bare_ticket_number_names_the_real_run_directory(self):
        self.write_host({'categories': [
            {'name': 'run-state', 'floor': 'full-gates',
             'globs': ['.artel/run/*/run-state.json']}]})
        code, out, _ = self.run_check(ticket='2452')
        self.assertEqual(code, 0)
        self.assertIn('.artel/run/AW-2452/run-state.json', out)

    def test_an_unknown_mode_is_ranked_like_yolo(self):
        """Never crash, never under-report: an unranked mode warns like the guard ranks it."""
        self.write_host({'categories': [
            {'name': 'ci-cd', 'floor': 'plan-gate',
             'globs': ['.artel/run/*/run-journal.md']}]})
        code, out, _ = self.run_check(mode='safety')
        self.assertEqual(code, 0)
        self.assertIn('.artel/run/{}/run-journal.md'.format(TICKET), out)


class TestSilent(CheckCase):
    def test_the_shipped_default_floors_nothing_run_owned(self):
        code, out, _ = self.run_check()
        self.assertEqual((code, out), (0, ''))

    def test_a_floor_the_mode_already_satisfies_is_silent(self):
        self.write_host({'categories': [
            {'name': 'run-state', 'floor': 'plan-gate',
             'globs': ['.artel/run/*/run-state.json']}]})
        self.assertEqual(self.run_check()[0:2], (0, ''))

    def test_a_broken_policy_is_ignored(self):
        self.write_host('{not json')
        code, out, err = self.run_check()
        self.assertEqual((code, out), (0, ''))
        self.assertIn('ignored', err)

    def test_a_policy_without_categories_is_silent(self):
        self.write_host({'categories': []})
        self.assertEqual(self.run_check()[0:2], (0, ''))


if __name__ == '__main__':
    unittest.main()
