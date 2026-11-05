# Bratishka duty — the daily review-day contract

One person is on duty for a day: they own the review queue, ask Bratishka for fixes, merge what
QA approved, close yesterday's testing PR and build today's. This document owns that day. The
bot's invocation surfaces — `/review`, `/agent`, `/investigate`, `/explain`, mentions, thread
replies — belong to the bot's own skill, `using-bratishka-bot`, and are not repeated here. The
few polling facts this contract needs are restated because that skill is not a runtime
dependency: poll every 15–30 s; a review or coding task completes with its comments or review on
the pull request; a read-only ask may only answer in the task report.

Five skills implement the day. Each is a thin orchestrator over `scripts/bratishka.py` and plain
`gh` calls:

| Skill | Kind | What it does |
|---|---|---|
| `/artel-bratishka-status` | read | The board: today's testing PR and its age, the repo queue by bucket, live bot tasks. Degrades to PR-only without the dashboard. |
| `/artel-bratishka-cancel-testing-pr` | write | Finds open testing PRs older than today, shows them, posts a close reason, closes them. Today's is never touched. |
| `/artel-bratishka-testing-pr` | write | Selects the source PRs, composes the coder prompt, starts the bot, finds the task, verifies the PR. `--local` is the explicit git+gh fallback. |
| `/artel-bratishka-request-fix` | write | Reads the bot's unresolved review threads, composes one fix comment, posts it, correlates the spawned task. |
| `/artel-bratishka-merge-approved` | write | Updates `BEHIND` branches, waits on `BLOCKED` within a bound, squash-merges QA-approved PRs one by one. |

The contract is project-agnostic: every host-specific value comes from the `bratishka` block in
`.artel/config.json` ([config.md](config.md)). The process assumes a GitHub VCS adapter; the
skills refuse to run when `vcs.adapter` is anything but `github-cli`.

## Config

The `bratishka` block is optional and every key has a working default. Types, allowed values and
per-key notes live in [config.md](config.md); this is the shape the day depends on, grouped by
what it feeds:

- **Connection** — `dashboardUrl` (`""`, origin only), `repo` (`""`, `owner/name`), `baseRef`
  (`""`), `botLogin` (`"bratishka-bot"`).
- **Teams** — `teams.codeReview` (`""`), `teams.qa` (`""`), `qaLogins` (`[]`).
- **Queue** — `excludeTitlePatterns` (`[]`), `fixCommand` (`"/agent"`).
- **Spawn** — `spawn.adapter` (`"manual"`), `spawn.tokenEnv` (`""`), `spawn.mcpToolPrefix`
  (`""`), `spawn.promptFile` (`""`, reserved).
- **Testing PR** — `testingPr.titleTemplate` (`"Testing {dd}-{mm}-{yy}"`),
  `testingPr.branchTemplate` (`"feature/testing-{dd}-{mm}-{yy}"`),
  `testingPr.deleteBranchOnCancel` (`false`), `testingPr.bodyFile` (`""`, reserved).

`spawn.promptFile` and `testingPr.bodyFile` are **reserved**: accepted and ignored, because the
prompt and the body keep their fixed built-in shape. A host override needs a substitution
contract before either key can work ([config.md](config.md), `bratishka`).

The fallbacks are part of the contract:

- `repo` and `baseRef` empty → resolve with `gh repo view --json nameWithOwner,defaultBranchRef`.
  `botLogin` needs no fallback.
- `teams.qa` empty with no `qaLogins`, or either team unreadable for want of `read:org` → the
  verdict it feeds is `unknown`. `qaLogins` is the login-list fallback for QA when the team
  cannot be read. An empty `teams.codeReview` is not unknown: it falls back to any `APPROVED`
  that is neither the bot's nor a QA login.
- `dashboardUrl` empty or unreachable → the board loses its live-task section and the fix skill
  loses task correlation; every other path still works.
- `spawn.adapter` is `"manual"` until a token (`client-api`) or an MCP server (`mcp`) is
  configured. A configured adapter whose credential or tools are missing falls back to `"manual"`,
  never to a failed run.

## Approval model

