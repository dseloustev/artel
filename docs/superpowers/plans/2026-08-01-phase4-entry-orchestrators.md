# Phase 4 — Entry-Point Orchestrators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the `feature-development` and `dev` entry-point orchestrator skills from the source project, add the `/artel:setup` config-interview skill, and introduce the `runtime.surface` and `setup.commands` config keys.

**Architecture:** Faithful port with mapped substitutions — the source skills' structure (step numbering, gate tables, the shared `## Checkpoint commits & pushes` section living in `feature-development` with `dev` referencing it) is kept verbatim; project literals are substituted with config keys, `.artel/run/` paths, adapter language, and `${CLAUDE_PLUGIN_ROOT}` contract references. Spec: `docs/superpowers/specs/2026-08-01-phase4-entry-orchestrators-design.md`.

**Tech Stack:** Markdown skill files (Claude Code plugin format), JSON config schema docs. No executable code; verification is greps + JSON validation of doc fences.

## Global Constraints

- Source material: `../adguard-wallet/.claude/skills/` (sibling checkout). **Genericize — never copy project literals**: no `AW-` ticket keys, no `mcp__aiguard__*`, no `make`/`fvm`/`dart`/`dcm` commands, no wallet paths (CLAUDE.md).
- Plugin-shipped files are referenced from skill/agent bodies as `${CLAUDE_PLUGIN_ROOT}/docs/<file>.md` / `${CLAUDE_PLUGIN_ROOT}/skills/<name>/SKILL.md`; docs among themselves keep relative links (design.md decision log).
- Ported skills drop `allowed-tools:` frontmatter; keep `model:` and `argument-hint:` (design.md decision log).
- Token style in ported bodies: `<specs.dir>`, `<TICKET_ID>`, `<N>`, `<PHASE_NUM>` (angle brackets), matching the ported contracts — not the source's `{TICKET_ID}`.
- Host-writable run state lives at `.artel/run/<TICKET_ID>/` (`run-state.json`, `run-journal.md`, `open-questions.md`, `runtime-observation.md`) per `docs/autonomous-run.md`; the human-readable spec trail stays under `<specs.dir>` per `docs/ticket-parsing.md`.
- Commits: conventional, English, subject line only, no trailers, no Co-Authored-By (CLAUDE.md).
- All docs in English. Forward references use the form `(Phase N — see ${CLAUDE_PLUGIN_ROOT}/docs/porting-plan.md)` on first reference in a file, bare `(Phase N)` on repeats.

---

### Task 1: Config keys — `runtime.surface` and `setup.commands`

**Files:**
- Modify: `docs/config.md`
- Modify: `docs/autonomous-run.md` (§5 note)

**Interfaces:**
- Consumes: nothing (foundation task).
- Produces: config keys `runtime.surface` (array of globs, default absent) and `setup.commands` (array of strings, default `[]`) — referenced by Tasks 3, 4, 5.

- [ ] **Step 1: Add the `setup` section to the default config JSON in `docs/config.md`**

In the fenced default-config JSON (the block under "## The default config"), insert between the `"verify"` object and `"language"`:

```json
  "setup": {
    "commands": []
  },
```

- [ ] **Step 2: Add the `setup` key-reference section**

Insert after the `### `verify` — the quality gate` section (after its closing paragraph "…makes the per-edit hook a no-op."):

```markdown
### `setup` — post-branch setup

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `setup.commands` | array of strings | `[]` | Ordered shell commands run once after a ticket branch is created (dependency install, code generation). Same execution rules as `verify.commands`: run from the host repo root, non-interactive, stop at the first non-zero exit. | `init-branch`'s post-branch setup step |

An empty list silently skips the step — a project whose toolchain needs nothing after a branch
switch simply leaves it unset.
```

- [ ] **Step 3: Add the `runtime.surface` row and semantics paragraph**

In the `### `runtime` — optional runtime and automation commands` table, add as the last row:

```markdown
| `runtime.surface` | array of strings | absent | Repo-relative glob patterns (`fnmatch` semantics, like the sensitive-paths policy) naming the files whose changes make the runtime gate worth running, e.g. `["lib/**/*.dart", "packages/*/lib/**/*.dart"]`. | The entry-point orchestrators' `RUNTIME_OK` gate decision (`feature-development`, `dev`) |
```

Replace the section's closing paragraph ("An absent or empty command means…never blocks a run.") with:

