"""parallel-contract: the frozen schema-1 worker payloads and `parallel.seats`.

Pure functions over already-parsed values: no file access, no printing, no process
state. Contract: docs/opencode-parallel.md -- §4 (assignment), §5 (result), §6
(task states), §7 (concurrency metadata) -- and docs/config.md (`parallel`).

The module fixes the vocabulary the coordinator, the scheduler and the adapter
share: the schema number, the task states of §6, the result statuses of §5, the
review verdicts and the deviation severities. It carries no queue, run-state,
scheduler or worktree policy.

Every function raises `ContractError` -- a `ValueError` carrying the `kind` a CLI
maps into its error envelope (`invalid_config`, `invalid_assignment`,
`invalid_result`). Nothing is coerced and nothing is dropped: a missing key, an
unknown key and a wrong-typed value are all refused.
"""
import os
import re
import uuid

import models

SCHEMA = 1
DEFAULT_SEATS = 1

# §4/§5: every key is required; the nullable ones carry an explicit null. A result
# repeats the assignment's identity exactly and adds its outcome.
ASSIGNMENT_FIELDS = (
    'schema', 'ticket', 'phase', 'task', 'run_id', 'attempt', 'claim', 'base_commit',
    'files', 'tests', 'route', 'model', 'worker_root', 'artifact_root',
)
RESULT_FIELDS = ASSIGNMENT_FIELDS + (
    'status', 'changed_files', 'verify', 'review', 'report', 'deviations',
)
VERIFY_FIELDS = ('command', 'ok', 'output')
REVIEW_FIELDS = ('required', 'route', 'verdict', 'rounds')
DEVIATION_FIELDS = ('severity', 'summary', 'paths')

ROUTES = ('light', 'full')
RESULT_STATUSES = ('succeeded', 'failed', 'blocked', 'cancelled')
REVIEW_VERDICTS = ('passed', 'changes-requested', 'skipped')
DEVIATION_SEVERITIES = ('minor', 'major')
# §6: the main sequence, then the side states a task can end in.
TASK_STATES = ('reserved', 'preparing', 'running', 'verifying/reviewing', 'prepared',
               'integrating', 'published')
SIDE_STATES = ('blocked', 'failed', 'cancelled')

TICKET_RE = re.compile(r'[A-Z][A-Z0-9]*-\d+\Z')
TASK_RE = re.compile(r'\d+\.\d+\Z')
COMMIT_RE = re.compile(r'[0-9a-f]{40}\Z')


