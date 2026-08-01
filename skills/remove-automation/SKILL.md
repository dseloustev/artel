---
name: remove-automation
description: "Remove the transient agent UI-automation scaffold from the current branch (reverse of /artel:add-automation) by running the host's configured runtime.scaffold.remove command, verify with verify.fast, commit exactly the files it changed, and push if an upstream exists. Run after the PR is created, before merge."
argument-hint: ""
disable-model-invocation: true
model: sonnet
---

Removes the **transient committed-lane artifacts** applied by `/artel:add-automation`, via the
host's `runtime.scaffold.remove` command (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "runtime"
section). **Re-derive, never `git revert`** — the ticket may have legitimately edited files the
scaffold also touches in between; this skill commits only what `runtime.scaffold.remove` itself
changes.

## 1. Configuration gate

Read `runtime.scaffold.remove` (config.md). Absent or empty → report "not configured" (the
`AUTOMATION_REMOVED` gate — `${CLAUDE_PLUGIN_ROOT}/agents/validator.md` — records `skipped`) and
stop.

## 2. Preflight

`git status --porcelain` must be empty before running the remove command — same rationale as
`/artel:add-automation`'s preflight: this skill doesn't know which paths the command will touch,
so it needs a clean baseline to isolate the removal's own diff. Dirty → stop and ask the user to
commit or stash first.

## 3. Remove

Run `runtime.scaffold.remove` via Bash. Capture `{command, exit_code, output}`. Non-zero exit →
stop and report the output.

## 4. Verify

1. Determine what changed: `git status --porcelain` (modified + deleted + untracked paths).
   Empty diff with exit `0` → nothing was applied to begin with; report "nothing to remove" and
   stop before step 5.
2. Run `verify.fast` (config.md) — the generic replacement for a project-specific analyzer pass,
   confirming the reversal didn't leave the tree broken. An unconfigured `verify.fast` degrades
   this check to skipped, never green (config.md). On failure, report the findings and stop — this
   skill does not attempt its own rollback of a rollback; the operator resolves it by hand.

## 5. Commit & push

Stage exactly the paths from step 4.1 and commit:

```bash
git add <paths from step 4.1>
git commit -m "chore: remove agent UI automation"
```

English, no trailers. Verify with `git show --stat HEAD` that the commit contains exactly those
paths before reporting success.

Push **only** when an upstream exists (updates the open PR):
`git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}"` succeeds → `git push` (never any
`--force` variant); otherwise skip and say so.

## 6. Report

Commit hash, the changed paths (from step 4.1), whether pushed.
