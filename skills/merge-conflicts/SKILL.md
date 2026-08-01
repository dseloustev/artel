---
name: merge-conflicts
description: "Resolve merge conflicts by analyzing both sides of each conflict and intelligently merging changes, verifying with the host's configured quality gate afterward. Use when merging branches with conflicts."
argument-hint: "[branch-name]"
model: opus
---

# Merge Conflict Resolution

Systematically resolve merge conflicts by analyzing both sides of each conflict, understanding
intent, and intelligently combining changes. Utility procedure — no agent, no ticket context.

## Phase 1: Setup

### Parse Arguments

- If `$ARGUMENTS` is provided, use it as the branch name (e.g., `origin/main`, `main`,
  `feature/some-branch`).
- If `$ARGUMENTS` is empty, detect the repo's default branch the same way
  `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone mode and
  `${CLAUDE_PLUGIN_ROOT}/skills/add-automation/SKILL.md` do — `git symbolic-ref
  refs/remotes/origin/HEAD`, asking the user if ambiguous — and default to `origin/<default-branch>`.
  Never a hardcoded `master`/`main`/`develop`.
- Store the resolved branch name as `TARGET_BRANCH`.

### Check Git State

1. Run `git status` to check current state.
2. **If a merge is already in progress** (unmerged paths exist): skip directly to Phase 2.
3. **If no active merge**, proceed with fetch and merge:

### Fetch and Merge

1. If `TARGET_BRANCH` starts with `origin/` or contains a remote prefix, run:
   ```
   git fetch origin
   ```
2. Start the merge:
   ```
   git merge TARGET_BRANCH --no-commit --no-ff
   ```
3. If the merge completes with **no conflicts**, report success:
   > Merge completed cleanly with no conflicts. Changes are staged but not committed. Review with `git diff --cached` and commit when ready.
4. If the merge **fails** (exit code 1 with conflicts), proceed to Phase 2.

## Phase 2: Identify Conflicts

1. List all unmerged (conflicting) files:
   ```
   git diff --name-only --diff-filter=U
   ```
2. Show the full scope of changes including auto-merged files:
   ```
   git diff --stat
   ```
3. Present the conflict summary to the user:
   - Number of conflicting files
   - List of conflicting file paths
   - Total files changed (including auto-merged)

## Phase 3: Analyze Conflicts (Plan Mode)

**Enter plan mode** to analyze conflicts and propose a resolution strategy before making changes.

### For Each Conflicting File

1. **Read the conflicted file** — see the conflict markers and surrounding context
2. **Read the source branch version:**
   ```
   git show TARGET_BRANCH:<file-path>
   ```
3. **Read the current branch (HEAD) version:**
   ```
   git show HEAD:<file-path>
   ```
4. **Understand the intent** of each side:
   - What was the source branch trying to accomplish?
   - What was the current branch trying to accomplish?
   - Are the changes complementary, overlapping, or contradictory?

### Cross-File Analysis

- If code was **moved or refactored** (e.g., extracted to a shared module or mixin, split into new
  files), read the destination files to understand the full picture
- Identify **non-conflicting improvements** from the source branch that should be ported to other
  files (e.g., bug fixes in code that was refactored and moved elsewhere on the current branch)
- Check for **renamed or deleted files** that may affect resolution

### Write Resolution Plan

For each conflicting file, document:
- **File path**
- **Conflict summary** — what each side changed and why
- **Resolution strategy** — one of:
  - **Take theirs** — source branch version is correct
  - **Take ours** — current branch version is correct
  - **Manual merge** — combine both sides, describe how
- **Additional actions** — any improvements to port to other files

Present the plan to the user for approval via plan mode. Wait for approval before proceeding.

## Phase 4: Resolve Conflicts

Apply the approved resolution strategy for each file:

### Simple Resolutions

- **Take theirs (source branch):**
  ```
  git checkout TARGET_BRANCH -- <file-path>
  ```
- **Take ours (current branch):**
  ```
  git checkout HEAD -- <file-path>
  ```

### Complex Merges

- Use the `Edit` tool to manually combine changes from both sides
- Remove ALL conflict markers: `<<<<<<<`, `=======`, `>>>>>>>`
- Ensure the resulting code is syntactically correct and logically consistent

### Port Non-Conflicting Improvements

- Apply any improvements identified in Phase 3 to their target files
- These are changes from the source branch that affect code which was moved/refactored on the
  current branch

### Stage Resolved Files

```
git add <resolved-file>
```

## Phase 5: Verify

### Check for Remaining Conflict Markers

Run via Grep tool across the whole tree (no language-specific file filter — the host project may
use any file types):
- Pattern: `<<<<<<<`

If any conflict markers remain, resolve them before proceeding.

### Run the quality gate

Run `verify.commands` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`) — the host's configured
lint/format/test gate. An unconfigured `verify.commands` degrades this check to `skipped`, never
green (config.md).

If the gate fails:
1. Read the problematic files.
2. Fix the errors (usually import issues, type mismatches from the merge).
3. Re-run the gate until it passes (or stays `skipped`).

### Report Status

Present the final summary:
- List of resolved files and their resolution strategy
- Any ported improvements
- Quality gate status (clean / skipped / findings)
- Remind the user: **Review changes with `git diff --cached` and commit when ready**

## Critical Rules

- **Never commit and never `git merge --abort` without explicit user confirmation** — the user
  reviews and finalizes.
- **Prefer HEAD's additions** when a conflict is semantically ambiguous — preserve feature-branch
  work by default.
- **Port non-conflicting improvements** — if the source branch has logic fixes that apply to code
  the feature branch moved or refactored, bring those across too.
- **Never hand-merge generated or build-output files.** If the host project has files it
  regenerates deterministically (compiled bindings, build artifacts, generated localization,
  lockfiles, etc.) and one of them conflicts, take either side of the conflict and regenerate it via
  the host's own generation step rather than hand-resolving the markers — check the host's
  `AGENTS.md`/`CLAUDE.md`/README for its regeneration command, and note in the report if none can
  be found.