```markdown
An absent or empty command means the corresponding skill reports `not configured` and the
`RUNTIME_OK` gate is recorded as `skipped`. Missing runtime configuration never blocks a run.

`runtime.surface` is a filter, not a command: when set, the entry-point orchestrators run the
`RUNTIME_OK` gate only when the run's diff (changed files vs the default branch plus the working
tree) matches at least one glob, recording `RUNTIME_OK: skipped (no runtime surface)` otherwise.
When absent with `runtime.run` configured, the gate always runs. It has no effect on manual
`/artel:run-app` invocations.
```

- [ ] **Step 4: Extend the filled example**

In the "A filled example" JSON: insert between `"verify"` and `"language"`:

```json
  "setup": {
    "commands": ["npm ci"]
  },
```

and add to the example's `"runtime"` object, after `"drive"`:

```json
    "surface": ["src/**"],
```

- [ ] **Step 5: Update `docs/autonomous-run.md` §5's runtime note**

Replace the sentence:

> The runtime-gate row only runs at all when `runtime.run` (and `runtime.drive`, for `drive-app`) are configured; absent those keys the gate is recorded as `skipped` (config.md) and this loop never arms.

with:

> The runtime-gate row only runs at all when `runtime.run` (and `runtime.drive`, for `drive-app`) are configured; absent those keys the gate is recorded as `skipped` (config.md) and this loop never arms. When `runtime.surface` is set (config.md), the gate additionally runs only when the run's diff matches it — a non-match is recorded as `skipped (no runtime surface)`.

- [ ] **Step 6: Verify**

```bash
python3 - <<'EOF'
import re, json
t = open('docs/config.md').read()
blocks = re.findall(r'```json\n(.*?)```', t, re.S)
assert len(blocks) >= 2, f"expected >=2 json fences, got {len(blocks)}"
for i, b in enumerate(blocks):
    json.loads(b)
    print(f"JSON fence {i}: OK")
EOF
grep -n "runtime.surface" docs/config.md docs/autonomous-run.md
grep -n "setup" docs/config.md | head -20
```

Expected: both JSON fences parse; `runtime.surface` appears in both docs; the `setup` section exists.

- [ ] **Step 7: Commit**

```bash
git add docs/config.md docs/autonomous-run.md
git commit -m "feat: add runtime.surface and setup.commands config keys"
```

---

### Task 2: The `setup` skill

**Files:**
- Create: `skills/setup/SKILL.md`
- Modify: `docs/config.md` ("When the config is missing" section)

**Interfaces:**
- Consumes: config schema from `docs/config.md` (incl. Task 1's keys).
- Produces: `Skill: setup` — invoked by Tasks 4 and 5's step 0 when `.artel/config.json` is missing.

- [ ] **Step 1: Create `skills/setup/SKILL.md` with exactly this content**

````markdown
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
  empty), and `language.docs` / `language.pr` (IETF BCP 47 codes, default `en`).
- **Round 4 — optional extras**, one multi-select question ("configure now, or leave inert?")
  offering: `setup.commands` (post-branch install/codegen), `design.figma` (the design-analysis
  stage), the `runtime.*` commands (`run`, `drive`, `scaffold.add`, `scaffold.remove`), and
  `runtime.surface` (globs gating when the runtime gate runs). Ask follow-up value questions
  only for the selected ones; everything skipped keeps its inert default.

## 3. Validate

Before writing: adapter names inside their allowed sets; `verify.commands` / `setup.commands` /
`runtime.surface` are arrays of strings; MCP-adapter prefixes non-empty; language codes plausible
BCP 47. A violation re-asks that round — never write a config that config.md's reading rules
would reject at run start.

## 4. Write

Write `.artel/config.json` — one complete, explicit file in the shape of config.md's "A filled
example": `version: 1` first, then every section in that example's order, interviewed values
filled in and untouched keys carrying their documented defaults. Strict JSON, UTF-8, no
comments, no trailing commas. This is the skill's only write to the file and its last mutating
step but one — an interview aborted earlier leaves no partial config behind.

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
````

- [ ] **Step 2: Point `docs/config.md`'s "When the config is missing" at the skill**

Replace the first bullet of that section:

> - **Entry-point skills** (`feature-development`, `dev`) find no `.artel/config.json`, run a
>   one-time init interview covering ticket grammar, tracker, VCS, verify commands and languages,
>   write `.artel/config.json`, and continue into the requested run. The interview happens once per
>   repo; afterwards the file is edited by hand (Phase 4 — see
>   [porting-plan.md](porting-plan.md)).

with:

```markdown
- **Entry-point skills** (`feature-development`, `dev`) find no `.artel/config.json` and invoke
  the `setup` skill (`/artel:setup`), which interviews for ticket grammar, tracker, VCS, verify
  commands and languages (plus optional extras), writes `.artel/config.json`, and returns
  control so the requested run continues. The interview happens once per repo; afterwards the
  file is edited by hand, or revised via `/artel:setup`.
```

