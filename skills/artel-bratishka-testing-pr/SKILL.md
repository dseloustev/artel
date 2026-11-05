---
name: artel-bratishka-testing-pr
description: "Use when the Bratishka duty reviewer wants today's testing pull request built through the bot — selects the source PRs, composes the coder prompt, opens the dashboard's /spawn page, then finds the spawned task by its run id and reports the PR separately from a failed completion gate. `--local` builds the branch and PR with git and gh instead."
argument-hint: "[--print] [--local] [--yes] [--dry-run]"
---

# Build today's testing PR

The daily QA vehicle: one PR bundling the source PRs that have code review but not QA
approval. It lives one day, and the next duty reviewer closes it
(`${ARTEL_ROOT}/docs/bratishka-duty.md`, `## Testing PR`).

Check `.artel/config.json` first: `vcs.adapter` must be `github-cli`, and the manual spawn path
needs `bratishka.dashboardUrl` (origin only, no trailing path). Stop and name the value or key
when either is missing; with no dashboard origin, `--local` is the only path. This release ships
the `manual` spawn adapter; another `spawn.adapter` value is reported and the manual handover
below is used — it never fails the run.

Every engine call prints one envelope. Exit `2` is a configuration or environment error: print
`error.message` and stop.

## 1. Plan

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-plan --json
```

`data.tasks` are the selected source PRs; `data.title`, `data.branch`, `data.base_ref`,
`data.base_sha` and `data.run_id` are the plan.

- `data.tasks` empty → say that no source PR is ready (code-reviewed and not QA-approved) and
  stop.
- Show a short table: ticket, PR number and head branch per source PR, then the planned title,
  branch, base ref and base sha.
- Refuse when today's testing PR or branch already exists: read
  `python3 ${ARTEL_ROOT}/scripts/bratishka.py status --json --offline` for a `testing_prs` entry
  dated today, and `git ls-remote --heads origin <branch>` for the planned branch. Say which one
  exists and stop; one testing PR per day.
- `--dry-run` → print the plan and the commands this run would take, then stop: no prompt file,
  nothing opened, nothing spawned.
- Unless `--yes`, ask with the `question` tool — **Build today's testing PR?** — options
  **Build it** / **Cancel**. On **Cancel**, or with no answer, stop: nothing was written.
  `--yes` is the person's explicit consent; never add it on your own.

With `--local`, go to step 5 after this confirmation.

## 2. Compose the prompt

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-prompt \
  --out .artel/run/bratishka/<date>/testing-pr-prompt.md --json
```

`<date>` is today's local date in `YYYY-MM-DD`. The answer carries `prompt_path` and a fresh
`run_id`; read the file and print its text. The prompt ends with its `Run id:` marker — that
marker is how the spawned task is found, so never edit it.

Copy the prompt to the clipboard when a clipboard command exists, and say which one was used:

- macOS — `pbcopy < <prompt_path>`
- Windows — `clip < <prompt_path>`
- Linux — `wl-copy < <prompt_path>`, else `xclip -selection clipboard < <prompt_path>`

No clipboard command → say so; the printed prompt is still copyable. Then print the `Run id`
and `<bratishka.dashboardUrl>/spawn`.

`--print` stops here: report the prompt path, the run id and the URL, and open nothing.
Otherwise open `<bratishka.dashboardUrl>/spawn` in the person's browser with the platform's
opener (`open`, `xdg-open`, `start`); when no opener exists, say the URL instead of failing.

## 3. The person spawns the task

Tell them: open the page if it is not open, choose the `coder` preset, paste the prompt, and
spawn. Then ask with the `question` tool — **Spawned?** — options **It's running** / **Not
yet** / **Stop**. Only **It's running** starts the polling below; **Stop**, or no answer, ends
the skill with the prompt path and run id in hand. Never poll before the person says it is
spawned.

## 4. Verify the PR

Poll:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-verify --run-id <run-id> --json
```

Every 30 seconds, at most 30 times (15 minutes). The envelope is `ok: true` whenever the read
succeeds; `data` is `{run_id, task, pr, gate_failed}`.

- `data.pr` is not null → the artifact exists. Report its `number`, `title`, `url` and head
  branch. If `data.gate_failed` is true, report the gate **separately**: the coder task is red
  on its completion gate (`final_tools_gate_not_satisfied`) while the PR is the artifact. That
  is expected while a repository-wide CI failure lasts, and is not a failed testing PR.
- `data.pr` is null → report `data.task` when it exists (`status`, `result_summary`); a null
  task means the dashboard has not shown the task yet. Keep polling.
- After 15 minutes with no PR, report the last task state, the run id and
  `<bratishka.dashboardUrl>/spawn`. Do not call it a failure: the task may still be running.
- The PR read does not require the dashboard: with `bratishka.dashboardUrl` empty,
  `testing-pr-verify` still matches today's title or branch; `task` stays null and the report
  says the task could not be read.

## 5. The `--local` fallback

With `--local`, skip steps 2–4. After the same plan and confirmation, run:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py testing-pr-local --yes --json
```

It builds the same branch and body in a throwaway worktree under `.artel/run/bratishka/`, pushes
it and opens the PR; a conflict stops it with the conflicting files and no push. Report
`pr_url` or the conflicts. The confirmation you already gave is this command's consent. Never
build the branch in the working checkout by hand.

## Rules

- One testing PR per day. Never create a second when today's title, branch or PR exists.
- Never merge the testing PR, push to the base branch or a source branch, or edit, close,
  rebase or force-update a source PR.
- The prompt's `Run id:` marker is the only correlation key; never guess a task by time alone.
- `--yes` is explicit consent; `--print` stops before `/spawn`; `--dry-run` writes nothing.
