#!/usr/bin/env python3
"""parallel-launch: the OpenCode worker-session adapter's policy half.

`opencode/plugin/parallel.ts` is the native transport (session create/prompt/status/
interrupt through the installed 2.0.29 plugin client); this module owns every decision it
makes, so no schema or lifecycle policy is reimplemented in TypeScript. Contract:
docs/opencode-parallel.md -- §2 (adapter surface), §4-§5 (assignment/result), §6/§10
(task states, the three outcome layers), §9 (model resolution) and §12 (capability
failure) -- plus the reviewed Task 6 role boundary.

The lifecycle, in order:

1. `plan_start` validates the request (the strict assignment via `parallel_contract`, the
   worker role, the pinned prompt, the optional permission ruleset), the caller (a worker
   location never drives the adapter), the capability report (exactly the proven OpenCode
   version and API surface, before any effect), the assigned model (a concrete value that
   resolves against the installed model list -- never an inherit), the worker root (the
   reviewed lifecycle marker via `hook_common.worker_context`, including the first-consumed
   `base_ref`/`result_ref`/`config` fields) and the launch journal, then persists the launch
   intent **before** any session can exist. The session id is derived from the assignment
   identity, so the native create is idempotent and a duplicate start reconciles instead of
   creating a second session.
2. `record_session` verifies the returned native session against the journal (id, root,
   role, model, permissions, metadata, no parent) and persists the session id **before** the
   pinned prompt is admitted.
3. `record_prompt` marks the prompt admitted.
4. `reconcile` is the crash path: an existing journal plus the durable native session and
   messages decide between create, adopt and prompt -- never a second create, never a
   double prompt, never an event replay.
5. `plan_status`/`interpret_status` recover the durable session state and the validated
   `<artifact_root>/result.json`: a native idle or `succeeded` without a valid,
   identity-matching result is not completion, and an `interrupted` outcome wins over a
   contradictory success result.
6. `plan_interrupt`/`interpret_interrupt` interrupt one known session and report the durable
   post-interrupt state.

Every journal is host-owned under `<main>/.artel/run/<ticket>/parallel/<run_id>/launches/`.
The module never schedules, claims, releases, publishes, commits, edits tasklists or answers
permissions; it has no queue, Git or manifest code at all.
"""
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import models
import parallel_contract

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import hook_common  # noqa: E402

SCHEMA = 1
PROVEN_VERSION = '2.0.29'
# The installed client surface the transport uses, as dotted capability keys.
REQUIRED_API = ('app.version', 'model.list', 'session.create', 'session.get',
                'session.prompt', 'session.interrupt', 'session.context')
REQUEST_KEYS = ('assignment', 'agent', 'prompt', 'permissions')
ASSIGNMENT_REQUEST_KEYS = ('assignment',)
PROMPT_KEYS = ('text', 'delivery')
DELIVERIES = ('steer', 'queue')
RULE_KEYS = ('action', 'resource', 'effect')
EFFECTS = ('allow', 'deny', 'ask')
CALLER_KEYS = ('session_id', 'location')
MODEL_FACT_KEYS = ('available', 'variants')
JOURNAL_KEYS = ('schema', 'ticket', 'phase', 'task', 'run_id', 'attempt', 'worker_root',
                'artifact_root', 'session_id', 'agent', 'model', 'prompt_sha256', 'state',
                'created_at', 'updated_at')
JOURNAL_STATES = ('intent', 'created', 'prompted', 'interrupted')
SESSION_ID_RE = 'ses'


