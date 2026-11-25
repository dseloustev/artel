---
name: artel-bratishka-request-fix
description: "Use when the Bratishka duty reviewer wants the bot to fix review findings on a pull request — reads the bot's unresolved review threads with the diff hunk, the images, replies and suggestion each one carries, composes one self-contained /agent comment for the selected findings plus any notes, posts it after confirmation, and correlates the spawned task. Never posts on a merged or closed PR."
argument-hint: "[<pr>] [--notes <text>] [--watch]"
---

# Request a fix

Turn the bot's unresolved review threads into **one** `/agent` comment, post it, and follow the
task it spawns. One comment, not one per finding: the bot serializes `/agent` tasks per branch,
so N comments queue N runs against the same branch
(`${ARTEL_ROOT}/docs/bratishka-duty.md`, `## Requesting a fix`).

The bot reads that comment without the pull request in front of it, so a finding is composed as a
block — the diff hunk its comment was written against with the commented line marked, every image
the thread embeds with the thread's own link, every reply in the thread, and the suggested change
— never as a bare `path:line`.

An image is a finding's other half. A reviewer who points instead of writing leaves an anchor and
a screenshot, and the screenshot is what is wrong; the anchor only says where. The bot can look
at a picture and cannot run the application, so the payload asks it to come back with the
reproduction — the change and how to reach the same state — which the person checks by eye or
hands to the project's own UI automation.

This is a write. Check `.artel/config.json` first: `vcs.adapter` must be `github-cli`,
otherwise stop and name the value. `bratishka.dashboardUrl` is optional — without it the task
correlation is unavailable and the report says so; posting still works.

Every engine call prints one envelope. Exit `2` is a configuration or environment error: print
`error.message` and stop. Exit `1` with `error.kind: nothing_to_do` is a state answer, not an
error.

## Prerequisites

One call each; nothing is posted on a guess.

1. **`vcs.adapter` is `github-cli`** and `gh auth status` passes — both the read and the comment go
   through `gh` (`${ARTEL_ROOT}/docs/config.md`, adapter identity checks).
2. **The engine answers** — `findings` exit `2` means the configuration or the environment, not an
   empty PR; print `error.message` and stop.
3. **The dashboard is optional** — `bratishka.dashboardUrl` empty or unreachable costs the task
   correlation only; posting still works, and the report says correlation is unavailable.

## 1. Resolve the PR

With no `<pr>`, read the current branch's PR; with an argument, read that number or URL:

```bash
gh pr view [<pr>] --json number,state,headRefName,url
```

- No PR for the branch, or a failed read → ask for the number and stop until it is given.
- `state` is not `OPEN` → say whether it is `MERGED` or `CLOSED` and stop: a merged or closed
  PR never receives a fix request.

Keep the number, the head branch and the URL for the report.

## 2. Read the findings

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py findings --pr <n> --json
```

`data.threads` are the **unresolved** review threads, numbered from 1 in `index`. A resolved
thread is never a finding. By default only the bot's own root threads are findings; add
`--all-authors` to the command only when the person explicitly asks to see every reviewer's
unresolved threads, and say that is what the list is.

Each thread carries what its comment was written against: `path` and `line` (the anchor),
`outdated` when GitHub reports the code it targeted has changed since, `original_line` (where it
sat before that), `hunk` (the diff hunk the comment was written against), `body` and `author` of
the root comment, `url` (the thread's own link), and `comments` — every comment in the thread,
root first, replies after.

A comment may carry images. They are the finding, not decoration: a comment whose body is only a
screenshot is a finding with no words, and the image says what is wrong while the anchor says
where. Never treat an image-only finding as empty, and never summarise one without saying it has
an image — the person chose the picture because describing it in text was the slow path.

Show a numbered list: `index`, `path:line`, the `author`, and the first line of `body`; for a
finding with images, say how many, and for an image-only one show the image count instead of a
blank. Say how many findings there are, and name any with `outdated` true: their anchor is gone
from the diff, so the fix has to start from the excerpt and the file, not from the line number.
When `data.threads` is empty, say that the bot has no unresolved thread on this PR; stop unless
`--notes` was given.

With `--notes "<text>"`, skip the list and compose from the notes alone (step 4 passes
`--select none`).

## 3. The active-task guard

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py task-find --pr <n> --json
```

