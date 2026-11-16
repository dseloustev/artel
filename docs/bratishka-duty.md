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
| `/artel-bratishka-request-fix` | write | Reads the bot's unresolved review threads with the code anchor each one carries, composes one fix comment, posts it, correlates the spawned task. |
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
  (`""`), `spawn.promptFile` (`""`).
- **Testing PR** — `testingPr.titleTemplate` (`"Testing {dd}-{mm}-{yy}"`),
  `testingPr.branchTemplate` (`"feature/testing-{dd}-{mm}-{yy}"`),
  `testingPr.deleteBranchOnCancel` (`false`), `testingPr.bodyFile` (`""`).
- **Delegate** — `delegate.startStatus` (`""`), `delegate.handoffStatus` (`""`),
  `delegate.reviewerLogin` (`""`), `delegate.maxBatch` (`5`).

`spawn.promptFile` and `testingPr.bodyFile` are **overrides**. Empty, the engine builds the prompt
and the body itself in the fixed shape above. A path to a Markdown file replaces that text: it is
rendered with a closed `{placeholder}` vocabulary and sent verbatim, an unknown placeholder is
`invalid_config`, and an unreadable file is `missing_config`. A prompt override must still carry
`Run id: {run_id}`, or the run cannot be correlated with its pull request. The two vocabularies
ship as the contract beside the skill
(`skills/artel-bratishka-testing-pr/assets/`); [config.md](config.md) carries the per-key rules.

The delegate statuses are empty by default as well: empty means "resolve the intent from the alias
lists", and a value is matched exactly — [tracker-status.md](tracker-status.md) is the one copy of
that resolution.

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

Approval is read from **review state, one latest effective state per login**, not from the event list.
`APPROVED`, `CHANGES_REQUESTED` and `DISMISSED` are the effective states; a later `COMMENTED`
or `PENDING` review is ignored, so an ordinary comment does not withdraw an approval. A reviewer
who approved, was dismissed and then approved again has a latest state of `APPROVED`; a reviewer
whose approval was dismissed has `DISMISSED`. Counting approval events would get both wrong.

- **Code review approval** — the latest `APPROVED` review from a member of `teams.codeReview`.
  When that key is empty, it is any latest `APPROVED` that is neither the bot's nor a configured
  QA login's. When the key is set but the team cannot be read (a missing team, or no
  `read:org`), the code-review verdict is `unknown` and the PR is flagged
  `code-review-unknown`; it never falls back to an arbitrary approval.
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

- **`bot-working`** — an active or queued coder task exists for the PR, whichever reviewer
  started it. Do not merge it or post a second task; report the task.
- **`blocked`** — a PR with both approvals that still cannot merge: `mergeStateStatus` is
  `BLOCKED` or `UNKNOWN`, or the branch conflicts (`DIRTY`). Report `gh`'s reason. A
  `needs-review` PR that GitHub reports as `BLOCKED` merely because reviews are required is not
  blocked in this sense — it is waiting in its approval bucket.

Testing PRs are excluded from the queue by the configured title and branch templates and by the
broader detection rule below — the author check alone cannot tell them apart, since the bot authors
both. `excludeTitlePatterns` entries are case-insensitive substrings, or regexes prefixed `re:`;
they exist for a repository whose bot account also runs scheduled automation.

`/artel-bratishka-status` renders the board read-only, in four sections: today's testing PR(s) with
date, age in days, QA-approval count (`qa_approvals`) and whether any bundled source PR has merged
or closed (`source_merged`); the queue with ticket, review state, QA state, CI/merge state
(`mergeable`, `merge_state`) and bot-task state; live bot tasks when the dashboard is up; and
suggested next actions, each naming the skill to run. The engine's `queue --json` command prints
the same classified queue and its counts alone, without the testing-PR or live-task sections.

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

**The read-back is the check.** A verification answer is trusted only when the created PR matches
the plan that produced it: the URL the create call returned, a head branch equal to the plan's
branch, a head SHA on that branch, a base ref equal to the plan's base, and the plan's title. A
field that disagrees is a naming defect to report — never a PR to rename, rebuild or re-spawn. The
created PR's own check runs are reported as observed: none published yet is "not started", never
green; `pending` is still running; a failing check is named with its reason. `checks: null` means
there was no PR to read or the read failed, and the checks are then unknown — the source PRs'
checks in `## CI status of source PRs` are a different question and never fill that gap.

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

`/artel-bratishka-request-fix` reads the bot's unresolved review threads through GraphQL
`reviewThreads`, 100 threads a page, following `pageInfo` so a large PR is not silently cut at
the first page. It shows a numbered list; the default selection is every unresolved thread, and
the person may add notes. It then posts **one** self-contained `/agent` comment, not one comment
per finding: the bot serializes `/agent` tasks per branch, so N comments queue N runs against the
same branch.

Each finding is composed as a **block**, not a location line, because the bot reads the comment
without the pull request in front of it:

- the `path:line` anchor, plus `(was line N)` when GitHub has nulled the live line;
- `outdated: true` when GitHub reports the code the comment targeted has changed since — the
  anchor is gone from the diff, so the block says so and the bot checks whether the problem
  survives before changing anything. An outdated thread stays a finding; it is never dropped
  silently;
- the diff hunk the comment was written against, with `← this finding` inserted under the
  anchored line, counted on the new side of the diff. The bot reads the code, not a coordinate;
- every comment in the thread by author — the root and each reply — because the correction is
  usually in a reply, not in the bot's first word;
- the GitHub `suggestion` fence, whole and multi-line, when the thread has one.

Fence delimiters in comment text are stripped before composition: a review comment is written by
whoever can comment on the PR, and a stray code fence in it would swallow the rest of the
payload. Line structure is kept, so numbered steps and lists arrive intact.

