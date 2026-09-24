import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import verify  # noqa: E402
import hook_common as h  # noqa: E402


class TestNormalizeKeys(unittest.TestCase):
    def test_strips_ansi_and_digits(self):
        out = '\x1b[31msrc/app.ts:10:5 error no-unused-vars\x1b[0m'
        self.assertEqual(verify.normalize_keys(out, 0),
                         ['s0:src/app.ts:: error no-unused-vars'])

    def test_collapses_whitespace_and_drops_empty_lines(self):
        out = 'a   b\n\n   \n\tc  d\n'
        self.assertEqual(verify.normalize_keys(out, 1), ['s1:a b', 's1:c d'])

    def test_dedupes_repeated_lines(self):
        out = 'warn: x1\nwarn: x2\nother\n'
        # digit-stripping folds x1/x2 into one key
        self.assertEqual(verify.normalize_keys(out, 0), ['s0:warn: x', 's0:other'])

    def test_caps_at_max_keys_per_stage(self):
        # digit-free lines so digit-stripping cannot fold them together
        lines = '\n'.join('issue-' + ('x' * (i + 1)) for i in range(250))
        keys = verify.normalize_keys(lines, 0)
        self.assertEqual(len(keys), verify.MAX_KEYS_PER_STAGE)

    def test_prefixes_stage_index(self):
        self.assertEqual(verify.normalize_keys('boom', 3), ['s3:boom'])


class TestSubstituteFiles(unittest.TestCase):
    def test_replaces_placeholder_with_quoted_paths(self):
        cmd = verify.substitute_files('eslint {files}', ['a.ts', 'dir with space/b.ts'])
        self.assertEqual(cmd, "eslint a.ts 'dir with space/b.ts'")

    def test_without_placeholder_returns_command_unchanged(self):
        self.assertEqual(verify.substitute_files('npm run lint', ['a.ts']), 'npm run lint')


class TestClassifyExit(unittest.TestCase):
    def test_zero_is_ok(self):
        self.assertEqual(verify.classify_exit(0), 'ok')

    def test_126_and_127_are_env_errors(self):
        self.assertEqual(verify.classify_exit(126), 'env_error')
        self.assertEqual(verify.classify_exit(127), 'env_error')

    def test_other_nonzero_is_findings(self):
        for code in (1, 2, 3, 100, 255):
            self.assertEqual(verify.classify_exit(code), 'findings')


class TestParseArgs(unittest.TestCase):
    def test_parses_legacy_fast_files_timeout(self):
        inv = verify.parse_args(['--fast', '--files', 'a.py,b.py', '--timeout', '90'])
        self.assertIsNone(inv['gate'])
        self.assertTrue(inv['fast'])
        self.assertEqual(inv['files'], ['a.py', 'b.py'])
        self.assertEqual(inv['timeout'], 90)
        self.assertFalse(inv['record_baseline'])
        self.assertIsNone(inv['ticket'])

    def test_defaults(self):
        inv = verify.parse_args([])
        self.assertEqual(inv, {'gate': None, 'fast': False, 'files': None,
                               'timeout': verify.DEFAULT_TIMEOUT,
                               'record_baseline': False, 'ticket': None})

    def test_rejects_blank_files(self):
        for blank in ('', '   ', ',,,'):
            with self.assertRaises(ValueError):
                verify.parse_args(['--files', blank])

    def test_rejects_unknown_flag(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['--paths', 'a.py'])

    def test_task_gate_needs_files(self):
        inv = verify.parse_args(['task', '--files', 'lib/a.dart'])
        self.assertEqual(inv['gate'], 'task')
        self.assertEqual(inv['files'], ['lib/a.dart'])
        with self.assertRaises(ValueError) as ctx:
            verify.parse_args(['task'])
        self.assertIn('--files', str(ctx.exception))

    def test_checkpoint_gate_flags(self):
        inv = verify.parse_args(['checkpoint', '--record-baseline', '--ticket', 'AW-12-3'])
        self.assertEqual(inv['gate'], 'checkpoint')
        self.assertTrue(inv['record_baseline'])
        self.assertEqual(inv['ticket'], 'AW-12-3')
        self.assertIsNone(inv['files'])

    def test_unknown_gate_token_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            verify.parse_args(['final'])
        self.assertIn('final', str(ctx.exception))

    def test_checkpoint_only_flags_are_rejected_elsewhere(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['--record-baseline'])
        with self.assertRaises(ValueError):
            verify.parse_args(['task', '--files', 'a.py', '--ticket', 'AW-1'])
        with self.assertRaises(ValueError):
            verify.parse_args(['--ticket', 'AW-1'])

    def test_fast_belongs_to_the_legacy_form(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['task', '--fast', '--files', 'a.py'])

    def test_blank_ticket_is_rejected(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['checkpoint', '--ticket', '   '])
        with self.assertRaises(ValueError):
            verify.parse_args(['checkpoint', '--ticket'])


