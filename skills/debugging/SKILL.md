---
name: debugging
description: "Use in an artel-configured repo when something is broken and needs fixing — a failing test or build, a wrong result, a slowdown, anything that does not behave as it should — before proposing any fix, and when an earlier fix did not work. Finds the root cause first, reproduces it with a failing test through the repo's configured gate, makes one fix at the origin, and turns a structural cause into a ticket draft instead of patching it. Runs in the session; never commits."
argument-hint: "[symptom | failing test | error text]"
---

Worker, not an orchestrator — no agent matches this job; it runs inline (like `knowledge`,
`sync-phases` and `setup`). Debugging needs the person and this session's context: the steps
that trigger the fault, the environment it happens in, the answer to "is that really not
happening?".

**The discipline is `${CLAUDE_PLUGIN_ROOT}/docs/debugging.md` — read it now, in full, before
anything else.** Its §1–§6 are this skill's process; what follows adds only what this repo
knows.

## 1. Context

- Read `.artel/config.json` when it exists: `verify.fast`, `verify.test` and `knowledge.adapter`
  decide steps 2 and 3. No config → the host's own test command, from its conventions docs (its
  CLAUDE.md and anything it points to); skip step 3.
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
apply. With the adapter off or absent, skip this step and say nothing about it.

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

## Rules

- **Never commits, pushes or opens a PR.** The person decides what to keep.
- **Never touches the spec trail** — spec documents, the tasklist, the task queue, or a
  ticket's run state under `.artel/run/<TICKET_ID>/`. A paused run resumes through its own
  entry-point command, which re-runs its gate.
- **Secrets stay out** of chat, reports and tests — `docs/debugging.md` §6.
- **Probes go** before the report — `docs/debugging.md` §3.
