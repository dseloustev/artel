---
name: artel-deep-review
description: "Review a branch once with the whole-branch reviewer, write deep-review.md, and offer to work its fixes"
argument-hint: "[ticket-id] [branch] [pr-link] [--local]"
---

This skill is a multi-step orchestrator — run the steps in order. It dispatches the `reviewer`
agent once, never judges the review itself, and never edits the reviewer's output. The checker in
Step 3b is its only judge.

`--local` flag: record nothing in the kartoteka task queue — the fix tasks still land in the
tasklist file. It is the same meaning the flag has in `run-reviewer`. It may appear in any
position — strip it before reading `$0`, `$1`, `$2`, and remember that it was passed.

The artifact contract — the pinned target, the machine report's schema, the report's exact
metadata, finding IDs and task format, the verification records and the completeness rules — is
single-homed in `${ARTEL_ROOT}/docs/deep-review.md`. This skill names the steps; it does
not restate the schema.

## Step 1: Resolve the ticket and check for an existing file

### 1a: Resolve the ticket key

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per
`${ARTEL_ROOT}/docs/orchestrator-common.md` §2 and
`${ARTEL_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty
line of `<specs.dir>/.active_ticket`; if no identifier is available, display the following and
terminate:
```
Error: No ticket key provided and <specs.dir>/.active_ticket is missing.
Usage: /artel-deep-review <ticket-id> [branch] [pr-link] [--local]
```

`deep-review` is ticket-wide only: `PHASE_NUM` is parsed for input compatibility (so a
phase-suffixed identifier is accepted) but discarded — the same convention
`${ARTEL_ROOT}/skills/artel-pr-description/SKILL.md` uses.

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
<specs.dir>/<TICKET_ID>/deep-review.md`), ask via the `question` tool:

> "`deep-review.md` already exists for <TICKET_ID>. Overwrite it with a fresh review?" — Yes / No.

On "No", display `Keeping the existing <specs.dir>/<TICKET_ID>/deep-review.md.` and terminate.
On "Yes", continue; the file is replaced in Step 3.

## Step 2: Pin the review target and run the quality gate

### 2a: Spec store

Before dispatching, read the ticket's storage decision:
`python3 ${ARTEL_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${ARTEL_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

### 2b: Branch and PR arguments

`$1` is the branch under review: store it as `BRANCH_NAME` and display
`Target branch: <BRANCH_NAME>`. Empty or absent → the checked-out branch, with no `BRANCH_NAME`
set. `$2` is the PR link: parse it per `vcs.adapter` (`${ARTEL_ROOT}/docs/config.md`)
and fetch the PR details as before —

**`"github-cli"`** (default) — expects `https://github.com/<owner>/<repo>/pull/<number>` (or a
bare PR number against the current repo). Extract `owner`, `repo`, `prNumber`. Call
`gh pr view <prNumber> --repo <owner>/<repo> --json title,body,baseRefName,headRefName`.

**`"bitbucket-mcp"`** — expects a Bitbucket Server PR URL:
```
/projects/([^/]+)/repos/([^/]+)/pull-requests/(\d+)
```
Extract `projectKey`, `repositorySlug`, `prId`. Call `<vcs.mcpToolPrefix>bitbucket_get_pr` with
the extracted values. When its result carries the PR's source and target branch names, use them
as the head and base below; ask for no field the configured adapter does not document.

If the link does not match the expected shape for the configured adapter, display an error and
terminate:
```
Error: Could not parse the PR link for the configured VCS adapter (<vcs.adapter>).
```

Store the result as `PR_TITLE` and `PR_DESCRIPTION` for use in subsequent steps, and display
`Fetched PR #<prId or prNumber>: <PR_TITLE>`.

**Confirming the claimed target.** The PR's head branch, when returned, is the claimed target:
with `BRANCH_NAME` unset, adopt it (`BRANCH_NAME = <PR head>`); when `BRANCH_NAME` names a
different branch, display the mismatch and terminate — never retarget:
```
Error: the PR is on branch <PR head>, but the requested target is <BRANCH_NAME>.
Review it from a worktree checked out on <PR head> instead.
```
The PR's base ref, when returned and it resolves locally
(`git rev-parse --verify --quiet <PR base>^{commit}`), is passed to `prepare` as `--base`;
otherwise the engine's default base resolution stands, and the run says which base the PR claims.