- [ ] **Step 3: Verify**

```bash
head -6 skills/setup/SKILL.md
grep -c "allowed-tools" skills/setup/SKILL.md || true
grep -n "artel:setup" docs/config.md
grep -rn "Phase 4" docs/config.md
```

Expected: frontmatter has `name: setup`, no `allowed-tools`; config.md names `/artel:setup`; no "Phase 4" forward reference remains in config.md.

- [ ] **Step 4: Commit**

```bash
git add skills/setup/SKILL.md docs/config.md
git commit -m "feat: add setup skill - first-run config interview"
```

---

### Task 3: Restore `init-branch`'s post-branch setup step

**Files:**
- Modify: `skills/init-branch/SKILL.md`
- Modify: `docs/porting-plan.md` (the Phase-3 follow-up note, lines ~55-59)

**Interfaces:**
- Consumes: `setup.commands` (Task 1).
- Produces: nothing downstream; closes the parked Phase-3 follow-up.

- [ ] **Step 1: Insert a new Step 3 and renumber**

In `skills/init-branch/SKILL.md`, insert after the `### Step 2: Create or check out the branch` section:

```markdown
### Step 3: Run the post-branch setup commands

Read `setup.commands` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "setup" section). Empty or absent
→ skip silently. Otherwise run the commands in order from the repo root, stopping at the first
non-zero exit. **A failure here is fatal**, like Step 2: a branch whose dependencies did not
install can't be worked on — report the failing command and its output and stop. Note the
outcome (ran / skipped) for the report.
```

Then renumber the old Steps 3–6 to 4–7 (headings: "Step 4: Restore the ticket's context", "Step 5: Refresh `CLAUDE.md`", "Step 6: Refresh the code-symbol index (optional host hook)", "Step 7: Report") and fix the in-body cross-references:

- In the restore step's blockquote: "then continue from Step 4" → "then continue from Step 5".
- In `## Rules`, the order line: "**Order matters and is fixed:** branch → restore → `/init` → reindex." → "**Order matters and is fixed:** branch → setup commands → restore → `/init` → reindex." (keep the trailing rationale sentence as-is).
- In `## Rules`, the fatal-step line: "A failed checkout (Step 2) or a missing context store (Step 3) halts the skill" → "A failed checkout (Step 2), a failed setup command (Step 3), or a missing context store (Step 4) halts the skill"; and "A ticket with no saved artifacts (Step 3 warning) and a reindex error (Step 5) are non-fatal" → "(Step 4 warning) … (Step 6)".

- [ ] **Step 2: Update the frontmatter description and the report bullets**

In the frontmatter `description`, replace "create and check it out from the repo's detected base branch, restore that ticket's context via /artel:restore-context" with "create and check it out from the repo's detected base branch, run the project's configured post-branch setup commands (setup.commands), restore that ticket's context via /artel:restore-context".

In the report step's bullet list, add after the branch bullet:

```markdown
- Whether the setup commands ran or were skipped (none configured).
```

Also update the `## Overview` first paragraph's flow enumeration to include the setup step: "derive the branch name from the resolved `TICKET_ID`, create/check it out from the repo's detected base branch, run the configured post-branch setup commands, restore the ticket's spec trail from the context store, and reconcile `CLAUDE.md` against the current tree."

- [ ] **Step 3: Mark the porting-plan follow-up resolved**

In `docs/porting-plan.md`, the Phase-3 bullet ending "…decide in Phase 4/5 (init interview / hooks config) whether a setup-command key is warranted." — append to that bullet:

```markdown
 Resolved in Phase 4: `setup.commands` added to config.md and the step restored in
      `init-branch`.
```

- [ ] **Step 4: Verify**

```bash
grep -n "^### Step" skills/init-branch/SKILL.md
grep -n "setup.commands" skills/init-branch/SKILL.md docs/porting-plan.md
grep -n "Step 3\|Step 4\|Step 5\|Step 6\|Step 7" skills/init-branch/SKILL.md
```

Expected: seven sequential step headings; no stale cross-reference to the old numbering (read the Rules section output carefully).

- [ ] **Step 5: Commit**

```bash
git add skills/init-branch/SKILL.md docs/porting-plan.md
git commit -m "feat: restore init-branch post-branch setup step via setup.commands"
```

---

### Task 4: Port `feature-development`

**Files:**
- Create: `skills/feature-development/SKILL.md` (source: `../adguard-wallet/.claude/skills/feature-development/SKILL.md`)
- Modify: `docs/autonomous-run.md` (§14 pointer)