class ContractError(ValueError):
    """A contract violation: `kind` is the error envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise ContractError(kind, message)


def _object(value, what, kind):
    if not isinstance(value, dict):
        _fail(kind, '{} is not an object: {!r}'.format(what, value))
    return value


def _keys(value, required, what, kind):
    """Every required key present, and no key outside the schema."""
    missing = [name for name in required if name not in value]
    if missing:
        _fail(kind, '{} is missing {}'.format(
            what, ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in value if name not in required)
    if unknown:
        _fail(kind, '{} carries unknown {}'.format(
            what, ', '.join('`{}`'.format(name) for name in unknown)))


def _text(value, what, kind, allow_empty=False):
    if not isinstance(value, str):
        _fail(kind, '{} is not a string: {!r}'.format(what, value))
    if not value and not allow_empty:
        _fail(kind, '{} is empty'.format(what))
    if value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _string(value, what, kind):
    """A string kept verbatim: a captured tail may be multi-line and carry whitespace."""
    if not isinstance(value, str):
        _fail(kind, '{} is not a string: {!r}'.format(what, value))
    return value


def _integer(value, what, kind, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(kind, '{} is not an integer: {!r}'.format(what, value))
    if value < minimum:
        _fail(kind, '{} must be at least {}: {!r}'.format(what, minimum, value))
    return value


def _relative(value, what, kind):
    """A non-empty relative path that does not escape upward on its own."""
    _text(value, what, kind)
    if os.path.isabs(value):
        _fail(kind, '{} is not a relative path: {!r}'.format(what, value))
    normalized = os.path.normpath(value)
    if normalized == '..' or normalized.startswith('..' + os.sep):
        _fail(kind, '{} escapes its root: {!r}'.format(what, value))
    return value


def _paths(value, what, kind):
    if not isinstance(value, list):
        _fail(kind, '{} is not an array: {!r}'.format(what, value))
    for entry in value:
        _relative(entry, '{} entry'.format(what), kind)
    return value


def _inside(root, relative, what, kind):
    """Refuse a path whose resolved form leaves `root` (§5: never written)."""
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, relative))
    if target != base and not target.startswith(base + os.sep):
        _fail(kind, '{} escapes its root {}: {!r}'.format(what, root, relative))
    return relative


def _identity(value, what, kind):
    """Validate the §4 identity fields shared by an assignment and a result."""
    schema = value['schema']
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SCHEMA:
        _fail(kind, 'the {} schema must be {}: {!r}'.format(what, SCHEMA, schema))
    if not isinstance(value['ticket'], str) or not TICKET_RE.match(value['ticket']):
        _fail(kind, 'the {} ticket is not a canonical <PROJECT_KEY>-<number>: {!r}'.format(
            what, value['ticket']))
    if value['phase'] is not None:
        _integer(value['phase'], 'the {} phase'.format(what), kind, 1)
    if not isinstance(value['task'], str) or not TASK_RE.match(value['task']):
        _fail(kind, 'the {} task is not an `<iteration>.<n>` number: {!r}'.format(
            what, value['task']))
    run_id = value['run_id']
    _text(run_id, 'the {} run_id'.format(what), kind)
    try:
        parsed = uuid.UUID(run_id)
    except (ValueError, AttributeError):
        _fail(kind, 'the {} run_id is not a UUID: {!r}'.format(what, run_id))
    if str(parsed) != run_id:
        _fail(kind, 'the {} run_id is not a canonical UUID: {!r}'.format(what, run_id))
    _integer(value['attempt'], 'the {} attempt'.format(what), kind, 1)
    if value['claim'] is not None:
        _text(value['claim'], 'the {} claim'.format(what), kind)
    base_commit = value['base_commit']
    if not isinstance(base_commit, str) or not COMMIT_RE.match(base_commit):
        _fail(kind, 'the {} base_commit is not a full 40-hex commit id: {!r}'.format(
            what, base_commit))
    _paths(value['files'], 'the {} files'.format(what), kind)
    _paths(value['tests'], 'the {} tests'.format(what), kind)
    if value['route'] not in ROUTES:
        _fail(kind, 'the {} route is not `light` or `full`: {!r}'.format(what, value['route']))
    if value['model'] is not None and not models.value_ok(value['model']):
        _fail(kind, 'the {} model is not a <provider>/<model>[#<variant>] value: {!r}'.format(
            what, value['model']))
    for name in ('worker_root', 'artifact_root'):
        _text(value[name], 'the {} {}'.format(what, name), kind)
        if not os.path.isabs(value[name]):
            _fail(kind, 'the {} {} is not an absolute path: {!r}'.format(
                what, name, value[name]))


def resolve_config(config):
    """`{'seats': n}` from a parsed `.artel/config.json` (docs/config.md `parallel`).

    An absent section or key resolves to `DEFAULT_SEATS` (1), which keeps the serial
    procedure exactly as it is. `parallel.seats` is a positive integer: a boolean is
    not an integer, and zero, a negative number, a string or a float is an
    `invalid_config` error rather than a silently substituted default.
    """
    _object(config, 'the config root', 'invalid_config')
    section = config.get('parallel')
    if section is None:
        return {'seats': DEFAULT_SEATS}
    _object(section, 'parallel', 'invalid_config')
    seats = section.get('seats')
    if seats is None:
        return {'seats': DEFAULT_SEATS}
    if isinstance(seats, bool) or not isinstance(seats, int) or seats < 1:
        _fail('invalid_config', 'parallel.seats is not a positive integer: {!r}'.format(seats))
    return {'seats': seats}


def validate_assignment(value):
    """The schema-1 assignment of docs/opencode-parallel.md §4, or `invalid_assignment`.

    Every key is required and a nullable key carries an explicit `null`; an unknown
    key is refused. The declared `files`/`tests` are repo-relative, but not confined
    to `worker_root`: `files` is a declaration, not a sandbox (§11).
    """
    kind = 'invalid_assignment'
    _object(value, 'the assignment', kind)
    _keys(value, ASSIGNMENT_FIELDS, 'the assignment', kind)
    _identity(value, 'assignment', kind)
    return {name: value[name] for name in ASSIGNMENT_FIELDS}


def validate_result(value, assignment):
    """The schema-1 result of §5 against its assignment, or `invalid_result`.

    The identity fields must equal the assignment's exactly. `changed_files` and the
    deviation paths must resolve inside the assignment's `worker_root`, and `report`
    inside its `artifact_root` -- a path escaping its root is refused, never written.
    """
    assigned = validate_assignment(assignment)
    kind = 'invalid_result'
    _object(value, 'the result', kind)
    _keys(value, RESULT_FIELDS, 'the result', kind)
    _identity(value, 'result', kind)
    for name in ASSIGNMENT_FIELDS:
        if value[name] != assigned[name]:
            _fail(kind, 'the result {} does not match the assignment: {!r} != {!r}'.format(
                name, value[name], assigned[name]))
    if value['status'] not in RESULT_STATUSES:
        _fail(kind, 'the result status is not one of {}: {!r}'.format(
            ', '.join(RESULT_STATUSES), value['status']))
    worker_root, artifact_root = assigned['worker_root'], assigned['artifact_root']
    _paths(value['changed_files'], 'changed_files', kind)
    for relative in value['changed_files']:
        _inside(worker_root, relative, 'a changed_files entry', kind)
    _verify(value['verify'], kind)
    _review(value['review'], kind)
    if value['report'] is not None:
        _relative(value['report'], 'report', kind)
        _inside(artifact_root, value['report'], 'report', kind)
    _deviations(value['deviations'], worker_root, kind)
    return {name: value[name] for name in RESULT_FIELDS}


def _verify(value, kind):
    if not isinstance(value, list):
        _fail(kind, 'verify is not an array: {!r}'.format(value))
    for entry in value:
        _object(entry, 'a verify entry', kind)
        _keys(entry, VERIFY_FIELDS, 'a verify entry', kind)
        _text(entry['command'], 'a verify command', kind)
        if not isinstance(entry['ok'], bool):
            _fail(kind, 'a verify `ok` is not a boolean: {!r}'.format(entry['ok']))
        _string(entry['output'], 'a verify output', kind)


def _review(value, kind):
    """`null` in the worker's submission; the coordinator fills the object (§5)."""
    if value is None:
        return
    _object(value, 'review', kind)
    _keys(value, REVIEW_FIELDS, 'review', kind)
    if not isinstance(value['required'], bool):
        _fail(kind, 'review.required is not a boolean: {!r}'.format(value['required']))
    if value['route'] not in ROUTES:
        _fail(kind, 'review.route is not `light` or `full`: {!r}'.format(value['route']))
    if value['verdict'] not in REVIEW_VERDICTS:
        _fail(kind, 'review.verdict is not one of {}: {!r}'.format(
            ', '.join(REVIEW_VERDICTS), value['verdict']))
    _integer(value['rounds'], 'review.rounds', kind, 0)


def _deviations(value, worker_root, kind):
    if not isinstance(value, list):
        _fail(kind, 'deviations is not an array: {!r}'.format(value))
    for entry in value:
        _object(entry, 'a deviation', kind)
        _keys(entry, DEVIATION_FIELDS, 'a deviation', kind)
        if entry['severity'] not in DEVIATION_SEVERITIES:
            _fail(kind, 'a deviation severity is not `minor` or `major`: {!r}'.format(
                entry['severity']))
        _text(entry['summary'], 'a deviation summary', kind)
        _paths(entry['paths'], 'a deviation paths', kind)
        for relative in entry['paths']:
            _inside(worker_root, relative, 'a deviation path', kind)