### 2c: Pin the target

Resolve `--specs-dir` from the host config: read `.artel/config.json` and pass its `specs.dir`
verbatim (default `specs/.current`, `${ARTEL_ROOT}/docs/config.md`). It is the host's
declaration of a dedicated artifact root — not product code, and not an exemption for source
changes. An invalid value (escaping the repo, `.`, a product directory) is a configuration
error: report it and terminate. Do not invent a source-root heuristic or a new config key.

Then run, from the checkout root:
```bash
python3 ${ARTEL_ROOT}/scripts/deep_review.py prepare \
  --ticket <TICKET_ID> --specs-dir <specs.dir> [--branch <BRANCH_NAME>] [--base <BASE>] \
  --output .artel/run/<TICKET_ID>/deep-review/target.json
```

- Exit `0`: the manifest is written. Display the line it prints (run id, branch, head, base,
  merge-base), then read the manifest and keep `run_id`, `branch`, `base`, `head` and
  `merge_base` for the dispatch. The manifest is run evidence on both spec-store paths.
- Exit `2`: display its `deep_review.py: <reason>` line and terminate. The reason for a branch
  mismatch or a dirty checkout already carries the worktree instruction — display it verbatim.
  Never retarget to the checked-out branch, never check out another branch, never fetch, and
  never review a tree the manifest does not pin.

### 2d: Quality gate (`verify.commands`)

The gate runs against the checkout `prepare` just pinned: its branch, HEAD and pending changes
are exactly the reviewed target's. A tree that fails the project's own checks is not reviewed.

1. Display:
   ```
   Running quality gate...
   ```
2. Run each command in `verify.commands` (`${ARTEL_ROOT}/docs/config.md`), in order,
   stopping at the first non-zero exit.
3. Inspect the result:
   - `verify.commands` is empty → the gate is **skipped** (config.md; never reported as green) —
     display `Quality gate: skipped (verify.commands not configured).` and continue.
   - Any command exits non-zero → verification failed.
   - Every command exits `0` → verification passed.
4. On failure, display:
   ```
   Quality gate failed. Review cannot proceed.

   Summary of issues:
   <copy the relevant failure output from the failing command(s)>

   Fix these and re-run /artel-deep-review <TICKET_ID> [branch] [pr-link] [--local].
   ```
   Then terminate the skill. Do not run any further steps.
5. On success (or skip), display `Quality gate passed.` (or the skipped notice from step 3) and
   continue to Step 3.

## Step 3: Dispatch the reviewer and validate its artifacts

### 3a: Dispatch

Use the `subagent` tool to spawn the `artel-reviewer` agent (`${ARTEL_ROOT}/agents/artel-reviewer.md`) in
standalone mode — no ticket mode. Its report is the ticket's `deep-review.md`: the agent's
standalone-mode Output section carries the deep-review shape, and the machine report sidecar the
dispatch names. Ticket files are passed as additional context only.

Set `ARTIFACT_RETRY_USED = false` before the first dispatch. A missing artifact (this step) and
an invalid artifact (Step 3b) share this one flag: whichever branch claims it first consumes the
run's only artifact retry, so the run accepts at most two reviewer attempts.

- the `artel-reviewer` agent
- `model`: resolve `reviewer.deepReview`
  (`python3 ${ARTEL_ROOT}/scripts/models.py resolve --site reviewer.deepReview`) and set
  it when non-null — the whole-branch review before a pull request is the one review on the most
  capable tier (`${ARTEL_ROOT}/docs/agents.md` `## Models`); a dispatch refused for its
  model is re-dispatched once without one
- `description`: `"Code review for <TICKET_ID>"`
- `prompt`: always starts with `Run in **standalone mode** — no ticket context.`

**Branch targeting line** (first line after the standalone-mode instruction):
- Without `BRANCH_NAME`: `Review the current branch changes following your standard checklist.`
- With `BRANCH_NAME`: `` Review the changes on branch `<BRANCH_NAME>` following your standard checklist. ``