**Interfaces:**
- Consumes: `Skill: setup` (Task 2), `runtime.surface` (Task 1), the ported contracts (`docs/autonomous-run.md`, `docs/orchestrator-common.md`, `docs/ticket-parsing.md`, `docs/config.md`).
- Produces: the `## Checkpoint commits & pushes` section — referenced by Task 5's `dev` as `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md`.

- [ ] **Step 1: Read the source file**

Read `../adguard-wallet/.claude/skills/feature-development/SKILL.md` in full. The port keeps its structure verbatim — steps 1–9, the two gate tables, `## Important`, `## Checkpoint commits & pushes` — applying only the substitutions below.

- [ ] **Step 2: Write `skills/feature-development/SKILL.md`**

Frontmatter (exact; `allowed-tools` dropped):

```yaml
---
name: feature-development
description: "End-to-end autonomous feature workflow: interview -> PRD -> vision -> plan -> tasks -> ONE approval pause -> autonomous implementation, review, QA, docs"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates] [--dry-run]"
model: sonnet
---
```

Global substitutions applied throughout the body:

1. `.claude/docs/autonomous-run.md` → `${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md`; `.claude/docs/orchestrator-common.md` → `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md`; `ticket-parsing.md` references → `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` on first reference, bare `ticket-parsing.md` on repeats (same pattern for the other contracts).
2. `specs/.current/` → `<specs.dir>/` everywhere; `{TICKET_ID}` → `<TICKET_ID>`; `{TICKET_ID}-{N}` → `<TICKET_ID>-<N>`; `{N}` → `<N>`; `{M}` → `<M>`.
3. `specs/.current/{TICKET_ID}/run-state.json` → `.artel/run/<TICKET_ID>/run-state.json`; same relocation for `run-journal.md` and `open-questions.md`. Where the source first mentions each, cite the contract section (`autonomous-run.md` §2 / §11 / §3).
4. `AW-XXXX-N` (phase-traversal wording) → `<TICKET_ID>-<N>`.

Targeted edits (in source order):

5. **New step 0** before "### 1. Set active ticket":

```markdown
### 0. Config gate

Read `.artel/config.json` per `${CLAUDE_PLUGIN_ROOT}/docs/config.md`. Missing → `Skill: setup`
(the one-time init interview), then continue with the written config. Present → run the
start-time check config.md's "When the adapter is unusable" prescribes for entry points:
`vcs.adapter: "bitbucket-mcp"` with an empty `vcs.mcpToolPrefix` is a configuration error —
report it and stop before the pipeline starts rather than failing hours later at the PR stage.
```

6. **Gate 0 row**, replace the action text with: `` `tracker.adapter` ≠ `"none"` → `Skill: generate-idea` with `$0`. `"none"` (local-only) → rely on the `$1` description file or an existing `idea.md`; if neither exists, the analysis input gate stops and asks. ``
7. **Gate 0.5 row**, prepend to the action: `` `design.figma` disabled (config.md) → skip silently. Enabled → `` and keep the source's grep-for-`figma.com/design` logic, Major-findings handshake, and `DESIGN_BLOCKED` handling unchanged.
8. **Gate 3 row**: "their questions land in `open-questions.md`" → "their questions land in `.artel/run/<TICKET_ID>/open-questions.md` (autonomous-run.md §3)".
9. **Gate 3.5 row**, replace the whole action with:

```markdown
Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <plan-path> --strict`, where
`<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. The script ships in Phase 5
(see `${CLAUDE_PLUGIN_ROOT}/docs/porting-plan.md`); **when it does not exist yet** (check before
running), journal `PLAN_GROUNDED: skipped (plan-check ships in Phase 5)` and proceed — an
unshipped tool degrades like an unconfigured gate. When it exists: exit 0 → proceed. Exit 1 →
append/update `**Plan-check bounces:** N` at the bottom of `<plan-path>`, and while
`N <= MAX_PLAN_CHECK_BOUNCES = 2`: `SendMessage` the `data.unresolved` list to the `planner`
agent ("resolve or declare `new:`"), regenerate, re-run the check. Planner regeneration rewrites
`<plan-path>` and drops the bounce line with it; after each regeneration re-append
`**Plan-check bounces:** N` (N = bounces performed so far) before re-running the check. Third
failure → stop and ask (chatty head — plain `AskUserQuestion`, no `pause_reason`) without
writing N=3 — the file shows `**Plan-check bounces:** 2` at the stop. Exit 2 → environment
error: stop-and-ask pointing at setup, never a bounce.
```

