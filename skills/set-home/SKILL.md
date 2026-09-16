---
name: set-home
description: "Move this project to a different VCS platform: parse a repository URL, rewrite vcs.* in .artel/config.json, and re-point the git remotes so config and origin agree"
argument-hint: "<repo-url>"
model: sonnet
---

Worker, not an orchestrator — no agent matches this job; it runs inline, like `setup` /
`sync-phases` / `generate-idea`. Config contract: `${CLAUDE_PLUGIN_ROOT}/docs/config.md`.

This skill is the only place where `.artel/config.json`'s `vcs.*` keys and the repository's git
remotes move together. Changing one without the other is the failure it exists to prevent.

## 1. No argument — report and stop

With an empty `$0`, print the current home and terminate without changing anything:

- `vcs.adapter` and `vcs.mcpToolPrefix` from `.artel/config.json`
- `git remote -v`
- whether the two agree (does `origin`'s host match the adapter's platform?)

## 2. Parse `$0`

| Platform | Accepted shapes |
|---|---|
| GitHub | `https://github.com/<owner>/<repo>[.git]`, `git@github.com:<owner>/<repo>.git`, and any `github.*` host (GitHub Enterprise) |
| Bitbucket Server | `https://<host>/scm/<projectKey>/<repoSlug>.git`, `https://<host>/projects/<P>/repos/<R>` |
| Bitbucket Cloud | `https://bitbucket.org/<workspace>/<repo>[.git]`, `git@bitbucket.org:<workspace>/<repo>.git` |

A host matching none of these → error and terminate. Never guess a platform from an unknown
host:

```
Error: Could not tell which platform <url> belongs to. Supported hosts: github.*, bitbucket.org,
and Bitbucket Server URLs carrying /scm/ or /projects/.
```

Target adapter: GitHub → `github-cli`; either Bitbucket → `bitbucket-mcp`.

## 3. Config gate

`.artel/config.json` missing → error and terminate; this skill revises a home, it does not
create a config:

```
Error: No .artel/config.json. Run /artel:setup first.
```

Target adapter `bitbucket-mcp` with an empty `vcs.mcpToolPrefix` → ask for the prefix (full,
including the trailing separator, e.g. `mcp__vcs__`) and re-ask until non-empty. config.md
reading rule 3 forbids writing an empty one.

## 4. Preview and confirm

Show exactly what will change — every config key, every remote — then confirm via
`AskUserQuestion`: **Apply** / **Abort**. Remote surgery is hard to reverse on a repository
shared with a team, so it never happens unasked. **Abort** leaves everything untouched.

## 5. Git surgery

Skip this whole section when `origin`'s URL already equals the target — the skill is
idempotent, and a re-run must not build a chain of renamed remotes.

1. `git remote rename origin <old-platform>` — `bitbucket` or `github`, or `old-origin` when the
   current `origin` URL's host matches no known platform. On a name collision, suffix `-2`, then
   `-3`, and so on.
2. `git remote add origin <new-url>`
3. `git fetch origin`
4. `git remote set-head origin -a` — **required, not cosmetic**: `pr-create` and
   `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone mode resolve the default branch
   through `git symbolic-ref refs/remotes/origin/HEAD`, which points at the old platform's
   default until this runs.
5. Re-point the current branch's upstream when a branch of that name exists on the new remote:
   `git branch --set-upstream-to=origin/<branch> <branch>`. Leave it alone otherwise.

Never any `--force` variant. Never delete a remote — the old one stays so `/artel:migrate-prs`
can fetch the open PRs' source branches, and the operator removes it by hand when done.

## 6. Write the config

Read-modify-write, preserving key order per config.md's "A filled example". Only `vcs.*`
changes:

- `vcs.adapter` → the target adapter.
- `vcs.mcpToolPrefix` → **left exactly as it was** when the target is `github-cli`. That adapter
  ignores the key, and `/artel:migrate-prs` needs it to address the old platform's MCP tools for
  reading. Clearing it would break the migration.
- `tracker.adapter` is asked about **only** when it names the platform being left — i.e.
  `github-issues` while moving off GitHub. Moving Bitbucket → GitHub leaves `jira-mcp` untouched
  and silent: Jira is platform-independent.

## 7. Report

- `<old adapter>` → `<new adapter>`, and the `git remote -v` layout now.
- A reminder that `.artel/config.json` is committed team configuration — commit it by hand.
- When the old platform is Bitbucket, check for open pull requests
  (`<vcs.mcpToolPrefix>bitbucket_list_repo_prs`, state `OPEN`) and, if any exist, offer
  `/artel:migrate-prs`.

## Rules

- Never `git push` anything. This skill rearranges remotes and config; it moves no commits.
- Never delete a remote or a branch.
- Never write a config that config.md's reading rules would reject at run start.
