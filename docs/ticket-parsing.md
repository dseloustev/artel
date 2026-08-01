# Ticket parsing & artifact path resolution

*Status: draft v0.1 · 2026-08-01*

This document is the contract that agents and skills use to recognize, normalize and resolve
ticket identifiers, and the canonical path layout every phase-aware skill and agent must follow
when reading or writing ticket artifacts. The concrete grammar — project key, pattern, phase-
suffix toggle — is not hardcoded here: it comes from `.artel/config.json`. See
[config.md](config.md) for the full schema. This doc defines the algorithm that consumes that
config and the paths it resolves to, not the grammar itself.

All skills and agents in the workflow chain reference this document for consistent behavior. Do
not duplicate path logic in agent prompts — link to this file instead.

---

## 1. Identifier formats

Ticket identifiers are matched against `ticket.pattern` (with the literal token `{projectKey}`
replaced by `ticket.projectKey`), case-insensitively. The pattern exposes the ticket number as
capture group 1 and the optional phase as capture group 2 — positional groups, not named ones,
so the same pattern compiles unchanged across regex engines.

The table below shows the accepted forms under the **default** `ticket.pattern` and
`ticket.projectKey: "PROJ"` (the same defaults documented in config.md). A project that
customizes `ticket.pattern` accepts different input forms, but the group semantics — group 1 is
always the number, group 2 is always the phase — never change.

| Input Format | TICKET_ID | TICKET_NUM | PHASE_NUM | Example |
|---|---|---|---|---|
| `PROJ-NNNN-P` | `PROJ-NNNN` | `NNNN` | `P` | `PROJ-2052-1` → ticket=`PROJ-2052`, num=`2052`, phase=`1` |
| `PROJ-NNNN-pP` | `PROJ-NNNN` | `NNNN` | `P` | `PROJ-2052-p3` → ticket=`PROJ-2052`, num=`2052`, phase=`3` |
| `NNNN-P` | `PROJ-NNNN` | `NNNN` | `P` | `2052-1` → ticket=`PROJ-2052`, num=`2052`, phase=`1` |
| `NNNN-pP` | `PROJ-NNNN` | `NNNN` | `P` | `2052-p3` → ticket=`PROJ-2052`, num=`2052`, phase=`3` |
| `PROJ-NNNN` | `PROJ-NNNN` | `NNNN` | `null` | `PROJ-2052` → ticket=`PROJ-2052`, num=`2052`, phase=none |
| `NNNN` | `PROJ-NNNN` | `NNNN` | `null` | `2052` → ticket=`PROJ-2052`, num=`2052`, phase=none |

**Variables:**
- `TICKET_ID`: canonical `<ticket.projectKey>-<group 1>` (e.g., `PROJ-2052`)
- `TICKET_NUM`: capture group 1, the numeric part (e.g., `2052`)
- `PHASE_NUM`: capture group 2, the phase/iteration number (e.g., `1`, `2`) or `null` if the
  group did not participate

**When `ticket.phaseSuffix` is `false`:** an identifier carrying a phase suffix is malformed —
reject it and ask for a bare ticket identifier — rather than reading the suffix as a phase.
Every run is then ticket-wide; the phase-scoped rows and rules in sections 3, 4, 5 and 7 never
apply for that project.

---

## 2. Parsing algorithm

```
1. Compile ticket.pattern with {projectKey} replaced by ticket.projectKey; match
   case-insensitively against the input identifier.

2. If it does not match: the identifier is malformed. Reject and ask for a valid one.

3. TICKET_NUM = capture group 1.

4. If ticket.phaseSuffix is true:
     PHASE_NUM = capture group 2, or null if that group did not participate.
   Else (ticket.phaseSuffix is false):
     If capture group 2 participated: the identifier is malformed (this project does not
       support phase suffixes) — reject and ask for a bare identifier.
     PHASE_NUM = null.

5. TICKET_ID = "<ticket.projectKey>-<TICKET_NUM>" — always the configured project key's
   canonical casing plus the number, regardless of the case or prefix the caller typed.
```

---

## 3. Directory layout

All ticket artifacts live under `<specs.dir>/<TICKET_ID>/` (default `specs.dir`:
`specs/.current` — see [config.md](config.md)). The directory carries the ticket; filenames
never repeat `<TICKET_ID>` or `<TICKET_NUM>`.

**Phase-scoped artifacts live in a `phase-<N>/` subfolder**, not as suffixed siblings.
Ticket-wide artifacts stay at the top level. (On a project with `ticket.phaseSuffix: false`, no
run ever produces a `phase-<N>/` folder — every artifact is ticket-wide.)