10. **Step 3 (THE ONE PAUSE)**: `open-questions.md` mentions get the `.artel/run/<TICKET_ID>/` path; everything else verbatim.
11. **Step 4 (Arm)**: run-state and journal paths per substitution 3. The planning-checkpoint sentence: commit `<specs.dir>/<TICKET_ID>/**` + `<specs.dir>/.active_ticket`; "No `make verify` here — docs only, no code yet." → "No verify gate here (`verify.commands`) — docs only, no code yet."
12. **Gate 6 row**, replace `Run \`ast-index update\`.` with: "Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it when the host has wired one up; silently absent otherwise."
13. **Gate 8 row**, replace the whole action with:

```markdown
Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs
the default branch plus the working tree) and match them against the globs; no match → write
`RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move
on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports
this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app
code** (runtime errors / ERROR logs / a broken UI tree) → append the quoted error as a `- [ ]`
task under `## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`);
when the RED stems from incomplete cross-phase wiring (this phase's code invokes pieces a later
phase will build), word the fix task to create the **minimal stubs** that restore launch —
no-op implementations / placeholder surfaces with a `TODO: phase <M>` marker — rather than real
implementations; stubbing is the expected resolution at a phase boundary and is recorded in the
completion's `Deviations:` line. Run `Skill: implementer` once (`MAX_RUNTIME_RETRIES = 1`;
counter in `.artel/run/<TICKET_ID>/runtime-observation.md`, autonomous-run.md §5) — it picks the
fix task up as the first incomplete task — then re-run the gate; second RED → cap escalation.
RED from an **environment failure** (a launch/setup failure of `runtime.run` itself, not app
code — run-app stops-and-asks for these) → cap escalation immediately, no implementer round. Do
not trust a stale green — re-run unless the observation postdates the last change to files
matching `runtime.surface` (or the last code change, when it is unset).
```

14. **Gate 10.7 row**: drop the `dcm_global.yaml` pin-restore mention; the action reads: "Run the phase-end checkpoint (see `## Checkpoint commits & pushes`): the `verify.commands` gate → capped `## Verify Fixes` implementer rounds → explicit staging → commit (`feat\|fix\|refactor: <TICKET_ID> phase <N> - <phase title>`; no phase → `<ticket summary>`) → push → journal. Then advance `.active_ticket` to the next incomplete phase; on a multi-phase run loop back to the traversal (next phase), else proceed to step 6."
15. **Step 7 (PR description + close)**: "(design §13.5)" → "(see `${CLAUDE_PLUGIN_ROOT}/skills/pr-description/SKILL.md`)"; "commit+push+Bitbucket PR+Jira comment" → "commit+push + PR via `vcs.adapter` + tracker comment via `tracker.adapter`"; "Jira/Bitbucket failures degrade to its fallback template" → "tracker/VCS fetch failures degrade to its fallback template".
16. **Step 8**: reference `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §4.
17. **`## Important`**, files-written bullet: "The only files this orchestrator writes directly: `<specs.dir>/.active_ticket`, `.artel/run/<TICKET_ID>/run-state.json`, `.artel/run/<TICKET_ID>/run-journal.md`, the `open-questions.md` status flips (same directory), and the description file during sync. Everything else is delegated."
18. **Checkpoint procedure**: branch-guard fallback `master` → `main`. Step 3 (quality gate) becomes: "**Quality gate (phase-end only).** Run `verify.commands` in order (config.md), stopping at the first failure; an empty list ⇒ record the verify step as `skipped` in the journal entry and continue to staging. Findings → append them as `- [ ]` tasks under `## Verify Fixes` in the phase-aware tasklist and loop `Skill: implementer` (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`; each round increments `counters.correction_rounds` per autonomous-run.md §5); still red after the cap → cap escalation. A non-zero exit that reports no actionable findings (a toolchain/version quirk) is an **environment error** — stop-and-ask, never a fix round." Delete the pin-restore step entirely and renumber the remaining steps (stage/commit/push/journal become 4–7). Staging step: "The ticket's changed files, `<specs.dir>/<TICKET_ID>/**`, and `<specs.dir>/.active_ticket`. Never `git add -A`; never generated files; never `.artel/**`."

- [ ] **Step 3: Update `docs/autonomous-run.md` §14's pointer**

Replace "…are defined in the `feature-development` skill (Phase 4 — see [porting-plan.md](porting-plan.md); shared with `dev`)." with "…are defined in the `feature-development` skill (`../skills/feature-development/SKILL.md`, `## Checkpoint commits & pushes`; shared with `dev`)."

- [ ] **Step 4: Verify**

