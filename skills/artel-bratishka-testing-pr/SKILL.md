---
name: artel-bratishka-testing-pr
description: "Use when the Bratishka duty reviewer wants today's testing pull request built through the bot — selects the source PRs, composes the coder prompt, starts the task through the bratishka MCP tools (spawn_task, get_task, get_task_result), then verifies the PR by its run id and reports it separately from a failed completion gate. `--local` builds the branch and PR with git and gh instead."
argument-hint: "[--print] [--local] [--yes] [--dry-run]"
---

# Build today's testing PR

The daily QA vehicle: one PR bundling the source PRs that have code review but not QA
approval. It lives one day, and the next duty reviewer closes it
(`${ARTEL_ROOT}/docs/bratishka-duty.md`, `## Testing PR`).

The task is started through the **bratishka MCP tools**, never through the dashboard: there is
no `/spawn` page in this path. Check `.artel/config.json` first: `vcs.adapter` must be
`github-cli`. When the host prefixes MCP tool names, `bratishka.spawn.mcpToolPrefix` is that
prefix; when the host reaches bratishka through a compressing MCP router instead of exposing
the tools directly, ask the router for the bratishka server's tools first. Without the tools,
stop and name the missing MCP connection — `--local` is the only non-MCP path.

Every engine call prints one envelope. Exit `2` is a configuration or environment error: print
`error.message` and stop.

## 1. Plan

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-plan --json
```

`data.tasks` are the selected source PRs; `data.title`, `data.branch`, `data.base_ref`,
`data.base_sha`, `data.repo` and `data.run_id` are the plan.

- `data.tasks` empty → say that no source PR is ready (code-reviewed and not QA-approved) and
  stop.
- Show a short table: ticket, PR number and head branch per source PR, then the planned title,
  branch, base ref and base sha.
- Refuse when today's testing PR or branch already exists: read
  `python3 ${ARTEL_ROOT}/scripts/bratishka.py status --json --offline` for a `testing_prs` entry
  dated today, and `git ls-remote --heads origin <branch>` for the planned branch. Say which one
  exists and stop; one testing PR per day.
- `--dry-run` → print the plan and the calls this run would take, then stop: no prompt file, no
  task started.
- Unless `--yes`, ask with the `question` tool — **Build today's testing PR?** — options
  **Build it** / **Cancel**. On **Cancel**, or with no answer, stop: nothing was written.
  `--yes` is the person's explicit consent; never add it on your own.

Keep the confirmed set — every `data.tasks[].number` — frozen for step 2; the approval means
that set and no other.

With `--local`, go to step 5 after this confirmation.

## 2. Compose the prompt

Pass the confirmed set as `--include`, so a PR the person did not approve cannot join:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-prompt \
  --out .artel/run/bratishka/<date>/testing-pr-prompt.md --json \
  --include <confirmed-n>,<confirmed-n>
```

`<date>` is today's local date in `YYYY-MM-DD`. The answer carries `prompt_path`, a fresh
`run_id` and `tasks` — the source PRs the prompt actually bundles. Before starting anything,
compare its `number` set with the confirmed set. `--include` blocks an unapproved PR from
joining, but a confirmed PR that lost candidacy (for example QA approved it between the two
calls) silently falls out of the intersection. If the sets differ, stop: say which PRs left or
joined and ask again with the same **Build today's testing PR?** question; do not use the stale
prompt (the next compose overwrites it). Continue only with an explicitly re-confirmed set, and
update the frozen numbers to the answer's `tasks`.

Read the file and print its text. The prompt ends with its `Run id:` marker; `testing-pr-verify`
correlates the PR with it, so never edit it.

`--print` stops here: report the prompt path and the run id, and start nothing.

## 3. Start the task through MCP

The Build it answer in step 1 is the consent for this step; do not ask again.

