# Scenario: mixed paginated fork PR

Fresh capture of a paginated fork PR stacked on a non-default base. Exercises the full
partition (code, tests, config, generated code, lockfile, operational Markdown, prose docs,
spec, asset, rename, deletion), contract-before-implementation ordering, direct diff links,
checklist persistence, one-file stopping, and the truncated-inventory variant.

## Sandbox

Follow `../README.md`, then:

```bash
mkdir -p "$SANDBOX/repo/.artel/run"
```

No saved state: the agent captures the PR itself. The fixture's `github/` data is:

- `acme/session-kit` PR **42**, head on the fork `mira-fork/session-kit`, base branch
  `feature/session-core` (stacked, not the default branch).
- 12 changed files across **two pages** (7 + 5); the rename, the deletion and all three
  exclusions are on page 2, so a run that never paginates sees only page 1.
- Patches are omitted for `package-lock.json` and `src/generated/session_schema.ts` (large /
  generated) and `assets/logo.svg` (binary): the full contents are the reliable context.

## Prompt

> Walk me through this PR one file at a time; include code and config, and use `reviewed` to
> mark each file reviewed. The PR is https://github.com/acme/session-kit/pull/42.

(The wording is verbatim; the URL is appended because the offline sandbox has no GitHub remote
and no branch to resolve "this PR" from.)

## Expected observations

- Resolution via `gh pr view` and capture of both pages; all 12 paths accounted for, with
  `docs/review-notes.md`, `docs/session-spec.md` and `assets/logo.svg` recorded as exclusions
  with reasons, and the deleted `src/legacy/old_reviewer.ts`, the renamed
  `src/legacy/session_store.ts → src/domain/session_store.ts`, the generated schema and the
  lockfile still receiving entries.
- Reading order puts the public contract `src/domain/session.ts` before the smaller
  implementation `src/domain/memory_session_store.ts`; config/tests order is the worker's
  judgment.
- A checklist is saved under
  `.artel/run/collaborative-review/acme/session-kit/pr-42/review-checklist.md` with all entries
  unchecked and the recorded snapshot identity.
- Exactly **one** file is presented, with its role, changed excerpt, evidenced/inferred why,
  assessment, limits, a direct `.../pull/42/files#diff-<sha256>` link, and the approval prompt;
  the turn then ends.
- No checkout or GitHub write happens; the only writes are under the sandbox run directory.

## Truncated variant (must stop)

```bash
cp "$FIXTURES/mixed-pr/variants/pages-truncated.json" "$SANDBOX/github/pages.json"
```

Re-run the same prompt in a fresh sandbox (or re-dispatch): the files endpoint now returns one
page of 7 rows while `pr-view.json` still declares `changedFiles: 12`. Expect the run to stop
and report an incomplete inventory — no checklist, no fabricated first file, no "reviewed"
claim.
