#!/usr/bin/env python3
"""Deterministic verify gates: run the configured commands, print one JSON envelope line.

Forms — legacy `[--fast] [--files a,b]` (what the hooks call), `task --files a,b` (verify.fast
on the paths, then verify.test on the test files among them) and `checkpoint
[--record-baseline] [--ticket T]` (verify.commands in order, compared against the baseline
recorded at arm time). Contract: docs/gates.md. Config keys: docs/config.md (`verify.*`).
Run from the host repo root.

Exit codes: 0 clean or skipped (no commands configured), 1 findings, 2 environment error.
An exit-2 always means a toolchain/invocation problem, never "the code has a bug".
"""
import fnmatch
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common as h  # noqa: E402

BASELINE_FILENAME = 'verify-baseline.json'
DEFAULT_TIMEOUT = 240
TAIL_CHARS = 2000
MAX_KEYS_PER_STAGE = 200
ENV_ERROR_EXIT_CODES = (126, 127)  # not executable / command not found
ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
GATES = ('task', 'checkpoint')
DEFAULT_TEST_SURFACE = ['test/**', 'tests/**', '**/*_test.*', '**/test_*.*',
                        '**/*.test.*', '**/*.spec.*']


def load_config():
    """.artel/config.json as a dict, {} when missing or unreadable (hook_common's reader)."""
    return h.load_config()


def resolve_ticket(inv, config):
    """Canonical ticket id for the checkpoint gate: --ticket, else <specs.dir>/.active_ticket.
    Phase suffixes never survive (hook_common.canonical_ticket), because the baseline is
    ticket-top-level like everything under .artel/run/<TICKET_ID>/."""
    if inv.get('ticket'):
        ticket = h.canonical_ticket(inv['ticket'], config)
        if ticket is None:
            raise ValueError('--ticket {!r} does not match ticket.pattern'.format(inv['ticket']))
        return ticket
    ticket = h.resolve_active_ticket(config)
    if ticket is None:
        raise ValueError('the checkpoint gate needs --ticket or <specs.dir>/.active_ticket')
    return ticket


def baseline_path_for(ticket):
    return h.ticket_run_dir(ticket) / BASELINE_FILENAME


def substitute_files(command, files):
    """Replace {files} with the space-joined shell-quoted paths; no placeholder -> unchanged."""
    if '{files}' not in command:
        return command
    return command.replace('{files}', ' '.join(shlex.quote(f) for f in files))


def matches_surface(path, patterns):
    """Same semantics as verify.surface (hooks/hook_common.py is_verifiable): fnmatch globs
    where '*' crosses '/', a '!' prefix excludes, a list with only excludes implies '*' as
    the positive set, and an empty or non-list value matches everything."""
    if not patterns or not isinstance(patterns, list):
        return True
    positives = [p for p in patterns if isinstance(p, str) and p and not p.startswith('!')]
    negatives = [p[1:] for p in patterns if isinstance(p, str) and p.startswith('!')]
    if any(fnmatch.fnmatch(path, n) for n in negatives):
        return False
    if not positives:
        return True
    return any(fnmatch.fnmatch(path, p) for p in positives)


def select_test_paths(files, config):
    """The test files among `files`, per verify.testSurface (DEFAULT_TEST_SURFACE when the key
    is absent, empty or not a list). Document order is kept."""
    surface = (config.get('verify') or {}).get('testSurface')
    if not isinstance(surface, list) or not surface:
        surface = DEFAULT_TEST_SURFACE
    return [f for f in files if matches_surface(f, surface)]


def normalize_keys(output, stage_index):
    """Finding keys: non-empty output lines of a red stage, ANSI- and digit-stripped,
    whitespace-collapsed, deduped, prefixed s<index>:, capped. Digit-stripping keeps keys
    stable against shifting line numbers and timing noise ("Done in 3.2s")."""
    keys = []
    seen = set()
    for line in output.splitlines():
        line = ANSI_RE.sub('', line)
        line = re.sub(r'\d+', '', line)
        line = re.sub(r'\s+', ' ', line).strip()
        if not line:
            continue
        key = 's{}:{}'.format(stage_index, line)
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
        if len(keys) >= MAX_KEYS_PER_STAGE:
            break
    return keys


def classify_exit(returncode):
    """0 -> ok; 126/127 -> env_error (fix the toolchain); any other non-zero -> findings."""
    if returncode == 0:
        return 'ok'
    if returncode in ENV_ERROR_EXIT_CODES:
        return 'env_error'
    return 'findings'