class LaunchError(ValueError):
    """A policy refusal: `kind` is the adapter's error kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise LaunchError(kind, message)


def _object(value, what, kind):
    if not isinstance(value, dict):
        _fail(kind, '{} is not an object: {!r}'.format(what, value))
    return value


def _keys(value, required, what, kind, allowed=None):
    allowed = required if allowed is None else allowed
    missing = [name for name in required if name not in value]
    if missing:
        _fail(kind, '{} is missing {}'.format(
            what, ', '.join('`{}`'.format(name) for name in missing)))
    unknown = sorted(name for name in value if name not in allowed)
    if unknown:
        _fail(kind, '{} carries unknown {}'.format(
            what, ', '.join('`{}`'.format(name) for name in unknown)))


def _text(value, what, kind):
    if not isinstance(value, str) or not value:
        _fail(kind, '{} is not a non-empty string: {!r}'.format(what, value))
    if value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _inside(base, target):
    """`target` resolves strictly inside `base` (both real paths)."""
    base = os.path.realpath(str(base))
    target = os.path.realpath(str(target))
    return target != base and target.startswith(base + os.sep)


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name + '.',
                                         suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


# --- capabilities -------------------------------------------------------------------------

def capability_report(capabilities):
    """The read-only capability report: exactly the proven version and API surface.

    `capabilities` is the transport's observation of the installed plugin host
    (`{version, api: {<dotted name>: bool}}`). Anything else -- a different version, a
    missing/ill-typed API key, a non-object report -- is refused with its reason; the
    caller (and `plan_start`) must stop before any claim, worktree, journal or session.
    """
    _object(capabilities, 'the capability report', 'invalid_request')
    version = capabilities.get('version')
    api = capabilities.get('api')
    reasons = []
    kind = None
    if not isinstance(version, str) or version != PROVEN_VERSION:
        reasons.append('the installed version is not the proven one: {!r} != {!r}'.format(
            version, PROVEN_VERSION))
        kind = 'unsupported_version'
    if not isinstance(api, dict):
        reasons.append('the API report is not an object: {!r}'.format(api))
        kind = kind or 'unsupported_api'
    else:
        missing = [name for name in REQUIRED_API if api.get(name) is not True]
        if missing:
            reasons.append('the installed API surface is missing: {}'.format(
                ', '.join(missing)))
            kind = kind or 'unsupported_api'
    return {'schema': SCHEMA, 'ok': not reasons, 'kind': kind,
            'version': version if isinstance(version, str) else None,
            'proven_version': PROVEN_VERSION, 'api': api if isinstance(api, dict) else {},
            'reasons': reasons}


# --- request, caller, model ---------------------------------------------------------------

def _prompt(value):
    _object(value, 'the prompt', 'invalid_prompt')
    _keys(value, ('text',), 'the prompt', 'invalid_prompt', allowed=PROMPT_KEYS)
    text = value['text']
    if not isinstance(text, str) or not text.strip():
        _fail('invalid_prompt', 'the prompt text is not a non-empty string')
    if any(char in text for char in '\x00'):
        _fail('invalid_prompt', 'the prompt text carries a NUL byte')
    delivery = value.get('delivery')
    if delivery is not None and delivery not in DELIVERIES:
        _fail('invalid_prompt', 'the prompt delivery is not `steer` or `queue`: {!r}'.format(
            delivery))
    return {'text': text, 'delivery': delivery}


def _permissions(value):
    if value is None:
        return None
    if not isinstance(value, list):
        _fail('invalid_permissions', 'permissions is not an array: {!r}'.format(value))
    for entry in value:
        _object(entry, 'a permission rule', 'invalid_permissions')
        _keys(entry, RULE_KEYS, 'a permission rule', 'invalid_permissions')
        _text(entry['action'], 'a permission action', 'invalid_permissions')
        _text(entry['resource'], 'a permission resource', 'invalid_permissions')
        if entry['effect'] not in EFFECTS:
            _fail('invalid_permissions', 'a permission effect is not allow/deny/ask: {!r}'.format(
                entry['effect']))
    return value


def _request(value):
    """The strict adapter request `{assignment, agent, prompt, permissions}`."""
    _object(value, 'the request', 'invalid_request')
    _keys(value, REQUEST_KEYS, 'the request', 'invalid_request')
    try:
        assignment = parallel_contract.validate_assignment(value['assignment'])
    except parallel_contract.ContractError as exc:
        _fail('invalid_assignment', exc.message)
    agent = _text(value['agent'], 'the agent', 'invalid_request')
    prompt = _prompt(value['prompt'])
    permissions = _permissions(value['permissions'])
    return {'assignment': assignment, 'agent': agent, 'prompt': prompt,
            'permissions': permissions}


def _assignment_request(value):
    """The strict status/interrupt request `{assignment}`: only the identity the journal
    and the worker root are checked against; no role, prompt or ruleset is consumed."""
    _object(value, 'the request', 'invalid_request')
    _keys(value, ASSIGNMENT_REQUEST_KEYS, 'the request', 'invalid_request')
    try:
        assignment = parallel_contract.validate_assignment(value['assignment'])
    except parallel_contract.ContractError as exc:
        _fail('invalid_assignment', exc.message)
    return {'assignment': assignment}


def _caller(value):
    _object(value, 'the caller', 'invalid_request')
    _keys(value, CALLER_KEYS, 'the caller', 'invalid_request')
    session_id = _text(value['session_id'], 'the caller session id', 'invalid_request')
    location = _text(value['location'], 'the caller location', 'invalid_request')
    if not os.path.isabs(location):
        _fail('invalid_request', 'the caller location is not absolute: {!r}'.format(location))
    return {'session_id': session_id, 'location': location}


def _check_caller(caller):
    """A worker location never drives the adapter: the authoritative marker reader, not
    the bridge's TS subset, decides whether the caller is a worker root."""
    if hook_common.worker_context(root=caller['location']) is not None:
        _fail('caller_is_worker',
              'the calling session is in a pinned worker root ({}); the adapter is '
              'coordinator-only and a worker must not orchestrate'.format(caller['location']))
    return caller


