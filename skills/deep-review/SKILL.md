---
name: deep-review
description: "Review a branch once, forecast from kartoteka precedents which changes will draw reviewer comments, write deep-review.md, and offer to work the fixes"
argument-hint: "[ticket-id] [branch] [pr-link] [--local]"
model: sonnet
---

This skill is a multi-step orchestrator — run the steps in order. It dispatches two agents,
the `reviewer` and the `review-forecaster`, and never reviews, forecasts or edits anything
itself. The forecast's contract is `${CLAUDE_PLUGIN_ROOT}/docs/review-forecast.md`; the steps
below cite it as `§N`.

`--local` flag: skip the kartoteka lookup and record why. It short-circuits §1 before
capability is considered, exactly as it does for `analysis` and `researcher`. It may appear
in any position — strip it before reading `$0`, `$1`, `$2`, and remember that it was passed.

## Step 0: Quality gate (`verify.commands`)

Always run this step first. The review must not proceed against a tree that fails the
project's own checks.

1. Display:
   ```
   Running quality gate...
   ```
2. Run each command in `verify.commands` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`), in order,
   stopping at the first non-zero exit.
3. Inspect the result:
   - `verify.commands` is empty → the gate is **skipped** (config.md; never reported as green) —
     display `Quality gate: skipped (verify.commands not configured).` and continue to Step 1.
   - Any command exits non-zero → verification failed.
   - Every command exits `0` → verification passed.
4. On failure, display:
   ```
   Quality gate failed. Review cannot proceed.

   Summary of issues:
   <copy the relevant failure output from the failing command(s)>

   Fix these and re-run /artel:deep-review <TICKET_ID> [branch] [pr-link] [--local].
   ```
   Then terminate the skill. Do not run any further steps.
5. On success (or skip), display `Quality gate passed.` (or the skipped notice from step 3) and
   continue to Step 1.

## Step 1: Resolve the ticket and check for an existing file

### 1a: Resolve the ticket key

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and
`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty
line of `<specs.dir>/.active_ticket`; if no identifier is available, display the following and
terminate:
```
Error: No ticket key provided and <specs.dir>/.active_ticket is missing.
Usage: /artel:deep-review <ticket-id> [branch] [pr-link] [--local]
```

`deep-review` is ticket-wide only: `PHASE_NUM` is parsed for input compatibility (so a
phase-suffixed identifier is accepted) but discarded — the same convention
`${CLAUDE_PLUGIN_ROOT}/skills/pr-description/SKILL.md` uses.

Then verify that `<specs.dir>/<TICKET_ID>/` exists on disk. On the kartoteka path the ticket
exists when `spec_store.py list <TICKET_ID>` prints a non-empty array; the directory need not
exist. If it does not, display the following and terminate:
```
Error: Ticket directory <specs.dir>/<TICKET_ID>/ does not exist.
```

Display:
```
Active ticket: <TICKET_ID>
Ticket directory: <specs.dir>/<TICKET_ID>/
```

### 1b: Existing output

If `<specs.dir>/<TICKET_ID>/deep-review.md` exists (kartoteka path: `spec_store.py exists
<specs.dir>/<TICKET_ID>/deep-review.md`), ask via `AskUserQuestion`:

> "`deep-review.md` already exists for <TICKET_ID>. Overwrite it with a fresh review?" — Yes / No.

On "No", display `Keeping the existing <specs.dir>/<TICKET_ID>/deep-review.md.` and terminate.
On "Yes", continue; the file is replaced in Step 4.

## Step 2: Parse arguments

### 2a: Branch name (`$1`)

If `$1` is empty or not provided → review current branch (no `BRANCH_NAME` set).

If `$1` is provided → store as `BRANCH_NAME`. Display:
```
Target branch: <BRANCH_NAME>
```

### 2b: PR link (`$2`)

If `$2` is empty or not provided, skip this substep — there is no PR context.

If `$2` is provided, parse it per `vcs.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`):

