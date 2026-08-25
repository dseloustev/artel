# hooks/

Quality gates: `hooks.json` registers seven Python hooks via `${CLAUDE_PLUGIN_ROOT}` paths.
Requires `python3` on the host. Verify commands come from host config
(`.artel/config.json` — [config.md](../docs/config.md)), never hardcoded.

Four layers:

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
- **Knowledge layer** — optional, driven by `knowledge.adapter` / `knowledge.baseUrl`
  (config.md):
  - `knowledge_mirror.py` (`PostToolUse` on `Edit|Write|MultiEdit`) — posts each
    deliberation artifact written under `<specs.dir>/<TICKET>/` to a kartoteka artifact
    store as it is written. Additive and best-effort: files on disk stay primary, nothing
    blocks, nothing retries, every attempt is logged to
    `.artel/run/.hooks/knowledge-mirror.log`. Inert unless `knowledge.adapter` is
    `"kartoteka"`.
- **Session layer** — turn-one routing, no gate:
  - `using_artel.py` (`SessionStart`, matcher `startup|clear|compact`) — injects the
    `using-artel` router skill (frontmatter stripped) plus three host-status lines
    (`config: present`, `knowledge.adapter` with `baseUrl`, the raw `.active_ticket`
    pointer) as `additionalContext`, so the routing rule is in context on turn one and comes
    back after `/clear` and compaction. Prints nothing without `.artel/config.json`; any
    error goes to stderr and the hook still exits 0 — a session never fails to start
    because of it.

Hook state lives in the host repo at `.artel/run/.hooks/` (session baselines, verify-stop
counters) — never inside the plugin directory. The verify-layer and session-layer hooks return
immediately when `.artel/config.json` does not exist, so an installed-but-unconfigured plugin
leaves zero footprint; `hook_common.py` is the shared helper library, not a registered hook.