def _model_ref(assignment):
    """The concrete `Model.Ref` for the assignment's model, or a refusal.

    `model` is nullable at the resolver boundary; a `null` here means nobody resolved it,
    and the adapter never lets a worker inherit the caller's model (§9).
    """
    value = assignment['model']
    if value is None:
        _fail('unresolved_model', 'the assignment model is null; the coordinator resolves a '
                                  'concrete model before dispatch (never an inherit)')
    if not models.value_ok(value):
        _fail('unresolved_model', 'the assignment model is not <provider>/<model>[#<variant>]: '
                                  '{!r}'.format(value))
    model_part, _, variant = value.partition('#')
    provider, _, model_id = model_part.partition('/')
    ref = {'providerID': provider, 'id': model_id}
    if variant:
        ref['variant'] = variant
    return ref


def _model_facts(value):
    _object(value, 'the model facts', 'invalid_request')
    _keys(value, MODEL_FACT_KEYS, 'the model facts', 'invalid_request')
    if value['available'] is not True:
        _fail('unresolved_model', 'the assigned model does not resolve against the installed '
                                  'model list (available: {!r})'.format(value['available']))
    variants = value['variants']
    if not isinstance(variants, list) or any(not isinstance(item, str) for item in variants):
        _fail('invalid_request', 'the model facts variants are not an array of strings: '
                                 '{!r}'.format(variants))
    return {'available': True, 'variants': variants}


def _check_variant(ref, facts):
    variant = ref.get('variant')
    if variant is not None and variant not in facts['variants']:
        _fail('unresolved_model', 'the model variant {!r} is not one of the installed '
                                  'variants: {}'.format(variant, ', '.join(facts['variants']) or
                                                        'none'))


# --- the worker root and its first-consumed marker fields ---------------------------------

def _marker_refs(assignment):
    base = 'refs/artel/parallel/{}/{}/{}/{}'.format(assignment['run_id'], assignment['task'],
                                                    assignment['attempt'], '{}')
    return {'base': base.format('base'), 'result': base.format('result')}


