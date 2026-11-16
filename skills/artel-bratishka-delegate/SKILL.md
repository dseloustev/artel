---
name: artel-bratishka-delegate
description: "Use when the person wants one or more tickets implemented by the Bratishka bot — one task, one branch and one pull request per ticket, run concurrently, with each ticket's status kept in step. Gathers each issue and its transitions itself, hands the facts to the engine, and sends the bot's work back when it misses a requirement. Never reviews or merges a pull request."
argument-hint: "<KEY> [<KEY> ...] [start=<status>] [handoff=<status>] [--yes] [--dry-run]"
---

# Delegate tickets to the bot

One ticket per task, branch and pull request, so history belongs to a single ticket and the batch
takes as long as its slowest issue — not the sum of them
(`${ARTEL_ROOT}/docs/bratishka-duty.md`, `## Delegating a ticket`).

The engine has **no tracker client**: this skill gathers the facts and the engine decides. Status
moves come from `${ARTEL_ROOT}/docs/tracker-status.md` and are never hardcoded.

## Prerequisites

One call each; nothing is spawned on a guess.

1. **`vcs.adapter` is `github-cli`** and `gh auth status` passes — the pull requests, the reviewer
   request and the close-out reads all go through `gh`
   (`${ARTEL_ROOT}/docs/config.md`, adapter identity checks). Name the failing account and stop.
2. **The tracker is reachable** — `tracker.adapter: "jira-mcp"` and its `whoami` tool answers under
   `tracker.mcpToolPrefix`. Without it the issues cannot be read: stop and name the missing server.
3. **The task surface** — `bratishka.spawn.adapter` is `"mcp"` with `spawn_task`, `get_task`,
   `get_task_result`, `continue_task` reachable, or `"client-api"` with `spawn.tokenEnv` exported.
   Prove it with one call before the first spawn.
4. **The reviewer** — `bratishka.delegate.reviewerLogin`, or the requesting user's own login from
   `gh api user --jq .login`. Never the ticket's reporter or assignee, never a guess.

## 1. Resolve the keys

Keys come from the request, space-, comma- or newline-separated. No key → **ask for it and stop**:
never pick the most recently updated issue and never infer one from the conversation.

Deduplicate, keep the person's order, and say so when a key is repeated. The per-key `start=` and
`handoff=` forms override the configured statuses for that one key.

## 2. Gather the facts

Call the tracker once per key, all in one parallel batch — the reads are independent:

- `<prefix>jira_get_issue(KEY)` — summary, description, status and its category;
- `<prefix>jira_get_transitions(KEY)` — the transitions available **from that status**.

Write both answers, keyed by issue key, to
`.artel/run/bratishka/<date>/delegate/issues.json` and `transitions.json`. `<date>` is today's
local date. These files are what the engine reads; nothing is re-fetched later in the run.

## 3. Plan — the engine decides

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py delegate-plan --keys <KEY>[,<KEY>] \
  --issues .artel/run/bratishka/<date>/delegate/issues.json \
  --transitions .artel/run/bratishka/<date>/delegate/transitions.json --json
