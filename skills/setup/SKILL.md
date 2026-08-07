---
name: setup
description: "One-time configuration interview: create or revise .artel/config.json — ticket grammar, tracker/VCS adapters, verify commands, languages, optional extras (setup commands, design stage, runtime gate). Invoked automatically by /artel:feature-development and /artel:dev when no config exists; run manually to create or revise the file."
argument-hint: ""
model: sonnet
---

Worker, not an orchestrator — no agent matches this job; the interview runs inline (like
`sync-phases` / `generate-idea`). Config contract: `${CLAUDE_PLUGIN_ROOT}/docs/config.md` — read
it first; the interview only ever writes keys that document defines, in its documented shapes.

## 1. Existing-config gate

Read `.artel/config.json`.

- **Missing** → step 2 directly (the first-run path the entry points trigger).
- **Present and parseable** → show the current values in one screen and ask via
  `AskUserQuestion`: **Revise** (re-run the interview with the current values pre-selected as
  defaults) / **Abort** (leave the file untouched, stop).
- **Present but not strict JSON** → report the parse error, quoting the failing content, and
  ask: **Recreate** (run the interview from config.md's defaults; the broken file is overwritten
  at step 4) / **Abort** (the user fixes it by hand).

## 2. Interview

`AskUserQuestion`, ≤4 questions per round. Pre-select each question's default: the config.md
default on first run, the current value on Revise. Free-form values (commands, prefixes, globs)
arrive via the "Other" option.

- **Round 1 — ticket grammar:** `ticket.projectKey` (required — the one key with no meaningful
  default; letters and digits, uppercase canonical) and `ticket.phaseSuffix` (does this project
  split tickets into phases?).
- **Round 2 — adapters:** `tracker.adapter` (`none` / `github-issues` / `jira-mcp`) and
  `vcs.adapter` (`github-cli` / `bitbucket-mcp`). When `jira-mcp` or `bitbucket-mcp` is chosen,
  ask the matching `mcpToolPrefix` in the same round (full prefix including the trailing
  separator, e.g. `mcp__tracker__`) — an empty prefix for these adapters is a configuration
  error (config.md reading rule 3), so re-ask rather than write one.
- **Round 3 — quality gate and languages:** `verify.commands` (ordered list, one command per
  line; empty = no gate, recorded `skipped`), `verify.fast` (one quick per-edit command, or
  empty; commands may carry a `{files}` token the hooks replace with the changed paths), and
  `language.docs` / `language.pr` (IETF BCP 47 codes, default `en`).
- **Round 4 — optional extras**, one multi-select question ("configure now, or leave inert?")
  offering: `setup.commands` (post-branch install/codegen), `design.figma` (the design-analysis
  stage), the `runtime.*` commands (`run`, `drive`, `scaffold.add`, `scaffold.remove`),
  `runtime.surface` (globs gating when the runtime gate runs), `verify.surface` (globs with
  `!`-excludes filtering which edits the verify hooks check), and a `.artel/sensitive-paths.json`
  scaffold (a copy of the plugin's default sensitive-paths policy, for projects that want to
  extend it). Ask follow-up value questions only for the selected ones; everything skipped keeps
  its inert default.

## 3. Validate

Before writing: adapter names inside their allowed sets; `verify.commands` / `setup.commands` /
`runtime.surface` / `verify.surface` are arrays of strings; MCP-adapter prefixes non-empty;
language codes plausible BCP 47. A violation re-asks that round — never write a config that
config.md's reading rules would reject at run start.

## 4. Write

Write `.artel/config.json` — one complete, explicit file in the shape of config.md's "A filled
example": `version: 1` first, then every section in that example's order, interviewed values
filled in and untouched keys carrying their documented defaults. Strict JSON, UTF-8, no
comments, no trailing commas. When the sensitive-paths scaffold was selected in Round 4, also
copy `${CLAUDE_PLUGIN_ROOT}/hooks/sensitive-paths.json` to `.artel/sensitive-paths.json`
(skip with a note if the host file already exists — never overwrite a policy). These are the
skill's only writes and its last mutating steps but one — an interview aborted earlier leaves
no partial config behind.

## 5. `.gitignore`

Ensure the host `.gitignore` contains `.artel/run/` and `.artel/context/` (config.md, "Purpose
and location"); append whichever is missing, touch nothing when both are present.

## 6. Report

- Path written and the chosen adapters.
- Which gates are armed vs will record `skipped` (verify, runtime, design).
- The `.gitignore` outcome.
- A reminder that `.artel/config.json` is committed team configuration — commit it by hand
  (orchestrator checkpoint commits never stage `.artel/`).

When an entry-point orchestrator invoked this skill, control returns to it and the run
continues.