**Without PR context** (Step 2b was skipped):
```
Run in **standalone mode** — no ticket context.
<branch targeting line>
**Spec store:** <kartoteka | files (<reason>)>
Write the ticket's deep review to <specs.dir>/<TICKET_ID>/deep-review.md (create the directory if needed) in your deep-review output — the document header and the `## Proposed fixes` tasks included. Return the deep-review count line.

Pinned target — review exactly this and nothing else:
- Target manifest: .artel/run/<TICKET_ID>/deep-review/target.json — run <RUN_ID>, branch <BRANCH>, base <BASE>, head <HEAD>, merge-base <MERGE_BASE>.
- Review range: `git diff <MERGE_BASE> <HEAD>`; regression guard: `git diff <BASE> <HEAD>`; anchors: the same merge-base..head range.
- Machine report (schema 1): write it to .artel/run/<TICKET_ID>/deep-review/machine.json.
- Report contract: `${ARTEL_ROOT}/docs/deep-review.md` — the header, the four metadata lines, the finding sections and DR-NNN IDs, the `### Tasks` blocks, the verification records and the completeness rules.

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
**Spec store:** <kartoteka | files (<reason>)>
Write the ticket's deep review to <specs.dir>/<TICKET_ID>/deep-review.md (create the directory if needed) in your deep-review output — the document header and the `## Proposed fixes` tasks included. Return the deep-review count line.

PR Compliance Check — the PR claims to implement the following:
Title: <PR_TITLE>
Description: <PR_DESCRIPTION>
Base: <PR base> · Head: <PR head>

Pinned target — review exactly this and nothing else:
- Target manifest: .artel/run/<TICKET_ID>/deep-review/target.json — run <RUN_ID>, branch <BRANCH>, base <BASE>, head <HEAD>, merge-base <MERGE_BASE>.
- Review range: `git diff <MERGE_BASE> <HEAD>`; regression guard: `git diff <BASE> <HEAD>`; anchors: the same merge-base..head range.
- Machine report (schema 1): write it to .artel/run/<TICKET_ID>/deep-review/machine.json.
- Report contract: `${ARTEL_ROOT}/docs/deep-review.md` — the header, the four metadata lines, the finding sections and DR-NNN IDs, the `### Tasks` blocks, the verification records and the completeness rules.

Additional ticket context:
[... same five bullets as above ...]
```

Wait for the agent to complete, then confirm the machine report and the report exist — files
path: `test -f` for both; kartoteka path: `test -f` for the machine report and
`spec_store.py exists <specs.dir>/<TICKET_ID>/deep-review.md` for the report.

If the report or the machine report is missing: when `ARTIFACT_RETRY_USED` is false, set it true
and re-dispatch once with the same prompt and model; otherwise display
`Error: the reviewer produced no report after two attempts.` and terminate — never write the
review yourself.

### 3b: Validate

Run the checker exactly as below. It is the only judge of the artifacts' correctness; never
inspect the report or the machine report for that, and never edit them. The `<the agent's
completion line>` is `Deep review: <c> Critical, <w> Warning, <s> Suggestion — <path>` — pass it
unchanged.

Files path:
```bash
python3 ${ARTEL_ROOT}/scripts/deep_review.py check \
  --target .artel/run/<TICKET_ID>/deep-review/target.json \
  --findings .artel/run/<TICKET_ID>/deep-review/machine.json \
  --report <specs.dir>/<TICKET_ID>/deep-review.md \
  --summary '<the agent's completion line>'
```

Kartoteka path — the stored document goes by pipe, never through a file:
```bash
set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/deep-review.md | python3 ${ARTEL_ROOT}/scripts/deep_review.py check \
  --target .artel/run/<TICKET_ID>/deep-review/target.json \
  --findings .artel/run/<TICKET_ID>/deep-review/machine.json \
  --report - --summary '<the agent's completion line>'
