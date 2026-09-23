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
- `--no-prompt` (headless): every conflict is left unresolved — no `--resolve` is passed for it,
  so its local copy is skipped, never uploaded — and the run still proceeds through step 4:
  `apply` uploads every `absent`/`successor` item regardless, needing no confirmation (an upload
  is append-only, guarded by `expected_version`, and verified before it counts), and flips any
  ticket whose whole local trail turns out fully migrated. Only step 5's deletion still needs a
  person: it runs unconfirmed under `--pending-only` (as always); without `--pending-only`, it
  reports the `deletable` list and stops without deleting anything.

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

Items are per local copy: when a document's working-tree copy and its `.artel/context` copy
differ, the plan lists each on its own, and a copy kartoteka already holds is `current` or
`stale` whatever the other copy is.

Unless `--no-prompt`, ask (`AskUserQuestion`) one question per document — per `logical` — that
has a `conflict` item. Show each conflicting copy's `sources`, `reason` and `diff` (already
capped by the script), then offer:

- **Keep local** → `--resolve <logical>=keep-local@<N>`. When the document has two or more
  conflicting copies (their reason starts `the local copies differ`), offer one option per copy
  instead, naming its source: `--resolve <logical>=keep-local:<source>@<N>` — a plain
  `keep-local` is refused there. The chosen copy is uploaded; once kartoteka holds it, the
  document's other local copies are deletable too.

  A conflict whose reason says a copy **was not read** has only one copy to offer — plain
  `keep-local@<N>` for it, same as any other single-copy conflict. Say so when asking: the unread
  copy is never uploaded and never deleted, whichever way this one resolves.

  **Always pass `@<N>`**, the item's `newest_version` (`0` when it is null): the answer belongs
  to the version the user was shown. If kartoteka has moved on by the time `apply` runs, the
  upload is refused and reported as `failed` — `kartoteka moved from v<N> to v<M> since you
  decided; run migrate-specs again` — instead of overwriting a version nobody saw.
- **Keep stored** → `--resolve <logical>=keep-stored` (the local copies are obsolete). Offer it
  only when the item's `newest_version` is not null: with nothing stored there is no stored copy
  to keep, and the script refuses it (exit `2`, kind `invalid_argument`).
- **Skip** → `--resolve <logical>=skip`: leave both alone and keep the local copies.

A `keep-local:<source>` naming a path that is not one of that document's own local copies, or one
that was skipped, is refused before anything uploads (exit `2`, kind `invalid_argument`) — offer
only the conflicting sources the plan listed for that document.

A conflict whose `diff` is `null` touches a **redacted** version: kartoteka removed text from
this document's history, so no diff is printed — the local copy may still carry it. Say so, give
the `reason`, and let the user open the file themselves before answering.

## 4. Apply

    SPEC_STORE migrate apply <tickets…|--all> [--pending-only] [--resolve …]…

- Exit `5` → kartoteka went down during the migration: report the printed reason and stop.
  Whatever had been uploaded is in kartoteka and verified, and no local copy was deleted, so
  running the skill again when the store is back is safe.
- Exit `2` → report the error and stop.

Report `uploaded` (document → new version) and every `failed` entry with its reason. A failure is
never retried silently: a `moved to v<N> during the migration` or `moved from v<N> to v<M> since
you decided` failure means someone wrote meanwhile — say so and suggest running the skill again.
One document kartoteka refuses (too large for its limit, say) fails on its own; the rest of the
run still moves.

`pending_left` counts the outage saves still on disk per ticket, after entries whose file is gone
were dropped. `0` for a ticket means that outage is drained.

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

  `delete` answers the same exits as `apply`: `5` (report the reason and stop — nothing was
  deleted) and `2`. With `--commit`, a `commit` of `null` means no commit was made — because no
  tracked file was removed, or because git refused: the deletions are **staged**, say so rather
  than reporting a commit. Every `kept` entry names its `logical` document (and the `source`
  file when one file in particular could not go) with the reason: a copy that changed since it
  was classified, or a tracked file with staged content kartoteka does not hold, is kept.

  Under `--no-prompt` without `--pending-only`, apply has already run — report the `deletable`
  list and stop without deleting.

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
