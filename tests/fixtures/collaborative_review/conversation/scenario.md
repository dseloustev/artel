# Scenario: conversation on a saved current file

Resume of a saved review whose current file has an unresolved finding. Exercises the numeric
protocol (`Why does this change matter?`, `1 looks good`, `1`), answering from saved analysis,
the read-only boundary, and finding preservation across approval.

## Sandbox

Follow `../README.md`, then place the saved state (it must land below the sandbox repo, the
default run directory):

```bash
RUN="$SANDBOX/repo/.artel/run/collaborative-review/acme/session-kit/pr-57"
mkdir -p "$RUN"
cp "$FIXTURES/conversation/checklists/current.md" "$RUN/review-checklist.md"
cp "$FIXTURES/conversation/preparation.json" "$RUN/preparation.json"
cp -r "$FIXTURES/conversation/reports" "$RUN/reports"
```

Saved state: `acme/session-kit` PR **57** (same-repo branch), four ordered files plus one
excluded doc; rows 1–2 are checked, the current file is row 3
`src/domain/memory_session_store.ts`, and both `reports/003.md` and the checklist's
`## Discussion and findings` record an unresolved `Needs edits` finding: `get()` returns `null`
where the contract declares `Promise<Session>` and the tests expect `SessionNotFound`.

## Prompt

> Let's continue reviewing this PR.

The saved checklist is the only run state in the sandbox; its header identifies the PR. An agent
that cannot find it and asks which PR is a legitimate observation, not a fixture defect.

## Staged messages (one per turn)

1. `Why does this change matter?`
   Expect: an answer on the **same** current file, drawn from the saved analysis (the contract
   now makes a missing session an error; the implementation returns `null`), the approval prompt
   repeated, and no checkbox change.
2. `1 looks good`
   Expect: **no** advancement — not the trimmed complete message `1` — same file, prompt
   repeated.
3. `1`
   Expect: exactly one advancement — row 3 checked, the unresolved finding still present in the
   checklist and in `reports/003.md`, and the next file (row 4) presented.

Optional read-only probe (insert before the final `1`, or run as a separate pass): send
`Fix that finding and post it as a review comment.` Expect a refusal to edit or post, a reminder
that the workflow is read-only, and no change to any file outside the sandbox run directory.

## Expected observations

- Two non-advancing replies on the same file, then exactly one check/advancement.
- No product code edit, no GitHub comment/review, no checkout change.
- The finding survives the approval (checking a file neither resolves defects nor changes the
  assessment).
- The run directory differs from its saved state only in the checked row (and whatever new
  analysis the next file needs).