def parse_args(argv):
    """Return the invocation as a dict. Raises ValueError on a bad invocation.

    Legacy form:  [--fast] [--files a,b] [--timeout N]            -> gate None
    Named gates:  task --files a,b [--timeout N]                   -> gate 'task'
                  checkpoint [--record-baseline] [--ticket T] [--timeout N]
    A present-but-blank --files is rejected rather than parsed to []: blank would be
    indistinguishable from the flag being absent and silently widen a scoped call."""
    inv = {'gate': None, 'fast': False, 'files': None, 'timeout': DEFAULT_TIMEOUT,
           'record_baseline': False, 'ticket': None}
    args = list(argv)
    if args and not args[0].startswith('--'):
        gate = args.pop(0)
        if gate not in GATES:
            raise ValueError('unknown gate: {} (expected one of: {})'.format(
                gate, ', '.join(GATES)))
        inv['gate'] = gate
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--fast':
            inv['fast'] = True
        elif arg == '--files':
            i += 1
            raw = args[i] if i < len(args) else ''
            files = [p.strip() for p in raw.split(',') if p.strip()]
            if not files:
                raise ValueError('blank --files: a scoped call must name its paths')
            inv['files'] = files
        elif arg == '--timeout':
            i += 1
            try:
                inv['timeout'] = int(args[i])
            except (IndexError, ValueError):
                raise ValueError('--timeout expects an integer number of seconds')
        elif arg == '--record-baseline':
            inv['record_baseline'] = True
        elif arg == '--ticket':
            i += 1
            ticket = args[i].strip() if i < len(args) else ''
            if not ticket:
                raise ValueError('--ticket expects a ticket id')
            inv['ticket'] = ticket
        else:
            raise ValueError('unknown flag: {}'.format(arg))
        i += 1
    if inv['gate'] == 'task' and inv['files'] is None:
        raise ValueError('the task gate needs --files: a task gate always has a scope')
    if inv['gate'] != 'checkpoint' and inv['record_baseline']:
        raise ValueError('--record-baseline belongs to the checkpoint gate')
    if inv['gate'] != 'checkpoint' and inv['ticket'] is not None:
        raise ValueError('--ticket belongs to the checkpoint gate')
    if inv['gate'] is not None and inv['fast']:
        raise ValueError('--fast belongs to the legacy form; the task gate runs verify.fast itself')
    return inv


def envelope(ok, elapsed_ms, data=None, error=None):
    out = {'ok': ok, 'verb': 'verify', 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def run_stage(command, files, timeout, index, name=None):
    """Run one configured command. Returns (stage_dict, error_kind_or_None).
    `name` is the stage's label in the envelope ('s<index>' for the legacy form and the
    checkpoint gate; 'fast' / 'test' for the task gate)."""
    cmd = substitute_files(command, files)
    label = name or 's{}'.format(index)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'name': label, 'command': cmd, 'ok': False}, 'timeout'
    except OSError:
        return {'name': label, 'command': cmd, 'ok': False}, 'spawn_failed'
    output = (proc.stdout or '') + (proc.stderr or '')
    verdict = classify_exit(proc.returncode)
    if verdict == 'env_error':
        return {'name': label, 'command': cmd, 'ok': False, 'exit_code': proc.returncode,
                'tail': output[-TAIL_CHARS:]}, 'command_not_found'
    return {
        'name': label,
        'command': cmd,
        'exit_code': proc.returncode,
        'ok': verdict == 'ok',
        'keys': normalize_keys(output, index) if verdict != 'ok' else [],
        'tail': output[-TAIL_CHARS:],
    }, None


def stage_error(stage, error_kind, stages):
    """The error payload for a stage that could not run (exit 2)."""
    return {'error': {
        'kind': error_kind,
        'message': 'stage {} ({}) failed: {}'.format(stage['name'], stage['command'], error_kind),
        'details': {'stages': stages},
    }}


def run_legacy(config, inv):
    """The pre-0.18 form: verify.fast (--fast) or verify.commands, stop at the first red.
    Returns (exit_code, payload) with payload {'data': ...} or {'error': ...}."""
    verify_cfg = config.get('verify') or {}
    if inv['fast']:
        fast_cmd = verify_cfg.get('fast') or ''
        commands = [fast_cmd] if isinstance(fast_cmd, str) and fast_cmd.strip() else []
    else:
        commands = [c for c in (verify_cfg.get('commands') or [])
                    if isinstance(c, str) and c.strip()]
    if not commands:
        # No gate configured: exit 0, but the caller journals "skipped", never "green".
        return 0, {'data': {'skipped': True, 'stages': []}}
    stages = []
    for index, command in enumerate(commands):
        stage, error_kind = run_stage(command, inv['files'] or [], inv['timeout'], index)
        stages.append(stage)
        if error_kind is not None:
            return 2, stage_error(stage, error_kind, stages)
        if not stage['ok']:
            break  # the gate stops at the first red stage (config.md rule)
    code = 0 if all(s['ok'] for s in stages) else 1
    return code, {'data': {'skipped': False, 'stages': stages}}


