---
name: drive-app
description: "Drive the running app's UI via the host's configured runtime.drive command (navigate, tap, enter text, screenshot, verify — however the host implements it) and record evidence to the ticket dir. Requires the automation scaffold applied via /artel:add-automation. Interactive counterpart to /artel:run-app; the RUNTIME_OK gate never uses this skill. Absent/empty runtime.drive reports not configured."
argument-hint: ""
---

Drive-and-verify orchestrator for agent UI interaction. Config key: `runtime.drive`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "runtime" section). `/artel:run-app --gate`
(observation-only) is unaffected — the `RUNTIME_OK` pipeline gate never uses this skill.

## 1. Preflight

1. **Configuration gate.** Read `runtime.drive` (config.md). Absent or empty → report "not
   configured" and stop.
2. **Scaffold prerequisite.** This skill does not verify the scaffold's on-disk state itself —
   which files denote "applied" is host-specific and unknowable generically (config.md defines
   only the `runtime.scaffold.add`/`remove` commands, never their artifacts). Running
   `runtime.drive` is the check: a host script should fail cleanly, with a message pointing at the
   missing scaffold, when it isn't applied. If that happens, relay the failure and tell the caller
   to run `/artel:add-automation` first — this skill never applies the scaffold itself.

## 2. Drive

Run `runtime.drive` via Bash with a bounded timeout generous enough for the host's full drive
sequence (navigation, interaction, verification). Capture `{command, exit_code, output}`
(stdout+stderr, truncated if huge) — the same evidence shape `/artel:run-app` and
`${CLAUDE_PLUGIN_ROOT}/skills/inner-loop/SKILL.md` use. Exit `0` ⇒ verified; non-zero ⇒ not
verified, quoting the captured output.

Whatever the command drives toward (a specific screen, a specific interaction) is the caller's
context to supply — pass the goal in the invocation prompt when one is known; a host script with
no way to take a goal simply runs its own fixed scenario.

## 3. Evidence

Resolve `TICKET_ID` / `PHASE_NUM` from `<specs.dir>/.active_ticket` per
`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md`. When an identifier is available, write to
`<specs.dir>/<TICKET_ID>/[phase-<PHASE_NUM>/]runtime/drive-observation.md`:

    # Drive observation — <TICKET_ID>
    - Date: <ISO date>
    - Command: `<runtime.drive value>`
    - Goal: <what was verified, if known>
    - Exit code: <n> · Output: <summary, or "none">
    - Verdict: verified | not verified (<reason>)

No active ticket → report inline instead.

## 4. Aftermath

Report the verdict to the caller. Whether the app stays running and how to stop it is whatever the
host's `runtime.drive` command leaves behind — this skill has no generic status/stop primitive to
offer (same limitation as `/artel:run-app`).

## Deep dives

Observation-only gate checks: `/artel:run-app`. Applying/removing the automation scaffold:
`/artel:add-automation` / `/artel:remove-automation`.
