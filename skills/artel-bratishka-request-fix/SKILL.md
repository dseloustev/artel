---
name: artel-bratishka-request-fix
description: "Use when the Bratishka duty reviewer wants the bot to fix review findings on a pull request — reads the bot's unresolved review threads, composes one self-contained /agent comment for the selected findings plus any notes, posts it after confirmation, and correlates the spawned task. Never posts on a merged or closed PR."
argument-hint: "[<pr>] [--notes <text>] [--watch]"
---

# Request a fix

Turn the bot's unresolved review threads into **one** `/agent` comment, post it, and follow the
task it spawns. One comment, not one per finding: the bot serializes `/agent` tasks per branch,
so N comments queue N runs against the same branch
(`${ARTEL_ROOT}/docs/bratishka-duty.md`, `## Requesting a fix`).

This is a write. Check `.artel/config.json` first: `vcs.adapter` must be `github-cli`,
otherwise stop and name the value. `bratishka.dashboardUrl` is optional — without it the task
correlation is unavailable and the report says so; posting still works.

Every engine call prints one envelope. Exit `2` is a configuration or environment error: print
`error.message` and stop. Exit `1` with `error.kind: nothing_to_do` is a state answer, not an
error.

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

`data.threads` are the **unresolved** review threads, numbered from 1 in `index`, each with
`path`, `line`, `body` and `author`. A resolved thread is never a finding. By default only the
bot's own root threads are findings; add `--all-authors` to the command only when the person
explicitly asks to see every reviewer's unresolved threads, and say that is what the list is.

Show a numbered list: `index`, `path:line`, and the first line of the body. Say how many
findings there are. When `data.threads` is empty, say that the bot has no unresolved thread on
this PR; stop unless `--notes` was given.

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
  --select <index,...> --notes "<notes>" \
  --out .artel/run/bratishka/<date>/fix-<n>.md
```

- Omit `--select` to select every finding; pass `--select none` with `--notes` to compose from
  the notes alone. `<index,...>` is the displayed numbering, not a zero-based position.
- `<date>` is today's local date in `YYYY-MM-DD`; `<n>` is the PR number. Omit `--notes` when
  there are none; never pass an empty string.
- Exit `1` with `nothing_to_do` → nothing was selected and there are no notes; say so and stop.

`data.body` is the comment and `data.body_path` the file written. Show the body verbatim, then
ask with the `question` tool: **Post this fix request?** — options **Post it** / **Cancel**. On
**Cancel**, or with no answer, stop: the file stays for inspection and nothing is posted.

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

## Rules

- One `/agent` comment per request, posted to the PR, never a review thread and never one comment
  per finding.
- Never post on a merged or closed PR, never when an `active`/`queued` coder task already works
  the PR.
- Never include secrets, tokens, cookies or customer data in the comment or the notes.
- Never edit, delete or resolve the bot's comment; the request is a new top-level comment.
- `--notes` composes from the notes alone; the finding list is skipped, not silently widened.
- The engine writes the body file and prints the envelope; nothing in this skill posts on its
  own. The contract is `${ARTEL_ROOT}/docs/bratishka-duty.md` (`## Requesting a fix`,
  `## Safety and degradation`).