def _worker_context(assignment):
    """The validated worker root for this exact assignment, or a refusal.

    `hook_common.worker_context` is the reviewed authoritative reader (schema, role,
    complete setup, realpath root equality, worktree area, actual Git common dir). The
    adapter additionally validates the marker fields it consumes first: the two namespaced
    refs must be this attempt's canonical names and `config` must be absent or the
    lifecycle's `{path, sha256}` shape -- the Task 6 review's unvalidated passthrough.
    """
    root = assignment['worker_root']
    context = hook_common.worker_context(root=root)
    if context is None:
        _fail('not_worker_root', '{} is not a launch-ready worker root for this repository '
                                 '(the lifecycle marker is missing, malformed, foreign or '
                                 'not complete)'.format(root))
    if context['assignment'] != assignment:
        _fail('worker_identity_mismatch', 'the worker marker at {} carries a different '
                                          'assignment'.format(root))
    expected = _marker_refs(assignment)
    if context.get('base_ref') != expected['base'] or context.get('result_ref') != expected['result']:
        _fail('invalid_worker_marker', 'the worker marker refs are not this attempt\'s '
                                       'canonical names: {!r} / {!r}'.format(
                                           context.get('base_ref'), context.get('result_ref')))
    config = context.get('config')
    if config is not None:
        if not isinstance(config, dict) or set(config) != {'path', 'sha256'} \
                or config['path'] != '.artel/config.json' \
                or not isinstance(config['sha256'], str) \
                or len(config['sha256']) != 64 \
                or any(char not in '0123456789abcdef' for char in config['sha256']):
            _fail('invalid_worker_marker', 'the worker marker config is not the lifecycle\'s '
                                           '{path, sha256} record: {!r}'.format(config))
    artifact = assignment['artifact_root']
    if not _inside(root, artifact):
        _fail('invalid_assignment', 'the artifact root {} is not strictly inside the worker '
                                    'root {}'.format(artifact, root))
    return context


# --- journal ------------------------------------------------------------------------------

def derive_session_id(assignment):
    """A deterministic session id from the assignment identity: the native create is
    idempotent by id, so a duplicate start adopts instead of creating a second session."""
    seed = '{}\x00{}\x00{}'.format(assignment['run_id'], assignment['task'],
                                   assignment['attempt'])
    return 'ses_' + hashlib.sha256(seed.encode('utf-8')).hexdigest()[:26]


def journal_path(main, assignment):
    return (Path(main) / '.artel' / 'run' / assignment['ticket'] / 'parallel' /
            assignment['run_id'] / 'launches' /
            '{}-a{}.json'.format(assignment['task'], assignment['attempt']))


def _read_journal(path, assignment):
    """The stored launch record, or None; anything malformed is refused, never rewritten."""
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        _fail('invalid_journal', 'the launch journal at {} cannot be read: {}'.format(path, exc))
    try:
        value = json.loads(text)
    except ValueError as exc:
        _fail('invalid_journal', 'the launch journal at {} is not valid JSON: {}'.format(
            path, exc))
    _object(value, 'the launch journal', 'invalid_journal')
    _keys(value, JOURNAL_KEYS, 'the launch journal', 'invalid_journal')
    if isinstance(value['schema'], bool) or value['schema'] != SCHEMA:
        _fail('invalid_journal', 'the launch journal schema is not {}'.format(SCHEMA))
    if value['state'] not in JOURNAL_STATES:
        _fail('invalid_journal', 'the launch journal state is unknown: {!r}'.format(
            value['state']))
    for name in ('ticket', 'phase', 'task', 'run_id', 'attempt', 'worker_root', 'artifact_root'):
        if value[name] != assignment[name]:
            _fail('invalid_journal', 'the launch journal {} does not match this assignment: '
                                     '{!r} != {!r}'.format(name, value[name], assignment[name]))
    return value


def _write_journal(path, assignment, session_id, agent, model, prompt_sha256, state,
                   created_at=None):
    now = _utc_now()
    value = {
        'schema': SCHEMA,
        'ticket': assignment['ticket'], 'phase': assignment['phase'], 'task': assignment['task'],
        'run_id': assignment['run_id'], 'attempt': assignment['attempt'],
        'worker_root': assignment['worker_root'], 'artifact_root': assignment['artifact_root'],
        'session_id': session_id, 'agent': agent, 'model': model,
        'prompt_sha256': prompt_sha256, 'state': state,
        'created_at': created_at or now, 'updated_at': now,
    }
    _atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + '\n')
    return value


def _journal_for(request):
    """(assignment, journal path, journal value or None, context, model ref)."""
    assignment = request['assignment']
    context = _worker_context(assignment)
    ref = _model_ref(assignment)
    main = context['repository']['main']
    path = journal_path(main, assignment)
    return assignment, path, _read_journal(path, assignment), context, ref


def _identity_metadata(assignment):
    return {'schema': SCHEMA, 'ticket': assignment['ticket'], 'phase': assignment['phase'],
            'task': assignment['task'], 'run_id': assignment['run_id'],
            'attempt': assignment['attempt'], 'claim': assignment['claim']}