```bash
grep -nE "AW-|aiguard|make verify|fvm|dart |dcm|ast-index|specs/\.current|\{TICKET_ID\}|master" skills/feature-development/SKILL.md
grep -c "allowed-tools" skills/feature-development/SKILL.md || true
grep -n "Checkpoint commits & pushes" skills/feature-development/SKILL.md
grep -n "Phase 4" docs/autonomous-run.md
```

Expected: first grep returns nothing; no `allowed-tools`; the checkpoint section exists (heading + references); no "Phase 4" left in autonomous-run.md.

- [ ] **Step 5: Commit**

```bash
git add skills/feature-development/SKILL.md docs/autonomous-run.md
git commit -m "feat: port feature-development entry-point orchestrator"
```

---

### Task 5: Port `dev`

**Files:**
- Create: `skills/dev/SKILL.md` (source: `../adguard-wallet/.claude/skills/dev/SKILL.md`)

**Interfaces:**
- Consumes: `Skill: setup` (Task 2), `runtime.surface` (Task 1), `skills/feature-development/SKILL.md` `## Checkpoint commits & pushes` (Task 4).
- Produces: nothing downstream.

- [ ] **Step 1: Read the source file**

Read `../adguard-wallet/.claude/skills/dev/SKILL.md` in full. Keep its structure verbatim — steps 1–9, `## Important` — applying the substitutions below.

- [ ] **Step 2: Write `skills/dev/SKILL.md`**

Frontmatter (exact; `allowed-tools` dropped):

```yaml
---
name: dev
description: "Lean autonomous dev workflow: mini-interview -> one work-list confirmation -> autonomous implement + review + runtime gate"
argument-hint: "[ticket-id] or [ticket-id]-[phase] [description-file] [--mode=yolo|plan-gate|full-gates]"
model: sonnet
---
```

Global substitutions 1–4 from Task 4 Step 2 apply identically (contract paths via `${CLAUDE_PLUGIN_ROOT}`, `<specs.dir>`/`<TICKET_ID>` tokens, `.artel/run/` relocation, `AW-XXXX-N` → `<TICKET_ID>-<N>`).

Targeted edits:

1. **Header paragraph**: the checkpoint-procedure reference becomes "commit/push procedure: `## Checkpoint commits & pushes` in `${CLAUDE_PLUGIN_ROOT}/skills/feature-development/SKILL.md` (shared)."
2. **New step 0** before "### 1. Set active ticket" — exactly this text (same as feature-development's):

```markdown
### 0. Config gate

Read `.artel/config.json` per `${CLAUDE_PLUGIN_ROOT}/docs/config.md`. Missing → `Skill: setup`
(the one-time init interview), then continue with the written config. Present → run the
start-time check config.md's "When the adapter is unusable" prescribes for entry points:
`vcs.adapter: "bitbucket-mcp"` with an empty `vcs.mcpToolPrefix` is a configuration error —
report it and stop before the pipeline starts rather than failing hours later at the PR stage.
```

3. **Step 2 (input ladder)**: structure verbatim; the checkpoint-authorization sentence's procedure pointer becomes "(procedure: `feature-development` `## Checkpoint commits & pushes`)" — a bare in-prose repeat is fine after the header's full `${CLAUDE_PLUGIN_ROOT}` reference.
4. **Step 3 (Arm)**: run-state/journal paths per the global substitutions; "No `make verify` here — docs only, no code yet." → "No verify gate here (`verify.commands`) — docs only, no code yet."
5. **Step 5 (Update AST index)**: rename the heading to "### 5. Refresh the code index (optional host hook)" and replace the body with: "Optional host index-refresh hook (`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §1): run it when the host has wired one up; silently absent otherwise."
6. **Step 7 (Runtime gate)**: replace the whole body with the same text as Task 4 Step 2's edit 13 (the surface-check version), reproduced here so this task is self-contained:

```markdown
Surface check: with `runtime.surface` set (config.md), collect the run's changed files (diff vs
the default branch plus the working tree) and match them against the globs; no match → write
`RUNTIME_OK: skipped (no runtime surface)` to the phase-aware `runtime/observation.md` and move
on. No `runtime.run` configured → the gate records `skipped (not configured)` (run-app reports
this itself). Otherwise `Skill: run-app` with `--gate`. RED caused by a **runtime error in app
code** (runtime errors / ERROR logs / a broken UI tree) → append the quoted error as a `- [ ]`
task under `## Runtime Fixes` in the phase-aware tasklist (mirroring `## Code Review Fixes`);
when the RED stems from incomplete cross-phase wiring (this phase's code invokes pieces a later
phase will build), word the fix task to create the **minimal stubs** that restore launch —
no-op implementations / placeholder surfaces with a `TODO: phase <M>` marker — rather than real
implementations; stubbing is the expected resolution at a phase boundary and is recorded in the
completion's `Deviations:` line. Run `Skill: implementer` once (`MAX_RUNTIME_RETRIES = 1`;
counter in `.artel/run/<TICKET_ID>/runtime-observation.md`, autonomous-run.md §5) — it picks the
fix task up as the first incomplete task — then re-run the gate; second RED → cap escalation.
RED from an **environment failure** (a launch/setup failure of `runtime.run` itself, not app
code — run-app stops-and-asks for these) → cap escalation immediately, no implementer round. Do
not trust a stale green — re-run unless the observation postdates the last change to files
matching `runtime.surface` (or the last code change, when it is unset).
```

7. **Step 7.5 (Phase checkpoint)**: drop the `dcm_global.yaml` pin-restore mention; the checkpoint enumeration reads "run the phase-end checkpoint per `feature-development` `## Checkpoint commits & pushes`: the `verify.commands` gate → capped `## Verify Fixes` implementer rounds (`MAX_CHECKPOINT_VERIFY_ROUNDS = 2`, counted toward `counters.correction_rounds`) → explicit staging → commit (`feat|fix|refactor: <TICKET_ID> phase <N> - <phase title>`; no phase → `<work summary>`) → `git push -u origin <branch>` → journal."
8. **`## Important`**, files-written bullet: "Files this orchestrator writes directly: `<specs.dir>/.active_ticket`, `.artel/run/<TICKET_ID>/run-state.json`, `.artel/run/<TICKET_ID>/run-journal.md`, the step-2.3 work-list tasklist, and the description file during sync. Everything else is delegated."

