---
name: add-automation
description: "Apply the transient agent UI-automation scaffold to the current branch by running the host's configured runtime.scaffold.add command, verify with verify.fast, and commit exactly the files it changed. Opt-in per task; undone by /artel:remove-automation before merge. Use when the user decides a task needs the agent to run and drive the app itself."
argument-hint: ""
disable-model-invocation: true
model: sonnet
---

Applies the **transient committed-lane artifacts** for agent UI driving, via the host's
`runtime.scaffold.add` command (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`, "runtime" section).
Counterpart: `/artel:remove-automation`. The pair is idempotent and cycles freely on one branch
(add → verify → remove → PR feedback → add again → …) as long as `runtime.scaffold.add` and
`runtime.scaffold.remove` are themselves safe to re-run — the same expectation `verify.commands`
carries (config.md).

## 1. Configuration gate

Read `runtime.scaffold.add` (config.md). Absent or empty → report "not configured" (the
`AUTOMATION_REMOVED` gate — `${CLAUDE_PLUGIN_ROOT}/agents/validator.md` — records `skipped`) and
stop.

## 2. Preflight (stop-and-report on any failure)

1. **Never on the default branch.** Determine the repo's default branch (e.g.
   `git symbolic-ref refs/remotes/origin/HEAD`, or ask if ambiguous — same convention
   `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone mode uses) and compare against
   `git branch --show-current`. Same branch → stop and report.
2. **Clean working tree.** `git status --porcelain` must be empty. This skill doesn't know in
   advance which paths `runtime.scaffold.add` will touch — a generic host command, not a fixed
   file list — so it requires the whole tree clean rather than a scoped subset, to keep the
   dedicated commit (step 5) free of unrelated edits. Dirty → stop and ask the user to commit or
   stash first.

## 3. Apply

Run `runtime.scaffold.add` via Bash. Capture `{command, exit_code, output}`. Non-zero exit → the
command may still have written part of the scaffold before it failed, so preflight's clean tree
proves nothing about the tree now: re-check `git status --porcelain -uall -z`. Empty → nothing to
undo. Non-empty → roll back with the step-4.3 procedure. Report the output and stop either way.

## 4. Verify

1. Determine what changed: `git status --porcelain -uall -z` (modified + untracked paths) — this
   is how the skill learns the scaffold's file list without the host ever declaring one. Both
   flags matter: plain `--porcelain` collapses a newly created directory into one `?? dir/` entry,
   which `rm -f` refuses, which never matches the per-file paths `git show --stat` prints in
   step 5, and which hands a directory to a `{files}` linter; `-z` returns paths unquoted, where
   `--porcelain` wraps any name containing a space in double quotes. Empty diff with
   exit `0` → the command idempotently no-op'd because the scaffold was already applied; report
   "already applied" and stop before step 5 (skip the commit — nothing new to commit).
2. Run `verify.fast` (config.md) — the generic replacement for a project-specific analyzer pass.
   If the command carries a `{files}` token, replace it with the step-4.1 paths, space-joined and
   shell-quoted — the same substitution `scripts/verify.py` performs, and the scope this skill is
   uniquely able to supply; a command without the token runs unscoped. Passing the token through
   unsubstituted sends a literal `{files}` to the shell, which fails a scaffold that is correct.
   An unconfigured `verify.fast` degrades this check to skipped, never green (config.md).
3. On failure: roll back — `git checkout -- <paths>` for tracked paths that were merely modified
   (safe: preflight proved them clean), `rm -f <paths>` for new untracked paths, then remove any
   directories those paths created and left empty — using the exact path list from step 4.1.
   Report the findings, stop.

## 5. Commit

Stage exactly the paths from step 4.1 (never a broad `git add -A`) and commit:

```bash
git add <paths from step 4.1>
git commit -m "chore: enable agent UI automation"
```

English, no trailers. Verify with `git show --stat HEAD` that the commit contains exactly those
paths before reporting success.

## 6. Report

- Commit hash + the changed paths (from step 4.1).
- Reminder: `/artel:drive-app` is now available (subject to its own `runtime.drive`
  configuration); run `/artel:remove-automation` before merge —
  `${CLAUDE_PLUGIN_ROOT}/agents/validator.md` reports the `AUTOMATION_REMOVED` gate red while the
  scaffold is applied.