def run_task_gate(config, inv):
    """The task gate (docs/gates.md §1): verify.fast on the changed paths, then verify.test on
    the test files among them. A missing command or an empty test scope records that half
    `skipped`; a red half stops the gate; exit 2 is an environment error."""
    verify_cfg = config.get('verify') or {}
    files = inv['files'] or []

    def command(key):
        value = verify_cfg.get(key) or ''
        return value.strip() if isinstance(value, str) else ''

    plan = [
        ('fast', command('fast'), files, 'no fast command'),
        ('test', command('test'), select_test_paths(files, config), 'no test command'),
    ]
    stages = []
    for index, (name, cmd, scope, why) in enumerate(plan):
        if not cmd:
            stages.append({'name': name, 'skipped': True, 'reason': why})
            continue
        if name == 'test' and not scope:
            stages.append({'name': name, 'skipped': True, 'reason': 'no test path in scope'})
            continue
        stage, error_kind = run_stage(cmd, scope, inv['timeout'], index, name)
        if name == 'test':
            stage['scoped'] = '{files}' in cmd
            stage['files'] = list(scope)
        stages.append(stage)
        if error_kind is not None:
            return 2, stage_error(stage, error_kind, stages)
        if not stage['ok']:
            return 1, {'data': {'skipped': False, 'stages': stages}}
    all_skipped = all(s.get('skipped') for s in stages)
    return 0, {'data': {'skipped': all_skipped, 'stages': stages}}


def record_baseline(path, stages):
    """Write the baseline: one entry per stage with the keys it produced (empty when green)."""
    payload = {
        'recorded_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'stages': [{'name': s['name'], 'command': s['command'], 'keys': list(s.get('keys') or [])}
                   for s in stages],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def load_baseline(path):
    """{stage_index: set(keys)} from a baseline file; None when absent. An unreadable or
    malformed file is also None — treated as absent, with one warning line on stderr — so
    a crash mid-record can never wedge a checkpoint (the gate then reads 'any red is red')."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        stages = payload['stages']
        return {index: set(entry.get('keys') or []) for index, entry in enumerate(stages)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print('verify: warning: baseline {} unreadable ({}); treating it as absent'.format(
            path, exc.__class__.__name__), file=sys.stderr)
        return None


def run_checkpoint_gate(config, inv):
    """The checkpoint gate (docs/gates.md §1): verify.commands in order, compared against the
    baseline recorded at arm time. A stage red only on baseline keys is `baseline_red` and does
    not stop the chain; a stage with `new_keys` does. --record-baseline runs every stage and
    writes the keys instead of comparing."""
    try:
        ticket = resolve_ticket(inv, config)
    except ValueError as exc:
        return 2, {'error': {'kind': 'invalid_argument', 'message': str(exc)}}
    path = baseline_path_for(ticket)
    verify_cfg = config.get('verify') or {}
    use_baseline = verify_cfg.get('baseline', True)
    if not isinstance(use_baseline, bool):
        return 2, {'error': {'kind': 'invalid_argument',
                             'message': 'verify.baseline must be a JSON boolean, got {!r}'.format(
                                 use_baseline)}}
    commands = [c for c in (verify_cfg.get('commands') or []) if isinstance(c, str) and c.strip()]
    if not commands:
        return 0, {'data': {'skipped': True, 'stages': [], 'baseline': 'skipped'}}
    record = inv['record_baseline']
    baseline = None
    if record:
        status = 'recorded'
    elif not use_baseline:
        status = 'disabled'
    else:
        baseline = load_baseline(path)
        status = 'loaded' if baseline is not None else 'absent'
    stages = []
    for index, command in enumerate(commands):
        stage, error_kind = run_stage(command, [], inv['timeout'], index)
        stages.append(stage)
        if error_kind is not None:
            return 2, stage_error(stage, error_kind, stages)
        if baseline is not None:
            known = baseline.get(index) or set()
            stage['new_keys'] = [k for k in stage['keys'] if k not in known]
            stage['baseline_red'] = (not stage['ok']) and not stage['new_keys']
        if record:
            continue  # the baseline wants every stage, red or not
        stops = (not stage['ok']) and (baseline is None or bool(stage['new_keys']))
        if stops:
            break
    data = {'skipped': False, 'stages': stages, 'baseline': status, 'baseline_path': str(path)}
    if record:
        record_baseline(path, stages)
        return 0, {'data': data}
    if baseline is not None:
        red = any(s.get('new_keys') for s in stages)
    else:
        red = any(not s['ok'] for s in stages)
    return (1 if red else 0), {'data': data}


def main(argv):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    try:
        inv = parse_args(argv)
    except ValueError as exc:
        print(envelope(False, elapsed(),
                       error={'kind': 'invalid_argument', 'message': str(exc)}))
        return 2

    config = load_config()
    if inv['gate'] == 'task':
        code, payload = run_task_gate(config, inv)
    elif inv['gate'] == 'checkpoint':
        code, payload = run_checkpoint_gate(config, inv)
    else:
        code, payload = run_legacy(config, inv)
    print(envelope(code == 0, elapsed(), data=payload.get('data'), error=payload.get('error')))
    return code


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # an uncaught crash is an environment error, reported loudly
        print(json.dumps({'ok': False, 'verb': 'verify', 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
