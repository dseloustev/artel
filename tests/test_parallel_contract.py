"""The frozen parallel contract: `parallel.seats`, the schema-1 assignment and result
payloads, and the task-state vocabulary the coordinator shares with Task 3.

Contract: docs/opencode-parallel.md (§4 assignment, §5 result, §6 task states,
§7 concurrency metadata) and docs/config.md (`parallel`). The functions are pure:
they read no file, print nothing, and raise `ContractError` carrying the envelope
kind (`invalid_config` / `invalid_assignment` / `invalid_result`).
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import parallel_contract  # noqa: E402

RUN_ID = '0f6f0f2e-6b1a-4f2e-9c5d-2b7a1c8e4d10'
COMMIT = '0123456789abcdef0123456789abcdef01234567'
WORKER_ROOT = '/tmp/parallel-contract-worker'
ARTIFACT_ROOT = '/tmp/parallel-contract-artifacts'


def assignment(**overrides):
    """A valid schema-1 assignment (§4) with `overrides` applied."""
    value = {
        'schema': 1,
        'ticket': 'PROJ-123',
        'phase': 2,
        'task': '3.1',
        'run_id': RUN_ID,
        'attempt': 1,
        'claim': 'artel@host',
        'base_commit': COMMIT,
        'files': ['src/a.py', 'tests/test_a.py'],
        'tests': ['tests/test_a.py'],
        'route': 'full',
        'model': 'provider/model#variant',
        'worker_root': WORKER_ROOT,
        'artifact_root': ARTIFACT_ROOT,
    }
    value.update(overrides)
    return value


def full_result(assigned, **overrides):
    """A valid schema-1 result (§5) for `assigned`, with `overrides` applied."""
    value = {name: assigned[name] for name in parallel_contract.ASSIGNMENT_FIELDS}
    value.update({
        'status': 'succeeded',
        'changed_files': ['src/a.py', 'tests/test_a.py'],
        'verify': [{'command': 'python3 -m pytest tests/test_a.py', 'ok': True,
                    'output': '3 passed'}],
        'review': None,
        'report': 'report.md',
        'deviations': [],
    })
    value.update(overrides)
    return value


def rejects(test, kind, call):
    """Assert `call` raises ContractError of `kind`; return the exception."""
    with test.assertRaises(parallel_contract.ContractError) as caught:
        call()
    test.assertEqual(caught.exception.kind, kind)
    test.assertEqual(str(caught.exception), caught.exception.message)
    return caught.exception


class TestResolveConfig(unittest.TestCase):
    def test_absent_seats_is_one(self):
        for config in ({}, {'parallel': None}, {'parallel': {}}, {'parallel': {'seats': None}},
                       {'verify': {'fast': 'npm run lint'}}):
            self.assertEqual(parallel_contract.resolve_config(config), {'seats': 1}, config)

    def test_positive_seats_are_resolved(self):
        for seats in (1, 2, 7):
            self.assertEqual(
                parallel_contract.resolve_config({'parallel': {'seats': seats}}),
                {'seats': seats})

    def test_bool_zero_negative_and_noninteger_seats_are_rejected(self):
        for seats in (True, False, 0, -1, '2', 2.0, [], {}, 1.0):
            rejects(self, 'invalid_config',
                    lambda seats=seats: parallel_contract.resolve_config(
                        {'parallel': {'seats': seats}}))

    def test_a_non_object_parallel_section_is_rejected(self):
        for section in (2, [], 'on', True):
            rejects(self, 'invalid_config',
                    lambda section=section: parallel_contract.resolve_config(
                        {'parallel': section}))

    def test_a_non_object_config_root_is_rejected(self):
        for config in ([], 'x', None, 1):
            rejects(self, 'invalid_config',
                    lambda config=config: parallel_contract.resolve_config(config))

    def test_unknown_keys_are_ignored(self):
        # Config reading rule 2: a newer artel's keys must not break this one.
        self.assertEqual(
            parallel_contract.resolve_config({'parallel': {'seats': 2, 'mode': 'pool'}}),
            {'seats': 2})
        self.assertEqual(parallel_contract.resolve_config({'future': {'x': 1}}), {'seats': 1})


class TestValidateAssignment(unittest.TestCase):
    def test_the_valid_assignment_round_trips(self):
        value = assignment()
        self.assertEqual(parallel_contract.validate_assignment(value), value)

    def test_null_fields_are_accepted_where_the_contract_allows_them(self):
        value = assignment(phase=None, claim=None, model=None, files=[], tests=[])
        self.assertEqual(parallel_contract.validate_assignment(value), value)

    def test_wrong_attempt_or_root_is_rejected(self):
        for overrides in ({'attempt': 0}, {'attempt': -1}, {'attempt': True},
                          {'attempt': '1'}, {'attempt': 1.0},
                          {'worker_root': 'relative/root'},
                          {'artifact_root': 'relative/root'},
                          {'worker_root': ''}, {'artifact_root': None}):
            rejects(self, 'invalid_assignment',
                    lambda overrides=overrides: parallel_contract.validate_assignment(
                        assignment(**overrides)))

    def test_a_missing_key_is_rejected(self):
        for name in parallel_contract.ASSIGNMENT_FIELDS:
            value = assignment()
            del value[name]
            rejects(self, 'invalid_assignment',
                    lambda value=value: parallel_contract.validate_assignment(value))

    def test_an_unknown_key_is_rejected(self):
        rejects(self, 'invalid_assignment',
                lambda: parallel_contract.validate_assignment(assignment(notes='extra')))

    def test_a_non_object_assignment_is_rejected(self):
        for value in (None, [], 'x', 1):
            rejects(self, 'invalid_assignment',
                    lambda value=value: parallel_contract.validate_assignment(value))

    def test_schema_must_be_exactly_one(self):
        for schema in (2, 0, '1', 1.0, True, None):
            rejects(self, 'invalid_assignment',
                    lambda schema=schema: parallel_contract.validate_assignment(
                        assignment(schema=schema)))

    def test_a_malformed_ticket_or_task_is_rejected(self):
        for overrides in ({'ticket': 'proj-123'}, {'ticket': 'PROJ_123'}, {'ticket': 'PROJ'},
                          {'ticket': '123'}, {'ticket': 123}, {'ticket': 'PROJ-1-2'},
                          {'task': '3'}, {'task': 'Task 3.1'}, {'task': '3.1.2'},
                          {'task': 'a.b'}, {'task': 31}, {'task': ''}):
            rejects(self, 'invalid_assignment',
                    lambda overrides=overrides: parallel_contract.validate_assignment(
                        assignment(**overrides)))

    def test_phase_is_an_integer_or_null(self):
        rejects(self, 'invalid_assignment',
                lambda: parallel_contract.validate_assignment(assignment(phase='2')))
        rejects(self, 'invalid_assignment',
                lambda: parallel_contract.validate_assignment(assignment(phase=True)))
        rejects(self, 'invalid_assignment',
                lambda: parallel_contract.validate_assignment(assignment(phase=2.0)))
        rejects(self, 'invalid_assignment',
                lambda: parallel_contract.validate_assignment(assignment(phase=0)))

    def test_run_id_must_be_a_canonical_uuid(self):
        for run_id in (RUN_ID.upper(), 'not-a-uuid', RUN_ID.replace('-', ''),
                       '{' + RUN_ID + '}', '', 7):
            rejects(self, 'invalid_assignment',
                    lambda run_id=run_id: parallel_contract.validate_assignment(
                        assignment(run_id=run_id)))

    def test_base_commit_must_be_a_full_lowercase_hex_id(self):
        for base_commit in (COMMIT[:7], COMMIT.upper(), 'z' * 40, '0' * 39, '0' * 41, 1):
            rejects(self, 'invalid_assignment',
                    lambda base_commit=base_commit: parallel_contract.validate_assignment(
                        assignment(base_commit=base_commit)))

    def test_claim_is_a_non_empty_string_or_null(self):
        for claim in (7, '', '  ', 'artel@host\n', ['artel@host']):
            rejects(self, 'invalid_assignment',
                    lambda claim=claim: parallel_contract.validate_assignment(
                        assignment(claim=claim)))

    def test_route_must_be_light_or_full(self):
        for route in ('fast', 'Light', None, 1):
            rejects(self, 'invalid_assignment',
                    lambda route=route: parallel_contract.validate_assignment(
                        assignment(route=route)))

    def test_model_is_a_provider_model_value_or_null(self):
        for model in ('model', 'a/b/c', 'a/b#', 'a/b#c#d', 'a b/c', 'a/', '/b', ''):
            rejects(self, 'invalid_assignment',
                    lambda model=model: parallel_contract.validate_assignment(
                        assignment(model=model)))

    def test_files_and_tests_are_relative_string_arrays(self):
        for name in ('files', 'tests'):
            for value in ('src/a.py', None, 7,
                          ['/etc/passwd'], ['../a.py'], ['a/../../b.py'],
                          [''], ['  '], [7], ['a\nb']):
                rejects(self, 'invalid_assignment',
                        lambda name=name, value=value: parallel_contract.validate_assignment(
                            assignment(**{name: value})))

    def test_declared_files_are_not_confined_to_the_worker_root(self):
        # §11: `files` is a declaration, not a sandbox -- the implementer may touch a
        # file it did not declare, and the declaration may name one outside its root.
        value = assignment(files=['src/a.py'], tests=['tests/test_a.py'])
        self.assertEqual(parallel_contract.validate_assignment(value), value)


class TestValidateResult(unittest.TestCase):
    def test_the_valid_result_round_trips(self):
        assigned = assignment()
        value = full_result(assigned)
        self.assertEqual(parallel_contract.validate_result(value, assigned), value)

    def test_every_status_is_accepted(self):
        for status in parallel_contract.RESULT_STATUSES:
            value = full_result(assignment(), status=status)
            self.assertEqual(parallel_contract.validate_result(value, assignment())['status'],
                             status)

    def test_an_unknown_status_is_rejected(self):
        for status in ('ok', 'SUCCEEDED', 'done', None, 1):
            rejects(self, 'invalid_result',
                    lambda status=status: parallel_contract.validate_result(
                        full_result(assignment(), status=status), assignment()))

    def test_a_mismatched_identity_is_rejected(self):
        for overrides in ({'task': '3.2'}, {'attempt': 2}, {'attempt': True},
                          {'run_id': RUN_ID.replace('0f', '1f')},
                          {'model': 'provider/other'}, {'phase': None},
                          {'files': ['src/a.py']}, {'tests': []},
                          {'base_commit': 'f' * 40}, {'worker_root': '/tmp/other'},
                          {'claim': None}, {'route': 'light'}, {'ticket': 'PROJ-9'}):
            assigned = assignment()
            rejects(self, 'invalid_result',
                    lambda overrides=overrides: parallel_contract.validate_result(
                        full_result(assigned, **overrides), assigned))

    def test_a_missing_or_unknown_result_key_is_rejected(self):
        for name in parallel_contract.RESULT_FIELDS:
            value = full_result(assignment())
            del value[name]
            rejects(self, 'invalid_result',
                    lambda value=value: parallel_contract.validate_result(
                        value, assignment()))
        rejects(self, 'invalid_result',
                lambda: parallel_contract.validate_result(
                    full_result(assignment(), notes='extra'), assignment()))

    def test_a_non_object_result_is_rejected(self):
        for value in (None, [], 'x', 1):
            rejects(self, 'invalid_result',
                    lambda value=value: parallel_contract.validate_result(value, assignment()))

    def test_an_invalid_assignment_is_rejected_first(self):
        rejects(self, 'invalid_assignment',
                lambda: parallel_contract.validate_result(
                    full_result(assignment()), assignment(worker_root='relative')))

    def test_changed_files_must_resolve_inside_the_worker_root(self):
        assigned = assignment()
        for changed in (['/etc/passwd'], ['../outside.py'], ['a/../../b.py'],
                        ['link/secret.py'], ['']):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'root'
                root.mkdir()
                outside = Path(tmp) / 'outside'
                outside.mkdir()
                (outside / 'secret.py').write_text('x', encoding='utf-8')
                (root / 'link').symlink_to(outside)
                local = assignment(worker_root=str(root))
                rejects(self, 'invalid_result',
                        lambda changed=changed, local=local: parallel_contract.validate_result(
                            full_result(local, changed_files=changed), local))
        value = full_result(assigned, changed_files=['src/a.py', 'src/new.py'])
        self.assertEqual(parallel_contract.validate_result(value, assigned), value)

    def test_report_is_relative_to_the_artifact_root(self):
        assigned = assignment()
        self.assertIsNone(
            parallel_contract.validate_result(full_result(assigned, report=None),
                                              assigned)['report'])
        for report in ('/tmp/report.md', '../report.md', 'a/../../report.md'):
            rejects(self, 'invalid_result',
                    lambda report=report: parallel_contract.validate_result(
                        full_result(assigned, report=report), assigned))
        with tempfile.TemporaryDirectory() as tmp:
            worker = Path(tmp) / 'worker'
            worker.mkdir()
            artifacts = Path(tmp) / 'artifacts'
            artifacts.mkdir()
            (worker / 'report.md').write_text('x', encoding='utf-8')
            (artifacts / 'link').symlink_to(worker)
            local = assignment(worker_root=str(worker), artifact_root=str(artifacts))
            # A symlink inside the artifact root that resolves into the worker root.
            rejects(self, 'invalid_result',
                    lambda: parallel_contract.validate_result(
                        full_result(local, changed_files=[], report='link/report.md'), local))

    def test_deviations_carry_a_severity_a_summary_and_confined_paths(self):
        assigned = assignment()
        for deviation in ({'severity': 'major', 'summary': 'touched the schema', 'paths': []},
                          {'severity': 'minor', 'summary': 'renamed a helper',
                           'paths': ['src/b.py']}):
            value = full_result(assigned, deviations=[deviation])
            self.assertEqual(
                parallel_contract.validate_result(value, assigned)['deviations'], [deviation])
        for deviation in ({'severity': 'critical', 'summary': 'x', 'paths': []},
                          {'severity': 'minor', 'summary': '', 'paths': []},
                          {'severity': 'minor', 'summary': 'x', 'paths': ['../b.py']},
                          {'severity': 'minor', 'summary': 'x', 'paths': ['/etc/passwd']},
                          {'severity': 'minor', 'summary': 'x'},
                          {'severity': 'minor', 'summary': 'x', 'paths': [], 'note': 'y'}):
            rejects(self, 'invalid_result',
                    lambda deviation=deviation: parallel_contract.validate_result(
                        full_result(assigned, deviations=[deviation]), assigned))

    def test_verify_entries_are_command_ok_output(self):
        assigned = assignment()
        for verify in ([], [{'command': 'python3 -m pytest tests/test_a.py', 'ok': False,
                             'output': ''}]):
            value = full_result(assigned, verify=verify)
            self.assertEqual(parallel_contract.validate_result(value, assigned)['verify'], verify)
        for verify in ('x', [{'command': 'x', 'ok': 'yes', 'output': ''}],
                       [{'command': '', 'ok': True, 'output': ''}],
                       [{'command': 'x', 'ok': True}],
                       [{'command': 'x', 'ok': True, 'output': '', 'tail': 1}]):
            rejects(self, 'invalid_result',
                    lambda verify=verify: parallel_contract.validate_result(
                        full_result(assigned, verify=verify), assigned))

    def test_verify_output_keeps_the_captured_tail_verbatim(self):
        # §5: `output` is the captured tail -- multi-line text, CR/LF endings and
        # surrounding whitespace are the tail itself, not a malformed value.
        assigned = assignment()
        for tail in ('3 passed\n',
                     'FAILED test_a.py::test_x\n1 failed, 2 passed\n',
                     '\n  FAILED test_a.py::test_x  \n',
                     'first\r\nsecond\r\n',
                     'first\rsecond\r',
                     '  padded  ',
                     ''):
            entry = {'command': 'python3 -m pytest tests/test_a.py', 'ok': False,
                     'output': tail}
            value = full_result(assigned, verify=[entry])
            self.assertEqual(parallel_contract.validate_result(value, assigned)['verify'],
                             [entry])

    def test_a_non_string_verify_output_is_rejected(self):
        assigned = assignment()
        for output in (None, 7, True, ['3 passed']):
            rejects(self, 'invalid_result',
                    lambda output=output: parallel_contract.validate_result(
                        full_result(assigned, verify=[{'command': 'pytest', 'ok': True,
                                                       'output': output}]), assigned))

    def test_a_verify_command_stays_single_line(self):
        # The command is one invocation; only `output` carries captured text.
        assigned = assignment()
        for command in ('pytest\nrm -rf /', 'pytest\r', '  pytest  ', ''):
            rejects(self, 'invalid_result',
                    lambda command=command: parallel_contract.validate_result(
                        full_result(assigned, verify=[{'command': command, 'ok': True,
                                                       'output': ''}]), assigned))

    def test_a_filled_review_is_accepted_and_a_malformed_one_is_rejected(self):
        assigned = assignment()
        review = {'required': True, 'route': 'full', 'verdict': 'passed', 'rounds': 1}
        value = full_result(assigned, review=review)
        self.assertEqual(parallel_contract.validate_result(value, assigned)['review'], review)
        for review in ({'required': True, 'route': 'full', 'verdict': 'passed'},
                       {'required': 'yes', 'route': 'full', 'verdict': 'passed', 'rounds': 0},
                       {'required': True, 'route': 'fast', 'verdict': 'passed', 'rounds': 0},
                       {'required': True, 'route': 'full', 'verdict': 'ok', 'rounds': 0},
                       {'required': True, 'route': 'full', 'verdict': 'passed', 'rounds': -1},
                       {'required': True, 'route': 'full', 'verdict': 'passed', 'rounds': True},
                       'passed', 1):
            rejects(self, 'invalid_result',
                    lambda review=review: parallel_contract.validate_result(
                        full_result(assigned, review=review), assigned))


class TestVocabulary(unittest.TestCase):
    def test_schema_is_one(self):
        self.assertEqual(parallel_contract.SCHEMA, 1)

    def test_the_task_states_are_the_contract_s_sequence(self):
        self.assertEqual(parallel_contract.TASK_STATES,
                         ('reserved', 'preparing', 'running', 'verifying/reviewing',
                          'prepared', 'integrating', 'published'))
        self.assertEqual(parallel_contract.SIDE_STATES, ('blocked', 'failed', 'cancelled'))
        self.assertEqual(set(parallel_contract.TASK_STATES) & set(parallel_contract.SIDE_STATES),
                         set())

    def test_the_result_status_vocabulary_is_frozen(self):
        self.assertEqual(parallel_contract.RESULT_STATUSES,
                         ('succeeded', 'failed', 'blocked', 'cancelled'))

    def test_the_review_and_deviation_vocabulary_is_frozen(self):
        self.assertEqual(parallel_contract.ROUTES, ('light', 'full'))
        self.assertEqual(parallel_contract.REVIEW_VERDICTS,
                         ('passed', 'changes-requested', 'skipped'))
        self.assertEqual(parallel_contract.DEVIATION_SEVERITIES, ('minor', 'major'))


class TestConfigDocs(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding='utf-8')

    def test_the_default_config_carries_parallel_seats(self):
        text = self.read('docs/config.md')
        start = text.index('```json', text.index('## The default config')) + len('```json')
        defaults = json.loads(text[start:text.index('```', start)])
        self.assertEqual(defaults['parallel'], {'seats': 1})

    def test_the_key_reference_documents_the_positive_integer(self):
        text = self.read('docs/config.md')
        heading = '### `parallel` — concurrent worker seats'
        self.assertIn(heading, text)
        body = text[text.index(heading):text.index('\n### ', text.index(heading) + 1)]
        flat = re.sub(r'\s+', ' ', body)
        for phrase in ('`parallel.seats`', 'A positive integer', 'A boolean is not an integer',
                       'Absent or `1` keeps the serial procedure'):
            self.assertIn(phrase, flat)

    def test_the_filled_example_shows_the_block_on(self):
        text = self.read('docs/config.md')
        example = text[text.index('## A filled example'):]
        self.assertIn('"parallel": {\n    "seats": 2\n  }', example)


if __name__ == '__main__':
    unittest.main()