The task tools are `spawn_task`, `get_task` and `get_task_result` — prefixed with
`bratishka.spawn.mcpToolPrefix` when the host prefixes MCP tool names, or reached through the
router's generic schema/invoke pair when the host compresses MCP servers. Read each tool's input
schema before the first call; argument names occasionally differ.

Call `spawn_task` once, with:

- `repo_url` — `gh repo view --repo <data.repo> --json url --jq .url`
- `base_ref` — `data.base_ref`
- `prompt` — the prompt file's text, verbatim
- `idempotency_key` — `data.run_id`; a retry with the same key returns the same task, so never
  start a second task for one run id
- `preset` — `coder`; `task_kind` — `agent`

Keep the returned `task_id`: it is the correlation key for everything below. No `task_id` →
print the failing call and its message and stop; nothing was started, and this path never falls
back to the dashboard.

## 4. Wait, then verify the PR

Poll `get_task` with `task_id` and `wait_timeout: 300`, at most 3 times (15 minutes); a terminal
`status` — `completed`, `failed` or `cancelled` — ends the wait, and a `wait_timeout_exceeded`
answer only means the deadline passed. Then call `get_task_result` and report `status`,
`result_status`, `conclusion`, `summary`, `goal_achieved` and `detail` when present.

Poll the PR itself:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-verify --run-id <run-id> --json
```

Every 30 seconds, at most 30 times (15 minutes). The envelope is `ok: true` whenever the read
succeeds; `data` is `{run_id, task, pr, gate_failed}`.

- `data.pr` is not null → the artifact exists. Check the naming before reporting: the title must
  be exactly `data.title` (`Testing dd-mm-yy`) and the head branch exactly `data.branch`
  (`feature/testing-dd-mm-yy`), the same date in both. A PR with another title or head is
  reported as a naming defect; never rename it, rebuild it or start a second task. Report its
  `number`, `title`, `url` and head branch. If `data.gate_failed` is true, report the gate
  **separately**: the coder task is red on its completion gate
  (`final_tools_gate_not_satisfied`) while the PR is the artifact. That is expected while a
  repository-wide CI failure lasts, and is not a failed testing PR.
- `data.pr` is null → keep polling; report the task state from `get_task` /
  `get_task_result`, not from the dashboard, and ignore `data.task`.
- After 15 minutes with no PR, report the last task state, the task id and the run id. Do not
  call it a failure: the task may still be running.

## 5. The `--local` fallback

With `--local`, skip steps 2–4. After the same plan and confirmation, run:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-local --yes --json \
  --include <confirmed-n>,<confirmed-n>
```

`--include` is the same frozen confirmed set the prompt path passes, so a PR the person did
not approve cannot join the build; `--exclude` drops a number that must stay out. Before any
worktree or push the engine requires the selected numbers to equal that confirmed set. If it
answers exit `1` with `error.kind: changed_plan`, nothing was built: say which confirmed PRs
left or joined (`error.left` / `error.joined`) and ask again with the new `error.selected` set;
continue only with an explicitly re-confirmed set. It builds the same branch and body in a
throwaway worktree under `.artel/run/bratishka/`, pushes it and opens the PR assigned to the
duty reviewer; a conflict stops it with the conflicting files and no push. Report `pr_url` or
the conflicts. The confirmation you already gave is this command's consent. Never build the
branch in the working checkout by hand.

## Rules

- One testing PR per day. Never create a second when today's title, branch or PR exists.
- Never merge the testing PR, push to the base branch or a source branch, or edit, close,
  rebase or force-update a source PR.
- The returned `task_id` is the correlation key; one run id is one task — a retried
  `spawn_task` returns the same task, and nothing starts a second one. Never guess a task by
  time alone.
- The PR's title and head branch are the contract: `Testing dd-mm-yy` and
  `feature/testing-dd-mm-yy`, the same date in both. Report a deviation, never repair it.
- `--yes` is explicit consent; `--print` stops before any task starts; `--dry-run` writes
  nothing.
