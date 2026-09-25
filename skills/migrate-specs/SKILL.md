---
name: migrate-specs
description: "Move a ticket's local spec trail — or every ticket's — into kartoteka, the project's spec store: upload what is missing or newer, never let a stale copy overwrite a newer stored one, show real conflicts, then delete the local copies kartoteka verifiably holds. Use when the user wants local specs uploaded, synced or cleaned up, when a run reports spec documents on disk, or after working locally while kartoteka was down."
argument-hint: "[<ticket-id>… | --all] [--pending-only] [--no-prompt]"
model: sonnet
---

Worker, not an orchestrator — it runs inline. The contract is
`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §5.3 and §7. Every step is one command. Document
contents never pass through this conversation, except a conflict's diff. Image bytes never do.

A trail holds spec **documents** and **images**. An image is any `*.png|jpg|jpeg|gif|webp` under
the ticket directory: `design/`, `runtime/`, `phase-<N>/runtime/`, anywhere.
- This skill moves the images git tracks under `<specs.dir>/<TICKET_ID>/`, and every image in
  `.artel/context/tickets/<TICKET_ID>/spec-trail/`.
- An untracked image in the working tree is not this skill's to move. The orchestrators' sweep
  (`spec_store.py image sync`) moves it.
- Every `plan`, `apply` and `delete` entry about an image carries `kind: "image"`. A document's
  entry carries no `kind`.

`SPEC_STORE` below is `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py`.

## 1. Resolve

- **Scope**:
  - the ticket ids given, in any accepted spelling. A phase suffix is ignored, because the trail
    is ticket-wide;
  - or `--all`, for every ticket directory under `<specs.dir>` and `.artel/context/tickets/`;
  - with neither, `<specs.dir>/.active_ticket`. With no pointer either, stop: "Name a ticket, or
    pass --all."
- **Gate**: `knowledge.adapter` must be `kartoteka` (`.artel/config.json`). Otherwise stop:
  "This project keeps its spec trail as files (knowledge.adapter is <value>); there is nothing to
  migrate to." — and point at `/artel:setup`.
- `--pending-only` limits the run to documents saved locally during an outage (the decision
  file's `pending`). Orchestrators pass it on resume. Images are never pending, so such a run
  touches none.
- `--no-prompt` (headless): every conflict is left unresolved, and the run still proceeds through step 4:
  - no `--resolve` is passed for a conflict, so its local copy is skipped, never uploaded;
  - `apply` uploads every `absent`/`successor` item and needs no confirmation. An upload is
    append-only, guarded by `expected_version`, and verified before it counts;
  - `apply` also flips any ticket whose whole local trail turns out fully migrated;
  - only step 5's deletion still needs a person. It runs unconfirmed under `--pending-only`, as
    always. Without `--pending-only`, it reports the `deletable` list and stops without deleting
    anything.

## 2. Plan

    SPEC_STORE migrate plan <tickets…|--all> [--pending-only]

- Exit `5` → kartoteka is not available: report the printed reason and stop. Nothing was
  written. A daemon older than kartoteka 0.44.0 answers this way, with the reason
  `the kartoteka daemon predates attachments (0.44.0); upgrade it`.
- Exit `2` → report the error and stop.

Otherwise present the summary per ticket. Document counts come from `summary`, image counts from
`image_summary`:
- **absent**: will upload;
- **successor**: newer than the store, so will upload. For an image, that means a working-tree
  copy written after kartoteka's newest version;
- **mergeable**: made from an older stored version (its header's `version:` is behind the
  store). `apply` merges it with the store: a clean merge uploads, a conflicting one comes back
  for a decision (step 4). Say how many, and that no conflict is resolved silently.
- **current** / **stale**: the store already holds this or something newer, so nothing to upload;
- **conflict**;
- **skipped**, with the reason.

An item with `legacy: true` has no usable header; it is classified as before the header existed.
Mention the count, nothing more.

An image skipped as `outside kartoteka's image path grammar` has a name kartoteka cannot store:
a space or another character that is not a letter, digit, `.`, `_` or `-`, or more than four
levels below the ticket directory. It can never move, and it never holds up the rest of the
migration. Name it, and tell the user to rename it (`git mv` for a tracked one) and run the
skill again.

An empty plan: "No spec documents or images on disk for <scope>." — stop.

## 3. Conflicts

Items are per local copy. When a document's or an image's working-tree copy and its
`.artel/context` copy differ, the plan lists each on its own. A copy kartoteka already holds is
`current` or `stale` whatever the other copy is.