`--all-authors` widens `findings` to every reviewer's unresolved threads, and
`compose-fix --all-authors` must be passed with it: without it the two commands number different
lists, and a reviewer's thread can never be selected. `bratishka.fixCommand` is the command word,
`/agent` by default.

Two guards come before posting:

- **Active-task guard** — `task-find --pr N`; its `active_tasks` list holds every `active` or
  `queued` task whose description names the PR, whichever reviewer started it and even when a
  newer completed task exists. Any entry means stop and report it instead of queueing another.
  `data.task` stays the caller-scoped, newest-task correlation used after posting; it is not the
  guard.
- **Terminal-state guard** — never post on a merged or closed PR.

After posting, correlation replaces blind polling. A bot task's description contains
`PR <owner>/<repo>#<n>`, and its detail carries `initiator`, `created_at`, `head_ref` and
`result_status`. The engine matches the PR marker first, then the caller's `initiator` and the
posting time (or `head_ref` when the task names it). Without the dashboard, poll the PR every
15–30 s and treat the bot's summary comment as completion; a read-only ask may answer only in its
task report. Never include secrets in a fix comment and never edit the bot's own comment.

When the task is terminal, the same `findings` read closes the loop: a selected finding absent
from the fresh list is resolved or outdated as far as GitHub is concerned, and one still present
was not fixed. The engine reports thread state, never the diff — it cannot tell a real fix from a
thread a human resolved — and it never resolves, replies to or edits a thread itself.

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
- After a merge, do not wait for its post-merge builds. Report the merged PRs and hand back: the
  build window is the next code review (`/artel-collaborative-review`), and `merge-approved` is
  re-run once the builds settle.
- Never merge a PR with an active or queued coder task. `merge-plan` marks such an entry
  `bot_working` and lists its `active_tasks`; report the task id and status and move on.

Nothing in this path force-pushes, pushes to `baseRef`, merges a testing PR, or edits a source PR.

## Delegating a ticket

`/artel-bratishka-delegate` runs one ticket through the bot: one task, one branch and one pull
request per issue, with the ticket's status kept in step. Issues are independent, so a batch runs
concurrently — and one impossible key is reported without cancelling the rest.

**The engine holds no tracker client.** The skill reads the issue and its available transitions
through the tracker's MCP tools and writes both answers under
`.artel/run/bratishka/<date>/delegate/`; `delegate-plan` then decides from those facts and returns
one row per key plus one filled prompt per ready key. `delegate-closeout` reads a finished task
against its pull request. Both verbs are read-only.

**Every requested key ends in one outcome** — `ready`, `invalid` (no such issue),
`needs-decision` (a done issue, or a status move that did not resolve), `blocked`, `spawn-failed`,
`failed` or `completed` — and the report carries one row per key, in the order given.

**The two moves are intents.** `delegate.startStatus` and `delegate.handoffStatus` are empty by
default, which means "resolve from the aliases"; a value is matched exactly and never falls back to
an alias. [tracker-status.md](tracker-status.md) is the one copy of that resolution, and a move it
cannot resolve stops that key for a decision instead of moving it anyway.

**The batch is by phase, not by issue.** Gather every issue, move every ready ticket, spawn every
task, then poll in rounds — `get_task(task_id, wait_timeout=300)` once per distinct task in one
message, so a round costs one wait however many tasks run. A task's own close-out starts as soon as
it lands: find its pull request, request the reviewer, read the result, move the ticket.

**The prompt is the task's only instruction.** It is composed per issue, self-contained, and
carries the issue verbatim, the root-cause constraint, the branch and title to use, the
repository's own pull-request template and the output language, and the reviewer login.
`spawn_task` has **no** reviewer parameter: the login travels in the prompt and is applied with
`gh pr edit --add-reviewer`.

**`idempotency_key` is per issue** (`<KEY>:agent`), because it is active-scoped per API client: one
key shared by two issues would collapse them into a single task. A deliberate second run of a
finished issue appends the date.

**The close-out sends work back.** `delegate-closeout` reports `gaps` — no pull request, an empty
body, a reviewer that was not requested — and `send_back`. The skill then calls
`continue_task(task_id, prompt)` with the gap verbatim, so the same branch and pull request update.
A task whose completion gate stayed red **with** a pull request is `completed` with `gate_failed`
set: the pull request is the artifact and the gate is a separate finding.

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
- **A run journal** is appended at `.artel/run/bratishka/<date>/journal.md`: one line per
  destructive engine command (`testing-pr-cancel`, `testing-pr-local`) with the UTC time, the
  action, the PR numbers it touched and their URLs. A journal write failure never fails the
  command.
- **No reported fact outlives the call that produced it.** A PR number, URL, SHA, check state or
  task id that no command in this run returned is reported as unknown. A guessed PR number
  silently points at a different pull request.
- **A check nobody published is not green.** Zero check runs, a `pending` bucket and a `null` read
  are three different answers, and none of them is a pass.
- **A delegated batch changes the tickets.** Every move is resolved from the transitions the
  tracker just returned, never from a stored identifier, and a key whose move does not resolve is
  left for a human decision instead of being moved anyway. Nothing is spawned before the batch is
  confirmed, and one bad key never cancels the others.

## Sources

- The design note this contract implements — *Bratishka duty toolkit — design*, 2026-11-05,
  approved the same day (kept with the project's planning notes rather than shipped).
- Two observed testing PRs from the host repository the rules were derived in — `#138`
  (2026-11-02) and `#144` (2026-11-03) — and their reviews, merge states and task records. They
  are evidence, not requirements: `#144` fixes the body shape and the strict naming, `#138`
  proves a legacy name must still be detected and cancelled.