def _create_payload(request, session_id, ref):
    assignment = request['assignment']
    payload = {
        'id': session_id,
        'title': 'artel worker {} {} a{}'.format(assignment['ticket'], assignment['task'],
                                                 assignment['attempt']),
        'agent': request['agent'],
        'model': ref,
        'location': {'directory': assignment['worker_root']},
        'metadata': _identity_metadata(assignment),
    }
    if request['permissions'] is not None:
        payload['permissions'] = request['permissions']
    return payload


def _prompt_payload(request):
    payload = {'text': request['prompt']['text']}
    if request['prompt']['delivery'] is not None:
        payload['delivery'] = request['prompt']['delivery']
    return payload


def _started_envelope(assignment, journal, adopted):
    return {
        'schema': SCHEMA, 'status': 'started', 'adopted': adopted,
        'session_id': journal['session_id'],
        'ticket': assignment['ticket'], 'phase': assignment['phase'],
        'task': assignment['task'], 'run_id': assignment['run_id'],
        'attempt': assignment['attempt'], 'agent': journal['agent'],
        'model': assignment['model'], 'worker_root': assignment['worker_root'],
        'artifact_root': assignment['artifact_root'],
    }


# --- session verification -----------------------------------------------------------------

def _metadata_matches(metadata, assignment):
    """Whether `metadata` carries this launch identity, extra host namespaces allowed.

    A host may decorate a session with its own metadata namespaces; every required
    identity key must still be PRESENT and exactly typed and equal. A missing key, a null
    where an integer belongs, a non-null where the identity is null, and a boolean or float
    for an integer are all refusals -- `.get(name) == expected` alone would pass a missing
    nullable key.
    """
    if not isinstance(metadata, dict):
        return False
    for name, expected in _identity_metadata(assignment).items():
        if name not in metadata:
            return False
        actual = metadata[name]
        if expected is None:
            if actual is not None:
                return False
        elif type(actual) is not type(expected) or actual != expected:
            return False
    return True


def _verify_session(session, journal, assignment, ref, agent=None, permissions=None):
    """The returned native session must be exactly the launched one, or a refusal.

    The launch path passes the request's role and ruleset; the status path passes neither
    and checks the session's role against the role the journal recorded at launch.
    """
    _object(session, 'the native session', 'wrong_session')
    session_id = session.get('id')
    if not isinstance(session_id, str) or session_id != journal['session_id']:
        _fail('wrong_session', 'the native session id {!r} is not the journal\'s {}'.format(
            session_id, journal['session_id']))
    if session.get('parentID') is not None:
        _fail('wrong_session', 'the native session {} is a child session; a worker is a root '
                               'session'.format(session_id))
    location = session.get('location')
    directory = location.get('directory') if isinstance(location, dict) else None
    if not isinstance(directory, str) or \
            os.path.realpath(directory) != os.path.realpath(assignment['worker_root']):
        _fail('wrong_root', 'the native session {} is at {!r}, not at the assigned worker root '
                            '{}'.format(session_id, directory, assignment['worker_root']))
    expected_agent = agent if agent is not None else journal['agent']
    if session.get('agent') != expected_agent:
        _fail('wrong_agent', 'the native session {} runs agent {!r}, not {!r}'.format(
            session_id, session.get('agent'), expected_agent))
    model = session.get('model')
    if not isinstance(model, dict) or model.get('providerID') != ref['providerID'] \
            or model.get('id') != ref['id']:
        _fail('wrong_model', 'the native session {} runs model {!r}, not {}'.format(
            session_id, model, ref))
    if ref.get('variant') is not None and model.get('variant') != ref['variant']:
        _fail('wrong_model', 'the native session {} runs variant {!r}, not {!r}'.format(
            session_id, model.get('variant'), ref['variant']))
    if not _metadata_matches(session.get('metadata'), assignment):
        _fail('wrong_session', 'the native session {} metadata does not carry this launch '
                               'identity'.format(session_id))
    if permissions is not None and session.get('permissions') != permissions:
        _fail('permissions_mismatch', 'the native session {} did not keep the requested '
                                      'permission ruleset'.format(session_id))
    return session