```
<specs.dir>/<TICKET_ID>/
├── idea.md              # ticket-wide seed
├── design-analysis.md   # optional, ticket-wide design analysis (never phase-scoped)
├── design/              # optional, design evidence (e.g. Figma screenshots) for design-analysis.md
├── vision.md            # ticket-wide technical vision
├── tasklist.md          # ticket-wide master tasklist (all phases)
├── implementation-notes.md # optional, ticket-wide deviations log
├── prd.md               # optional, ticket-wide overall PRD
├── plan.md              # optional, ticket-wide overall plan
├── research.md          # optional, ticket-wide overall research
├── qa.md                # optional, ticket-wide overall QA report
├── review.md            # optional, ticket-wide review (always ticket-level, even for phase runs)
├── review/              # optional, machine-readable review findings (reviewer agent)
│   └── findings.json   # lens findings (convention/architecture/security) for a ticket-wide review run
├── verify/              # optional, quality-gate evidence from inner-loop runs (fast-check/full-gate JSON per iteration, residual.json on stop-and-ask)
├── runtime/             # optional, runtime-gate evidence (run-app, drive-app)
│   ├── observation.md   # RUNTIME_OK evidence from run-app
│   └── drive-observation.md # UI-driving evidence from drive-app
├── adr.md               # optional, ticket-wide ADR
├── summary.md           # optional, ticket-wide summary
├── change-report.html   # optional, derived HTML change-comprehension report (change-digest skill) — regenerable, never committed
├── pr-description.md    # ticket-wide
├── pr-pending.md        # optional, intended PR/tracker actions recorded by pr-create when an identity check fails; ticket-wide only
├── post_feedback.md     # ticket-wide
└── phase-<N>/
    ├── prd.md           # phase-scoped PRD
    ├── plan.md          # phase-scoped plan
    ├── research.md      # phase-scoped research
    ├── qa.md            # phase-scoped QA report
    ├── tasks.md         # phase-scoped task breakdown
    ├── implementation-notes.md # phase-scoped deviations log (optional)
    ├── review/          # optional, machine-readable review findings for this phase (reviewer agent)
    │   └── findings.json # lens findings scoped to this phase's review run
    ├── verify/          # optional, quality-gate evidence from inner-loop runs scoped to this phase
    ├── runtime/         # optional, runtime-gate evidence scoped to this phase (run-app, drive-app)
    ├── adr.md           # phase-scoped ADR (optional)
    ├── change-report.html # optional, phase-scoped variant of the change-digest report (derived, never committed)
    └── summary.md       # phase-scoped summary (optional)
```

---

## 4. Artifact path resolution

### 4.1 Phase-scoped (`PHASE_NUM` is set)

