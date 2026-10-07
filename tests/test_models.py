"""The OpenCode dispatch-model resolver: scripts/models.py.

Spec: docs/superpowers/specs/2026-10-07-opencode-model-selection-design.md (gitignored).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'models.py'

sys.path.insert(0, str(ROOT / 'scripts'))
import models  # noqa: E402


def run_cli(*args, cwd=None):
    proc = subprocess.run([sys.executable, str(SCRIPT)] + list(args),
                          capture_output=True, text=True, cwd=cwd)
    return proc.returncode, json.loads(proc.stdout)


class ResolverCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)

    def write_config(self, config):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding='utf-8')

    def resolve(self, site, repo=None):
        return run_cli('resolve', '--site', site, '--repo', repo or self.repo.name)


class TestResolve(ResolverCase):
    def test_every_fixed_site_resolves(self):
        sites = {'implementer': {'light': 'p/light', 'full': 'p/full', 'fix': 'p/fix',
                                 'stepUp': 'p/step-up'},
                 'reviewer': {'task': 'p/task', 'phase': 'p/phase', 'reReview': 'p/review',
                              'plan': 'p/plan', 'deepReview': 'p/deep'},
                 'reviewForecaster': 'p/forecast'}
        self.write_config({'models': {'opencode': dict(sites, agents={'analyst': 'p/analyst'})}})
        expected = {'implementer.light': 'p/light', 'implementer.full': 'p/full',
                    'implementer.fix': 'p/fix', 'implementer.stepUp': 'p/step-up',
                    'reviewer.task': 'p/task', 'reviewer.phase': 'p/phase',
                    'reviewer.reReview': 'p/review', 'reviewer.plan': 'p/plan',
                    'reviewer.deepReview': 'p/deep', 'reviewForecaster': 'p/forecast',
                    'agents.analyst': 'p/analyst'}
        for site, model in expected.items():
            code, out = self.resolve(site)
            self.assertEqual(code, 0, site)
            self.assertTrue(out['ok'], site)
            self.assertEqual(out['data'], {'site': site, 'model': model})

    def test_a_variant_resolves_verbatim(self):
        self.write_config({'models': {'opencode': {'reviewer': {'task': 'p/m#xhigh'}}}})
        code, out = self.resolve('reviewer.task')
        self.assertEqual((code, out['data']['model']), (0, 'p/m#xhigh'))

    def test_unset_site_is_null(self):
        self.write_config({'models': {'opencode': {'reviewer': {'task': 'p/task'}}}})
        code, out = self.resolve('reviewer.phase')
        self.assertEqual(code, 0)
        self.assertIsNone(out['data']['model'])

    def test_missing_config_is_null(self):
        code, out = self.resolve('reviewer.task')
        self.assertEqual((code, out['data']['model']), (0, None))

    def test_reserved_agent_names_are_ignored(self):
        self.write_config({'models': {'opencode': {'agents': {
            'implementer': 'p/x', 'reviewer': 'p/y', 'review-forecaster': 'p/z'}}}})
        for name in ('implementer', 'reviewer', 'review-forecaster'):
            code, out = self.resolve('agents.' + name)
            self.assertEqual(code, 0, name)
            self.assertIsNone(out['data']['model'], name)

    def test_unknown_keys_are_ignored(self):
        self.write_config({'models': {'opencode': {
            'claude-code': {'anything': True},
            'implementer': {'light': 'p/light'}}}})
        code, out = self.resolve('reviewer.phase')
        self.assertEqual((code, out['data']['model']), (0, None))
        code, out = self.resolve('implementer.light')
        self.assertEqual((code, out['data']['model']), (0, 'p/light'))

    def test_unknown_site_is_invalid_argument(self):
        code, out = self.resolve('reviewer.review')
        self.assertEqual(code, 2)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error']['kind'], 'invalid_argument')
        self.assertIn('unknown site: reviewer.review', out['error']['message'])

    def test_missing_site_flag_is_invalid_argument(self):
        code, out = run_cli('resolve', '--repo', self.repo.name)
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_no_command_is_invalid_argument(self):
        code, out = run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_unknown_flag_is_invalid_argument(self):
        code, out = run_cli('resolve', '--site', 'reviewer.task', '--wat')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_malformed_values_are_invalid_config(self):
        for bad in ('sonnet', 'a/b/c', 'a b/c', '/b', 'a/', 'a/b#', 'a/b#c#d'):
            self.write_config({'models': {'opencode': {'reviewer': {'task': bad}}}})
            code, out = self.resolve('reviewer.task')
            self.assertEqual(code, 2, bad)
            self.assertEqual(out['error']['kind'], 'invalid_config', bad)

    def test_non_string_site_value_is_invalid_config(self):
        self.write_config({'models': {'opencode': {'reviewer': {'task': 3}}}})
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_non_object_intermediate_is_invalid_config(self):
        self.write_config({'models': {'opencode': {'reviewer': []}}})
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_non_object_models_is_invalid_config(self):
        self.write_config({'models': []})
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_non_object_opencode_is_invalid_config(self):
        self.write_config({'models': {'opencode': []}})
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_non_object_agents_is_invalid_config(self):
        self.write_config({'models': {'opencode': {'agents': []}}})
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_unreadable_config_is_invalid_config(self):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{not json', encoding='utf-8')
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_config')

    def test_repo_defaults_to_the_working_directory(self):
        self.write_config({'models': {'opencode': {'reviewer': {'task': 'p/task'}}}})
        code, out = run_cli('resolve', '--site', 'reviewer.task', cwd=self.repo.name)
        self.assertEqual((code, out['data']['model']), (0, 'p/task'))

    def test_the_config_is_read_at_call_time(self):
        self.write_config({'models': {'opencode': {'reviewer': {'task': 'p/one'}}}})
        _, first = self.resolve('reviewer.task')
        self.write_config({'models': {'opencode': {'reviewer': {'task': 'p/two'}}}})
        _, second = self.resolve('reviewer.task')
        self.assertEqual((first['data']['model'], second['data']['model']), ('p/one', 'p/two'))

    def test_the_envelope_shape(self):
        code, out = self.resolve('reviewer.task')
        self.assertEqual(code, 0)
        self.assertEqual(out['verb'], 'models-resolve')
        self.assertIsInstance(out['elapsed_ms'], int)
        self.assertNotIn('error', out)


class TestValueForm(unittest.TestCase):
    def test_the_value_form(self):
        for good in ('p/m', 'provider/model#variant', 'a-b_c/d.e#f'):
            self.assertTrue(models.value_ok(good), good)
        for bad in ('sonnet', 'a/b/c', 'a b/c', '/b', 'a/', 'a/b#', 'a/b#c#d', '', None, 3):
            self.assertFalse(models.value_ok(bad), bad)


if __name__ == '__main__':
    unittest.main()
