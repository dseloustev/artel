#!/usr/bin/env python3
"""Stop gate for autonomous orchestrator runs.

Blocks the session from ending while an autonomous run is active and incomplete.
Contract: ${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md (sections 2 and 8).

Allow when any of:
  - no active ticket / no run-state.json / run_active false  (interactive session)
  - pause_reason set                                          (waiting for a human)
  - completed true                                            (run finished)
  - started_at older than WALL_CLOCK_HOURS                    (stale run)
  - MAX_CONSECUTIVE_BLOCKS reached                            (fail-safe, loud warning)
  - any infra error                                           (fail open)
Otherwise: block with a reason instructing the model to continue or record an abort.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MAX_CONSECUTIVE_BLOCKS = 5
WALL_CLOCK_HOURS = 3


def allow(counter_file):
    if counter_file is not None:
        try:
            counter_file.unlink(missing_ok=True)
        except OSError:
            pass
    sys.exit(0)


def main():
    h.read_hook_input()  # payload unused; consume defensively
    config = h.load_config()
    ticket = h.resolve_active_ticket(config)
    if ticket is None:
        allow(None)
    ticket_dir = h.ticket_run_dir(ticket)
    state_file = ticket_dir / 'run-state.json'
    counter_file = ticket_dir / '.stop-gate-blocks'

    if not state_file.is_file():
        allow(counter_file)
    state = json.loads(state_file.read_text(encoding='utf-8'))
    if not state.get('run_active', False):
        allow(counter_file)
    if state.get('completed', False) or state.get('pause_reason'):
        allow(counter_file)

    started_raw = str(state.get('started_at', ''))
    try:
        started = datetime.fromisoformat(started_raw.replace('Z', '+00:00'))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - started > timedelta(hours=WALL_CLOCK_HOURS):
            allow(counter_file)  # stale run: budget expired
    except ValueError:
        allow(counter_file)  # unparseable timestamp: fail open

    blocks = 0
    if counter_file.is_file():
        try:
            blocks = int(counter_file.read_text(encoding='utf-8').strip() or '0')
        except ValueError:
            blocks = 0
    if blocks >= MAX_CONSECUTIVE_BLOCKS:
        print(
            'stop_gate: {} consecutive blocks for {} — allowing stop. '
            'The run is NOT complete; resume it or record an abort.'.format(
                MAX_CONSECUTIVE_BLOCKS, ticket),
            file=sys.stderr,
        )
        allow(counter_file)

    counter_file.write_text(str(blocks + 1), encoding='utf-8')
    reason = (
        'Autonomous run for {t} is active and gates are not green — continue the '
        'pipeline, or record an explicit abort in .artel/run/{t}/run-state.json '
        '(set pause_reason="user-abort" or run_active=false). '
        'Block {n}/{cap}.'.format(t=ticket, n=blocks + 1, cap=MAX_CONSECUTIVE_BLOCKS)
    )
    print(json.dumps({'decision': 'block', 'reason': reason}))
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail open on any infra error
        print('stop_gate: hook error, failing open: {}'.format(exc), file=sys.stderr)
        sys.exit(0)