def _prompt_admitted(messages, prompt_sha256):
    if not isinstance(messages, list):
        _fail('invalid_request', 'the durable messages are not an array: {!r}'.format(messages))
    for message in messages:
        if not isinstance(message, dict) or message.get('type') != 'user':
            continue
        text = message.get('text')
        if isinstance(text, str) and _sha256_text(text) == prompt_sha256:
            return True
    return False


# --- result recovery ----------------------------------------------------------------------

def _read_result(assignment):
    """`(validated result, error kind)` from `<artifact_root>/result.json`, no events."""
    root = assignment['worker_root']
    artifact = Path(assignment['artifact_root'])
    if not _inside(root, artifact):
        return None, 'escapes_root'
    path = artifact / 'result.json'
    if not path.is_file():
        return None, 'missing'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, 'malformed'
    if not isinstance(value, dict):
        return None, 'malformed'
    try:
        return parallel_contract.validate_result(value, assignment), None
    except parallel_contract.ContractError:
        return None, 'identity_mismatch'


# --- commands -----------------------------------------------------------------------------

def plan_start(request, capabilities, caller, model_facts):
    """Validate everything and persist the launch intent before any session exists."""
    report = capability_report(capabilities)
    if not report['ok']:
        _fail(report['kind'], '; '.join(report['reasons']))
    _check_caller(_caller(caller))
    req = _request(request)
    facts = _model_facts(model_facts)
    assignment, path, journal, _context, ref = _journal_for(req)
    _check_variant(ref, facts)
    session_id = derive_session_id(assignment)
    if journal is None:
        digest = _sha256_text(req['prompt']['text'])
        _write_journal(path, assignment, session_id, req['agent'], ref, digest, 'intent')
        return {'action': 'create', 'session_id': session_id, 'journal': str(path),
                'create': _create_payload(req, session_id, ref)}
    if journal['session_id'] != session_id \
            or journal['agent'] != req['agent'] or journal['model'] != ref \
            or journal['prompt_sha256'] != _sha256_text(req['prompt']['text']):
        _fail('invalid_journal', 'the launch journal at {} belongs to a different launch '
                                 '(session, agent, model or prompt changed)'.format(path))
    if journal['state'] == 'prompted':
        return {'action': 'adopted', 'session_id': session_id, 'journal': str(path),
                'envelope': _started_envelope(assignment, journal, adopted=True)}
    if journal['state'] == 'interrupted':
        _fail('already_interrupted', 'the launch journal at {} records an interrupted '
                                     'session; a retry takes the next attempt'.format(path))
    return {'action': 'reconcile', 'session_id': session_id, 'journal': str(path)}


def record_session(request, session):
    """Verify the created session and persist its id before the prompt is admitted."""
    req = _request(request)
    assignment, path, journal, _context, ref = _journal_for(req)
    if journal is None or journal['state'] not in ('intent', 'created'):
        _fail('invalid_journal', 'no launch intent to record at {}'.format(path))
    _verify_session(session, journal, req['assignment'], ref, agent=req['agent'],
                    permissions=req['permissions'])
    _write_journal(path, assignment, journal['session_id'], req['agent'], ref,
                   journal['prompt_sha256'], 'created', created_at=journal['created_at'])
    return {'action': 'prompt', 'session_id': journal['session_id'],
            'prompt': _prompt_payload(req)}


def record_prompt(request, inbox=None):
    """Mark the pinned prompt admitted on the recorded session."""
    req = _request(request)
    assignment, path, journal, _context, ref = _journal_for(req)
    if journal is None or journal['state'] not in ('intent', 'created'):
        _fail('invalid_journal', 'no launch intent to prompt at {}'.format(path))
    if inbox is not None:
        _object(inbox, 'the prompt admission', 'invalid_request')
        if inbox.get('sessionID') not in (None, journal['session_id']):
            _fail('wrong_session', 'the prompt admission belongs to session {!r}, not {}'.format(
                inbox.get('sessionID'), journal['session_id']))
    journal = _write_journal(path, assignment, journal['session_id'], req['agent'], ref,
                             journal['prompt_sha256'], 'prompted',
                             created_at=journal['created_at'])
    return {'action': 'started', 'envelope': _started_envelope(assignment, journal,
                                                               adopted=False)}