```

The exit code decides:

- `0` — valid and complete. The envelope's `data` carries the validated counts (`data.counts`),
  the task counts (`data.tasks`), `data.completeness`, `data.gaps` and the report path. These —
  never the raw completion line — are the counts this skill displays and offers from.
  Do not open the report to recount.
- `1` — inconsistent, invalid or partial. The envelope's `errors` say why; `data` is
  best-effort. In this order:
  1. A pending-changes, moved-HEAD or moved-branch error means the checkout left the pinned
     target: stop and tell the user to commit, stash or move the work to a worktree — never
     clean, stash or reset for them, and never spend the artifact retry on a drift no
     re-dispatch can fix.
  2. `data.completeness` is `partial` — display its `data.gaps` and terminate: no copy, no
     implementer, never `Nothing to apply.`; the gaps are what the user must see.
  3. otherwise — the run's one bounded retry, the artifact correction branch: when
     `ARTIFACT_RETRY_USED` is false, set it true and re-dispatch the same reviewer once with
     the same prompt and model plus the checker's `errors` verbatim, then run the checker once
     more; when the flag is already consumed, display the errors and terminate. Never edit the
     report or the machine report yourself, and never copy tasks from a report the checker did
     not validate.
- `2` — the tool cannot evaluate its inputs (usage, unreadable files, an invalid manifest, a
  target that no longer resolves, or an empty report). Display the `deep_review.py: <reason>`
  line and terminate. An empty report names `empty_input`: on the kartoteka path a failed
  `spec_store.py get` is the cause — its own stderr says why — and on the files path the
  reviewer wrote nothing. Unprocessable input is never corrected: never re-dispatch the
  reviewer for it, never materialize the stored document as a file, and never spend the
  artifact retry on it.

The checker proves the artifacts' structure, identities and anchors — the metadata and SHA links,
the heading/ID/task correspondence, and that every line anchor falls in the pinned diff. It
proves never that a finding is true; say so when you report the result.

## Step 4: Offer to apply

Display:
```
Deep review written to <specs.dir>/<TICKET_ID>/deep-review.md
Critical: <c> · Warnings: <w> · Suggestions: <s>
```
`<c>`, `<w>` and `<s>` are the validated `data.counts` from Step 3b.

If `c` and `w` are both `0`, display `Nothing to apply.` and terminate. (A partial review never
reaches this step — Step 3b stopped on its gaps.)

Otherwise ask via the `question` tool which fixes to work. Offer only the options that have rows,
using the validated `data.tasks` counts:

- **Critical only** — the `### Tasks (Critical)` block under `## Proposed fixes` (when
  `data.tasks.Critical > 0`).
- **Critical and warnings** — that block plus the `### Tasks (Warning)` block (when
  `data.tasks.Warning > 0`; with `c = 0` label it **Warnings only**).
- **Not now** — display `Fixes are in <specs.dir>/<TICKET_ID>/deep-review.md; re-run /artel-deep-review <TICKET_ID> to apply them later.` and terminate.

## Step 5: Apply

1. Copy the checkbox items of the chosen `### Tasks` block(s) from `deep-review.md` — not their
   `### Tasks` heading — under `## Code Review Fixes` in the ticket-wide
   `<specs.dir>/<TICKET_ID>/tasklist.md` — on the kartoteka path one `artifact_patch(project=<project>, …)`: insert before the next `## ` heading, or `append` (spec-storage.md §4.3) — beneath one new source heading
   `### deep-review-<YYYY-MM-DD>`, the date from `date +%F`. The heading keeps this batch's
   rows apart from an earlier round's with the same text
   (`${ARTEL_ROOT}/docs/task-queue.md` §6); when it is already in the section, use
   `-2`, then `-3`, ….
   - The file is missing → create it (kartoteka path: `artifact_put(project=<project>, …, expected_version=0)`) with the document header (`${ARTEL_ROOT}/docs/spec-storage.md` §3.2: `type: tasklist`, `produced_by: artel:deep-review`), `# Tasklist — <TICKET_ID>` and the section, and
     display `Created <specs.dir>/<TICKET_ID>/tasklist.md with only a ## Code Review Fixes section.`
   - The section is missing → append `## Code Review Fixes` at the end of the file.
   - The source heading goes at the end of the section, before the next `## ` heading.
   - Renumber the copied tasks to continue from the highest `Task N` already in the file — on
     a task-format tasklist, the highest under `## Code Review Fixes`: its `### Task N.M:`
     headings number iteration tasks and never count.
     Renumbering changes the number only: a `(behavior)` marker stays in place
     (`${ARTEL_ROOT}/agents/artel-reviewer.md`).
   - A block reading `- none` copies nothing, and no heading.

   Display `Appended <count> tasks under ## Code Review Fixes in <specs.dir>/<TICKET_ID>/tasklist.md.`

