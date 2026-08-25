# Orchestrator common contract

*Status: draft v0.1 · 2026-08-01*

Shared procedures for the `feature-development` and `dev` entry-point orchestrators. Both invoke
stage skills and agents and keep state in `<specs.dir>/.active_ticket` plus the ticket's artifact
tree under `<specs.dir>/<TICKET_ID>/` (see [ticket-parsing.md](ticket-parsing.md)) — orchestrators
never write code themselves. This is the contract every workflow skill in the plugin obeys:
resolve context, invoke the matching agent via the `Agent` tool, report — never inline the
agent's work (see [design.md](design.md), "the skill–orchestrator contract").

---

## 1. Contract

Orchestrators:

- Resolve the ticket identifier and parse it per [ticket-parsing.md](ticket-parsing.md) §§1-2,
  producing `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM`.
- Write the full identifier to `<specs.dir>/.active_ticket` at the top of the flow so downstream
  skills can read it without explicit arguments — and keep it phase-accurate: on multi-phase
  traversal, rewrite it to `<TICKET_ID>-<N>` at each phase start and advance it to the next
  incomplete phase after each phase checkpoint ([autonomous-run.md](autonomous-run.md) §15).
- Run the checkpoint commits & pushes ([autonomous-run.md](autonomous-run.md) §14): a
  planning/work-list checkpoint at arm time, and a phase-end checkpoint once a phase's
  `verify.commands` gate ([config.md](config.md)) passes. These are the only direct git mutations
  orchestrators perform; everything else external — opening or updating a pull request — goes
  through the `pr-create` skill and the configured `vcs.adapter` ([config.md](config.md)).
- Ensure the ticket directory `<specs.dir>/<TICKET_ID>/` exists.
- Check which artifacts already exist under it and skip gates that are already satisfied
  (skip-if-exists — [autonomous-run.md](autonomous-run.md) §9).
- Invoke stage skills via the `Skill` tool and agents via the `Agent` tool, passing the resolved
  identifier so the phase is preserved.
- **Index refresh** (optional): if the host project maintains a code-symbol index, refreshing it
  after implementation is an optional host hook, not an `.artel/config.json` key — the plugin
  defines no mechanics for it; the host wires one up itself (its own hooks or CLAUDE.md
  instructions). Silently absent when the host has not wired one up; nothing is reported as
  missing. **Refreshing is this bullet; querying is not.** How agents *read* an index that
  exists — the availability probe, the index-before-grep rule, staleness, and the grounding
  rule the anti-hallucination gates rest on — is [code-navigation.md](code-navigation.md),
  and that contract needs no host hook at all.
- **Knowledge consultation** (optional): when `knowledge.adapter` is `kartoteka`
  ([config.md](config.md)) and the kartoteka MCP tools are present in the session, the `analyst`
  and `researcher` agents consult the institutional-knowledge index before doing their own work.
  Unlike the index refresh above this one *is* an `.artel/config.json` key, because the index is
  single-project and an undeclared one must not be consulted. `analysis` and `researcher` accept
  `--local` to force a knowledge-free run, and `feature-development` accepts it and passes it down
  to both; `dev` does not, because it never invokes either of them. The contract is
  [knowledge-consultation.md](knowledge-consultation.md); it never writes, and it never blocks a
  gate.
- Report status to the user.
- Never write code themselves.

## 2. Ticket resolution

Parse the invocation argument per [ticket-parsing.md](ticket-parsing.md) §§1-2. If it is empty,
read the first non-empty line of `<specs.dir>/.active_ticket`; if that is also empty or missing,
error and terminate ([ticket-parsing.md](ticket-parsing.md) §6).

With the default `ticket.pattern` and `ticket.projectKey: "PROJ"` ([config.md](config.md)),
recognized inputs resolve as follows — see [ticket-parsing.md](ticket-parsing.md) §1 for the full
table and the general grammar behind a customized pattern:

| Input | TICKET_ID | TICKET_NUM | PHASE_NUM |
|-------|-----------|------------|-----------|
| `PROJ-123-2` | `PROJ-123` | `123` | `2` |
| `123-2` | `PROJ-123` | `123` | `2` |
| `PROJ-123` | `PROJ-123` | `123` | `null` |
| `123` | `PROJ-123` | `123` | `null` |

## 3. Artifact paths (phase-aware)

All paths are rooted at `<specs.dir>/<TICKET_ID>/`. Phase-scoped artifacts live in a
`phase-<PHASE_NUM>/` subfolder; ticket-wide artifacts stay at the top. Authoritative table:
[ticket-parsing.md](ticket-parsing.md) §4 — orchestrators do not duplicate it here, only invoke
skills and agents with the resolved identifier and let each resolve its own paths.

**Refuse-and-ask rule:** a write with no `PHASE_NUM` against a ticket that already has
`phase-*/` folders must stop and ask the user to disambiguate — re-invoke as `<TICKET_ID>-<N>`
for a phase-scoped run, or pass `--ticket-level` for explicit ticket-wide intent. See
[ticket-parsing.md](ticket-parsing.md) §5.

## 4. Description file sync

When the user passes a description file as an argument:

1. Read it.
2. If it is not a phase-style file and has no checkbox tasks (`- [ ]` / `- [x]`), skip — nothing
   to sync.
3. Read the completed tasklist from `<specs.dir>/<TICKET_ID>/`: phase-scoped
   `phase-<PHASE_NUM>/tasks.md`, or ticket-wide `tasklist.md`.
4. Compare: for each task listed in the description file, verify it is marked `- [x]` in the
   tasklist.
5. If any described task is not completed: report `Error: The following tasks from the
   description file were not completed: <list>` and terminate.
6. If all are completed: flip every `- [ ]` in the description file to `- [x]` and report
   `Updated <filename>: marked N tasks as complete`.

The description-file sync is always the last step — run only after the rest of the pipeline has
finished cleanly.

## 5. Autonomous run contract

Both orchestrators run autonomously by default. The run-state file, question bundling, capped
loops, AFK/HITL tags, the `--step` compatibility mode, and the Stop-hook contract are defined in
[autonomous-run.md](autonomous-run.md) — read it before executing either orchestrator. That
contract adds two orchestrator-written files to the artifact list above:
`.artel/run/<TICKET_ID>/run-state.json` and status flips in
`.artel/run/<TICKET_ID>/open-questions.md` — host-writable run state, kept separate from the
human-readable spec trail (see [autonomous-run.md](autonomous-run.md)'s introduction).

Orchestrators surface, but do not implement, the deviation protocol — the escalation rule for
implementation-time divergences from the approved plan/tasklist, defined in
[deviation-protocol.md](deviation-protocol.md) (see also [autonomous-run.md](autonomous-run.md) §1).
