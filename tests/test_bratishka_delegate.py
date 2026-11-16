"""The delegation batch grammar: keys, status moves, prompts, close-out.

The engine holds no tracker client, so these are the decisions taken from the
facts a skill gathered -- and the two invariants a batch lives by: every
requested key ends in one outcome, and one bad key never cancels the others.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import bratishka  # noqa: E402
import bratishka_delegate as delegate  # noqa: E402

TRANSITIONS = [
    {'id': '721', 'name': 'In Progress', 'to': {'name': 'In Development'}},
    {'id': '961', 'name': 'Ready for review', 'to': {'name': 'Code Review'}},
]


def issue(summary='Add CSV export', status='To Do', category='new'):
    return {'url': 'https://tracker.example/browse/PROJ-1',
            'fields': {'summary': summary,
                       'description': 'Export the visible rows.',
                       'status': {'name': status, 'statusCategory': {'key': category}}}}


def config(**delegate_values):
    import bratishka_config as cfg
    block = json.loads(json.dumps(cfg.DEFAULTS))
    block['repo'] = 'o/r'
    block['baseRef'] = 'master'
    block['delegate'].update(delegate_values)
    return block


class TestKeys(unittest.TestCase):
    def test_dedupes_and_keeps_the_order_the_person_gave(self):
        self.assertEqual(delegate.parse_keys(['proj-2, PROJ-1', 'PROJ-2']),
                         ['PROJ-2', 'PROJ-1'])

    def test_the_idempotency_key_is_per_issue(self):
        self.assertEqual(delegate.idempotency_key('PROJ-1'), 'PROJ-1:agent')
        self.assertNotEqual(delegate.idempotency_key('PROJ-1'),
                            delegate.idempotency_key('PROJ-2'))

    def test_a_second_run_of_one_issue_takes_a_date_suffix(self):
        self.assertEqual(delegate.idempotency_key('PROJ-1', date(2026, 11, 16)),
                         'PROJ-1:agent:2026-11-16')


class TestResolveStatus(unittest.TestCase):
    def test_both_intents_resolve_from_the_aliases(self):
        self.assertEqual(delegate.resolve_status(TRANSITIONS, 'start')['transition_id'], '721')
        self.assertEqual(delegate.resolve_status(TRANSITIONS, 'handoff')['transition_id'], '961')

    def test_an_explicit_override_wins(self):
        found = delegate.resolve_status(TRANSITIONS, 'start', override='In Development')
        self.assertEqual(found['transition_id'], '721')
        self.assertEqual(found['outcome'], 'resolved')

    def test_a_typo_never_falls_back_to_the_aliases(self):
        # 'In Review' is not a transition here; the alias 'review' must not rescue it.
        found = delegate.resolve_status(TRANSITIONS, 'start', override='In Review')
        self.assertEqual(found['outcome'], 'needs-decision')
        self.assertEqual(len(found['candidates']), 2, 'the choices are shown')

    def test_two_statuses_matching_the_aliases_is_a_decision(self):
        wide = TRANSITIONS + [{'id': '5', 'name': 'Start', 'to': {'name': 'In Progress'}}]
        found = delegate.resolve_status(wide, 'start')
        self.assertEqual(found['outcome'], 'needs-decision')

    def test_an_issue_already_there_is_skipped(self):
        found = delegate.resolve_status(TRANSITIONS, 'start', current='in development')
        self.assertEqual(found['outcome'], 'skip')

    def test_a_move_backwards_is_confirmed_first(self):
        back = [{'id': '9', 'name': 'Back to To Do', 'to': {'name': 'Ready for review'}}]
        self.assertEqual(delegate.resolve_status(back, 'handoff')['outcome'], 'confirm')

    def test_nothing_matching_stops_with_the_choices(self):
        found = delegate.resolve_status([{'id': '1', 'name': 'Done', 'to': {'name': 'Done'}}],
                                        'start')
        self.assertEqual(found['outcome'], 'needs-decision')
        self.assertEqual(found['candidates'][0]['to'], 'Done')

    def test_an_unknown_intent_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            delegate.resolve_status(TRANSITIONS, 'publish')


class TestPlan(unittest.TestCase):
    def test_every_requested_key_gets_exactly_one_outcome(self):
        issues = {'PROJ-1': issue(), 'PROJ-2': issue(status='Done', category='done')}
        data = delegate.plan(config(), ['PROJ-1', 'PROJ-2', 'PROJ-3'], issues,
                             {'PROJ-1': TRANSITIONS, 'PROJ-2': TRANSITIONS}, date(2026, 11, 16))
        self.assertEqual([row['key'] for row in data['rows']],
                         ['PROJ-1', 'PROJ-2', 'PROJ-3'])
        self.assertEqual(data['rows'][0]['outcome'], 'ready')
        self.assertEqual(data['rows'][1]['outcome'], 'needs-decision')
        self.assertEqual(data['rows'][2]['outcome'], 'invalid')

    def test_one_impossible_key_does_not_cancel_the_batch(self):
        issues = {'PROJ-1': issue(), 'PROJ-2': issue()}
        data = delegate.plan(config(), ['PROJ-1', 'PROJ-2'], issues,
                             {'PROJ-2': TRANSITIONS}, date(2026, 11, 16))
        self.assertEqual([row['outcome'] for row in data['rows']],
                         ['needs-decision', 'ready'])
        self.assertEqual([call['key'] for call in data['spawn_calls']], ['PROJ-2'])

    def test_each_spawn_call_carries_its_own_prompt_and_key(self):
        data = delegate.plan(config(), ['PROJ-1'], {'PROJ-1': issue()},
                             {'PROJ-1': TRANSITIONS}, date(2026, 11, 16))
        call = data['spawn_calls'][0]
        self.assertEqual(call['idempotency_key'], 'PROJ-1:agent')
        self.assertEqual(call['task_kind'], 'agent', 'omitting it silently makes a review')
        self.assertEqual(call['context'], {'jira_task': 'PROJ-1'})
        self.assertNotIn('head_ref', call, 'the bot must create the branch and the PR')
        self.assertIn('PROJ-1', call['prompt'])
        self.assertIn('Add CSV export', call['prompt'])
        self.assertIn('o/r', call['prompt'])
        self.assertEqual(call['repo_url'], 'https://github.com/o/r.git')

    def test_a_typo_in_the_override_stops_that_key_only(self):
        data = delegate.plan(config(startStatus='In Review'), ['PROJ-1'], {'PROJ-1': issue()},
                             {'PROJ-1': TRANSITIONS}, date(2026, 11, 16))
        self.assertEqual(data['rows'][0]['outcome'], 'needs-decision')
        self.assertEqual(data['spawn_calls'], [])
        self.assertIn('start', data['rows'][0]['reason'])

    def test_a_batch_over_the_limit_asks_first(self):
        issues = {'PROJ-{}'.format(n): issue() for n in (1, 2, 3)}
        transitions = {'PROJ-{}'.format(n): TRANSITIONS for n in (1, 2, 3)}
        data = delegate.plan(config(maxBatch=2), sorted(issues), issues, transitions,
                             date(2026, 11, 16))
        self.assertTrue(data['needs_confirmation'])
        self.assertEqual(data['counts'], {'ready': 3})

    def test_the_report_is_one_row_per_key(self):
        data = delegate.plan(config(), ['PROJ-1'], {'PROJ-1': issue()},
                             {'PROJ-1': TRANSITIONS}, date(2026, 11, 16))
        table = delegate.report(data['rows'])
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0][0], 'PROJ-1')
        self.assertEqual(table[0][1], 'ready')
        self.assertEqual(table[0][3], '721 -> In Development')


class TestCloseout(unittest.TestCase):
    def test_a_red_gate_with_a_pull_request_is_delivered(self):
        task = {'status': 'failed',
                'result_detail': {'error': 'final_tools_gate_not_satisfied'}}
        found = delegate.closeout('PROJ-1', task, {'body': 'body', 'number': 3})
        self.assertEqual(found['outcome'], 'completed')
        self.assertTrue(found['gate_failed'])
        self.assertTrue(found['send_back'], 'the gate is a gap to report')

    def test_a_failed_task_without_a_pull_request_failed(self):
        found = delegate.closeout('PROJ-1', {'status': 'failed'}, None)
        self.assertEqual(found['outcome'], 'failed')
        self.assertIn('no pull request was opened', found['gaps'])

    def test_an_empty_body_is_a_gap_to_send_back(self):
        found = delegate.closeout('PROJ-1', {'status': 'completed'}, {'body': '  '})
        self.assertTrue(found['send_back'])
        self.assertIn('the pull-request body is empty', found['gaps'])

    def test_a_missing_reviewer_is_a_gap(self):
        found = delegate.closeout('PROJ-1', {'status': 'completed'},
                                  {'body': 'b', 'review_requests': False})
        self.assertIn('the reviewer is not requested', found['gaps'])


class FakeGh:
    def __init__(self):
        self.calls = []

    def repo_slug(self):
        return 'o/r'


def run(argv, gh=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bratishka.main(argv, gh=gh or FakeGh(), dashboard=None)
    return code, json.loads(out.getvalue())


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo.cleanup)
        self.dir = Path(self.repo.name)

    def write(self, name, payload):
        path = self.dir / name
        path.write_text(json.dumps(payload), encoding='utf-8')
        return str(path)

    def test_plan_command_returns_the_batch_and_the_table(self):
        issues = self.write('issues.json', {'PROJ-1': issue()})
        transitions = self.write('transitions.json', {'PROJ-1': TRANSITIONS})
        code, out = run(['delegate-plan', '--keys', 'PROJ-1', '--issues', issues,
                         '--transitions', transitions], gh=FakeGh())
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['rows'][0]['outcome'], 'ready')
        self.assertEqual(out['data']['table'][0][0], 'PROJ-1')

    def test_plan_without_keys_is_an_argument_error(self):
        code, out = run(['delegate-plan'])
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'invalid_argument')

    def test_a_missing_fact_file_is_a_config_error(self):
        code, out = run(['delegate-plan', '--keys', 'PROJ-1',
                         '--issues', str(self.dir / 'gone.json')])
        self.assertEqual(code, 2)
        self.assertEqual(out['error']['kind'], 'missing_config')

    def test_closeout_command_reads_the_two_files(self):
        task = self.write('task.json', {'status': 'completed'})
        pr = self.write('pr.json', {'body': 'b', 'number': 7})
        code, out = run(['delegate-closeout', '--key', 'PROJ-1', '--task', task,
                         '--pr', pr])
        self.assertEqual(code, 0)
        self.assertEqual(out['data']['outcome'], 'completed')
        self.assertFalse(out['data']['send_back'])


class TestDocs(unittest.TestCase):
    def test_the_contract_has_a_delegating_section(self):
        text = (ROOT / 'docs/bratishka-duty.md').read_text(encoding='utf-8')
        self.assertIn('## Delegating a ticket', text)
        self.assertIn('/artel-bratishka-delegate', text)

    def test_the_skill_exists_with_its_folder_name(self):
        skill = ROOT / 'skills/artel-bratishka-delegate/SKILL.md'
        self.assertTrue(skill.is_file())
        head = skill.read_text(encoding='utf-8').split('---')[1]
        self.assertIn('name: artel-bratishka-delegate', head)

    def test_the_skill_cites_the_status_resolution(self):
        text = (ROOT / 'skills/artel-bratishka-delegate/SKILL.md').read_text(encoding='utf-8')
        self.assertIn('docs/tracker-status.md', text)


if __name__ == '__main__':
    unittest.main()