- [ ] **Step 3: Verify**

```bash
grep -nE "AW-|aiguard|make verify|fvm|dart |dcm|ast-index|specs/\.current|\{TICKET_ID\}|master" skills/dev/SKILL.md
grep -c "allowed-tools" skills/dev/SKILL.md || true
grep -n "feature-development/SKILL.md" skills/dev/SKILL.md
```

Expected: first grep returns nothing; no `allowed-tools`; the checkpoint reference points at the feature-development skill.

- [ ] **Step 4: Commit**

```bash
git add skills/dev/SKILL.md
git commit -m "feat: port dev entry-point orchestrator"
```

---

### Task 6: Close out Phase 4 — decision log, porting plan, README, CHANGELOG, sweep

**Files:**
- Modify: `docs/design.md` (decision log)
- Modify: `docs/porting-plan.md` (Phase 4 checkboxes)
- Modify: `skills/README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: everything above.
- Produces: Phase 4 closed.

- [ ] **Step 1: Append four decision-log entries to `docs/design.md`**

```markdown
- **2026-08-01 — Gate 3.5 (plan-check) ports as spec, skips until Phase 5.** The
  `feature-development` port keeps the full bounce contract (bounce-line bookkeeping,
  `MAX_PLAN_CHECK_BOUNCES = 2`, exit-code semantics) but the deterministic checker itself —
  `scripts/plan_check.py`, the open-question-1 Python rewrite — ships in Phase 5. Until it
  exists the gate journals `PLAN_GROUNDED: skipped (plan-check ships in Phase 5)` and proceeds:
  an unshipped tool degrades exactly like an unconfigured gate, and the absence of a recorded
  green means the gate re-runs for real once the tool lands.
- **2026-08-01 — Runtime-surface detection becomes `runtime.surface`.** The source
  orchestrators skipped the `RUNTIME_OK` launch when no `*.dart` under `lib/` /
  `packages/*/lib/` changed — a Dart-specific test with no generic equivalent. Added an
  optional `runtime.surface` config key (array of globs, config.md): set → the gate runs only
  when the run's diff matches, else `skipped (no runtime surface)`; absent with `runtime.run`
  configured → the gate always runs. The source behavior is expressible as
  `["lib/**/*.dart", "packages/*/lib/**/*.dart"]`.
- **2026-08-01 — `setup.commands` config key; init-branch regains its post-branch step**
  (Phase-3 Task-7 follow-up). The source `init-branch` ran dependency install/codegen after
  branch creation; the Phase-3 port dropped it for lack of a generic key. Added
  `setup.commands` (array, `verify.commands` execution rules, default `[]`) and restored the
  step in `init-branch` — fatal on failure, silently skipped when empty. The `setup` skill's
  interview offers it as an optional extra.
