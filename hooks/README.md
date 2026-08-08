# hooks/

Quality gates: `hooks.json` registers five Python hooks via `${CLAUDE_PLUGIN_ROOT}` paths.
Requires `python3` on the host. Verify commands come from host config
(`.artel/config.json` — [config.md](../docs/config.md)), never hardcoded.

Two layers:

- **Run layer** — enforces the autonomous-run contract
  ([autonomous-run.md](../docs/autonomous-run.md)):
  - `stop_gate.py` (`Stop`) — blocks a session from ending while a run is armed
    (`run_active: true`, `completed: false`, `pause_reason: null` in
    `.artel/run/<TICKET_ID>/run-state.json`). Fail-safes: disarms past
    `WALL_CLOCK_HOURS = 3` (stale run) and after `MAX_CONSECUTIVE_BLOCKS = 5`
    (counter: `.artel/run/<TICKET_ID>/.stop-gate-blocks`); fails open on infra errors.
  - `sensitive_guard.py` (`PreToolUse` on `Edit|Write|MultiEdit`) — during an armed run,
    denies edits to paths matched by the sensitive-paths policy when the category's floor
    outranks the effective mode, or before `TASKLIST_READY` is in `gates_confirmed`.
    Policy: [`sensitive-paths.json`](sensitive-paths.json) (shipped defaults: `secrets` and
    `gate-config` at `full-gates`, `ci-cd` at `plan-gate`), replaced **wholesale** by a host
    `.artel/sensitive-paths.json` when present. Inert outside armed runs.
- **Verify layer** — same-session quality feedback, driven by `verify.fast` /
  `verify.surface` (config.md) through the `scripts/verify.py` envelope
  (exit 0 clean / 1 findings / 2 environment error):
  - `session_baseline.py` (`SessionStart`) — captures the findings baseline so pre-existing
    findings never block.
  - `fast_verify_post_edit.py` (`PostToolUse` on `Edit|Write|MultiEdit`) — runs `verify.fast`
    scoped to the edited file for same-turn feedback; never blocks.
  - `verify_stop_gate.py` (`Stop`) — blocks completion only while findings **new vs the
    session baseline** stay red; after `MAX_CONSECUTIVE_BLOCKS = 2` it latches open with a
    loud warning. Escape hatch: delete `.artel/run/.hooks/baseline-<session_id>.json` to
    re-baseline on the next stop.

Hook state lives in the host repo at `.artel/run/.hooks/` (session baselines, verify-stop
counters) — never inside the plugin directory. The verify-layer hooks return immediately when
`.artel/config.json` does not exist, so an installed-but-unconfigured plugin leaves zero
footprint; `hook_common.py` is the shared helper library, not a registered hook.