Unless `--no-prompt`, ask (`AskUserQuestion`) one question per document or image — per
`logical` — that has a `conflict` item, or a `mergeable` item that `apply` reported in
`conflicted`. For a document, show each conflicting copy's `sources`,
`reason` and `diff`; the script has already capped the diff. For an image, see
**Image conflicts** below. Then offer:

- **Keep local** → `--resolve <logical>=keep-local@<N>`. The chosen copy is uploaded, and once
  kartoteka holds it, the address's other local copies are deletable too.
  - Two or more conflicting copies, where each reason starts `the local copies differ`: offer one
    option per copy instead, naming its source: `--resolve <logical>=keep-local:<source>@<N>`. A
    plain `keep-local` is refused there.
  - A conflict whose reason says a copy **was not read** has only one copy to offer: plain
    `keep-local@<N>` for it, as for any other single-copy conflict. Say so when asking: the unread
    copy is never uploaded and never deleted, whichever way this one resolves.
  - **Always pass `@<N>`**, the item's `newest_version`, or `0` when it is null. The answer
    belongs to the version the user was shown. If kartoteka has moved on by the time `apply`
    runs, the upload is refused and reported as `failed` — `kartoteka moved from v<N> to v<M>
    since you decided; run migrate-specs again` — instead of overwriting a version nobody saw.
- **Keep stored** → `--resolve <logical>=keep-stored`: the local copies are obsolete. Offer it
  only when the item's `newest_version` is not null. With nothing stored there is no stored copy
  to keep, and the script refuses it (exit `2`, kind `invalid_argument`).
- **Skip** → `--resolve <logical>=skip`: leave both alone and keep the local copies.

A `keep-local:<source>` is refused before anything uploads (exit `2`, kind `invalid_argument`)
when it names a path that is not one of that address's own local copies, or one that was
skipped. Offer only the conflicting sources the plan listed for it.

A document conflict whose `diff` is `null` touches a **redacted** version. kartoteka removed text
from this document's history, so no diff is printed: the local copy may still carry it. Say so,
give the `reason`, and let the user open the file themselves before answering.

### Merge conflicts

