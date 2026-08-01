---
name: deep-review
description: "Run dual code review (two independent reviewer agents), create a merged summary, and plan improvements"
argument-hint: "[ticket-id] [branch] [pr-link]"
model: sonnet
---

This skill is a multi-step orchestrator — run the steps in order. Skipping ahead is only allowed
when Step 1 explicitly routes you there.

## Step 0: Quality gate (`verify.commands`)

Always run this step first, even on resumes. The review must not proceed against a tree that fails
the project's own checks.

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

   Fix these and re-run /artel:deep-review <TICKET_ID> [branch] [pr-link].
   ```
   Then terminate the skill. Do not run any further steps.
5. On success (or skip), display `Quality gate passed.` (or the skipped notice from step 3) and
   continue to Step 1.

## Step 1: Resolve ticket and check existing review files

### 1a: Resolve the ticket key

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per
`${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and
`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty
line of `<specs.dir>/.active_ticket`; if no identifier is available, display the following and
terminate:
```
Error: No ticket key provided and <specs.dir>/.active_ticket is missing.
Usage: /artel:deep-review <ticket-id> [branch] [pr-link]
```

`deep-review` is ticket-wide only: `PHASE_NUM` is parsed for input compatibility (so a
phase-suffixed identifier is accepted) but discarded — the same convention
`${CLAUDE_PLUGIN_ROOT}/skills/pr-description/SKILL.md` uses.

Then verify that `<specs.dir>/<TICKET_ID>/` exists on disk. If it does not, display the following
and terminate:
```
Error: Ticket directory <specs.dir>/<TICKET_ID>/ does not exist.
```

Display:
```
Active ticket: <TICKET_ID>
Ticket directory: <specs.dir>/<TICKET_ID>/
```

### 1b: Check existing review files

Check which review files already exist:

```bash
ls -la <specs.dir>/<TICKET_ID>/review-claude.md <specs.dir>/<TICKET_ID>/review-second.md <specs.dir>/<TICKET_ID>/review-summary.md 2>/dev/null || true
```

Based on the results, determine which step to proceed to. Evaluate the rows top-to-bottom; the
first matching row wins:

| Files Present | Action |
|---------------|--------|
| `review-summary.md` exists | Skip to **Step 6** (enter plan mode) |
| Both `review-claude.md` AND `review-second.md` exist | Skip to **Step 5** (create merged summary) |
| Only `review-claude.md` exists | Skip to **Step 4** (dispatch the second reviewer) |
| `review-second.md` exists but `review-claude.md` is missing | Proceed to **Step 2** then **Step 3** (produce the first review), then continue directly to **Step 5** — the second review already exists, so Step 4 is skipped |
| None exist | Proceed to **Step 2** (parse arguments) then **Step 3** (run the reviewer agent) |

**Important:** When skipping to Step 4, Step 5, or Step 6 but `$1` or `$2` are provided, execute
**Step 2** first to parse them before continuing to the target step (Step 4's dispatched prompt
depends on Step 2's branch/PR output).

Display a message indicating which step you're starting from, e.g.:
```
Found existing review-claude.md. Skipping to Step 4 (dispatching the second reviewer).
```

## Step 2: Parse arguments (branch name and PR link)

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

## Step 3: Invoke the reviewer agent (standalone mode)

Use the Agent tool to spawn the `reviewer` agent (`${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`) in
standalone mode — no ticket mode; the skill keeps writing to
`<specs.dir>/<TICKET_ID>/review-claude.md`. Ticket files are passed as additional context only.

- `subagent_type`: `"reviewer"`
- `description`: `"Code review for branch"`
- `prompt`: constructed based on which arguments were provided. Always start with `Run in
  **standalone mode** — no ticket context.` so the agent skips ticket-mode's tasklist write-back
  and writes its report to `<specs.dir>/<TICKET_ID>/review-claude.md` — overriding the mode's own
  default path (`${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone-mode Output section already
  supports a caller-specified path).

**Branch targeting line** (first line after the standalone-mode instruction):
- Without `BRANCH_NAME`: `Review the current branch changes following your standard checklist.`
- With `BRANCH_NAME`: `` Review the changes on branch `<BRANCH_NAME>` following your standard checklist. ``

**Without PR context** (Step 2b was skipped):
```
Run in **standalone mode** — no ticket context.
<branch targeting line>
Save your review report to <specs.dir>/<TICKET_ID>/review-claude.md.

Additional ticket context:
- Active ticket: <TICKET_ID>
- Ticket directory: <specs.dir>/<TICKET_ID>/
- Read files from that directory (idea.md, vision.md, prd.md, plan.md, tasklist.md, research.md, phase-*/ subfolders, etc.) as needed to understand the feature's intent, scope, and acceptance criteria.
- Use this context when judging whether the diff fulfills the ticket. Do NOT switch to ticket mode (do not write into the tasklist) — still write your report to <specs.dir>/<TICKET_ID>/review-claude.md.
- If you need additional tracker/PR detail, you may call the configured tracker (`tracker.adapter`) / VCS (`vcs.adapter`) tools per config.md, when connected.
```

**With PR context** (Step 2b fetched PR details): same prompt, plus a PR Compliance block ahead of
the "Additional ticket context" bullets — the agent's own standalone-mode contract already adds a
PR Compliance section whenever a PR description is present in the prompt, so just supply it:

```
Run in **standalone mode** — no ticket context.
<branch targeting line>
Save your review report to <specs.dir>/<TICKET_ID>/review-claude.md.

PR Compliance Check — the PR claims to implement the following:
Title: <PR_TITLE>
Description: <PR_DESCRIPTION>

Additional ticket context:
[... same four bullets as above ...]
```

Wait for the agent to complete before proceeding.

## Step 4: Invoke the second reviewer agent (independent)

Dispatch a SECOND, independent review with the Agent tool. Its value is a fresh, unbiased
perspective: the `reviewer` agent's standalone mode already runs the full convention/architecture/
security lens set and the regression guard
(`${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md` — "Review lenses", "Regression guard (standalone)") on
every dispatch, so what distinguishes the second call from the first is independence, not a
different checklist. It must never see the first review.

- `subagent_type`: `"reviewer"`
- `description`: `"Second independent code review"`
- `prompt`: constructed like Step 3 (same branch targeting line; include the PR Compliance block
  only when Step 2b fetched PR context), with these differences:

```
Run in **standalone mode** — no ticket context.
<branch targeting line>
You are the SECOND, independent reviewer for this branch. Do NOT read any prior review artifact in the ticket directory — review.md, the review/ subfolder, or any review-*.md file (including <specs.dir>/<TICKET_ID>/review-claude.md) — a fresh, unbiased perspective is the point of this dispatch.
Save your review report to <specs.dir>/<TICKET_ID>/review-second.md.
[PR Compliance block, when PR context exists — same shape as Step 3]

Additional ticket context:
- Active ticket: <TICKET_ID>
- Ticket directory: <specs.dir>/<TICKET_ID>/
- Read files from that directory (idea.md, vision.md, prd.md, plan.md, tasklist.md, research.md, phase-*/ subfolders, etc.) as needed to understand the feature's intent, scope, and acceptance criteria — but never review.md, the review/ subfolder, or any review-*.md file.
- Do NOT switch to ticket mode — write your report to <specs.dir>/<TICKET_ID>/review-second.md.
- If you need additional tracker/PR detail, you may call the configured tracker/VCS tools per config.md, when connected.
```

Wait for the agent to complete, then verify the file exists:
```bash
test -f <specs.dir>/<TICKET_ID>/review-second.md && echo "Found review-second.md" || echo "File not found"
```

If the file is missing, re-dispatch the agent once with the same prompt. If it is still missing
after the retry, report the failure and terminate — do not write the second review yourself (that
would defeat the independent-reviewer design).

## Step 5: Create merged summary

Read both review files:
- `<specs.dir>/<TICKET_ID>/review-claude.md`
- `<specs.dir>/<TICKET_ID>/review-second.md`

Create `<specs.dir>/<TICKET_ID>/review-summary.md` with the following format:

```markdown
# Code Review Summary

## Critical Issues
<!-- Combined critical issues from both reviews, deduplicated -->

## Warnings
<!-- Combined warnings from both reviews, deduplicated -->

## Suggestions
<!-- Combined suggestions from both reviews, deduplicated -->

## PR Compliance
<!-- ONLY include this section when PR context was fetched in Step 2 -->
<!-- If no PR link was provided, OMIT this entire section -->

### PR Claims
- **Title:** <PR_TITLE>
- **Description:** <PR_DESCRIPTION>

### Verified Claims
<!-- List each PR claim confirmed as implemented -->

### Unverified / Missing Claims
<!-- List each PR claim NOT implemented or only partially implemented -->

### Undocumented Changes
<!-- List code changes not mentioned in the PR description -->

## QA Plan

### Prerequisites
<!-- Environment setup, test accounts, required state -->

### Test Scenarios
<!-- Step-by-step manual test cases derived from:
     - Code changes identified in both reviews
     - PR-claimed functionality (when PR link was provided)
     - Edge cases and error paths found during review -->

#### Scenario 1: <descriptive name>
1. Step one
2. Step two
3. **Expected:** <expected result>

#### Scenario 2: <descriptive name>
1. Step one
2. Step two
3. **Expected:** <expected result>

<!-- Add more scenarios as needed -->

### Regression Checks
<!-- Areas that may be affected by the changes and should be smoke-tested -->

---

## Sources
- First Review (R1): [review-claude.md](./review-claude.md)
- Second Review (R2): [review-second.md](./review-second.md)
```

**Merge Guidelines:**
- Combine issues from both reviews under appropriate categories.
- Deduplicate identical or very similar issues.
- Preserve the source attribution for each issue: "[R1]" (review-claude.md) or "[R2]"
  (review-second.md). If both reviews mention the same issue, mark it "[Both]".
- **PR Compliance section**: only include when PR context exists (Step 2b was executed). Merge
  compliance findings from both reviews if both contain them.
- **QA Plan section**: always include. When no PR link (`$2`) was provided, derive test scenarios
  from code changes and review findings only. When a PR link was provided, also cover
  PR-claimed functionality.

## Step 6: Enter plan mode

After creating the summary (or if it already exists), output:

```
Review summary created at <specs.dir>/<TICKET_ID>/review-summary.md

Now entering plan mode to create an improvement plan based on the combined review findings.
```

Then use the `EnterPlanMode` tool to begin planning improvements based on the review summary. The
plan should address all Critical Issues first, then Warnings, and optionally Suggestions.