```

`data.table` is one row per requested key — key, outcome, branch, the two moves, the reason.
`data.spawn_calls` is one entry per `ready` key, already carrying the prompt and the idempotency
key. `data.needs_confirmation` is true when the batch is larger than `delegate.maxBatch`.

- **`ready`** — the key will be delegated; nothing to decide.
- **`needs-decision`** — show `reason` and the `candidates` (transition → target status) and ask
  which one to use. Never pick one yourself. A key that stays undecided is left out and reported.
- **`invalid`** — the tracker returned no such issue; report it and leave it out.

Then confirm once, unless `--yes`: **Delegate these N tickets?** — **Delegate them** / **Cancel**.
With `--dry-run`, print the table and the spawn calls and stop: nothing was moved or spawned.

## 4. Start the batch

**Move every ready ticket first**, in one parallel batch: for each key apply the transition the
engine resolved for `start` (`data.rows[].start.transition_id`). Skip a `skip`, and ask before a
`confirm` — the engine flags a transition that reads like a move backwards.

Then **spawn every task**, one `spawn_task` per ready key, all in one batch, passing
`data.spawn_calls[]` verbatim: `prompt`, `idempotency_key`, `context`, `preset: "coder"`,
`task_kind: "agent"`, `repo_url`, `base_ref`. Do **not** pass `head_ref` — the bot creates the
branch and the pull request itself.

Record each returned `task_id` against its key. A `deduplicated: true` answer means an existing
task was reused; record it and move on. A failed spawn marks that key `spawn-failed` and leaves the
ticket in its start status — the rest of the batch continues.

## 5. Close out as tasks land

Poll in rounds: in one message call `get_task(task_id, wait_timeout=300)` once for **each distinct**
task still running. A round costs one wait, however many tasks are in flight; a
`wait_timeout_exceeded` answer is a passed deadline, not a failure.

For each task that reaches a terminal state, independently of the others:

1. **Find its pull request** — `gh pr list --repo <repo> --state open --json
   number,title,url,headRefName,author,body --limit 100`, matching a head branch or title that
   carries the key. The bot commits as `bratishka.botLogin`.
2. **Request the reviewer** the moment the PR exists:
   `gh pr edit <n> --add-reviewer <login>`, then re-read `reviewRequests`. A failed request is a
   gap to report, never a silent success.
3. **Write the facts** — `task.json` with the `get_task`/`get_task_result` answer and `pr.json`
   with the normalized PR — and run:

   ```bash
   python3 ${ARTEL_ROOT}/scripts/bratishka.py delegate-closeout --key <KEY> \
     --task .artel/run/bratishka/<date>/delegate/<KEY>.task.json \
     --pr .artel/run/bratishka/<date>/delegate/<KEY>.pr.json --json
   ```

   `data.gaps` is what the bot got wrong, `data.send_back` whether to send it back, and
   `data.gate_failed` says a failed task still delivered its pull request.
4. **Send it back** on `send_back: true` with `continue_task(task_id, prompt)` — it forks the
   finished task with its history and branch, so the same pull request updates. The prompt names
   the gap verbatim and nothing else.
5. **Move the ticket** to its `handoff` status, then comment the pull request URL on the ticket.
   Both only for a delivered PR: a failed key stays in its start status.

## 6. Report

One row per **requested** key, in the order given — including the keys that never reached a task:
key, moves applied, task id and status, outcome, pull request. Then per key: the conclusion quoted,
the gap that was sent back and whether it closed, and what is left for a human (review, QA).
Close with `data.counts` as one line.

## Hard rules

- NEVER guess a key, a repository, a base branch, a status name or a reviewer login: ask.
- NEVER pass `head_ref`; the bot must create the branch and the pull request.
- NEVER share an idempotency key between two issues: `<KEY>:agent` is per issue, and one shared key
  silently collapses the batch into a single task.
- NEVER spawn before the confirmed batch, and never add `--yes` on your own.
- NEVER choose a status the engine answered `needs-decision` for, and never reopen a done issue
  without asking.
- NEVER merge a pull request, push to a branch, or edit a ticket this run did not touch.
- NEVER report a status change or a pull request the run did not observe, and never present a
  failed completion gate with an existing pull request as a failed delegation.
- NEVER stop the batch for one bad key: mark it, continue, and report it.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Running one ticket end to end before the next | Batch by phase: gather all, move all, spawn all, then poll |
| Spawning before the status moves | Move every ready ticket first, then spawn |
| Picking a status when two matched | Show the candidates and ask |
| Using the Jira reporter or assignee as reviewer | The requesting user's login, or `delegate.reviewerLogin` |
| Looking for a reviewer parameter on `spawn_task` | There is none: `gh pr edit --add-reviewer` |
| Re-spawning for a follow-up | `continue_task` on the finished task |
| Waiting for the whole batch before a handoff | Close out each key as its own task lands |
| Dropping a key that failed to load | Every requested key gets a row with an outcome |
| Treating a red gate with an existing PR as a failure | The PR is the artifact; report the gate separately |
| Reporting "live" from the dashboard instead of `get_task` | The task state comes from the task tools |

## Quick reference

| Tool | Purpose |
| --- | --- |
| `<prefix>jira_get_issue` | The issue, its description and its status category |
| `<prefix>jira_get_transitions` | The transitions available from the current status |
| `<prefix>jira_transition_issue` | Apply a resolved move |
| `<prefix>jira_add_comment` | Record the pull request on the ticket |
| `delegate-plan` | Outcomes, moves, prompts and spawn arguments from the gathered facts |
| `delegate-closeout` | One finished task's outcome, gaps and send-back flag |
| `spawn_task` | Start one coder task per issue, never with `head_ref` |
| `get_task` / `get_task_result` | Poll a round, then read the authoritative outcome |
| `continue_task` | Send a finished task back with the gaps |
| `gh pr list` / `gh pr edit` / `gh pr view` | Find the PR, request the reviewer, read its state |
