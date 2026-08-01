---
name: change-digest
description: "Generate a self-contained HTML change-comprehension report for a ticket's branch work — context, what was done and why, key decisions and deviations, module-by-module breakdown — ending with an interactive quiz the reader must complete to confirm understanding. Use whenever the user wants to understand, recap, review, or absorb what happened in a change, branch, or ticket ('what happened in this change', 'explain what was done on this branch', 'give me a report on the changes', or an equivalent request in another language), or asks for a change/branch report or a quiz on the changes — even if they don't say 'HTML' or name a ticket."
argument-hint: "[ticket-id or ticket-id-phase] (optional; defaults to <specs.dir>/.active_ticket)"
---

# change-digest

Produce **one self-contained HTML file** that lets a reader deeply understand the work done on the
current branch for a ticket: the context it started from, what was actually built and why, the
decisions and deviations along the way — and, at the bottom, an interactive quiz the reader must
complete to confirm they absorbed it. The reader is typically the developer or reviewer who wants to
*own* the change, not skim it. Everything below serves that goal: the report must teach, and the quiz
must test what actually matters.

## 1. Resolve the ticket

- If an argument was passed, parse it per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and
  `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2.
- Otherwise read `<specs.dir>/.active_ticket` (single line; may carry a phase suffix).
- If neither yields a ticket, stop and ask the user for one — do not guess from the branch name.
- If `PHASE_NUM` is set but `<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/` does **not** exist,
  proceed ticket-wide and say so in the report header. Branch names sometimes carry a series
  number (e.g. `feature/<TICKET_ID>-5-...` on a single-phase ticket) that is not a spec phase —
  don't invent a phase folder for a derived artifact.

## 2. Read the ticket documentation

Read from `<specs.dir>/<TICKET_ID>/` — everything that exists, silently skipping what doesn't.
Each file answers a different question the report needs:

| File | What to extract |
|------|-----------------|
| `idea.md`, `prd.md` | The **why**: problem, user-facing goal, requirements |
| `vision.md`, `plan.md`, `adr.md` | Intended design, key decisions, **alternatives rejected** (prime quiz material) |
| `research.md`, `open-questions.md` | Constraints discovered, how open questions were resolved |
| `tasklist.md` (phase run: `phase-<N>/tasks.md`) | What was actually done, per-section notes, ratified deviations |
| `implementation-notes.md`, `review.md`, `qa.md`, `summary.md` | Deviations from plan, review findings and their fixes, verification status |

On a phase-scoped run, prefer `phase-<N>/` files and use ticket-wide files as context (read
fallback per `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §5).

The gap between *planned* and *shipped* is the most valuable content in these files — deviations,
overridden questions, and review-driven fixes capture decisions that exist nowhere else. Hunt for
them explicitly.

## 3. Analyze the branch changes

Docs describe intentions; the diff is what happened. Read enough of the actual diff to write with
specifics — real class, method, and file names, real behavior. A report written only from the docs
is a summary of promises, not of the change.

- Determine the repo's default branch first (e.g. via `git symbolic-ref refs/remotes/origin/HEAD`,
  or ask if ambiguous — the same resolution `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone mode uses); call it
  `<default-branch>` below.
- Establish scope: `git diff <default-branch>...HEAD --stat` and `git log <default-branch>..HEAD --oneline`.
- Exclude workflow noise from the *code* analysis: `<specs.dir>`, `.artel/`, `CLAUDE.md`, and any
  files the host repo marks as generated (analyzer/linter exclusion lists, generated-file
  headers). Focus on the host's own source and test directories, plus `CHANGELOG.md`
  (user-visible).
- **Stacked branches:** check the log for merge commits that pull in other tickets' branches
  (branches named after `<ticket.projectKey>-<N>`). When present, the diff vs the default branch
  contains other tickets' work. The core narrative covers *this ticket's* commits and files —
  cross-check commit subjects and the file lists in `tasklist.md` — with one short "inherited from
  stacked branches" subsection for the rest. Never silently blend them; the reader must know which
  changes this ticket owns.
- For large diffs, work module-by-module (`git diff <default-branch>...HEAD -- <path>/`): read
  full diffs of the files central to the ticket, stat-level only for mechanical renames and churn.
- On a phase-scoped run, scope the narrative to that phase's commits (subjects usually name the
  phase) and its `tasks.md` file list; mention the surrounding phases only as context.

## 4. Write the report

Start from `assets/report_template.html` (relative to this skill directory): read it, replace every
`{{...}}` placeholder, and fill the marked sections. The template carries the styling and the quiz
engine so you spend your effort on content, not plumbing.

Sections, in order:

1. **Header** — ticket ID + title, branch, commit count, files/insertions/deletions, scope note
   (ticket-wide / phase / stacked-branch caveat).
2. **Context — why this change exists** — the problem and goal, from `idea.md`/`prd.md`, in a few
   tight paragraphs.
3. **What was done** — the narrative of the change, grouped by concern (whatever concerns the host
   project actually has — e.g. data layer, domain, UI, routing), connecting plan to diff: what was
   built, how it works, with real identifiers.
4. **Key decisions & deviations** — each decision as: what was chosen, what was rejected, and *why*.
   Include ratified plan deviations and review-driven changes.
5. **Change map** — a table of the meaningful changed files/modules with a one-line "what changed
   and why" each. Group mechanical churn into single rows; don't pad with dozens of rows of noise.
6. **Risks & follow-ups** — anything the docs or diff flag as deferred, fragile, or worth watching.
7. **Quiz** — see below.

Writing bar: explain *why*, not just *what*; call out the non-obvious; every claim traceable to the
diff or a doc — never invent. Prefer prose a teammate would enjoy reading over bullet dumps.
Audience knows the codebase but didn't watch this work happen.

**Language:** write the report and quiz in `language.docs` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`).
An explicit language instruction from the user for this invocation overrides it for this run only,
per config.md's precedence rules.

## 5. Write the quiz

Fill the template's `QUIZ_DATA` array with **6–10 multiple-choice questions** (4 options each, one
correct). The quiz is the comprehension gate, so questions must test substance:

- why a decision was made, and what was rejected (ADR alternatives make perfect distractors);
- what behavior changed for the user;
- how a deviation differs from the original plan;
- edge cases and guards the change introduced.

Never ask trivia (file counts, commit hashes, dates). Distractors must be *plausible* — real
alternative designs or subtly wrong statements, not obvious throwaways. Every question carries an
`explain` string shown after checking, so a wrong answer still teaches. The template enforces
"must complete": checking is disabled until every question is answered.

## 6. Deliver

- Output path: `<specs.dir>/<TICKET_ID>/change-report.html`; on a phase-scoped run where the
  phase folder exists, `<specs.dir>/<TICKET_ID>/phase-<N>/change-report.html`. It's a derived,
  regenerable artifact — overwrite a previous one without asking, and never commit it.
- Sanity-check the written file: no `{{` placeholder remains, `QUIZ_DATA` is non-empty, and every
  `<script>`/`<link>` is inline (no external URLs — the file must render offline).
- Open it for the user: `open <path>` (macOS). Skip opening when running headless or as a subagent.
- Close with a short chat message: the file path, three-to-five bullets of the essence, and a nudge
  that the quiz at the bottom is the comprehension check.
