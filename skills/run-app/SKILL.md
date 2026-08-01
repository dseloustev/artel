---
name: run-app
description: "Build and launch the app via the host's configured runtime.run command, capture the launch outcome as evidence, and record the RUNTIME_OK gate. --gate mode for pipelines: fresh run, GREEN/RED verdict. Interactive mode: launch for inspection, leave it running. Absent/empty runtime.run reports skipped (not configured) and never blocks a run."
argument-hint: "[--gate]"
model: sonnet
---

Launch-and-observe orchestrator for the `RUNTIME_OK` gate. Config key: `runtime.run`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "runtime" section). Counterpart for UI driving:
`/artel:drive-app` (requires `/artel:add-automation` applied first).

## Arguments

`$0` may contain the `--gate` flag.

- **Gate mode** (`--gate`): pipeline use — fresh run, capture the outcome, report a GREEN/RED
  verdict. This is the only mode `${CLAUDE_PLUGIN_ROOT}/agents/validator.md`'s `RUNTIME_OK` gate
  treats as authoritative.
- **Interactive mode** (default): launch for the caller to inspect; the app is left running (or,
  when `runtime.run` itself stays in the foreground, running for the rest of the session).

## 1. Configuration gate

Read `runtime.run` (config.md). Absent or empty → report `RUNTIME_OK: skipped (not configured)`
and stop — do not attempt a launch. Per config.md, missing runtime configuration never blocks the
caller; it continues without this gate.

## 2. Launch

Run `runtime.run` via Bash with a bounded timeout generous enough for the host's own build+launch
(300s is a reasonable default absent other guidance). Capture the same evidence shape
`${CLAUDE_PLUGIN_ROOT}/skills/inner-loop/SKILL.md` uses for a `verify.commands` entry:
`{command, exit_code, output}` (stdout+stderr, truncated if huge).

- Exit `0` — or, for a command designed to keep running (a dev server, an app process), a clean
  start with no early non-zero exit or crash — ⇒ candidate GREEN.
- Non-zero exit, or a timeout with no clean completion/start, ⇒ candidate RED.
- On a first failure, retry once. A second failure is the verdict: RED, quoting the captured
  output — do not retry further.

There is no generic "already running" or "stop" primitive: `runtime.run` is the host's one
adapter command (config.md), so this skill never assumes a companion status/stop command exists.

## 3. Evidence

Resolve `TICKET_ID` / `PHASE_NUM` from `<specs.dir>/.active_ticket` per
`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md`. When an identifier is available, write to
`<specs.dir>/<TICKET_ID>/[phase-<PHASE_NUM>/]runtime/observation.md`:

    # Runtime observation — <TICKET_ID>
    - Date: <ISO date> · Mode: gate | interactive
    - Command: `<runtime.run value>`
    - Exit code: <n> · Output: <summary, or "none">
    - Verdict: GREEN | RED (<reason>)

    RUNTIME_OK: green | red (<reason>) | skipped (not configured)

Evidence is written in both modes when a ticket is active; only gate mode's fresh run is what the
`RUNTIME_OK` gate treats as authoritative (`${CLAUDE_PLUGIN_ROOT}/agents/validator.md`: "Green
requires gate-mode evidence, not merely an interactive run, or a recorded skip"). No active ticket
→ report the same content inline instead.

## 4. Aftermath

- **Gate:** report the verdict; RED must quote the captured output.
- **Interactive:** tell the caller the app was launched, quoting whatever `runtime.run` itself
  reported (a URL, a port, a PID — whatever the host's command prints). Stopping it is whatever
  the host's own tooling requires; this skill has no generic stop command to offer. A RED
  observation is reported the same way as gate mode instead — the app stays up for debugging.

## Deep dives

For UI driving (taps, text entry, screenshots — requires `/artel:add-automation` applied on the
branch): `/artel:drive-app`.