`data.active_tasks` lists **every** `active` or `queued` task whose description names this PR,
whichever reviewer started it and even when a newer completed task exists. `data.task` is the
caller-scoped newest task used for post-posting correlation, and `data.match` says how it was
found (`description`, `head_ref`, `pr_time` or `none`).

- `data.active_tasks` is not empty → **stop**: report each `task_id`, status and initiator, and
  do not post. A second `/agent` comment would queue a duplicate run on the same branch.
- `data.dashboard_online` is false → proceed, but say that correlation is unavailable: the
  dashboard is not configured or unreachable, and the report will have to follow the PR instead.
- `data.login_error` is set → proceed, but say the post-action match is weaker (`match: pr_time`)
  because the caller login could not be read; the active-task list is still authoritative.
- Any other status, or `match: none` → proceed.

## 4. Compose, then confirm

Ask which findings to fix — default **all** — and for any extra notes. Use the `question` tool:
**Which findings should the bot fix?** — **All of them** / **Pick numbers** / **Cancel**. On
**Pick numbers**, take the displayed `index` values; on **Cancel**, or with no answer, stop
without composing. Then ask, in the chat, for extra notes; no reply means no notes.

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py compose-fix --pr <n> --json \
  [--all-authors] --select <index,...> --notes "<notes>" \
  --out .artel/run/bratishka/<date>/fix-<n>.md
```

- Omit `--select` to select every finding; pass `--select none` with `--notes` to compose from
  the notes alone. `<index,...>` is the displayed numbering, not a zero-based position.
- `--all-authors` **must** repeat the flag given to `findings` in step 2. Without it `compose-fix`
  reads the bot's threads only, numbers a different list, and `--select` would point at the wrong
  findings — a reviewer's thread would silently drop out of the request.
- `<date>` is today's local date in `YYYY-MM-DD`; `<n>` is the PR number. Omit `--notes` when
  there are none; never pass an empty string.
- Exit `1` with `nothing_to_do` → nothing was selected and there are no notes; say so and stop.

`data.body` is the comment and `data.body_path` the file written. Each finding is composed as a
block, not a line: its anchor, the diff hunk with the commented line marked, every image the
thread embeds listed with its URL and the thread's own link, the thread's comments and replies,
and the suggested change when there is one. The payload also names the images as part of the
requirement — and asks the bot, for a finding whose images show a screen or a document, to answer
with a short "What changed and how to see it": the change and the way to reach the same state.
That is deliberate — the bot reads the comment without the PR in front of it, and it cannot run
the application. Show the body verbatim, then ask with the
`question` tool: **Post this fix request?** — options **Post it** / **Cancel**. On **Cancel**, or
with no answer, stop: the file stays for inspection and nothing is posted.

## 5. Post it

Re-read the PR state first; a PR that became merged or closed since step 1 never receives the
comment:

```bash
gh pr view <n> --json state
```

While the state is `OPEN`, record the UTC time (`date -u +%Y-%m-%dT%H:%M:%SZ`) **before**
posting — it is the `--since` bound for the correlation — then post the composed file as one
top-level comment:

```bash
gh pr comment <n> --repo <repo> --body-file .artel/run/bratishka/<date>/fix-<n>.md
```

`<repo>` is `data.repo` from the compose answer. Never post a second comment for the same
request, never reply inside a review thread, and never edit or delete the bot's comment.

## 6. Correlate the task

Poll with the posting time recorded in step 5:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py task-find --pr <n> --since <posted-at> --json
```

