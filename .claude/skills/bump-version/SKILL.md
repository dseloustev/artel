---
name: bump-version
description: "Cut a release of artel itself: pick the next semver from the CHANGELOG's Unreleased section (or an explicit argument), bump .claude-plugin/plugin.json, roll [Unreleased] into a dated release section, commit as chore(release), and tag. Use when releasing artel, bumping its version, or cutting a new plugin version."
argument-hint: "[major|minor|patch|x.y.z] (optional; inferred from the Unreleased section when omitted)"
disable-model-invocation: true
model: sonnet
---

## Overview

`bump-version` performs **this repository's own release chore**. It is repo-local by design: its
logic is specific to artel's two version carriers — `.claude-plugin/plugin.json` and
`CHANGELOG.md` — and it is not part of the shipped plugin. It never runs against a host project.

It is a **worker that runs inline** — plain `git` and file edits, no agent delegation. Because it
writes files, commits, and tags, it is user-invoked only (`disable-model-invocation: true`).

The skill stops at the tag. Pushing and publishing a GitHub release stay a deliberate manual step.

## Steps

Run the steps in order. Each later step assumes the previous one succeeded.

### Step 1: Preflight

All four checks are **fatal** — report what failed and stop, changing nothing:

1. **Right repo.** `.claude-plugin/plugin.json` and `CHANGELOG.md` exist at the repo root. If not,
   this is not the artel checkout.
2. **Clean tree.** `git status --porcelain` is empty. A release commit must contain only the bump;
   never stash, never commit someone else's work-in-progress alongside it. If the tree is dirty,
   list what is uncommitted and let the user decide.
3. **On `main`.** `git rev-parse --abbrev-ref HEAD` is `main`. If it is not, **ask** before
   continuing rather than refusing outright — a release from another branch is unusual but the
   user's call. This is the one preflight check the user can wave through.
4. **Tests pass.** `python3 -m unittest discover -s tests -p 'test_*.py'`. The suite includes the
   documentation drift-guards, which are exactly what goes stale between releases. A failure here
   halts the release; report the failing tests.

### Step 2: Read the current state

1. `CURRENT_VERSION` — the `version` field of `.claude-plugin/plugin.json`. This is the **only**
   file in the repo carrying a version: `.claude-plugin/marketplace.json` has no `version` field,
   so there is nothing to keep in sync there. Do not add one.
2. `UNRELEASED_BODY` — everything in `CHANGELOG.md` between the `## [Unreleased]` heading and the
   next `## [` heading (or end of file). Note which `### ` subsections it contains
   (`Added`, `Changed`, `Fixed`, `Removed`, …).
3. If `UNRELEASED_BODY` has no entries, **stop**: there is nothing to release. Say so plainly
   rather than cutting an empty version.

### Step 3: Decide the new version

`$0` decides how `NEW_VERSION` is reached:

| `$0` | Behaviour |
|------|-----------|
| `x.y.z` | Used verbatim. Validate: well-formed semver, and strictly greater than `CURRENT_VERSION`. Either check failing is fatal. |
| `major` / `minor` / `patch` | Computed from `CURRENT_VERSION`. Honoured **verbatim, including pre-1.0** — an explicit `major` on `0.x` yields `1.0.0`. That is the user overriding the inference below, on purpose. |
| *(empty)* | Inferred from `UNRELEASED_BODY`, per the table below. |

Inference, when no argument was given:

- **`CURRENT_VERSION` is `0.x`** (artel today): a `Removed` section or any entry describing a
  breaking change → **minor**; an `Added` section → **minor**; only `Changed` / `Fixed` /
  documentation entries → **patch**. Pre-1.0, breaking changes ride a minor bump — semver's own
  convention, and artel has never been released or tagged.
- **`CURRENT_VERSION` is `1.0.0` or later**: `Removed`/breaking → **major**; `Added` → **minor**;
  otherwise → **patch**.