class GateCase(unittest.TestCase):
    """A temp checkout as cwd, with .artel/config.json written per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, previous)
        os.makedirs('.artel', exist_ok=True)

    def write_config(self, verify_cfg=None, **extra):
        cfg = {'verify': verify_cfg or {}}
        cfg.update(extra)
        Path('.artel/config.json').write_text(json.dumps(cfg), encoding='utf-8')

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = verify.main(argv)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1, 'exactly one envelope line')
        return code, json.loads(lines[0])

    def touch(self, *paths):
        for rel in paths:
            path = Path(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('', encoding='utf-8')

    def many_findings_script(self, extra_line=None):
        """A command printing 240 distinct, digit-free finding lines (past the envelope's
        key cap), optionally one more, then exiting 1."""
        lines = ["import sys",
                 "for i in range(240):",
                 "    print('lib/' + chr(97 + i // 26) + chr(97 + i % 26) + '.py: error')"]
        if extra_line:
            lines.append('print({!r})'.format(extra_line))
        lines.append('sys.exit(1)')
        Path('many.py').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return 'python3 many.py'


GREEN = "sh -c 'echo ok {files}; exit 0'"
RED = "sh -c 'echo lib/a.py:3:1 error boom; exit 1'"
RED_OTHER = "sh -c 'echo test/a_test.py:9 FAILED; exit 1'"
MISSING = 'no-such-command-artel-gate-test'


class TestLegacyForm(GateCase):
    def test_no_commands_is_skipped_exit_0(self):
        self.write_config({})
        code, env = self.run_main([])
        self.assertEqual(code, 0)
        self.assertTrue(env['ok'])
        self.assertEqual(env['verb'], 'verify')
        self.assertEqual(env['data'], {'skipped': True, 'stages': []})

    def test_green_commands_keep_the_old_fields_and_gain_a_name(self):
        self.write_config({'commands': [GREEN, GREEN]})
        code, env = self.run_main([])
        self.assertEqual(code, 0)
        self.assertFalse(env['data']['skipped'])
        self.assertEqual([s['name'] for s in env['data']['stages']], ['s0', 's1'])
        for stage in env['data']['stages']:
            for field in ('command', 'exit_code', 'ok', 'keys', 'tail'):
                self.assertIn(field, stage)
            self.assertTrue(stage['ok'])
            self.assertEqual(stage['keys'], [])

    def test_red_stage_stops_the_chain_and_exits_1(self):
        self.write_config({'commands': [RED, GREEN]})
        code, env = self.run_main([])
        self.assertEqual(code, 1)
        self.assertFalse(env['ok'])
        self.assertEqual(len(env['data']['stages']), 1)
        self.assertEqual(env['data']['stages'][0]['keys'], ['s0:lib/a.py:: error boom'])

    def test_missing_command_is_an_environment_error(self):
        self.write_config({'commands': [MISSING]})
        code, env = self.run_main([])
        self.assertEqual(code, 2)
        self.assertEqual(env['error']['kind'], 'command_not_found')

    def test_fast_form_substitutes_files(self):
        self.write_config({'fast': GREEN})
        code, env = self.run_main(['--fast', '--files', 'a.py,b.py'])
        self.assertEqual(code, 0)
        self.assertIn('a.py b.py', env['data']['stages'][0]['command'])
        self.assertEqual(env['data']['stages'][0]['name'], 's0')


class TestTestSurface(unittest.TestCase):
    def test_default_surface_picks_common_test_layouts(self):
        files = ['lib/a.dart', 'test/a_test.dart', 'tests/test_b.py', 'src/c.spec.ts',
                 'src/d.test.tsx', 'packages/x/test/e_test.dart', 'docs/f.md',
                 'test_cli.py', 'index.test.js', 'app.spec.ts', 'main_test.go']
        self.assertEqual(verify.select_test_paths(files, {}),
                         ['test/a_test.dart', 'tests/test_b.py', 'src/c.spec.ts',
                          'src/d.test.tsx', 'packages/x/test/e_test.dart',
                          'test_cli.py', 'index.test.js', 'app.spec.ts', 'main_test.go'])

    def test_default_surface_leaves_helpers_fixtures_and_goldens_alone(self):
        files = ['test/helpers/pump_app.dart', 'tests/conftest.py', 'tests/__init__.py',
                 'test/goldens/home.png', 'test/fixtures/wallet.json', 'lib/test_data.dart']
        self.assertEqual(verify.select_test_paths(files, {}), ['lib/test_data.dart'])

    def test_configured_surface_replaces_the_default(self):
        cfg = {'verify': {'testSurface': ['spec/**']}}
        self.assertEqual(verify.select_test_paths(['spec/a.rb', 'test/b_test.py'], cfg),
                         ['spec/a.rb'])

    def test_excludes_and_only_excludes(self):
        self.assertTrue(verify.matches_surface('test/a_test.dart', ['test/**', '!**/*.g.dart']))
        self.assertFalse(verify.matches_surface('test/a.g.dart', ['test/**', '!**/*.g.dart']))
        self.assertTrue(verify.matches_surface('anything.py', ['!**/*.md']))
        self.assertFalse(verify.matches_surface('docs/x.md', ['!**/*.md']))
        # fnmatch semantics, shared with verify.surface: '**/' needs a directory component.
        self.assertTrue(verify.matches_surface('x.md', ['!**/*.md']))

    def test_empty_or_invalid_surface_means_default(self):
        for bad in ([], 'test/**', None, 7):
            cfg = {'verify': {'testSurface': bad}}
            self.assertEqual(verify.select_test_paths(['test/a_test.dart', 'lib/b.dart'], cfg),
                             ['test/a_test.dart'])

    def test_no_test_paths_returns_empty_list(self):
        self.assertEqual(verify.select_test_paths(['lib/a.dart'], {}), [])


class TestTaskGate(GateCase):
    def test_fast_green_and_no_test_paths(self):
        self.write_config({'fast': GREEN, 'test': GREEN})
        self.touch('lib/a.dart')
        code, env = self.run_main(['task', '--files', 'lib/a.dart'])
        self.assertEqual(code, 0)
        stages = env['data']['stages']
        self.assertEqual([s['name'] for s in stages], ['fast', 'test'])
        self.assertTrue(stages[0]['ok'])
        self.assertIn('lib/a.dart', stages[0]['command'])
        self.assertEqual(stages[1], {'name': 'test', 'skipped': True,
                                     'reason': 'no test path in scope'})
        self.assertFalse(env['data']['skipped'])

    def test_test_stage_runs_only_on_test_paths(self):
        self.write_config({'fast': GREEN, 'test': GREEN})
        self.touch('lib/a.dart', 'test/a_test.dart')
        code, env = self.run_main(['task', '--files', 'lib/a.dart,test/a_test.dart'])
        self.assertEqual(code, 0)
        test_stage = env['data']['stages'][1]
        self.assertTrue(test_stage['ok'])
        self.assertTrue(test_stage['scoped'])
        self.assertEqual(test_stage['files'], ['test/a_test.dart'])
        self.assertIn('test/a_test.dart', test_stage['command'])
        self.assertNotIn('lib/a.dart', test_stage['command'])

    def test_red_test_stage_exits_1(self):
        self.write_config({'fast': GREEN, 'test': RED_OTHER})
        self.touch('test/a_test.py')
        code, env = self.run_main(['task', '--files', 'test/a_test.py'])
        self.assertEqual(code, 1)
        self.assertEqual(env['data']['stages'][1]['keys'], ['s1:test/a_test.py: FAILED'])

    def test_red_fast_stage_stops_before_test(self):
        self.write_config({'fast': RED, 'test': GREEN})
        self.touch('lib/a.py', 'test/a_test.py')
        code, env = self.run_main(['task', '--files', 'lib/a.py,test/a_test.py'])
        self.assertEqual(code, 1)
        self.assertEqual(len(env['data']['stages']), 1)
        self.assertEqual(env['data']['stages'][0]['name'], 'fast')

    def test_both_halves_empty_is_skipped(self):
        self.write_config({})
        self.touch('lib/a.py')
        code, env = self.run_main(['task', '--files', 'lib/a.py'])
        self.assertEqual(code, 0)
        self.assertTrue(env['data']['skipped'])
        self.assertEqual([s['reason'] for s in env['data']['stages']],
                         ['no fast command', 'no test command'])

    def test_unscoped_test_command_runs_and_says_so(self):
        self.write_config({'fast': '', 'test': "sh -c 'echo whole suite; exit 0'"})
        self.touch('test/a_test.py')
        code, env = self.run_main(['task', '--files', 'test/a_test.py'])
        self.assertEqual(code, 0)
        test_stage = env['data']['stages'][1]
        self.assertFalse(test_stage['scoped'])
        self.assertEqual(test_stage['command'], "sh -c 'echo whole suite; exit 0'")

    def test_environment_error_in_test_stage(self):
        self.write_config({'fast': GREEN, 'test': MISSING})
        self.touch('test/a_test.py')
        code, env = self.run_main(['task', '--files', 'test/a_test.py'])
        self.assertEqual(code, 2)
        self.assertEqual(env['error']['kind'], 'command_not_found')
        self.assertIn('stage test', env['error']['message'])

    def test_missing_paths_are_dropped_and_reported(self):
        self.write_config({'fast': GREEN, 'test': GREEN})
        self.touch('lib/a.dart')
        code, env = self.run_main(['task', '--files', 'lib/a.dart,test/removed_test.dart'])
        self.assertEqual(code, 0)
        self.assertEqual(env['data']['missing'], ['test/removed_test.dart'])
        self.assertNotIn('removed_test', env['data']['stages'][0]['command'])
        self.assertEqual(env['data']['stages'][1]['reason'], 'no test path in scope')

    def test_only_missing_paths_skips_both_halves(self):
        self.write_config({'fast': GREEN, 'test': GREEN})
        code, env = self.run_main(['task', '--files', 'gone.py'])
        self.assertEqual(code, 0)
        self.assertTrue(env['data']['skipped'])
        self.assertEqual(env['data']['stages'][0]['reason'], 'no existing path in scope')
        self.assertEqual(env['data']['missing'], ['gone.py'])


class TestTicketResolution(GateCase):
    def test_explicit_ticket_is_canonicalised(self):
        self.write_config({}, ticket={'projectKey': 'AW'})
        ticket = verify.resolve_ticket({'ticket': 'aw-12-3'}, verify.load_config())
        self.assertEqual(ticket, 'AW-12')
        self.assertEqual(str(verify.baseline_path_for(ticket)),
                         os.path.join('.artel', 'run', 'AW-12', 'verify-baseline.json'))

    def test_ticket_outside_the_pattern_is_rejected(self):
        self.write_config({}, ticket={'projectKey': 'AW'})
        with self.assertRaises(ValueError) as ctx:
            verify.resolve_ticket({'ticket': 'not a ticket'}, verify.load_config())
        self.assertIn('ticket.pattern', str(ctx.exception))

    def test_active_ticket_pointer_is_the_fallback(self):
        self.write_config({}, ticket={'projectKey': 'AW'}, specs={'dir': 'specs/.current'})
        os.makedirs('specs/.current')
        Path('specs/.current/.active_ticket').write_text('AW-7-2\n', encoding='utf-8')
        self.assertEqual(verify.resolve_ticket({'ticket': None}, verify.load_config()), 'AW-7')

    def test_no_ticket_anywhere_is_rejected(self):
        self.write_config({})
        with self.assertRaises(ValueError) as ctx:
            verify.resolve_ticket({'ticket': None}, verify.load_config())
        self.assertIn('.active_ticket', str(ctx.exception))


class TestCheckpointGate(GateCase):
    def setUp(self):
        super().setUp()
        self.ticket_cfg = {'projectKey': 'AW'}

    def test_no_commands_is_skipped(self):
        self.write_config({}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)
        self.assertEqual(env['data'], {'skipped': True, 'stages': [], 'baseline': 'skipped'})

    def test_without_a_baseline_any_red_is_red(self):
        self.write_config({'commands': [RED, GREEN]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        self.assertEqual(env['data']['baseline'], 'absent')
        self.assertEqual(len(env['data']['stages']), 1)
        self.assertNotIn('new_keys', env['data']['stages'][0])

    def test_green_without_a_baseline(self):
        self.write_config({'commands': [GREEN]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)
        self.assertEqual(env['data']['baseline'], 'absent')
        self.assertTrue(env['data']['baseline_path'].endswith(
            os.path.join('AW-1', 'verify-baseline.json')))

    def test_record_baseline_runs_every_stage_and_exits_0(self):
        self.write_config({'commands': [RED, RED_OTHER, GREEN]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--record-baseline', '--ticket', 'AW-12-3'])
        self.assertEqual(code, 0)
        self.assertEqual(env['data']['baseline'], 'recorded')
        self.assertEqual([s['name'] for s in env['data']['stages']], ['s0', 's1', 's2'])
        recorded = json.loads(Path('.artel/run/AW-12/verify-baseline.json').read_text())
        self.assertIn('recorded_at', recorded)
        self.assertEqual([s['keys'] for s in recorded['stages']],
                         [['s0:lib/a.py:: error boom'], ['s1:test/a_test.py: FAILED'], []])
        self.assertEqual([s['ok'] for s in recorded['stages']], [False, False, True])
        # the command as executed, {files} substituted, exactly as the envelope's stage shows it
        self.assertEqual(recorded['stages'][2]['command'], verify.substitute_files(GREEN, []))

    def test_record_baseline_env_error_writes_nothing(self):
        self.write_config({'commands': [RED, MISSING]}, ticket=self.ticket_cfg)
        os.makedirs('.artel/run/AW-1')
        Path('.artel/run/AW-1/verify-baseline.json').write_text('{"stages": []}')
        code, env = self.run_main(['checkpoint', '--record-baseline', '--ticket', 'AW-1'])
        self.assertEqual(code, 2)
        self.assertEqual(env['error']['kind'], 'command_not_found')
        self.assertEqual(Path('.artel/run/AW-1/verify-baseline.json').read_text(),
                         '{"stages": []}')

    def test_record_baseline_with_no_commands_writes_nothing(self):
        self.write_config({}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--record-baseline', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)
        self.assertFalse(Path('.artel/run/AW-1/verify-baseline.json').exists())

    def test_missing_ticket_is_invalid_argument(self):
        self.write_config({'commands': [GREEN]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint'])
        self.assertEqual(code, 2)
        self.assertEqual(env['error']['kind'], 'invalid_argument')
        self.assertIn('.active_ticket', env['error']['message'])


class TestBaselineCompare(GateCase):
    def setUp(self):
        super().setUp()
        self.ticket_cfg = {'projectKey': 'AW'}

    def record(self, commands):
        self.write_config({'commands': commands}, ticket=self.ticket_cfg)
        code, _ = self.run_main(['checkpoint', '--record-baseline', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)

    def test_red_only_on_baseline_keys_is_green(self):
        self.record([RED, GREEN])
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)
        self.assertEqual(env['data']['baseline'], 'loaded')
        s0, s1 = env['data']['stages']
        self.assertFalse(s0['ok'])
        self.assertTrue(s0['baseline_red'])
        self.assertEqual(s0['new_keys'], [])
        self.assertTrue(s1['ok'], 'a baseline-red stage does not stop the chain')

    def test_new_keys_after_a_baseline_red_stage_exit_1(self):
        self.record([RED, GREEN])
        self.write_config({'commands': [RED, RED_OTHER, GREEN]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        s0, s1 = env['data']['stages']
        self.assertTrue(s0['baseline_red'])
        self.assertEqual(s1['new_keys'], ['s1:test/a_test.py: FAILED'])
        self.assertFalse(s1['baseline_red'])
        self.assertEqual(len(env['data']['stages']), 2, 'a new-keys stage stops the chain')

    def test_new_key_in_a_baseline_red_stage_is_red(self):
        self.record([RED])
        both = "sh -c 'echo lib/a.py:3:1 error boom; echo lib/b.py:1:1 error fresh; exit 1'"
        self.write_config({'commands': [both]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        self.assertEqual(env['data']['stages'][0]['new_keys'], ['s0:lib/b.py:: error fresh'])

    def test_baseline_disabled_by_config(self):
        self.record([RED])
        self.write_config({'commands': [RED], 'baseline': False}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        self.assertEqual(env['data']['baseline'], 'disabled')
        self.assertNotIn('new_keys', env['data']['stages'][0])

    def test_corrupt_baseline_is_absent_with_a_warning(self):
        self.write_config({'commands': [RED]}, ticket=self.ticket_cfg)
        os.makedirs('.artel/run/AW-1')
        Path('.artel/run/AW-1/verify-baseline.json').write_text('{not json', encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        self.assertEqual(env['data']['baseline'], 'absent')
        self.assertIn('verify: warning:', err.getvalue())

    def test_non_boolean_baseline_key_is_invalid(self):
        self.write_config({'commands': [GREEN], 'baseline': 'yes'}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 2)
        self.assertEqual(env['error']['kind'], 'invalid_argument')
        self.assertIn('verify.baseline', env['error']['message'])

    def test_record_ignores_the_disabled_key(self):
        self.write_config({'commands': [RED], 'baseline': False}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--record-baseline', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)
        self.assertEqual(env['data']['baseline'], 'recorded')
        self.assertTrue(Path('.artel/run/AW-1/verify-baseline.json').exists())

    def test_new_key_past_the_envelope_cap_is_still_red(self):
        self.record([self.many_findings_script()])
        self.write_config({'commands': [self.many_findings_script('lib/zz.py: NEW_REGRESSION')]},
                          ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        s0 = env['data']['stages'][0]
        self.assertEqual(s0['new_keys'], ['s0:lib/zz.py: NEW_REGRESSION'])
        self.assertFalse(s0['baseline_red'])
        self.assertEqual(len(s0['keys']), 241, 'the checkpoint gate does not cap its keys')

    def test_green_at_arm_then_red_without_output_is_red(self):
        self.record(["sh -c 'exit 0'"])
        self.write_config({'commands': ["sh -c 'exit 1'"]}, ticket=self.ticket_cfg)
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 1)
        s0 = env['data']['stages'][0]
        self.assertFalse(s0['baseline_red'])
        self.assertEqual(s0['new_keys'], [])

    def test_red_at_arm_then_red_without_output_stays_baseline_red(self):
        self.record(["sh -c 'exit 1'"])
        code, env = self.run_main(['checkpoint', '--ticket', 'AW-1'])
        self.assertEqual(code, 0)
        self.assertTrue(env['data']['stages'][0]['baseline_red'])


class TestSubprocessForm(GateCase):
    """The hooks execute verify.py as a subprocess; this is the only test that does too."""

    def test_the_hooks_call_shape_runs_and_keeps_the_legacy_fields(self):
        self.write_config({'fast': GREEN})
        self.touch('a.py')
        proc = subprocess.run([sys.executable, str(h.VERIFY_SCRIPT), '--fast', '--files', 'a.py'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        env = json.loads(proc.stdout.strip().splitlines()[-1])
        stage = env['data']['stages'][0]
        for field in ('name', 'command', 'exit_code', 'ok', 'keys', 'tail'):
            self.assertIn(field, stage)
        self.assertEqual(stage['name'], 's0')
        code, env2 = h.run_fast_verify(['a.py'])
        self.assertEqual(code, 0)
        self.assertEqual(h.finding_keys(env2), set())
        self.assertFalse(env2['data']['skipped'])


if __name__ == '__main__':
    unittest.main()
