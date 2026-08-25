# Deviation protocol

*Status: draft v0.1 · 2026-08-01*

How implementation-time deviations from an approved plan are recorded, escalated, and reviewed.
Referenced by the `implementer` agent and skill (writers), the `reviewer` agent (verifier), and
the `dev` / `feature-development` orchestrators (reporting). Do not duplicate these rules in
agent prompts — link to this file instead.

---

## 1. Baseline

A **deviation** is any divergence between what gets implemented and the **approved proposal**
for the current task — plus the plan / tasklist / vision where those artifacts exist. The
approved proposal is the anchor because it exists in both the full `feature-development`
pipeline and the lean `dev` loop (which may have no `plan.md`).

The protocol applies **after** approval, during implementation. Conflicts discovered while
drafting a proposal are surfaced in the proposal itself, not here.

## 2. Severity

| Severity | Criteria | Action |
|----------|----------|--------|
| **Minor** | All of: no scope change; no public API / DB schema / DI wiring change beyond the task; no new dependency; no impact on other tasks. Examples: analyzer- or conventions-forced rename or restructure; the plan referenced a moved/renamed file or symbol but the intent is unchanged; a missing null-guard the plan didn't anticipate. | Choose the most conservative option, record an entry (§3), continue working. |
| **Major** | Any of: the approach can't work as planned (won't compile, API doesn't exist, architectural conflict); touches public API, DB schema, or DI wiring beyond the task's stated scope; requires a new dependency; contradicts `vision.md` / `prd.md`; invalidates or reorders other tasks. | Halt **before** applying the deviating change and escalate (§4). |
| **Unsure** | — | Treat as major. |

## 3. The notes file — `implementation-notes.md`

Path resolution mirrors the tasklist ([ticket-parsing.md](ticket-parsing.md) §4): use the same
scope as the tasklist the current task came from. When no tasklist exists (ad-hoc `dev` runs),
resolve by `PHASE_NUM` per that document's §4.

- Phase-scoped: `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/implementation-notes.md`
- Ticket-wide: `<specs.dir>/<TICKET_ID>/implementation-notes.md`

Created lazily on the first deviation — **no file means a clean run**. Entries are append-only
and numbered sequentially (`D1`, `D2`, …).

Format:

```markdown
# Implementation Notes — <TICKET_ID>[ — Phase <PHASE_NUM>]

## Deviations

### D1: <short title> (Task N — minor|major)
- **Planned:** <what the approved proposal / plan said>
- **Actual:** <what was done instead>
- **Why:** <the edge case that forced the deviation>
- **Decision:** conservative default | user decision: "<answer>" | task aborted for re-planning
```

## 4. Escalation handshake (major deviations)

The implementer agent never prompts the user directly — the orchestrator owns user interaction.

1. **Agent halts** before applying the deviating change and returns a `DEVIATION` report
   *instead of* a completion:

   ```markdown
   DEVIATION — Task N: <task title>
   - **Planned:** <what the approved proposal said>
   - **Blocked by:** <the edge case / why it can't proceed as planned>
   - **Already applied:** <changes made so far in this task, or "none">
   - **Options:**
     1. <option> (recommended — <one-line why>)
     2. <option>
   - **Recommendation:** 1
   ```

2. **The orchestrating skill asks the user** via `AskUserQuestion`: one option per report
   option — the agent's recommendation first, labeled "(Recommended)" — plus an **Abort task**
   option (stop this task for re-planning). Free-form alternatives arrive via the built-in
   "Other".

3. **The skill resumes the agent** via `SendMessage` with the decision.

4. **The agent records** the entry (§3) with the user's decision, then:
   - a way forward was chosen → continue implementing accordingly;
   - **Abort task** → leave the task checkbox unchecked, record
     `Decision: task aborted for re-planning`, and return control, including the
     recorded deviation in the returned message so the orchestrator can report it.

If a report is malformed (missing Planned / Blocked by / Options), the orchestrator asks the
agent to re-emit it before involving the user.

## 5. Completion contract

Every implementer completion message ends with a `Deviations:` line:

- `Deviations: none`
- `Deviations: D1 (minor), D2 (major)`

Orchestrators (`dev`, `feature-development`) aggregate these lines and always include a
deviations line in their final report — the user must never have to open the notes file to
learn that something diverged.

The line closes a short contract, not a narrative: the completion names the task, the changed
paths and a `Report:` path, and the diff, evidence and the deviations' detail live in that
report (`.artel/run/<TICKET_ID>/reports/`, [autonomous-run.md](autonomous-run.md) §1). The
`Deviations:` line is the one piece of the detail that must also travel in the message.

## 6. Review duties

In ticket mode the `reviewer` agent:

- Reads `implementation-notes.md` (missing file = no deviations recorded — not an error).
- Verifies each entry is justified and matches the actual diff.
- The approved proposal itself is not persisted; judge against the plan / tasklist artifacts
  that exist and the entry's own **Planned** text.
- Flags **undocumented** deviations — the diff diverges from the plan/proposal with no
  corresponding entry — as **Important**.