For each `conflicted` entry of `apply` (step 4) whose `merge_file` is not null, the marked text
is there (`<logical>.merge`, beside the document's path), with `local` / `kartoteka v<B>` /
`kartoteka v<S>` sections. Ask:

- **Keep merged** → the user edits the markers out of `merge_file` first, then
  `--resolve <logical>=keep-merged@<merged_against>`. Refused while any marker line remains.
- **Keep local** → `--resolve <logical>=keep-local@<newest_version>`: the local copy wins whole.
- **Keep stored** → `--resolve <logical>=keep-stored`.
- **Skip** → `--resolve <logical>=skip`.

`newest_version` is the version kartoteka holds now; `merged_against` is the version the merge file
was made against (the same, unless the entry is `stale`). Never edit `merge_file` for the user
unless they ask.
Under `--no-prompt` the entry stays unresolved: nothing with markers is ever uploaded.

A `conflicted` entry with `merge_file: null` means git itself could not merge the three copies —
most often a missing `git` — and names why in its `reason`. There is nothing to edit: offer keep
local, keep stored or skip, never keep-merged.

An entry with `stale: true` was merged against an older stored version than kartoteka now holds
(its `note` names both). Its `keep-merged` would be refused. Say so, and offer: delete
`merge_file` and run the skill again (a fresh merge against the newest version), **Keep local**,
**Keep stored** or **Skip**.

A plan item already showing a `merge_file` while still `mergeable` means an earlier `apply` wrote
it and a later one left it alone rather than overwrite it: treat it as already `conflicted`, go
straight to this section, and either resolve the existing file (edit its markers out, then
`keep-merged@<merged_against>`) or delete it and run `apply` again to have it remerged from scratch. Once an
address has nothing left open, a repeated `keep-merged` with the same arguments is accepted as a
no-op — safe to re-run `delete` or `apply` after a resume.

### Image conflicts

An image conflict never has a diff: its `diff` is always `null`, redacted or not. Show these
instead:
- for each conflicting local copy: its `sources`, `reason`, `byte_size` and `sha256`;
- for the stored newest version: `stored.version`, `stored.byte_size`, `stored.sha256` and
  `stored.cache_path`. The cache path is where `migrate plan` fetched that version, the same
  cache `image fetch --version <N>` fills.

Ask the user to open the local file and `stored.cache_path` side by side before answering. Do
not open either image yourself unless the user asks you to compare them.

`stored.redacted: true` (its `cache_path` is `null`) means kartoteka removed that version's
bytes. Say so, because the local copy may show what was removed, and let the user decide from the
local file alone. A `reason` naming redacted versions says the same of an older version.

The answers are the ones above, per image `logical`:
- **Keep local** → `--resolve <logical>=keep-local@<N>`, or `keep-local:<source>@<N>` when its
  copies differ. `<N>` is the item's `newest_version`, or `0` when it is null. The upload carries
  it as `expected_version`, so a store that moved since is refused, never overwritten.
- **Keep stored** → `keep-stored`, offered only when `newest_version` is not null.
- **Skip** → `skip`.

## 4. Apply

    SPEC_STORE migrate apply <tickets…|--all> [--pending-only] [--resolve …]…

- Exit `5` → kartoteka went down during the migration: report the printed reason and stop.
  Whatever had been uploaded is in kartoteka and verified, and no local copy was deleted, so
  running the skill again when the store is back is safe.
- Exit `2` → report the error and stop.

Report `uploaded` (document or image → new version) and every `failed` entry with its reason.
Report `merged` (document → new version, with `local_changes`/`stored_changes`; `unchanged: true`
means every local change was already in kartoteka and nothing was uploaded) and `conflicted` (go
to **Merge conflicts** in step 3, then run `apply` again with the answers). An `unaligned` list on
an `uploaded` or `merged` entry names local copies that changed since the plan: they are kept, and
the next run classifies them again.
- A failure is never retried silently. `moved to v<N> during the migration` and
  `moved from v<N> to v<M> since you decided` both mean someone wrote meanwhile: say so and
  suggest running the skill again.
- One document or image kartoteka refuses (too large for its limit, say) fails on its own. The
  rest of the run still moves.

`pending_left` counts, per ticket, the outage saves still on disk, after entries whose file is
gone were dropped. `0` for a ticket means that outage is drained.

`apply` flips a ticket's storage decision to kartoteka only when its **whole** local trail has
nothing left behind: no unresolved conflict, and no item skipped or failed. An image kartoteka
cannot address never holds a flip back. `apply` reports the tickets it flipped as `flipped`.
- A ticket left with anything outstanding keeps its previous decision untouched.
- So a run that had been working locally keeps doing so until a later, fully clean migration
  flips it.

## 5. Delete

`deletable` lists every local file kartoteka now verifiably holds, whether its own copy or a
newer one, plus those the user chose to discard. That covers documents and images. A tracked
image goes with `git rm` under the same rules as a tracked document. Every file, image or
document, is re-hashed at the moment of deletion.

- **`--pending-only`**: delete without asking. The user allowed these to be kept locally only
  until kartoteka was back:

      SPEC_STORE migrate delete <ticket> --pending-only [--resolve …]…

- **Otherwise**, ask once (`AskUserQuestion`), listing the files by ticket and naming the current
  branch (`git branch --show-current`):
  - **Delete and commit** → `SPEC_STORE migrate delete <…> [--resolve …]… --commit` — one commit,
    `chore: move <tickets> spec trail to kartoteka`, holding only these deletions.
  - **Delete, leave staged** → the same without `--commit`.
  - **Keep local copies** → run nothing. The next run on each ticket reports them again.

  `delete` answers the same exits as `apply`: `5` (report the reason and stop; nothing was
  deleted) and `2`.
  - With `--commit`, a `commit` of `null` means no commit was made, either because no tracked
    file was removed or because git refused. The deletions are then **staged**: say so rather
    than reporting a commit.
  - Every `kept` entry names its `logical` document or image, and the `source` file when one file
    in particular could not go, with the reason. A copy that changed since it was classified, or
    a tracked file with staged content kartoteka does not hold, is kept.

  Under `--no-prompt` without `--pending-only`, apply has already run: report the `deletable`
  list and stop without deleting.

**Never deleted, whatever is chosen:** `.active_ticket`; gate evidence text (for example
`review/findings.json`, `verify/` reports, `runtime/*.md`); `change-report.html`;
`pr-pending.md`; release-scope files under `<specs.releases>`; untracked working-tree images,
which are the sweep's; and anything skipped, unresolved or unverified. An image is never
evidence: one under `verify/` is migrated like any other image, not kept in place. Say so in
the report rather than leaving it to be inferred.

## 6. Report

Per ticket, list with the reason for each:
- uploaded or merged, name → version (an image by its path);
- already stored;
- deleted, and the commit if any;
- kept.

Close with any remaining conflict or skipped file, and what to do about it. Name each image
outside the path grammar, to be renamed.

Other worktrees hold their own `<specs.dir>`: run the skill inside each one to migrate its
trails.

Do not run this while another session is actively working the same ticket.