| Artifact | Path |
|---|---|
| Tasks | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md` |
| Implementation notes | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/implementation-notes.md` |
| Review findings | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/review/findings.json` — machine-readable lens findings from the `reviewer` agent, scoped to this phase's review run (`review.md` itself stays ticket-level — see 4.2) |
| Verify evidence | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/verify/` — quality-gate evidence from `inner-loop` runs scoped to this phase (ticket-wide variant: 4.2) |
| Runtime evidence | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/runtime/observation.md` (`run-app`) and `.../runtime/drive-observation.md` (`drive-app`), scoped to this phase (ticket-wide variant: 4.2) |
| Change report | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/change-report.html` — derived HTML change-comprehension report from the `change-digest` skill, scoped to this phase; regenerable, never committed (ticket-wide variant: 4.2) |
| PRD | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/prd.md` |
| Plan | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/plan.md` |
| Research | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/research.md` |
| QA | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/qa.md` |
| Summary | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/summary.md` |
| ADR | `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/adr.md` |
| Context (read-only) | `<specs.dir>/<TICKET_ID>/idea.md`, `<specs.dir>/<TICKET_ID>/vision.md` (find the Phase/Iteration `<PHASE_NUM>` section) |
| Inherited context (read-only) | The ticket-wide counterpart at `<specs.dir>/<TICKET_ID>/<artifact>.md` may be **read** for context, but is **never written** from a phase-scoped run. |

Create the `phase-<PHASE_NUM>/` subfolder lazily on first write.

### 4.2 Ticket-wide (`PHASE_NUM` is null)

| Artifact | Path |
|---|---|
| Tasklist | `<specs.dir>/<TICKET_ID>/tasklist.md` |
| Implementation notes | `<specs.dir>/<TICKET_ID>/implementation-notes.md` |
| PRD | `<specs.dir>/<TICKET_ID>/prd.md` |
| Plan | `<specs.dir>/<TICKET_ID>/plan.md` |
| Research | `<specs.dir>/<TICKET_ID>/research.md` |
| QA | `<specs.dir>/<TICKET_ID>/qa.md` |
| Summary | `<specs.dir>/<TICKET_ID>/summary.md` |
| Review | `<specs.dir>/<TICKET_ID>/review.md` |
| Review findings | `<specs.dir>/<TICKET_ID>/review/findings.json` — machine-readable lens findings from the `reviewer` agent (phase-scoped variant: 4.1) |
| Verify evidence | `<specs.dir>/<TICKET_ID>/verify/` — quality-gate evidence from `inner-loop` runs: `iteration-<i>.json` (fast check), `iteration-<i>-full.json` (full gate), `residual.json` (on stop-and-ask) (phase-scoped variant: 4.1) |
| Runtime evidence | `<specs.dir>/<TICKET_ID>/runtime/observation.md` — `RUNTIME_OK` evidence from `run-app`; `<specs.dir>/<TICKET_ID>/runtime/drive-observation.md` — UI-driving evidence from `drive-app` (phase-scoped variant: 4.1) |
| Change report | `<specs.dir>/<TICKET_ID>/change-report.html` — derived HTML change-comprehension report from the `change-digest` skill; regenerable, never committed (phase-scoped variant: 4.1) |
| ADR | `<specs.dir>/<TICKET_ID>/adr.md` |
| PR description | `<specs.dir>/<TICKET_ID>/pr-description.md` |
| PR pending | `<specs.dir>/<TICKET_ID>/pr-pending.md` — intended PR/tracker actions recorded by `pr-create` when an adapter identity check fails ([config.md](config.md)); ticket-wide only |
| Post-feedback | `<specs.dir>/<TICKET_ID>/post_feedback.md` |
| Design analysis | `<specs.dir>/<TICKET_ID>/design-analysis.md` (ticket-level only — never phase-scoped) |
| Design evidence | `<specs.dir>/<TICKET_ID>/design/` |
| Context | `<specs.dir>/<TICKET_ID>/idea.md`, `<specs.dir>/<TICKET_ID>/vision.md` |

---

## 5. Write rules (refuse-and-ask)

A phase-aware skill or agent that is about to **write** a per-artifact file (PRD, plan,
research, QA, tasks, summary, ADR) must follow this decision tree:

1. **If `PHASE_NUM` is set:** write to `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/<artifact>.md`.
   Create the `phase-<PHASE_NUM>/` folder if missing.

2. **If `PHASE_NUM` is null:**
   - Use `Glob` to check whether `<specs.dir>/<TICKET_ID>/phase-*/` exists.
   - **If no phase folder exists** → write to the ticket-wide path
     `<specs.dir>/<TICKET_ID>/<artifact>.md` (this is a brand-new ticket, or a project with
     `ticket.phaseSuffix: false`).
   - **If at least one phase folder exists** → **refuse and ask**. Do **not** write. Stop and
     return a message of the form:
     ```
     Cannot infer scope: ticket <TICKET_ID> already has phase folder(s) (<list of phase-N/ found>).
     The active phase per tasklist.md is: phase-<N>.
     Re-invoke as `<TICKET_ID>-<N>` for a phase-scoped run, or pass `--ticket-level` to write the ticket-wide <artifact> explicitly.
     ```
     The exact wording can vary, but it must (a) state the ambiguity, (b) name the discovered
     phase folders, (c) name the current phase from `tasklist.md` if discoverable, and (d) tell
     the user how to disambiguate.

3. **Explicit ticket-level intent:** A caller may force step 2's first branch by passing
   `--ticket-level` (or an equivalent prose instruction). When honored, write to the ticket-wide
   path even if phase folders exist. This is the escape hatch for updating the overall roadmap.

### Read fallback

Reading is more permissive than writing:

1. **Phase run reading its own artifact:** Try
   `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/<artifact>.md` first. If absent, the ticket-wide
   counterpart `<specs.dir>/<TICKET_ID>/<artifact>.md` may be read for context — but never
   written from this run.
2. **Ticket-wide run:** Reads only the ticket-wide path. Never silently merges phase content.

---

## 6. `.active_ticket` format

The active-ticket pointer lives at `<specs.dir>/.active_ticket`. A single non-empty line carries
a composite identifier in any of the forms section 1 accepts, canonicalized on write, e.g. with
the default pattern:

```
PROJ-2052-1
```

This sets both the ticket and phase (or just the ticket, when `ticket.phaseSuffix` is `false` or
no phase is active), so commands can be run without arguments.

If the file is missing or empty, callers must require an explicit identifier argument and error
out otherwise.

---

## 7. Phase scope rules

Applies only where `ticket.phaseSuffix` is `true`. When `PHASE_NUM` is specified:
- **Tasklist operations**: only work on tasks within that phase (Iteration N).
- **Code review**: scope to changes made in that phase.
- **QA**: generate QA plan for that phase only.
- **Summary**: create phase-specific summary.

When `PHASE_NUM` is null — including every run on a project with `ticket.phaseSuffix: false`:
- Work across all phases.
- Use ticket-wide artifacts only.