def reconcile(request, session, messages):
    """The crash path: decide from the durable session and messages, never from events."""
    req = _request(request)
    assignment, path, journal, _context, ref = _journal_for(req)
    if journal is None:
        _fail('invalid_journal', 'no launch journal to reconcile at {}'.format(path))
    if journal['state'] == 'prompted':
        return {'action': 'adopted', 'session_id': journal['session_id'], 'journal': str(path),
                'envelope': _started_envelope(assignment, journal, adopted=True)}
    if journal['state'] == 'interrupted':
        _fail('already_interrupted', 'the launch journal at {} records an interrupted '
                                     'session; a retry takes the next attempt'.format(path))
    if session is None:
        if journal['state'] != 'intent':
            _fail('wrong_session', 'the launch journal at {} records session {}, but the '
                                   'native service has none'.format(path, journal['session_id']))
        return {'action': 'create', 'session_id': journal['session_id'], 'journal': str(path),
                'create': _create_payload(req, journal['session_id'], ref)}
    _verify_session(session, journal, req['assignment'], ref, agent=req['agent'],
                    permissions=req['permissions'])
    journal = _write_journal(path, assignment, journal['session_id'], req['agent'], ref,
                             journal['prompt_sha256'], 'created',
                             created_at=journal['created_at'])
    if _prompt_admitted(messages, journal['prompt_sha256']):
        journal = _write_journal(path, assignment, journal['session_id'], req['agent'], ref,
                                 journal['prompt_sha256'], 'prompted',
                                 created_at=journal['created_at'])
        return {'action': 'adopted', 'session_id': journal['session_id'], 'journal': str(path),
                'envelope': _started_envelope(assignment, journal, adopted=True)}
    return {'action': 'prompt', 'session_id': journal['session_id'], 'journal': str(path),
            'prompt': _prompt_payload(req)}


def _unknown_envelope():
    return {'schema': SCHEMA, 'known': False, 'present': False, 'state': 'unknown',
            'session_id': None, 'success': False, 'result': None, 'result_error': 'missing',
            'interrupted_over_result': False}


def plan_status(request, caller):
    """Which session to read, or the unknown envelope; never an effect."""
    _check_caller(_caller(caller))
    req = _assignment_request(request)
    assignment, path, journal, _context, _ref = _journal_for(req)
    if journal is None:
        return {'action': 'unknown', 'envelope': _unknown_envelope()}
    return {'action': 'read', 'session_id': journal['session_id'], 'journal': str(path)}


def interpret_status(request, session, messages):
    """The durable status envelope: native state plus the validated result file.

    Idle and native success are not completion; an interruption wins over a contradictory
    success result; a missing, malformed, escaping or identity-mismatched result stays
    non-successful.
    """
    req = _assignment_request(request)
    assignment, path, journal, _context, ref = _journal_for(req)
    if journal is None:
        return {'envelope': _unknown_envelope()}
    if session is None:
        envelope = _unknown_envelope()
        envelope.update({'known': True, 'present': False, 'state': 'missing',
                         'session_id': journal['session_id']})
        return {'envelope': envelope}
    _verify_session(session, journal, req['assignment'], ref)
    result, error = _read_result(assignment)
    outcome = session.get('outcome')
    time = session.get('time') if isinstance(session.get('time'), dict) else {}
    idle = time.get('idle') is not None
    if outcome == 'interrupted':
        state, success, over = 'interrupted', False, result is not None
    elif outcome == 'failed':
        state, success, over = 'failed', False, False
    elif outcome == 'succeeded':
        if result is not None:
            state, success = 'completed', result.get('status') == 'succeeded'
        else:
            state, success = 'awaiting_result', False
        over = False
    elif idle:
        state, success, over = 'idle', False, False
    else:
        state, success, over = 'running', False, False
    envelope = {
        'schema': SCHEMA, 'known': True, 'present': True,
        'session_id': journal['session_id'],
        'worker_root': assignment['worker_root'], 'artifact_root': assignment['artifact_root'],
        'outcome': outcome, 'state': state, 'success': success,
        'result': result, 'result_error': error, 'interrupted_over_result': over,
        'agent': session.get('agent'), 'model': session.get('model'),
        'location': session.get('location'), 'time': time,
        'messages': len(messages) if isinstance(messages, list) else 0,
    }
    return {'envelope': envelope}


