---
name: figma-analysis
description: "Analyze the ticket's Figma design workflow: flow graph, screen-to-code mapping, desktop/mobile differences, discrepancy handshake"
argument-hint: "[ticket-id] [figma-url]"
model: sonnet
---

Design-analysis orchestrator for the ticket's Figma workflow. Config key: `design.figma`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "design" section). Full stage contract — pipeline
placement, the runtime-optional degrade when no Figma MCP is connected, headless behavior — lives
in `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §13; this skill implements it.

## Configuration Gate

Read `design.figma` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`). Absent or `false` → report `Design
analysis disabled (design.figma: false) — skipped` and stop before resolving a ticket. `true`
enables the rest of this skill, but the stage stays runtime-optional — Phase 2 below degrades the
same way when no Figma MCP is connected.

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

Design analysis is **ticket-level only** (like `generate-idea`): a `PHASE_NUM` suffix is parsed but
ignored for pathing and noted in the final report. This skill never writes
`<specs.dir>/.active_ticket`.

If `$0` itself is a `figma.com` URL, treat it as `$1` and resolve the ticket from
`<specs.dir>/.active_ticket`.

## URL Resolution

1. An explicit `$1` URL wins — analysis proceeds even when `idea.md` is missing (the
   `figma-analyst` agent creates the ticket directory if needed).
2. Else Grep `<specs.dir>/<TICKET_ID>/idea.md` for `figma.com/design` and collect every design
   URL.
3. Other `figma.com` links (`/board/`, `/slides/`, FigJam) are collected separately and passed to
   the agent as "present, not analyzed" — never analysis targets.
4. No design URL anywhere → report `No design link — skipped` and terminate. (Pipeline invocations
   treat this as the design-analysis gate auto-skipping; manual invocations see the same message.)

## Pre-flight

If `<specs.dir>/<TICKET_ID>/design-analysis.md` exists:

- **Pipeline invocation** (from an orchestrator): skip — report `Design analysis exists — skipped`
  (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9).
- **Manual invocation:** ask via `AskUserQuestion` — `design-analysis.md already exists. Overwrite
  from Figma?` Options: `Overwrite` / `Abort`. On Abort, report the file was left untouched and
  terminate.

If the existing artifact's `Status:` is `DESIGN_BLOCKED`, the ticket is still parked: pipeline
invocation → report `DESIGN_BLOCKED` (unchanged) and stop; manual invocation → say so in the
Overwrite prompt (re-analyze only after the design is fixed).

## Execute

### Phase 1: Analysis

Use the Agent tool with:
- `subagent_type`: `"figma-analyst"`
- `description`: `"Design analysis for <TICKET_ID>"`
- `prompt`: the parsed ticket values plus:

```
You are analyzing the Figma design workflow for <TICKET_ID>.

## Context

- **Ticket ID:** <TICKET_ID>
- **Design URLs:** [list]
- **Other Figma links (record as "present, not analyzed"):** [list or "none"]
- **Overwrite:** [true when the user chose Overwrite, else false]

## Instructions

Follow your agent definition's Procedure end-to-end: context & preflight → metadata extraction →
flow graph (screenshot cross-check) → form-factor pairing → code mapping → findings classification →
write <specs.dir>/<TICKET_ID>/design-analysis.md + design/ evidence.
Do NOT resolve Major findings yourself — return them per your Return format.
```

**Save the agent ID** — Phase 3 resumes it.

**Stale-registry fallback:** if the agent returns `ENV_ERROR` naming ToolSearch / Figma tools as
unavailable, the session's agent registry may predate the current `figma-analyst` definition
(agent frontmatter is cached per session). Re-dispatch the same prompt once via `subagent_type:
"general-purpose"`, prefixed with: "Read `${CLAUDE_PLUGIN_ROOT}/agents/figma-analyst.md` and
follow it as your agent definition." Continue the phases with that agent. If the re-dispatch still
returns `ENV_ERROR`, treat it as "no Figma MCP connected" per Phase 2 below.

### Phase 2: Major-findings handshake

Branch on the agent's return:

- **`ENV_ERROR: <detail>`** → `design.figma`'s runtime-optional semantics apply
  (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §13):
  skip silently — report `Design analysis skipped — no Figma MCP connected (<detail>)` — and let
  the pipeline continue. Never stop the run over this stage; never fabricate a finding in its
  place.
- **`MAJOR_FINDINGS: none`** → skip to Phase 3 with `resolutions = "none"`.
- **Findings** → present via `AskUserQuestion` (≤4 findings per call; more → consecutive calls).
  One question per finding, options:
  - **Apply proposed correction (Recommended)** — first option when the agent proposed one.
  - **Proceed as designed** — the mockup wins; the concern is noted, implement as drawn.
  - **Park for designer** — the design must be fixed before the ticket proceeds.

### Phase 3: Finalize

`SendMessage` to the saved agent ID:

```
User resolutions: [numbered list matching the findings, or "none"].
Record each in §5 Major as **Resolution:** <decision>, finalize the artifact (`Status: DESIGN_ANALYZED`, or `Status: DESIGN_BLOCKED` if any resolution
is Park for designer), and return DESIGN_ANALYSIS_COMPLETE with summary counts.
```

If any resolution was **Park for designer**: after the agent returns, report
`DESIGN_BLOCKED: <the parked findings>` to the caller and terminate — the pipeline must not
continue on a broken design. Re-run after the design is fixed (manual re-run → Overwrite).

### Completion

Report: sections analyzed; screen counts (exists as-is / needs modification / new); transition
count; Major count (with resolutions) and Minor count; evidence file count; artifact path
`<specs.dir>/<TICKET_ID>/design-analysis.md`. Note the ignored phase suffix when applicable.

## Important Rules

- **Orchestrator** per the skill-orchestrator contract
  (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md`) — all analysis and writing belongs to the
  `figma-analyst` agent; this skill only resolves context, relays the handshake, and reports.
- The handshake is chatty-head interaction: plain `AskUserQuestion`, no `pause_reason` bracketing —
  the stage runs before any run is armed (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §13).
- Minor findings never pause here — they reach the user through the analysis interview.
- **Paths in output: repo-relative only** — see `${CLAUDE_PLUGIN_ROOT}/docs/path-conventions.md`.