2. Record them in the task queue before any implementer is dispatched
   (`${ARTEL_ROOT}/docs/task-queue.md` §2, fix-writer rule). Decide the path per
   task-queue.md §1: `--local` was passed, `knowledge.adapter` is `none` or absent,
   `knowledge.project` is unset, or the kartoteka task tools are absent → skip this step and
   display `Task queue: not used (<reason>)`. Otherwise run. Files path first, kartoteka path
   (`docs/spec-storage.md` §4.2) second:

       python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py --tasklist <specs.dir>/<TICKET_ID>/tasklist.md --ticket-key <TICKET_ID>
       set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/tasklist.md | python3 ${ARTEL_ROOT}/scripts/tasklist_tasks.py --tasklist - --ticket-key <TICKET_ID>

   and for each entry of `data.sections`, in order,
   `task_create(project=<project>, ticket_key=<TICKET_ID>, title=…, description=…, status=…)`
   the section row, then each of its `children` with `parent_id` set to that row's `task_id` —
   not `data.iterations`. A child emitted `backlog` that comes back `done` → display
   `fix task #<id> is done in the queue but open in the file: <title>`. Surface every
   `data.warnings` line. Exit `2`, or a `Rejected:` line naming `kartoteka project add` →
   display it and continue: the tasks are in the file. Display
   `Recorded <n> fix rows in the task queue.`

3. For each appended task, in order: invoke the `artel-implementer` skill with `<TICKET_ID>` (plus `--local`
   when this run was invoked with it), naming `## Code Review Fixes` in the invocation — the
   implementer treats a dispatch that names that section as file-scan work, takes the first
   incomplete box under it, and on the queue path keeps its row current. On a `HITL:` or
   `DEVIATION` return, or an aborted task, stop the loop and report it; the remaining tasks
   stay unchecked for the user to decide.

4. Run `verify.commands` once more, exactly as in Step 2d, and record the result (`passed`,
   `failed` with the quoted output, or `skipped`). A failure here is reported, not
   terminated on — the tasks that were worked are already in the tree.

5. Report:
   ```
   Applied fixes for <TICKET_ID>:
   - Tasks appended: <count>
   - Tasks completed: <checked count> of <count>
   - Quality gate: <passed | failed | skipped>
   Re-run /artel-deep-review <TICKET_ID> to re-review the branch.
   ```

## Rules

- **Orchestrator only.** This skill never reads the diff, never judges code, and never writes a
  review. The checker is the judge; the counts it validates are the counts this skill uses.
  Copying task blocks between two files under `<specs.dir>` is the only text it moves, and the
  rows Step 5 records are its only writes to kartoteka.
- **The pin is the target.** A branch that is not checked out stops the run with the worktree
  instruction; the skill never retargets, never checks out, never fetches, and never reviews a
  tree the manifest does not pin.
- **Read-only on the VCS host** — the PR is fetched, never commented on or edited.
- **Ticket-wide only** — a phase suffix is accepted and discarded.
- **One agent, one pass, one artifact retry.** The `reviewer` is dispatched once; a second
  dispatch is allowed only as the run's single artifact retry — Step 3a's missing artifact or
  Step 3b's invalid artifact, whichever fires first claims `ARTIFACT_RETRY_USED` — so the run
  accepts at most two reviewer attempts. There is no second reviewer and no merged summary. The
  whole-branch review before a pull request is this skill's only seat.
- **No inline correction** — an invalid artifact is corrected by the reviewer or not at all.
