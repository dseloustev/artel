"""The bratishka config block and the date-templated testing-PR names."""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka_config as cfg  # noqa: E402


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)

    def write(self, payload):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path


class TestLoad(ConfigCase):
    def test_missing_file_is_defaults(self):
        self.assertEqual(cfg.load(self.repo.name), cfg.DEFAULTS)

    def test_invalid_json(self):
        path = Path(self.repo.name) / '.artel' / 'config.json'
        path.parent.mkdir(parents=True)
        path.write_text('{', encoding='utf-8')
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.load(self.repo.name)
        self.assertEqual(ctx.exception.kind, 'invalid_config')

    def test_bratishka_is_not_an_object(self):
        self.write({'bratishka': []})
        with self.assertRaises(cfg.ConfigError):
            cfg.load(self.repo.name)

    def test_a_bad_key_type_names_the_key(self):
        self.write({'bratishka': {'qaLogins': 'alex'}})
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.load(self.repo.name)
        self.assertIn('qaLogins', ctx.exception.message)

    def test_a_bad_adapter_is_refused(self):
        self.write({'bratishka': {'spawn': {'adapter': 'carrier-pigeon'}}})
        with self.assertRaises(cfg.ConfigError) as ctx:
            cfg.load(self.repo.name)
        self.assertIn('spawn.adapter', ctx.exception.message)

    def test_a_partial_block_keeps_the_defaults(self):
        self.write({'bratishka': {'repo': 'o/r'}})
        block = cfg.load(self.repo.name)
        self.assertEqual(block['repo'], 'o/r')
        self.assertEqual(block['botLogin'], 'bratishka-bot')
        self.assertEqual(block['spawn']['adapter'], 'manual')

    def test_dashboard_url_loses_the_trailing_slash(self):
        self.write({'bratishka': {'dashboardUrl': 'https://b.example/'}})
        self.assertEqual(cfg.load(self.repo.name)['dashboardUrl'], 'https://b.example')


class TestTemplates(unittest.TestCase):
    def test_render(self):
        self.assertEqual(cfg.render('Testing {dd}-{mm}-{yy}', date(2026, 11, 1)),
                         'Testing 01-11-26')

    def test_render_yyyy(self):
        self.assertEqual(cfg.render('{yyyy}/{mm}/{dd}', date(2026, 11, 1)),
                         '2026/11/01')

    def test_match_date(self):
        self.assertEqual(cfg.match_date('Testing {dd}-{mm}-{yy}', 'Testing 01-11-26'),
                         date(2026, 11, 1))

    def test_match_date_rejects_the_legacy_dots(self):
        self.assertIsNone(cfg.match_date('Testing {dd}-{mm}-{yy}', 'Testing 02.11.26'))

    def test_match_date_rejects_an_impossible_day(self):
        self.assertIsNone(cfg.match_date('Testing {dd}-{mm}-{yy}', 'Testing 31-02-26'))

    def test_testing_date_tries_title_then_branch(self):
        block = dict(cfg.DEFAULTS)
        self.assertEqual(
            cfg.testing_date('Testing 03-11-26', 'feature/testing-03-11-26', block),
            date(2026, 11, 3))
        self.assertEqual(
            cfg.testing_date('whatever', 'feature/testing-03-11-26', block),
            date(2026, 11, 3))
        self.assertIsNone(cfg.testing_date('Testing 02.11.26', 'testing/x', block))