Approval is read from **review state, one latest state per login**, not from the event list. A
reviewer who approved, was dismissed and then approved again has a latest state of `APPROVED`; a
reviewer whose approval was dismissed has `DISMISSED`. Counting approval events would get both
wrong.

- **Code review approval** — the latest `APPROVED` review from a member of `teams.codeReview`.
  When that key is empty, it is any latest `APPROVED` that is neither the bot's nor a configured
  QA login's.
- **QA approval** — the latest `APPROVED` review from a member of `teams.qa`, or from a login in
  `qaLogins` when the team is unset or cannot be read.
- **`CODEOWNERS` is neither.** An approval from a code owner outside both sets is not QA approval
  and does not make a PR mergeable. It still counts as an ordinary code-review approval only when
  `teams.codeReview` is empty and the login is not a QA login.
- **`DISMISSED` wins.** A dismissed review, or a later `CHANGES_REQUESTED` from the same login,
  replaces the earlier `APPROVED`. GitHub's own dismissal semantics are the reason the engine
  reads states instead of counting events.
- **`unknown` is not approved.** When the team cannot be read and no fallback login matches, the
  verdict is `unknown`; the read skills print the key to add and the merge skill refuses.

Approval answers "did the right person sign off". It does not answer "can this merge" — that is
`mergeStateStatus` ([Merging](#merging)).

## Queue and board

The queue is **repo-scoped**: open, non-draft PRs authored by `bratishka.botLogin` in
`bratishka.repo`, minus the testing PRs and minus titles dropped by `excludeTitlePatterns`. An
unscoped bot filter mixes in every repository the account serves, mostly scheduled dependency
bumps, and is not a usable queue.

Each PR has exactly one **approval bucket**, decided by its strongest state: `qa-approved`
(both a code review and QA approval), `needs-qa` (code review, no QA yet) or `needs-review`
(neither — including a PR whose only approval is a `CODEOWNERS` one). Two further buckets are
**overlays**: a PR can appear in its approval bucket and in an overlay at the same time, and the
board renders all five as separate lists.

- **`bot-working`** — an active or queued coder task exists for the PR. Do not merge it or post
  a second task; report the task.
- **`blocked`** — a PR with both approvals that still cannot merge: `mergeStateStatus` is
  `BLOCKED` or `UNKNOWN`, or the branch conflicts (`DIRTY`). Report `gh`'s reason. A
  `needs-review` PR that GitHub reports as `BLOCKED` merely because reviews are required is not
  blocked in this sense — it is waiting in its approval bucket.

Testing PRs are excluded from the queue by the configured title and branch templates and by the
broader detection rule below — the author check alone cannot tell them apart, since the bot authors
both. `excludeTitlePatterns` entries are case-insensitive substrings, or regexes prefixed `re:`;
they exist for a repository whose bot account also runs scheduled automation.

`/artel-bratishka-status` renders the board read-only, in four sections: today's testing PR(s) with
date, age in days, QA-approval count and whether any bundled source PR has merged or closed; the
queue with ticket, review state, QA state, CI/merge state and bot-task state; live bot tasks when
the dashboard is up; and suggested next actions, each naming the skill to run.

## Testing PR

The testing PR is a **one-day QA vehicle**. It bundles the source PRs that have code review but
not QA approval, so QA can test the day's work as one build. QA leaves its verdicts in the
individual source PRs; the bundle is closed unmerged by the next duty reviewer.

**Creation is strict. Detection is broad.** `/artel-bratishka-testing-pr` creates the PR with
`testingPr.titleTemplate` — by default `Testing {dd}-{mm}-{yy}` — and
`testingPr.branchTemplate` — by default `feature/testing-{dd}-{mm}-{yy}` — rendered with the
current local date. The tokens are `{dd}`, `{mm}`, `{yy}` (two-digit year) and `{yyyy}`. A PR
from before the rule, such as a `Testing 02.11.26` title on a `testing/…` branch, still must be
found and cancelled, so detection accepts a title matching the template, any title starting with
`Testing `, or any branch containing `testing`; when the name carries no parseable date, the
engine falls back to `created_at`. `--delete-branch` is the only way a cancel removes the branch,
and `testingPr.deleteBranchOnCancel` (default `false`) is the configured default for the same
choice. `/artel-bratishka-cancel-testing-pr` never touches today's PR.

**The bot creates it.** The default path composes the coder prompt and starts the task through
`spawn.adapter`. Phase 1 ships `manual`: the skill prints and copies the prompt, opens
`<dashboardUrl>/spawn`, then finds the spawned task by the unique `Run id:` marker it put in the
prompt — `Run id: artel-<uuid>` — and verifies the PR from there. `--local` is the explicit
git+gh fallback: it builds the same branch and body in a throwaway worktree under
`.artel/run/bratishka/`, never in the working checkout, and stops on conflict.

**A failed coder gate with an existing PR is not a failed PR.** Bot tasks report `failed` when
their completion gate cannot go green — a repository-wide red build does that to every task — yet
the PR may exist and be correct. Verification checks for the PR first: when it exists, the report
names it as the artifact and reports the gate separately. `/artel-bratishka-testing-pr` refuses
when today's branch or testing PR already exists, and `--print` stops before `/spawn`.

Source PRs with failing CI are **not** excluded — the observed runs bundled them and named the
failures. The PR is never merged, the base branch is never pushed to, and source PRs are never
edited or closed by this path.

### Body

The body follows the shape observed on the reference testing PR (`#144`) in eight ordered parts —
the wrapper is composed by `testing-pr-prompt` in its fixed built-in shape (`testingPr.bodyFile`
is reserved — accepted and ignored — until a substitution contract exists), and the content is
drawn from the source PRs:

````markdown
## Summary

<YYYY-MM-DD> — `origin/<baseRef>` at `<short sha>`; <N> source PRs; the branches were merged
without conflicts.

> **QA: leave feedback and approvals in the individual source PRs, not in this testing PR** —
> it only bundles the changes.

## Tasks

- **<ticket>** — [#<n> <title>](<url>)

## Notes for QA

### <ticket> — PR #<n>

<the source PR's Notes for QA, verbatim and minus its heading>

**Platforms to test:** <comma-separated list from the source checklist>

## CI status of source PRs

- **#<n>** — green, or the failing check and why it fails

## Merge notes

- clean merges, files a merge re-introduced, anything flagged for a source-PR owner

## Local verification

- `<verify command>` — result, or the missing tool that prevented it

---

Single testing PR for <YYYY-MM-DD> — it lives one day and will be closed by the next duty
reviewer.
````

1. `## Summary` — the date, `origin/<baseRef>` and its short sha, the PR count, and the
   no-conflicts sentence.
2. A blockquote before the task list telling QA to review in the source PRs.
3. `## Tasks` — one line per bundled PR: ticket, number, title, link.
4. `## Notes for QA` — per PR, `### <ticket> — PR #<n>`, the source PR's `Notes for QA` verbatim
   minus its heading, then a one-line `**Platforms to test:**`. A source PR without notes gets an
   explicit sentence saying so and is flagged in the report.
5. `## CI status of source PRs` — per PR, green or the failing check and its reason. A red source
   check is never silently treated as resolved.
6. `## Merge notes` — clean merges, files a merge re-introduced, and anything the source-PR owner
   should know.
7. `## Local verification` — the host's `verify.commands` and their results. The bot runs them
   itself; `--local` runs them in the worktree. A missing tool is reported, not hidden.
8. The one-day line, outside every heading.

## Requesting a fix

`/artel-bratishka-request-fix` reads the bot's unresolved review threads — GraphQL
`reviewThreads`, first 100 — keeping the root bot comment, path, line and any suggested change.
It shows a numbered list; the default selection is every unresolved thread, and the person may
add notes. It then posts **one** self-contained `/agent` comment, not one comment per finding:
the bot serializes `/agent` tasks per branch, so N comments queue N runs against the same branch.

The comment names each selected finding with enough context to act on it — location, problem,
expected result — and asks for a scoped change and the repository's verification before pushing.
`bratishka.fixCommand` is the command word, `/agent` by default.

Two guards come before posting:

- **Active-task guard** — `task-find --pr N`; an active coder task for that PR means stop and
  report it instead of queueing another.
- **Terminal-state guard** — never post on a merged or closed PR.

After posting, correlation replaces blind polling. A bot task's description contains
`PR <owner>/<repo>#<n>`, and its detail carries `initiator`, `created_at`, `head_ref` and
`result_status`. The engine matches the PR marker first, then the caller's `initiator` and the
posting time (or `head_ref` when the task names it). Without the dashboard, poll the PR every
15–30 s and treat the bot's summary comment as completion; a read-only ask may answer only in its
task report. Never include secrets in a fix comment and never edit the bot's own comment.

## Merging

`/artel-bratishka-merge-approved` merges only PRs that have both a real code review and a real QA
approval — never `unknown` — and are merge-ready by `mergeStateStatus`. The check list is not the
gate: a failing non-required check shows as `UNSTABLE`, which is mergeable, while a single required
failure shows as `BLOCKED`.

- `CLEAN`, `HAS_HOOKS`, `UNSTABLE` with `mergeable: MERGEABLE` → merge.
- `BEHIND` → update the branch with the REST endpoint,
  `gh api -X PUT repos/<owner>/<repo>/pulls/<n>/update-branch`, then re-read once after a
  bounded wait (30 s): GitHub can report `BLOCKED` or `UNKNOWN` for a moment while it
  recomputes. The `gh pr update-branch` command is not used — the API call carries the intent
  reliably, and `hooks/vcs_guard.py` classifies the subcommand as an unknown verb and denies it.
- `BLOCKED` or `DIRTY` → report the PR with the reason `gh` gives and move on. `BLOCKED` is
  re-checked only after an update; there is no unbounded wait, and `DIRTY` (a conflict) is handed
  back to a human.
- Merge with `gh pr merge --squash`. Do not delete the branch by default — this path has no
  branch-deletion flag.
- One PR at a time. After each merge, re-read the queue: the base moved and a `BEHIND` state may
  have appeared on the next PR.

Nothing in this path force-pushes, pushes to `baseRef`, merges a testing PR, or edits a source PR.

## Safety and degradation

- **`--yes` is never the default.** Every destructive step — closing PRs, posting a fix comment,
  merging, building the branch — shows its plan and asks once. `--yes` exists for automation and
  is an explicit opt-in; `--dry-run` prints the plan and stops.
- **No force-push, ever.** No push to the base branch. The testing PR is never merged. Source PRs
  are never edited, closed or force-updated by the toolkit.
- **The host checkout is not a workspace.** `--local` assembles the testing PR in a throwaway
  worktree under `.artel/run/bratishka/`; the bot owns its own workspace on the default path. A
  conflict leaves the worktree for inspection and stops.
- **The dashboard is optional.** `dashboardUrl` empty or unreachable costs the live-task section
  and task correlation; the PR paths keep working and the report says what was skipped. The
  dashboard is read-only — the toolkit never writes to it.
- **`read:org` is optional.** Without it the team lookups are `unknown`; `qaLogins` is the login
  fallback; `unknown` never becomes approved, and the merge skill names the key to add.
- **Spawn degrades to `manual`.** A `client-api` adapter without its token, or an `mcp` adapter
  without its tools, prints and copies the prompt and opens `/spawn` instead of failing.
- **`gh` and the adapter are preconditions.** A missing or unauthenticated `gh`, or a
  `vcs.adapter` other than `github-cli`, stops every skill before it writes, naming the fix.
- **A run journal** lands at `.artel/run/bratishka/<date>/journal.md`: what was read, what was
  changed, and the PR and task URLs.

## Sources

- The design note this contract implements — *Bratishka duty toolkit — design*, 2026-11-05,
  approved the same day (kept with the project's planning notes rather than shipped).
- Two observed testing PRs from the host repository the rules were derived in — `#138`
  (2026-11-02) and `#144` (2026-11-03) — and their reviews, merge states and task records. They
  are evidence, not requirements: `#144` fixes the body shape and the strict naming, `#138`
  proves a legacy name must still be detected and cancelled.
