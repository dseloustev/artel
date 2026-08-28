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
changes — and then checks, from git alone, that everything the add commit put on the branch is
gone (step 4), because the host command's exit `0` says it finished, not that it was complete.

Scope is the committed-lane scaffold: everything `/artel:add-automation` put on the branch.
`/artel:drive-app` adds no configuration of its own — it requires the scaffold and writes
`drive-observation.md` into the ticket's spec trail, which is evidence and stays (like
`run-app`'s `observation.md`); whatever `runtime.drive` leaves running is outside this skill's
reach (drive-app, "Aftermath").

## 1. Configuration gate

Read `runtime.scaffold.remove` (config.md). Absent or empty → report "not configured" (the
`AUTOMATION_REMOVED` gate — `${CLAUDE_PLUGIN_ROOT}/agents/validator.md` — records `skipped`) and
stop.

## 2. Preflight (stop-and-report on any failure)

1. **Never on the default branch.** Same check as `/artel:add-automation`'s preflight
   (`git symbolic-ref refs/remotes/origin/HEAD` against `git branch --show-current`). Add refuses
   to apply the scaffold on the default branch, so finding it there means a branch was merged with
   the scaffold still applied — exactly what the `AUTOMATION_REMOVED` gate exists to catch.
   Removing it is the right call, but it goes through a branch and a PR like any other change:
   step 5 commits and pushes, and must never do that straight to the default branch. Stop and ask
   the user to branch first.
2. **Clean working tree.** `git status --porcelain` must be empty before running the remove
   command — same rationale as `/artel:add-automation`'s preflight: this skill doesn't know which
   paths the command will touch, so it needs a clean baseline to isolate the removal's own diff.
   Dirty → stop and ask the user to commit or stash first.

## 3. Remove

Run `runtime.scaffold.remove` via Bash. Capture `{command, exit_code, output}`. Non-zero exit →
the command may have removed part of the scaffold before it failed, so report the output together
with `git status --porcelain -uall` to show what state the tree was left in, and stop. As in
step 4.4, this skill does not roll back a rollback — the operator resolves it by hand.

## 4. Verify

1. Determine what changed: `git status --porcelain -uall -z` (modified + deleted + untracked
   paths) — same flags and reasons as `/artel:add-automation`'s step 4.1: `-uall` so a directory
   the command leaves behind is listed as its individual files rather than one `?? dir/` entry,
   `-z` so paths containing spaces come back unquoted. Empty diff with exit `0` → nothing was
   applied to begin with; report "nothing to remove" and stop before step 5.
2. **Cross-check against what `/artel:add-automation` committed.** The add commit is findable —
   add-automation always commits under one fixed subject:
   `git log -1 --format=%H --fixed-strings --grep='chore: enable agent UI automation' HEAD`.
   Every path that commit added (`git show --format= --name-only --diff-filter=A <hash>`) must
   be absent from disk now. One that survives means `runtime.scaffold.remove` is incomplete.
   When the survivor is byte-identical to what the add commit introduced
   (`git diff --quiet <hash> -- <path>` exits `0`) it is still purely the scaffold's: `rm <path>`
   — unstaged, like every other change here; staging is step 5's, and a `git rm` would drop the
   path from the index so that step 5's `git add` of it fails — then re-derive step 4.1 (the
   directory it leaves empty is step 4.3's). Modified since → the ticket may have built on it;
   stop and report the path. No such commit reachable from `HEAD` (scaffold applied by hand,
   history squashed) → skip this check and say so in the report.
3. **Sweep the directories the scaffold emptied.** git lists files, never directories, so a
   directory the scaffold created is invisible to step 4.1 and to the commit: a host command
   that deletes the entrypoint but not its directory — or whose `rmdir` failed because an editor
   or Finder had dropped an ignored file there — leaves the directory on disk under a clean
   `git status`, and the removal commit still reads as complete. For each deleted path in
   step 4.1 take its directory and walk upward while the directory still exists and every
   tracked file under it (`git ls-tree -r --name-only HEAD -- <dir>`) is among the step-4.1
   deletions — those are the directories the scaffold created for itself; one holding anything
   else tracked is the ticket's and stays. Classify each with
   `git status --porcelain --ignored -uall -z -- <dir>`: nothing but step-4.1 deletions (` D`)
   and ignored entries (`!!`) → remove it (`rmdir` when empty, `rm -r` when only ignored
   entries remain) and name it in the report; any `??` entry is untracked work that is not the
   scaffold's → stop and report.
4. Run `verify.fast` (config.md) — the generic replacement for a project-specific analyzer pass,
   confirming the reversal didn't leave the tree broken. If the command carries a `{files}` token,
   replace it with the step-4.1 paths, space-joined and shell-quoted, the same substitution
   `scripts/verify.py` performs; a command without the token runs unscoped. Left unsubstituted the
   literal `{files}` reaches the shell and fails a removal that is in fact clean. Deleted paths are
   worth dropping from that scope — a linter handed a file that no longer exists will fail on it.
   An unconfigured `verify.fast` degrades this check to skipped, never green (config.md). On
   failure, report the findings and stop — this skill does not attempt its own rollback of a
   rollback; the operator resolves it by hand.

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

Commit hash, the changed paths (from step 4.1), the cross-check outcome (paths verified gone,
or skipped: no add commit reachable), the directories the sweep removed, whether pushed.
