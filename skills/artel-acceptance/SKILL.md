---
name: artel-acceptance
description: "Use when someone wants a ticket's acceptance criteria drafted quickly — reads the ticket and its discussion once, turns the requirements into numbered criteria with an observable check each, in the PRD's `## Requirements` shape, and stops there. Never interviews for a full PRD, never renumbers or restates a PRD's existing criteria, and never edits the ticket unless asked."
argument-hint: "[ticket-id] [--out <path>] [--comment]"
---

# Draft acceptance criteria

The fast pass over a ticket: what it must do, and how a person or a test sees that it does.

`/artel-analysis` answers the same question in depth — a PRD, a requirements interview, a vision.
This skill exists for the runs that never open one: a lean or bug run, a ticket that arrives
half-specified, or a review that needs something to check against. It reads once, asks at most six
questions, and produces one list.

**A worker, not an orchestrator.** No agent matches this job, so it runs inline like `sync-phases`
and `generate-idea` (`skills/README.md`), and it says so here rather than delegating.

## Prerequisites

One check each; nothing is drafted on a guess.

1. **The ticket is readable** — `tracker.adapter` names a source (`${ARTEL_ROOT}/docs/config.md`):
   `jira-mcp` answers `<tracker.mcpToolPrefix>jira_get_issue`; `github-issues` needs a passing
   `gh auth status`; `none` means the person supplies the text and this skill fetches nothing.
2. **The spec trail resolves** — `<specs.dir>` from the config, because a PRD that already exists
   changes what this skill may produce (step 4).

## 1. Resolve the ticket

Parse the argument into `TICKET_ID` per `${ARTEL_ROOT}/docs/orchestrator-common.md` §2 and
`${ARTEL_ROOT}/docs/ticket-parsing.md` §§1–2. With no argument, read the first non-empty line of
`<specs.dir>/.active_ticket`; with neither, ask for the ticket and stop. A phase suffix is ignored —
criteria are ticket-level — and the report says so.

## 2. Read the ticket — once

Fetch the issue, its description and its comments through the configured adapter, in one pass.
Comments matter: a decision stated in a reply is as binding as one in the description.

Then check whether a PRD already exists for this ticket — `<specs.dir>/<TICKET_ID>/prd/prd.md`,
phase-aware per `${ARTEL_ROOT}/docs/ticket-parsing.md` §§4–5, and through the store's own path when
`spec_store.py decision` says kartoteka (`${ARTEL_ROOT}/docs/spec-storage.md` §2.1). Do not read it
twice and do not read the whole trail: only its `## Requirements`.

## 3. Draft the criteria

The shape is the PRD's, because these criteria have to be usable by everything that reads a PRD
(`${ARTEL_ROOT}/docs/task-grammar.md` §2):

```text
- **R1** — A completed checkout shows a receipt with the order total.
  *Accepts when:* checking out with the test card shows the receipt with the total.
```

The entry's first line is one line, never wrapped. Use the next free number when a PRD already
defines some (step 4); otherwise start at `R1`.

Rules:

- **One behaviour per entry.** "The list loads and exports and paginates" is three criteria.
- **The check is observable.** It names what is seen — a screen, a value, an exit code, a log line —
  never how the code is built.
- **Only what the source states.** The description, the comments, the author's answers. Anything
  else is invented scope; leave it out and list it as an open question instead.
- **Twelve entries is the ceiling.** A ticket that needs more is a PRD: say so and point at
  `/artel-analysis` rather than drafting a second, thinner requirements document.
- **`language.docs`** (`${ARTEL_ROOT}/docs/config.md`) decides the language; identifiers, keys and
  paths stay verbatim.

## 4. A PRD that already exists

Its criteria are the truth, and its IDs are never renumbered, restated or reused — a second copy
with shifted numbers is worse than none (`${ARTEL_ROOT}/docs/task-grammar.md` §5). So the output
changes shape:

- **kept** — the PRD entry, and the ticket text that supports it;
- **gap** — the behaviour the ticket or its comments describe that no entry covers, drafted in the
  same shape with the next free ID;
- **extra** — a PRD entry the ticket does not support at all, reported without being edited.

Never write the PRD. Adding to it is `analysis`'s edit, under its own status rules.

## 5. The questions, at most six

Ask only what leaves a criterion unverifiable — an undefined limit, two readings of one sentence, a
role nobody named. Use the `question` tool, batch them, and give a recommended reading for each so
"no answer" still produces a list. An unanswered gap becomes an open question in the report, not a
guess in the criteria.

## 6. Report

Print the list as one fenced block, ready to paste into the ticket or hand to `/artel-analysis`.
Then state: the entries drafted, the open questions, and — when a PRD exists — the kept/gap/extra
mapping.

- `--out <path>` writes the same block to that path (a run directory such as
  `.artel/run/acceptance/<TICKET_ID>.md`, or wherever the person asked). It never writes into the
  spec trail: the PRD owns that file.
- `--comment` posts the block to the ticket. Show it, ask once with the `question` tool, and post
  only on an explicit yes. Never comment on your own.

## Hard rules

- NEVER invent a behaviour, a limit or a role the source does not state: a criterion nobody asked
  for becomes work nobody wants.
- NEVER renumber, rewrite or delete an existing PRD's criteria — report the gap instead.
- NEVER write the PRD, the tasklist or any spec-trail file; `analysis` owns the PRD.
- NEVER post to the ticket without an explicit confirmation, and never with `--comment` implied.
- NEVER draft more than twelve entries: past that, say it is a PRD and route to `/artel-analysis`.
- NEVER read the whole ticket trail twice in one run.
- NEVER present an unanswered question as a criterion.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Interviewing like `analysis` | One read, ≤6 questions, then the list |
| Writing criteria for the whole epic | Twelve entries is the ceiling; a bigger scope is a PRD |
| Renumbering a PRD's requirements | Keep its IDs; the new behaviour takes the next free one |
| A check that says "works correctly" | Name what is seen: a screen, a value, an exit code |
| Restating the description as one criterion | Split it: one behaviour per entry |
| Saving into `<specs.dir>` | `--out` takes a run path; the spec trail belongs to the PRD |
| Commenting on a ticket after drafting | Ask once, and only with `--comment` |