- **2026-08-01 — The init interview is `/artel:setup`, its own skill.** A self-declared
  procedural worker both entry points invoke when `.artel/config.json` is missing and users
  invoke manually to create or revise the config (revise pre-selects current values). Named
  `setup`, not `init`: Claude Code's built-in `/init` (CLAUDE.md generator) already exists and
  artel's own `init-branch` chains it, so an `artel:init` would collide cognitively; kinship
  with `setup.commands` is a bonus. Writes one complete explicit config file (the config.md
  "filled example" shape) as its last step — an aborted interview writes nothing — and
  maintains the `.gitignore` entries for `.artel/run/` and `.artel/context/`.
```

- [ ] **Step 2: Check off Phase 4 in `docs/porting-plan.md`**

Flip the three Phase-4 checkboxes to `[x]`. Append to the third bullet (init interview): " Shipped as the `setup` skill (`/artel:setup`) — see design.md's decision log for the naming."

- [ ] **Step 3: Update `skills/README.md`**

Replace the sentence "All [porting-plan Phase 3](../docs/porting-plan.md#phase-3--stage-skills) stage, runtime-adapter and utility skills are in place; entry points follow in [Phase 4](../docs/porting-plan.md#phase-4--entry-point-orchestrators)." with:

```markdown
All [porting-plan Phase 3](../docs/porting-plan.md#phase-3--stage-skills) stage, runtime-adapter
and utility skills are in place, plus the
[Phase 4](../docs/porting-plan.md#phase-4--entry-point-orchestrators) entry points:
`feature-development` (full pipeline), `dev` (lean loop), and `setup` (the one-time config
interview both entry points trigger when `.artel/config.json` is missing).
```

Keep the rest of the file unchanged.

- [ ] **Step 4: Add the CHANGELOG entry**

Append one bullet to the `### Added` list in `CHANGELOG.md` (matching the existing style):

```markdown
- Entry-point orchestrators: `skills/feature-development` (full pipeline: chatty head, one
  approval pause, autonomous tail, completion gate, PR close-out) and `skills/dev` (lean loop:
  input ladder, one work-list confirmation, implement + review + runtime gate), ported and
  genericized from the source project. Run state, journal and open questions live at
  `.artel/run/<TICKET_ID>/` per `docs/autonomous-run.md`; the shared
  `## Checkpoint commits & pushes` procedure lives in `feature-development` (referenced by
  `dev`, branch-guard fallback now `main`, verify gate via `verify.commands`, Dart-specific pin
  restore dropped); gate 3.5's deterministic plan-check ports as contract but skips until the
  Phase-5 `scripts/plan_check.py` ships; `ast-index` steps become the optional host
  index-refresh hook; the runtime gate's Dart-specific surface test becomes the new
  `runtime.surface` config key. New `skills/setup` — the one-time config interview both entry
  points invoke when `.artel/config.json` is missing (also run manually to create or revise the
  config; named `setup` to avoid colliding with the built-in `/init`). New config keys:
  `runtime.surface` (globs gating when the runtime gate runs) and `setup.commands` (post-branch
  install/codegen, restoring `init-branch`'s dropped source step — the parked Phase-3
  follow-up). Decisions logged in `docs/design.md`.
```

- [ ] **Step 5: Forward-reference sweep and final verification**

```bash
grep -rn "Phase 4" skills/ agents/ docs/config.md docs/autonomous-run.md docs/orchestrator-common.md docs/ticket-parsing.md
grep -rnE "AW-|aiguard|make verify|fvm |dcm|wallet" skills/feature-development skills/dev skills/setup
grep -rn "artel:setup\|Skill: setup" skills/feature-development/SKILL.md skills/dev/SKILL.md docs/config.md
git diff --stat HEAD~5 2>/dev/null | tail -3
```

Expected: the first grep's only hits are `skills/merge-conflicts/SKILL.md` (an internal "Phase 4" section heading, not a forward reference) — fix anything else found; the second grep returns nothing; the third shows both orchestrators and config.md referencing `setup`.

- [ ] **Step 6: Commit**

```bash
git add docs/design.md docs/porting-plan.md skills/README.md CHANGELOG.md
git commit -m "docs: close out phase-4 entry-point orchestrators"
```

---

## Post-plan manual verification (operator, not a subagent)

The spec's smoke test needs an interactive Claude Code session and cannot run inside this plan:
in a scratch host repo, `/plugin marketplace add <path-to-this-repo>` then
`/plugin install artel@artel`; confirm `/artel:feature-development`, `/artel:dev`, and
`/artel:setup` surface under the `artel:` namespace; run `/artel:setup` end-to-end and check it
writes parseable `.artel/config.json` plus the two `.gitignore` entries. The full pipeline
dry-run stays Phase 6.
