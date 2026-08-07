#!/usr/bin/env python3
"""Deterministic verify gate: run the configured verify commands, print one JSON envelope line.

Wraps `verify.fast` (--fast) or `verify.commands` (default) from .artel/config.json in the
envelope contract the hooks consume. Run from the host repo root.

Exit codes: 0 clean or skipped (no commands configured), 1 findings, 2 environment error.
An exit-2 always means a toolchain/invocation problem, never "the code has a bug".
Contract: docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md
"""
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

CONFIG_PATH = Path('.artel/config.json')
DEFAULT_TIMEOUT = 240
TAIL_CHARS = 2000
MAX_KEYS_PER_STAGE = 200
ENV_ERROR_EXIT_CODES = (126, 127)  # not executable / command not found
ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def substitute_files(command, files):
    """Replace {files} with the space-joined shell-quoted paths; no placeholder -> unchanged."""
    if '{files}' not in command:
        return command
    return command.replace('{files}', ' '.join(shlex.quote(f) for f in files))


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
    """Return (fast, files_or_None, timeout). Raises ValueError on a bad invocation.
    A present-but-blank --files is rejected rather than parsed to []: blank would be
    indistinguishable from the flag being absent and silently widen a scoped call."""
    fast = False
    files = None
    timeout = DEFAULT_TIMEOUT
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--fast':
            fast = True
        elif arg == '--files':
            i += 1
            raw = argv[i] if i < len(argv) else ''
            files = [p.strip() for p in raw.split(',') if p.strip()]
            if not files:
                raise ValueError('blank --files: a scoped call must name its paths')
        elif arg == '--timeout':
            i += 1
            try:
                timeout = int(argv[i])
            except (IndexError, ValueError):
                raise ValueError('--timeout expects an integer number of seconds')
        else:
            raise ValueError('unknown flag: {}'.format(arg))
        i += 1
    return fast, files, timeout


def envelope(ok, elapsed_ms, data=None, error=None):
    out = {'ok': ok, 'verb': 'verify', 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def run_stage(command, files, timeout, index):
    """Run one configured command. Returns (stage_dict, error_kind_or_None)."""
    cmd = substitute_files(command, files)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'command': cmd, 'ok': False}, 'timeout'
    except OSError:
        return {'command': cmd, 'ok': False}, 'spawn_failed'
    output = (proc.stdout or '') + (proc.stderr or '')
    verdict = classify_exit(proc.returncode)
    if verdict == 'env_error':
        return {'command': cmd, 'ok': False, 'exit_code': proc.returncode,
                'tail': output[-TAIL_CHARS:]}, 'command_not_found'
    return {
        'command': cmd,
        'exit_code': proc.returncode,
        'ok': verdict == 'ok',
        'keys': normalize_keys(output, index) if verdict != 'ok' else [],
        'tail': output[-TAIL_CHARS:],
    }, None


def main(argv):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    try:
        fast, files, timeout = parse_args(argv)
    except ValueError as exc:
        print(envelope(False, elapsed(),
                       error={'kind': 'invalid_argument', 'message': str(exc)}))
        return 2

    config = load_config()
    verify_cfg = config.get('verify') or {}
    if fast:
        fast_cmd = verify_cfg.get('fast') or ''
        commands = [fast_cmd] if isinstance(fast_cmd, str) and fast_cmd.strip() else []
    else:
        commands = [c for c in (verify_cfg.get('commands') or [])
                    if isinstance(c, str) and c.strip()]

    if not commands:
        # No gate configured: exit 0, but the caller journals "skipped", never "green".
        print(envelope(True, elapsed(), data={'skipped': True, 'stages': []}))
        return 0

    stages = []
    for index, command in enumerate(commands):
        stage, error_kind = run_stage(command, files or [], timeout, index)
        stages.append(stage)
        if error_kind is not None:
            print(envelope(False, elapsed(), error={
                'kind': error_kind,
                'message': 'stage {} ({}) failed: {}'.format(
                    index, stage['command'], error_kind),
                'details': {'stages': stages},
            }))
            return 2
        if not stage['ok']:
            break  # the gate stops at the first red stage (config.md rule)

    if all(s['ok'] for s in stages):
        print(envelope(True, elapsed(), data={'skipped': False, 'stages': stages}))
        return 0
    print(envelope(False, elapsed(), data={'skipped': False, 'stages': stages}))
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # an uncaught crash is an environment error, reported loudly
        print(json.dumps({'ok': False, 'verb': 'verify', 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