Then, before writing anything: **present the proposed version and the reasoning that produced it**
— which subsections were seen, which rule fired — and **wait for an explicit yes**. This is the
skill's only pause, and it is not optional, including when `$0` named the version explicitly
(confirming a typo'd `1.2.3` costs one line; an unwanted tag costs more).

Finally, verify `git tag -l "v<NEW_VERSION>"` is empty. An existing tag for that version is fatal.

### Step 4: Write the two files

1. **`.claude-plugin/plugin.json`** — replace the `version` value with `NEW_VERSION`. A targeted
   edit of that one field: preserve the file's key order, indentation, and trailing newline. Do not
   reformat the JSON.
2. **`CHANGELOG.md`** — retitle the existing `## [Unreleased]` heading to
   `## [<NEW_VERSION>] - <DATE>`, and insert a fresh `## [Unreleased]` heading above it, followed
   by a blank line and no placeholder subsections.
   - `<DATE>` comes from `date +%F` — read the date from the system, never from memory or an
     assumption about today.
   - The released section's body is untouched: the entries move under the version heading exactly
     as written.
   - This CHANGELOG carries **no link-reference definitions** at the bottom (no `[Unreleased]: …`
     compare links). Do not invent them.

### Step 5: Verify, then commit

1. Re-read both files. Confirm `plugin.json`'s `version` is `NEW_VERSION`, that `CHANGELOG.md` now
   has exactly one `## [<NEW_VERSION>] - <DATE>` heading and one empty `## [Unreleased]` above it,
   and that no other content moved.
2. Show the user `git diff`.
3. Stage exactly the two files and commit them together:
   `git add .claude-plugin/plugin.json CHANGELOG.md` then
   `git commit -m "chore(release): v<NEW_VERSION>"`.
   Name the paths explicitly rather than using `git add -A` or `git commit -a` — the tree was clean
   at Step 1, so the two are equivalent today, but an explicit stage keeps that true if a later
   step ever writes something else. **Subject line only, no trailers** — CLAUDE.md's commit
   convention overrides the harness default of appending `Co-Authored-By:` / `Claude-Session:`
   lines.

### Step 6: Tag

`git tag -a "v<NEW_VERSION>" -m "v<NEW_VERSION>"` — annotated, on the commit just made.

**The tag belongs on the tree the `[<NEW_VERSION>]` section describes.** At this point in the skill
that is the release commit, because nothing follows it yet. It stops being the release commit as
soon as work continues on the same version — a release cut on a branch, then fixes that amend the
`[<NEW_VERSION>]` entries in place rather than opening a fresh `[Unreleased]`. The notes now
describe the merge, so the tag moves with them:
`git tag -f -a "v<NEW_VERSION>" -m "v<NEW_VERSION>" <merge-sha>`. The skill never pushes (see
Rules), so until the user does, the tag is local and moving it costs nothing; after a push, moving
it is a force-update every clone has to recover from. Decide before pushing. `v0.13.0` is the
worked example — see the 2026-09-16 tag entry in the `docs/design.md` decision log.

If tagging fails after the commit landed, say so explicitly: the release commit exists and only the
tag is missing, which the user fixes with a single command. Do not attempt to unwind the commit.

### Step 7: Report

Print a concise summary:

- `CURRENT_VERSION` → `NEW_VERSION`, and how it was decided (explicit argument vs. which inference
  rule fired).
- The release date stamped into the CHANGELOG, and how many entries moved under it.
- The commit SHA and the tag name.
- The manual follow-up, as a command to copy — **printed, not run**:
  `git push origin main --follow-tags`
- A reminder that publishing a GitHub release is separate, and that `gh` against this repo needs the
  corporate profile: `GH_CONFIG_DIR=~/.config/gh-adguard gh …`.

## Rules

- **Never push, never publish.** The skill's last action is the tag. Pushing to `origin` and
  creating a GitHub release are outward-facing and stay with the user.
- **One pause, always.** Step 3 confirms the version before any file is written. Everything before
  that pause is read-only; everything after it is mechanical.
- **The CHANGELOG is the source of truth for the bump size.** The inference reads what is actually
  written in `[Unreleased]` — it does not re-derive the release's significance from `git log`.
- **Two carriers, no more.** `plugin.json` and `CHANGELOG.md`. If a third file ever starts carrying
  the version, this skill is where that gets added — and `marketplace.json` deliberately is not one.
- **Fail loudly, change nothing.** Every preflight failure and every validation failure stops the
  skill before the first write. There is no partial release.
- **Not idempotent — do not re-run blindly.** A second run on an unchanged tree stops at Step 2's
  empty-`[Unreleased]` check, and a re-run after adding entries would cut a further version. To
  redo a botched release, undo the commit and tag first.

## Examples

- `bump-version` — read `[Unreleased]`, propose (e.g.) `0.1.0 → 0.2.0` because it contains an
  `Added` section, confirm, then write, commit, tag.
- `bump-version patch` — force `0.1.0 → 0.1.1` regardless of what the section contains.
- `bump-version 1.0.0` — the first stable release: validated as semver and greater than current,
  then confirmed like any other.
