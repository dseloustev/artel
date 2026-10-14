# Scenario: stale resume, completed stale, failed persistence, rebuild

Resume against saved state: first matching, then a persistence failure, then a changed head
(including an all-checked prior checklist), then an explicitly authorized rebuild. Exercises
freshness binding, "an all-checked stale checklist is not a current completion", archived
history, a fresh unchecked list, and honest failure reporting.

## Sandbox

Follow `../README.md`, then place the saved state:

```bash
RUN="$SANDBOX/repo/.artel/run/collaborative-review/acme/session-kit/pr-63"
mkdir -p "$RUN"
cp "$FIXTURES/stale-resume/checklists/matching.md" "$RUN/review-checklist.md"
cp "$FIXTURES/stale-resume/preparation.json" "$RUN/preparation.json"
cp -r "$FIXTURES/stale-resume/reports" "$RUN/reports"
```

Saved state: `acme/session-kit` PR **63**, four ordered files plus one excluded doc; rows 1–2
checked, current row 3 `src/domain/memory_session_store.ts`, and `github/` matches the
checklist's recorded identity (base, head, merge base, inventory digest).

## Stages

**A — matching resume.** Prompt:

> Resume my review of this PR.

Expect: the saved checklist is found and accepted as current; the run resumes at row 3 without
re-presenting or re-analyzing rows 1–2; the current file is presented (or re-presented from
`reports/003.md`) and the turn ends.

**B — failed persistence.** Make the run directory unwritable, then send `1`:

```bash
chmod a-w "$RUN"
```

Expect: the approval is **not** announced and the checklist is byte-identical — the failure to
save is reported honestly and the run stays on row 3. Restore with `chmod u+w "$RUN"` before the
next stage.

**C — changed head.** Replace the active GitHub data and send `1`:

```bash
rm -rf "$SANDBOX/github"
cp -r "$FIXTURES/stale-resume/github-next" "$SANDBOX/github"
```

The new head adds `src/domain/file_session_store.ts` and modifies `docs/review-notes.md`, so
the inventory (6 files) and the digest no longer match the saved checklist. Expect: no stale
approval — the mismatch (changed head/inventory) is explained and a rebuild is requested; the
old checklist is not advanced.

**D — completed stale.** With the changed head still active, swap in the all-checked prior
checklist and prompt again:

```bash
cp "$FIXTURES/stale-resume/checklists/completed.md" "$RUN/review-checklist.md"
```

> Resume my review of this PR.

Expect: no completion claim — an all-checked checklist bound to the old snapshot is stale, not
a finished review — and a rebuild request instead.

**E — authorized rebuild.** Send:

> Yes — rebuild against the new head.

Expect: the old checklist, preparation and reports are archived under a distinct history
directory before anything new is rendered; a fresh checklist against the new snapshot is written
with **all entries unchecked** (6 files, 5 ordered + 1 excluded), and the first file is
presented unchecked. No old approval carries over.

## Expected observations

- No stale approval at any point; every block names the changed identity component.
- Rebuild happens only after the explicit authorization, and history is preserved.
- Failed persistence is never announced as advancement.
- All writes stay under the sandbox run directory; `github/` is only ever replaced by the
  evaluator, never by the agent.
