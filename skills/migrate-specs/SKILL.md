---
name: migrate-specs
description: "Move a ticket's local spec trail — or every ticket's — into kartoteka, the project's spec store: upload what is missing or newer, never let a stale copy overwrite a newer stored one, show real conflicts, then delete the local copies kartoteka verifiably holds. Use when the user wants local specs uploaded, synced or cleaned up, when a run reports spec documents on disk, or after working locally while kartoteka was down."
argument-hint: "[<ticket-id>… | --all] [--pending-only] [--no-prompt]"
model: sonnet
---

Worker, not an orchestrator — it runs inline. The contract is
`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.3 and §7. Every step is one command; document
contents never pass through this conversation except a conflict's diff.

`SPEC_STORE` below is `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py`.

## 1. Resolve

- **Scope**: the ticket ids given (any accepted spelling; a phase suffix is ignored — the trail is
  ticket-wide), or `--all` for every ticket directory under `<specs.dir>` and
  `.artel/context/tickets/`. With neither, use `<specs.dir>/.active_ticket`; with no pointer
  either, stop: "Name a ticket, or pass --all."
- **Gate**: `knowledge.adapter` must be `kartoteka` (`.artel/config.json`). Otherwise stop:
  "This project keeps its spec trail as files (knowledge.adapter is <value>); there is nothing to
  migrate to." — and point at `/artel:setup`.
- `--pending-only` limits the run to documents saved locally during an outage (the decision
  file's `pending`). Orchestrators pass it on resume.
- `--no-prompt` (headless): conflicts are skipped and deletion needs no confirmation for
  `--pending-only` only. A full migration without prompts stops after step 3 and reports what it
  would delete.

## 2. Plan

    SPEC_STORE migrate plan <tickets…|--all> [--pending-only]

- Exit `5` → kartoteka is not available: report the printed reason and stop. Nothing was
  written.
- Exit `2` → report the error and stop.

Otherwise present the summary, per ticket: how many documents are **absent** (will upload),
**successor** (newer than the store — will upload), **current** / **stale** (the store already
holds this or something newer — nothing to upload), **conflict**, and **skipped** (with the
reason).

An empty plan: "No spec documents on disk for <scope>." — stop.

## 3. Conflicts

For each `conflict` item, unless `--no-prompt`: show its `reason` and its `diff` (already capped
by the script), then ask (`AskUserQuestion`), one question per document:

- **Keep local** → `--resolve <logical>=keep-local`. When the reason says the two local copies
  differ, offer one option per source instead:
  `--resolve <logical>=keep-local:<source>`.
- **Keep stored** → `--resolve <logical>=keep-stored` (the local copy is obsolete).
- **Skip** → `--resolve <logical>=skip`: leave both alone and keep the local copy.

A `keep-local:<source>` naming a path that is not one of that document's own local copies is
refused before anything uploads (exit `2`, kind `invalid_argument`) — offer only the sources the
plan listed for that document.

## 4. Apply

    SPEC_STORE migrate apply <tickets…|--all> [--pending-only] [--resolve …]…

Report `uploaded` (document → new version) and every `failed` entry with its reason. A failure is
never retried silently: a `moved to v<N> during the migration` failure means someone wrote
meanwhile — say so and suggest running the skill again.

`apply` flips a ticket's storage decision to kartoteka only when its **whole** local trail has
nothing left behind — no unresolved conflict, no item skipped or failed — and reports which
tickets it flipped as `flipped`. A ticket left with anything outstanding keeps its previous
decision untouched: a run that had been working locally keeps doing so until a later, fully
clean migration flips it.

## 5. Delete

`deletable` lists every local file kartoteka now verifiably holds — its own copy or a newer one —
plus those the user chose to discard.

- **`--pending-only`**: delete without asking — the user allowed these to be kept locally only
  until kartoteka was back:

      SPEC_STORE migrate delete <ticket> --pending-only [--resolve …]…

- **Otherwise**, ask once (`AskUserQuestion`), listing the files by ticket and naming the current
  branch (`git branch --show-current`):
  - **Delete and commit** → `SPEC_STORE migrate delete <…> [--resolve …]… --commit` — one commit,
    `chore: move <tickets> spec trail to kartoteka`, holding only these deletions.
  - **Delete, leave staged** → the same without `--commit`.
  - **Keep local copies** → run nothing. The next run on each ticket reports them again.

  Under `--no-prompt`, report the list and stop without deleting.

**Never deleted, whatever is chosen:** `.active_ticket`; gate evidence (`review/findings.json`,
`verify/`, `runtime/`, `design/`); `change-report.html`; `pr-pending.md`; release-scope files
under `<specs.releases>`; and anything skipped, unresolved or unverified. Say so in the report
rather than leaving it to be inferred.

## 6. Report

Per ticket: uploaded (name → version), already stored, deleted (and the commit, if any), and kept,
with the reason for each. Close with any remaining conflict or skipped file and what to do about
it. Other worktrees hold their own `<specs.dir>`: run the skill inside each one to migrate its
trails.

Do not run this while another session is actively working the same ticket.
