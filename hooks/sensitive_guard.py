"""tool.execute.before (edit|write|patch): during an armed autonomous run (run-state.json,
autonomous-run.md sections 2 and 10), deny writes to sensitive paths whose floor the effective
mode does not satisfy. Inactive in normal sessions.

The run's own bookkeeping under .artel/run/<ticket>/ is exempt: a run must be able to record its
completion or it can never end (hook_common.RUN_OWNED_FILES, hooks/README.md)."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MODE_RANK = h.MODE_RANK  # the one rank table: scripts/sensitive_policy_check.py uses the same
WALL_CLOCK_HOURS = 3  # single staleness constant — matches autonomous-run.md §2 and stop_gate.py
STALE_AFTER_SECONDS = WALL_CLOCK_HOURS * 3600
FLOOR_PAUSE_GATE = 'TASKLIST_READY'


def _run_state(ticket):  # -> dict | None (comment, not annotation: 3.9 compatibility)
    if ticket is None:
        return None
    state_path = h.ticket_run_dir(ticket) / 'run-state.json'
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if not state.get('run_active') or state.get('completed'):
        return None
    started = state.get('started_at') or ''
    try:
        started_ts = datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()
        if time.time() - started_ts > STALE_AFTER_SECONDS:
            return None  # stale run — never arm guards forever
    except Exception:
        return None
    return state


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }}))


def main():
    data = h.read_hook_input()  # first: it moves into the session's worktree
    config = h.load_config()
    ticket = h.resolve_active_ticket(config)
    state = _run_state(ticket)
    if state is None:
        return 0  # no active autopilot run — guard disarmed
    rel = h.relpath_from_tool_input(data)
    if not rel:
        return 0
    if h.is_run_owned(rel, ticket):
        # The run records its own completion; a host policy may not floor that record shut at
        # any mode (docs/autonomous-run.md §10, hooks/README.md). The set is explicit, so the
        # rest of the run directory stays guarded.
        return 0
    effective = state.get('effective_mode') or 'yolo'
    confirmed = state.get('gates_confirmed') or []
    for name, glob, floor in h.sensitive_matches(rel, h.load_sensitive_categories()):
        if MODE_RANK.get(effective, 0) < MODE_RANK.get(floor, 2):
            deny("sensitive path ({}): floor '{}' exceeds effective mode '{}' — the "
                 'risk classifier must re-run and the mode be escalated before this '
                 'file can change'.format(name, floor, effective))
            return 0
        if FLOOR_PAUSE_GATE not in confirmed:
            deny("sensitive path ({}): floor '{}' requires the {} pause to be "
                 'confirmed in run-state.json before writes'.format(
                     name, floor, FLOOR_PAUSE_GATE))
            return 0
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('sensitive guard error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
