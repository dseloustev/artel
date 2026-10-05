---
name: debugging
description: "Use in an artel-configured repo when something is broken and needs fixing — a failing test or build, a wrong result, a slowdown, anything that does not behave as it should — before proposing any fix, and when an earlier fix did not work. Finds the root cause first, reproduces it with a failing test through the repo's configured gate, makes one fix at the origin, and turns a structural cause into a ticket draft instead of patching it. Runs in the session; never commits. With --diagnose on a ticket it stops at the cause and writes the diagnosis that the ticket's work list is planned from."
argument-hint: "[symptom | failing test | error text] or <ticket-id> --diagnose [--local]"
---

Worker, not an orchestrator — no agent matches this job; it runs inline (like `knowledge`,
`sync-phases` and `setup`). Debugging needs the person and this session's context: the steps
that trigger the fault, the environment it happens in, the answer to "is that really not
happening?".

**The discipline is `${CLAUDE_PLUGIN_ROOT}/docs/debugging.md` — read it now, in full, before
anything else.** Its §1–§6 are this skill's process; what follows adds only what this repo
knows. With `--diagnose` the process stops before the fix: section 6.

## 1. Context

- Read `.artel/config.json` when it exists: `verify.fast`, `verify.test` and `knowledge.adapter`
  decide steps 2 and 3. No config → the host's own test command, from its conventions docs (its
  AGENTS.md and anything it points to); skip step 3.
- The symptom is `$ARGUMENTS`, or what the person described. Missing → ask once: what happened,
  what should have happened, and how to trigger it.

## 2. Reproduce through the repo's gate

The reproduction (`docs/debugging.md` §2.1) and the failing test (§2.4) run through the gate the
pipeline uses, so red and green here mean what they mean in a run:

    python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py task --files <the test file>

Run it from the repository root. With `verify.test` empty the `test` stage is `skipped`, and a
`skipped` stage is never red evidence: run the host's own single-file test command instead. A
fault no test can reach gets a reproduction script under `.artel/run/repro/`, which
`/artel:setup` already keeps out of git.

## 3. Ask the institutional record

With `knowledge.adapter: "kartoteka"`, add one search to the investigation: `Skill: knowledge`
with the error text or the component's name, for prior tickets, reviews and decisions on the
same area. Its search budget and its ⚠ NON-CURRENT rule apply. What comes back is evidence for
`docs/debugging.md` §2.1 and §2.2 — a place to look, a pattern that worked — never a fix to
apply. With the adapter off or absent, skip this step and say nothing about it. With `--local`
in the arguments, skip this step as well.

## 4. Structural cause

When `docs/debugging.md` §4 says the fix is structural — an interface change, a design
reversal, scope growth, or three failed fixes — stop without fixing. Report the cause and what
makes the proper fix structural, then ask via `AskUserQuestion`:

- **Draft a bug ticket** — `/artel:issue-draft <the report> --type bug`, when the fault is a
  defect whose right fix is larger than this session;
- **Draft a task ticket** — `/artel:issue-draft <the report> --type task`, when the right fix
  is a design change;
- **Leave it here** — the report stays in chat.

A drafted ticket goes through `/artel:feature-development` like any other.

## 5. Report

In chat; nothing on disk beyond the fix and its test:

- **Root cause** — one sentence: what is wrong and why.
- **Evidence** — what reproduced it, and the red result before the fix.
- **Fix** — the files changed, and why the change sits at the origin.
- **Proof** — the test that failed before and passes now, and the gate green.

## 6. Diagnose mode (`--diagnose`)

`/artel:debugging <ticket-id> --diagnose [--local]` is the head of a bug ticket in
`feature-development`, and it runs à la carte for a person who wants the cause on record before
anything is planned. It finds the cause and stops: **no fix, and nothing left in the tree.**

**The symptom is the ticket's.** Resolve `<ticket-id>` per
`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2 — the canonical key; a phase suffix is
ignored — and read `<specs.dir>/<TICKET_ID>/idea.md`. When it does not say how to trigger the
fault, ask once, as step 1 does.

**Spec store.** Before any read or write of a spec document, read the ticket's storage
decision: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`.
`fresh: true` → use its `store`. Anything else → resolve per
`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §2.1. Reads and writes follow its §4.1 and §4.2.

**The process.** Run `docs/debugging.md` §2.1–§2.3 — investigate until the fault happens on
demand, compare, and confirm one hypothesis — with steps 2 and 3 above as they are. Stop before
§2.4: the failing test and the fix are the first tasks of the run, not this skill's.

**Leave the tree as you found it.** Probes are undone (`docs/debugging.md` §2.3). A test written
to watch the fault fail is moved out of the tree to `.artel/run/repro/` and named in the
diagnosis, so the first task can start from it. `git status --porcelain` shows nothing this mode
added outside `.artel/`.

**Write `<specs.dir>/<TICKET_ID>/diagnosis.md`**, the one spec document this skill ever writes.
Its header follows `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §3.2: `type: diagnosis`,
`produced_by: artel:debugging`, and `status`. Its sections, in this order:

- `## Symptom` — what happens, and what should.
- `## Reproduction` — the exact steps or command, the red output's tail, and the test a fix
  must turn green, described precisely enough to write.
- `## Root Cause` — one sentence, and the file and symbol it sits in.
- `## Evidence` — what confirmed the hypothesis, and what ruled the others out.
- `## Fix Origin` — where the change belongs, and what must not change.
- `## Structural` — `no`, or which condition of `docs/debugging.md` §4 holds and why.

| Status | When |
|---|---|
| `DIAGNOSED` | the fault repeats on demand, the cause is confirmed, and the fix is local |
| `DIAGNOSED_STRUCTURAL` | the cause is confirmed and its proper fix is structural (§4). It is recorded, and step 4's ticket question is not asked: the ticket exists, and its caller moves it to the full pipeline |
| `NOT_REPRODUCED` | the fault would not repeat after `docs/debugging.md` §6's "cannot reproduce" was followed and the person was asked once for more. `## Root Cause` says `not established`; `## Evidence` lists what was examined and what it showed |

**An existing diagnosis.** `DIAGNOSED` or `DIAGNOSED_STRUCTURAL` → print its last line and stop;
a person who wants it redone says so. `NOT_REPRODUCED` → run again and replace it.

**Last line:** `Diagnosis: <status> — <specs.dir>/<TICKET_ID>/diagnosis.md`. Step 5's report is
not printed in this mode: the document is the report.

## Rules

- **Never commits, pushes or opens a PR.** The person decides what to keep.
- **Never touches the spec trail** — spec documents, the tasklist, the task queue, or a
  ticket's run state under `.artel/run/<TICKET_ID>/`. A paused run resumes through its own
  entry-point command, which re-runs its gate. The one exception is `diagnosis.md`, in diagnose
  mode.
- **Secrets stay out** of chat, reports and tests — `docs/debugging.md` §6.
- **Probes go** before the report — `docs/debugging.md` §3.
