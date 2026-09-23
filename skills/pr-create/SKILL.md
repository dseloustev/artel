---
name: pr-create
description: "Commit, push, open the pull request with the generated description, and link the tracker — idempotent loop-close"
argument-hint: "[ticket-id]"
model: sonnet
---

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

**Spec store.** This skill reads and writes spec-trail documents itself. Resolve the store
first: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>` — `fresh: true`
→ its `store`; otherwise resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §2.1, asking the
user only while no run is active for the ticket. On `kartoteka`, every spec-trail path below is
an address: operate on it as §4.1 (MCP tools) and §4.2 (scripts, by pipe) map each file
operation — never with Read/Write/Edit or a shell file command.

Idempotent by design: re-runs must never duplicate a PR, a commit of nothing, or a tracker comment.

### 1. Pre-flight

- Identity check, per adapter (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`): `vcs.adapter: "github-cli"` → `gh auth status`; `vcs.adapter: "bitbucket-mcp"` → `<vcs.mcpToolPrefix>bitbucket_whoami`. Same for `tracker.adapter`: `"jira-mcp"` → `<tracker.mcpToolPrefix>jira_whoami`; `"github-issues"` → `gh auth status` (same check as the VCS one above when both are `github-cli`/`github-issues` — don't run it twice); `"none"` → nothing to check. Any configured adapter's check failing → write the intended actions (commit subject, branch, PR title, tracker key) to `<specs.dir>/<TICKET_ID>/pr-pending.md`, report, stop. Never fabricate.
- `git branch --show-current` — on the default branch (determine it the same way `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone mode does: `git symbolic-ref refs/remotes/origin/HEAD`, or ask if ambiguous) → error out: `pr-create` never commits to the default branch.
- `git fetch origin <default>` (same default-branch resolution as above; no other git mutations) — freshens the local `origin/<default>` ref before any range computation, so a stale ref can't leak merged-in default-branch history into §5's tracker-key scrape.

### 2. Description

`<specs.dir>/<TICKET_ID>/pr-description.md` missing (kartoteka path: `spec_store.py exists …` exits 3) → `Skill: pr-description` with `$0` first.

### 3. Commit & push

- `git status --porcelain` clean → skip the commit (idempotent re-run), else stage the ticket's changed files explicitly (never `git add -A` on `.artel/**` or `<specs.dir>/**` unless they are the ticket's own artifacts; on the kartoteka path `<specs.dir>/<TICKET_ID>/` holds only evidence) and commit: conventional message, subject line only, no trailers, following the host repo's own commit-message conventions — `language.docs`/`language.pr` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`) govern spec-trail artifacts and PR-facing text, never commit messages.
- Push: `git push -u origin <branch>`. Never any `--force` variant. Rejected non-fast-forward → stop and report (the user reconciles; force-push is never an option).

### 4. PR (idempotency check first)

Branch on `vcs.adapter`:

- **`"github-cli"`**: `gh pr list --head <branch> --state open --json url,number,body` in the host repo. **Existing open PR** → do not create: report its URL; if `pr-description.md`'s content differs from the PR body, update the PR directly — `gh pr edit <number> --body-file <specs.dir>/<TICKET_ID>/pr-description.md` — kartoteka path: `doc=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/pr-description.md) && printf '%s\n' "$doc" | gh pr edit <number> --body-file -`; compare contents by piping both through `shasum` rather than reading the document here (the `gh` CLI can edit a PR body in place, unlike the Bitbucket adapter below).
  Else `gh pr create --base <default> --head <branch> --title "<TICKET_ID>: <tracker summary>" --body-file <specs.dir>/<TICKET_ID>/pr-description.md` — kartoteka path: `doc=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/pr-description.md) && printf '%s\n' "$doc" | gh pr create --base <default> --head <branch> --title "<TICKET_ID>: <tracker summary>" --body-file -` (title falls back to the branch name when no tracker summary is available).
- **`"bitbucket-mcp"`**: derive `projectKey`/`repositorySlug` from `git remote get-url origin` (Bitbucket Server clone-URL shape: `.../<projectKey>/<repositorySlug>.git`) — do not hardcode them. If the remote is missing or its URL doesn't match the expected shape, stop and ask. Call `<vcs.mcpToolPrefix>bitbucket_list_my_prs` with `role: AUTHOR`, then match entries on BOTH `fromRef.displayId` == current branch AND `fromRef.repository.slug` == `repositorySlug` — the tool is a cross-repo dashboard with no projectKey/repositorySlug params and also returns PRs where the user is only a reviewer, so branch name alone is not a safe filter. **Existing open PR** → do not create: report its URL, and if `pr-description.md` changed since the PR was opened, post the refreshed description as a PR comment (`<vcs.mcpToolPrefix>bitbucket_create_pr_comment`, prefixed `Updated description:`) — this adapter has no PR-update tool.
  Else `<vcs.mcpToolPrefix>bitbucket_create_pr` (`projectKey`, `repositorySlug`): source = current branch, target = default branch, title = `<TICKET_ID>: <tracker summary>` (fallback: branch name), description = `pr-description.md` content (kartoteka path: read it with `artifact_get(project=<project>, …)` — the MCP call needs the text anyway).

### 5. Tracker link

Branch on `tracker.adapter`:

- **`"jira-mcp"`**: primary key sources: `TICKET_ID` (resolved in the preamble) and the branch name. Additionally scrape commits unique to this branch — `git log origin/<default>..HEAD --oneline` (pattern `<ticket.projectKey>-\d+`, case-insensitive), never the full `<base>..HEAD` range — so a key that exists only in merged-in `origin/<default>` history (not in this restricted range) is never commented. For each key found (usually one): `<tracker.mcpToolPrefix>jira_get_issue` to confirm it exists, then `<tracker.mcpToolPrefix>jira_add_comment` with the PR URL — unless an identical comment already exists (check the latest comments first). **Never** transition the ticket's status.
- **`"github-issues"`**: same key-scrape (`<ticket.projectKey>-\d+`, or the bare issue number if the host's issue references don't carry the project key); `gh issue view <TICKET_NUM> --json comments` to confirm it exists and check for a duplicate, then `gh issue comment <TICKET_NUM> --body "<PR URL>"` unless an identical comment already exists.
- **`"none"`**: report `skipped (no tracker)`.

### 6. Report

`PR_OPENED: <url>` (or `PR_EXISTS: <url>`), commit hash, tracker keys commented, and anything skipped.

Additionally: if `runtime.scaffold` is configured (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`) and its artifacts still appear present on the branch, append to the report: "automation still applied — run `/artel:remove-automation` before merge (expected order: it lands as a cleanup commit on this PR)".