Every 30 seconds, at most 10 times (5 minutes). A task appears when `data.task` is not null;
report its `task_id`, `status`, `initiator` and `head_ref`. A `data.match` of `head_ref` means
the description had no `PR <owner>/<repo>#<n>` marker and the branch was used instead; `pr_time`
means the caller login could not be read and the match is weaker.

- No task after 5 minutes, or `data.dashboard_online` false → post the comment URL and say that
  the dashboard has not shown the task. Poll the PR every 15–30 seconds instead and treat the
  bot's summary comment as completion; a read-only ask may answer only in its task report.

With `--watch` and a task, follow it to a terminal state:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py task-watch --task-id <task-id> --json
```

The default bound is 15 minutes, polled every 30 seconds. `data.terminal` true means the task
reached `completed`, `failed` or `cancelled`; summarize its `status` and `result_summary`.
`data.timed_out` true is not a failure: report the last `data.task` state and the task id, and
say it may still be running. Without `--watch`, report the task and stop; do not poll further.

When the bot's summary carries a **"What changed and how to see it"** for a visual finding, quote
it in the report and give the link to the summary comment. That reproduction is the visual check:
the person pastes the link into the host project's own automation skill to reach the same state,
or follows the steps by hand. The engine does not read it and no gate depends on it — never
report it as a verification result.

## 7. Check what the fix changed

Once the task is terminal — or later, when the person asks — re-read the threads, with the same
`--all-authors` flag step 2 used:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py findings --pr <n> --json
```

Compare with the selection from step 4. A selected finding that no longer appears is resolved or
outdated as far as GitHub is concerned; one that still appears was not fixed. Report both by
`path:line`. Do not claim a finding is fixed: the engine reads thread state, never the diff, and
cannot tell a real fix from a thread a human resolved by hand.

This step is read-only. Never resolve, reply to or edit a thread, and never close the PR on the
strength of it.

## Hard rules

- NEVER post on a merged or closed PR, and never when an `active` or `queued` coder task already
  works the PR.
- NEVER post a second comment for the same request, reply inside a review thread, or edit or
  delete the bot's comment.
- NEVER include secrets, tokens, cookies or customer data in the comment or in the notes.
- NEVER widen the selection: `--notes` composes from the notes alone, and `--select none` says so.
- NEVER send `--all-authors` to one of the two engine calls and not the other.
- NEVER trim a finding back to its `path:line`: the block carries the hunk, the replies and the
  suggestion because the bot reads it without the PR.
- NEVER drop a finding's images or the thread's link — and never read an image-only finding as an
  empty one: the picture is what is wrong, the anchor is only where.
- NEVER report the bot's "What changed and how to see it" as a verification result: it is a
  reproduction for the person's eye or the host's automation, not a gate.
- NEVER claim a finding is fixed: the engine reads thread state, never the diff.
- NEVER write the comment body by hand or post without the composed file.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Posting while a coder task is active on the PR | `task-find` first; a second run queues behind the first |
| Using `--select` after an `--all-authors` list without the flag | Repeat the flag on both calls or the numbering differs |
| Summarising a finding to one line | Keep the composed block whole |
| Reading a screenshot-only finding as empty | It is a finding: the image says what, the anchor says where |
| Calling a design finding verified because the task completed | Quote its reproduction and the summary link; the eye decides |
| Claiming a finding is fixed because its thread vanished | Report resolved or outdated as GitHub sees it, and say which |
| Posting the notes as a new comment per finding | One comment per request |
| Polling forever after 5 minutes with no task | Report the comment URL and say the dashboard has not shown the task |
| Treating `data.dashboard_online: false` as a failure | Proceed, and say correlation is unavailable |
| Posting on a PR that closed while you composed | Re-read `state` immediately before posting |

The full contract for this flow is `${ARTEL_ROOT}/docs/bratishka-duty.md` (`## Requesting a fix`,
`## Safety and degradation`).