**`"github-cli"`** (default) — expects `https://github.com/<owner>/<repo>/pull/<number>` (or a
bare PR number against the current repo). Extract `owner`, `repo`, `prNumber`. Call
`gh pr view <prNumber> --repo <owner>/<repo> --json title,body`.

**`"bitbucket-mcp"`** — expects a Bitbucket Server PR URL:
```
/projects/([^/]+)/repos/([^/]+)/pull-requests/(\d+)
```
Extract `projectKey`, `repositorySlug`, `prId`. Call `<vcs.mcpToolPrefix>bitbucket_get_pr` with the
extracted values.

If the link does not match the expected shape for the configured adapter, display an error and
terminate:
```
Error: Could not parse the PR link for the configured VCS adapter (<vcs.adapter>).
```

Store the result as `PR_TITLE` and `PR_DESCRIPTION` for use in subsequent steps. Display:
```
Fetched PR #<prId or prNumber>: <PR_TITLE>
```

### 2c: Forecast mode and forecast config

Resolve the **forecast mode** per `${CLAUDE_PLUGIN_ROOT}/docs/review-forecast.md` §1, in
this order:

1. `--local` was passed → `off: local-only run requested`.
2. Read `knowledge.adapter` from `.artel/config.json` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`).
   `none` or absent → `off: knowledge.adapter is not kartoteka for this project` — whether or
   not kartoteka tools happen to be present (an undeclared index is not this project's).
   Any value other than `none` or `kartoteka` → configuration error (config.md reading rule 3):
   display `Error: knowledge.adapter has an unrecognised value (<value>).` and terminate.
   `kartoteka` with `knowledge.project` empty or outside `^[a-z0-9][a-z0-9-]*$` →
   `off: kartoteka is configured for this project but knowledge.project is not set` — the
   forecaster gets no project to name, so it cannot consult (review-forecast.md §1).
3. `kartoteka`, and `search_knowledge`, `related` and `index_status` are among the tools
   available to you in this session → `on`. Otherwise →
   `off: kartoteka is configured for this project but its MCP tools are not available in this session`.

Display `Forecast: <mode>`.

Then read the forecast config (config.md, `review` section):

- `review.forecast.threshold` — default `70`. Anything but an integer from 1 to 99 is a
  configuration error: display `Error: review.forecast.threshold must be an integer from 1 to 99 (got <value>).`
  and terminate. Store as `THRESHOLD`.
- `review.forecast.reviewers` — default `[]`. Anything but an array of strings is a
  configuration error: display `Error: review.forecast.reviewers must be an array of strings.`
  and terminate. Store as `REVIEWERS`.

## Step 3: Dispatch the reviewer (standalone mode)

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

Use the Agent tool to spawn the `reviewer` agent (`${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`) in
standalone mode — no ticket mode; the report goes to run-state evidence at
`.artel/run/<TICKET_ID>/reports/deep-review-findings.md` (the agent's standalone-mode Output
section accepts a caller-specified path). Ticket files are passed as additional context only.

- `subagent_type: "reviewer"`
- `description`: `"Code review for <TICKET_ID>"`
- `prompt`: always starts with `Run in **standalone mode** — no ticket context.`

**Branch targeting line** (first line after the standalone-mode instruction):
- Without `BRANCH_NAME`: `Review the current branch changes following your standard checklist.`
- With `BRANCH_NAME`: `` Review the changes on branch `<BRANCH_NAME>` following your standard checklist. ``

**Without PR context** (Step 2b was skipped):
```
Run in **standalone mode** — no ticket context.
<branch targeting line>
Save your review report to .artel/run/<TICKET_ID>/reports/deep-review-findings.md (create the directory if needed).

Additional ticket context:
- Active ticket: <TICKET_ID>
- Ticket directory: <specs.dir>/<TICKET_ID>/
- Read files from that directory (idea.md, vision.md, prd.md, plan.md, tasklist.md, research.md, phase-*/ subfolders, etc.) as needed to understand the feature's intent, scope, and acceptance criteria.
- Use this context when judging whether the diff fulfills the ticket. Do NOT switch to ticket mode (do not write into the tasklist) — still write your report to the path above.
- If you need additional tracker/PR detail, you may call the configured tracker (`tracker.adapter`) / VCS (`vcs.adapter`) tools per config.md, when connected.
```

**With PR context** (Step 2b fetched PR details): the same prompt, plus a PR Compliance block
ahead of the "Additional ticket context" bullets — the agent's standalone-mode contract adds a
PR Compliance section whenever a PR description is present in the prompt, so just supply it:

```
Run in **standalone mode** — no ticket context.
<branch targeting line>
Save your review report to .artel/run/<TICKET_ID>/reports/deep-review-findings.md (create the directory if needed).

PR Compliance Check — the PR claims to implement the following:
Title: <PR_TITLE>
Description: <PR_DESCRIPTION>

Additional ticket context:
[... same five bullets as above ...]
```

Wait for the agent to complete, then verify the file exists:
```bash
test -f .artel/run/<TICKET_ID>/reports/deep-review-findings.md && echo "Found deep-review-findings.md" || echo "File not found"
```

If the file is missing, re-dispatch once with the same prompt. If it is still missing, display
`Error: the reviewer produced no report after two attempts.` and terminate — never write the
review yourself.

## Step 4: Dispatch the forecaster

Use the Agent tool to spawn the `review-forecaster` agent
(`${CLAUDE_PLUGIN_ROOT}/agents/review-forecaster.md`). It always runs — with the forecast off
it still writes the file, with the definite-issues table filled from the reviewer's report and
the forecast table listed without numbers.

- `subagent_type: "review-forecaster"`
- `description`: `"Forecast review outcome for <TICKET_ID>"`
- `prompt`:

```
<branch targeting line — the same line Step 3 used>
Reviewer's report: .artel/run/<TICKET_ID>/reports/deep-review-findings.md
Ticket directory: <specs.dir>/<TICKET_ID>/
Forecast mode: <mode, verbatim from Step 2c>
Threshold: <THRESHOLD>
Reviewers: <REVIEWERS as a JSON array, e.g. [] or ["Name One", "Name Two"]>
Output path: <specs.dir>/<TICKET_ID>/deep-review.md
[PR title: <PR_TITLE> — only when Step 2b fetched PR context]
[PR description: <PR_DESCRIPTION> — only when Step 2b fetched PR context]

Follow ${CLAUDE_PLUGIN_ROOT}/docs/review-forecast.md. Write the output file and return the three-line completion.
```

Wait for the agent to complete, then verify the file exists:
```bash
test -f <specs.dir>/<TICKET_ID>/deep-review.md && echo "Found deep-review.md" || echo "File not found"
```

Kartoteka path:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py exists <specs.dir>/<TICKET_ID>/deep-review.md && echo "Found deep-review.md" || echo "File not found"
```

If the file is missing, re-dispatch once with the same prompt. If it is still missing, display
`Error: the forecaster produced no file after two attempts.` and terminate.

Read the three counts from the agent's completion (`Table 1 … <n> rows`, `Table 2 … <m> rows`,
`At risk … <k> rows`). Do not open the file to recount — bulk stays in files
(`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §1).

## Step 5: Offer to apply

Display:
```
Deep review written to <specs.dir>/<TICKET_ID>/deep-review.md
Forecast: <mode>
Definite issues: <n> · Forecast rows: <m> · At risk (below <THRESHOLD>%): <k>
```

If `n` and `k` are both `0`, display `Nothing to apply.` and terminate.

Otherwise ask via `AskUserQuestion` which fixes to work. Offer only the options that have rows:

- **Definite issues only** — the `### Tasks` block under `## 1. Definite issues` (when `n > 0`).
- **Definite issues and at-risk changes** — that block plus the `### Tasks` block under
  `## 3. Proposed fixes` (when `k > 0`; with `n = 0` label it **At-risk changes only**).
- **Not now** — display `Fixes are in <specs.dir>/<TICKET_ID>/deep-review.md; re-run /artel:deep-review <TICKET_ID> to apply them later.` and terminate.

## Step 6: Apply

1. Copy the checkbox items of the chosen `### Tasks` block(s) from `deep-review.md` — not their
   `### Tasks` heading — under `## Code Review Fixes` in the ticket-wide
   `<specs.dir>/<TICKET_ID>/tasklist.md`, beneath one new source heading
   `### deep-review-<YYYY-MM-DD>`, the date from `date +%F`. The heading keeps this batch's
   rows apart from an earlier round's with the same text
   (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §6); when it is already in the section, use
   `-2`, then `-3`, ….
   - The file is missing → create it with `# Tasklist — <TICKET_ID>` and the section, and
     display `Created <specs.dir>/<TICKET_ID>/tasklist.md with only a ## Code Review Fixes section.`
   - The section is missing → append `## Code Review Fixes` at the end of the file.
   - The source heading goes at the end of the section, before the next `## ` heading.
   - Renumber the copied tasks to continue from the highest `Task N` already in the file.
   - A block reading `- none` copies nothing, and no heading.

   Display `Appended <count> tasks under ## Code Review Fixes in <specs.dir>/<TICKET_ID>/tasklist.md.`

2. Record them in the task queue before any implementer is dispatched
   (`${CLAUDE_PLUGIN_ROOT}/docs/task-queue.md` §2, fix-writer rule). Decide the path per
   task-queue.md §1: `--local` was passed, `knowledge.adapter` is `none` or absent,
   `knowledge.project` is unset, or the kartoteka task tools are absent → skip this step and
   display `Task queue: not used (<reason>)`. Otherwise run. Files path first, kartoteka path
   (`docs/spec-storage.md` §4.2) second:

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
       set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

   and for each entry of `data.sections`, in order,
   `task_create(project=<project>, ticket_key=<TICKET_ID>, title=…, description=…, status=…)`
   the section row, then each of its `children` with `parent_id` set to that row's `task_id` —
   not `data.iterations`. A child emitted `backlog` that comes back `done` → display
   `fix task #<id> is done in the queue but open in the file: <title>`. Surface every
   `data.warnings` line. Exit `2`, or a `Rejected:` line naming `kartoteka project add` →
   display it and continue: the tasks are in the file. Display
   `Recorded <n> fix rows in the task queue.`

3. For each appended task, in order: `Skill: implementer` with `<TICKET_ID>` (plus `--local`
   when this run was invoked with it), naming `## Code Review Fixes` in the invocation — the
   implementer treats a dispatch that names that section as file-scan work, takes the first
   incomplete box under it, and on the queue path keeps its row current. On a `HITL:` or
   `DEVIATION` return, or an aborted task, stop the loop and report it; the remaining tasks
   stay unchecked for the user to decide.

4. Run `verify.commands` once more, exactly as in Step 0, and record the result (`passed`,
   `failed` with the quoted output, or `skipped`). A failure here is reported, not
   terminated on — the tasks that were worked are already in the tree.

5. Report:
   ```
   Applied fixes for <TICKET_ID>:
   - Tasks appended: <count>
   - Tasks completed: <checked count> of <count>
   - Quality gate: <passed | failed | skipped>
   Re-run /artel:deep-review <TICKET_ID> to refresh the forecast.
   ```

## Rules

- **Orchestrator only.** This skill never reads the diff, never judges code, never writes a
  review or a forecast, and never edits code. Copying task blocks between two files under
  `<specs.dir>` is the only text it moves, and the rows Step 6 records are its only writes to
  kartoteka.
- **Read-only on the VCS host** — the PR is fetched, never commented on or edited.
- **Ticket-wide only** — a phase suffix is accepted and discarded.
- **Two agents, one seat each** — the `reviewer` is dispatched once; there is no second
  reviewer and no merged summary. Independence between reviewers was the old design's
  purpose; precedent from kartoteka is this one's.