def plan_interrupt(request, caller):
    _check_caller(_caller(caller))
    req = _assignment_request(request)
    assignment, path, journal, _context, _ref = _journal_for(req)
    if journal is None:
        return {'action': 'unknown', 'envelope': _unknown_envelope()}
    if journal['state'] == 'interrupted':
        return {'action': 'none', 'session_id': journal['session_id'], 'journal': str(path),
                'envelope': {'schema': SCHEMA, 'known': True, 'present': True,
                             'already_interrupted': True, 'interrupted': True,
                             'session_id': journal['session_id'], 'state': 'interrupted',
                             'success': False}}
    if journal['state'] == 'intent':
        _fail('not_started', 'the launch journal at {} has no admitted session to '
                             'interrupt'.format(path))
    return {'action': 'interrupt', 'session_id': journal['session_id'], 'resume': False,
            'journal': str(path)}


def interpret_interrupt(request, interrupt, session):
    req = _assignment_request(request)
    assignment, path, journal, _context, ref = _journal_for(req)
    _object(interrupt, 'the interrupt reply', 'invalid_request')
    if not isinstance(interrupt.get('interrupted'), bool):
        _fail('invalid_request', 'the interrupt reply carries no boolean `interrupted`: '
                                 '{!r}'.format(interrupt))
    if journal is None:
        return {'envelope': _unknown_envelope()}
    present = session is not None
    if present:
        _verify_session(session, journal, req['assignment'], ref)
    if interrupt['interrupted']:
        journal = _write_journal(path, assignment, journal['session_id'], journal['agent'], ref,
                                 journal['prompt_sha256'], 'interrupted',
                                 created_at=journal['created_at'])
    outcome = session.get('outcome') if present else None
    return {'envelope': {
        'schema': SCHEMA, 'known': True, 'present': present,
        'session_id': journal['session_id'], 'interrupted': interrupt['interrupted'],
        'already_interrupted': False,
        'state': 'interrupted' if interrupt['interrupted'] else (outcome or 'unknown'),
        'outcome': outcome, 'success': False,
    }}


def capabilities_command(capabilities, caller):
    """The read-only capability tool: coordinator callers only, no effect at all."""
    _check_caller(_caller(caller))
    return {'report': capability_report(capabilities)}


COMMANDS = {
    'capabilities': lambda payload: capabilities_command(payload['capabilities'],
                                                         payload['caller']),
    'plan-start': lambda payload: plan_start(payload['request'], payload['capabilities'],
                                             payload['caller'], payload['model_facts']),
    'record-session': lambda payload: record_session(payload['request'], payload['session']),
    'record-prompt': lambda payload: record_prompt(payload['request'], payload.get('inbox')),
    'reconcile': lambda payload: reconcile(payload['request'], payload.get('session'),
                                           payload.get('messages')),
    'plan-status': lambda payload: plan_status(payload['request'], payload['caller']),
    'interpret-status': lambda payload: interpret_status(payload['request'],
                                                         payload.get('session'),
                                                         payload.get('messages')),
    'plan-interrupt': lambda payload: plan_interrupt(payload['request'], payload['caller']),
    'interpret-interrupt': lambda payload: interpret_interrupt(
        payload['request'], payload['interrupt'], payload.get('session')),
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(json.dumps({'ok': False, 'error': {
            'kind': 'invalid_argument',
            'message': 'usage: parallel_launch.py <{}>'.format('|'.join(sorted(COMMANDS)))}}))
        return 2
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        print(json.dumps({'ok': False, 'error': {
            'kind': 'invalid_argument', 'message': 'stdin is not JSON: {}'.format(exc)}}))
        return 2
    try:
        data = COMMANDS[argv[0]](payload)
    except LaunchError as exc:
        print(json.dumps({'ok': False, 'error': {'kind': exc.kind, 'message': exc.message}}))
        return 0
    except Exception as exc:  # never mask an internal failure as a policy refusal
        print(json.dumps({'ok': False, 'error': {
            'kind': 'internal_error', 'message': str(exc)}}))
        return 2
    print(json.dumps({'ok': True, 'data': data}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
