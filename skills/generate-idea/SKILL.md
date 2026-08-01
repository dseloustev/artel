---
name: generate-idea
description: "Import the ticket from the configured tracker (or gather a description directly, when untracked) and write <specs.dir>/<TICKET_ID>/idea.md as the seed for the feature workflow"
argument-hint: "[ticket-id] [description-file]"
model: sonnet
---

## Overview

Bootstraps the feature workflow by producing `<specs.dir>/<TICKET_ID>/idea.md`, the seed that
`generate-vision`, `analysis`, `researcher`, and `tasklist`/`generate-tasklist` all consume.
Behavior is driven by `tracker.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`):

- **`"none"` (default)** — no tracker to fetch from. `<specs.dir>/<TICKET_ID>/idea.md` is a local
  file: this skill gathers its content from a description-file argument or directly from the
  user, the same way the `analysis` skill's input gate does.
- **`"jira-mcp"`** — fetch the issue and its comments through
  `<tracker.mcpToolPrefix>jira_get_issue` / `<tracker.mcpToolPrefix>jira_get_issue_comments`.
- **`"github-issues"`** — fetch the issue and its comments through the `gh` CLI.

Either way, natural-language content is translated into `language.docs` (config.md) before it is
rendered into the idea document.

This skill is a **worker, not an orchestrator** — like `sync-phases`, it runs inline rather than
delegating to a subagent: no agent matches this job, and pure-procedure utility skills keep their
procedural shape (they stay config-driven instead).

## Ticket Resolution

Parse `$0` into `TICKET_ID` and `TICKET_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

Ideas are ticket-level only. If a phase suffix is present in the input, ignore it and note this in the final report.

## File Path Resolution

| Artifact | Path |
|---|---|
| Output idea | `<specs.dir>/<TICKET_ID>/idea.md` |
| Template | `${CLAUDE_PLUGIN_ROOT}/skills/generate-idea/assets/templates/idea.template.md` |
| Active ticket | `<specs.dir>/.active_ticket` |

Ensure `<specs.dir>/<TICKET_ID>/` exists before writing.

## Steps

### Step 1: Pre-flight — check for an existing idea file

Read `<specs.dir>/<TICKET_ID>/idea.md`.

**Pipeline invocation** (from an orchestrator): if it exists, skip — report `Idea exists —
skipped` and terminate without touching the tracker (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9).

**Manual invocation:** if it exists, ask via `AskUserQuestion`:
- **Question:** `<specs.dir>/<TICKET_ID>/idea.md already exists. Overwrite it?`
- **Options:** `Overwrite` — replace with freshly gathered content / `Abort` — leave untouched and stop.

On `Abort`, report `Aborted: existing <specs.dir>/<TICKET_ID>/idea.md left untouched.` and terminate.

### Step 2: Gather the ticket content

Branch on `tracker.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`):

**`"none"`:**
1. If a description-file argument (`$1`) was passed, read it as the source content.
2. Otherwise, ask the user for a feature description via `AskUserQuestion` — the same input gate
   the `analysis` skill runs: show one concrete example of an actionable description, and reject
   vague input ("make it better", "fix stuff") the same way. Never guess, never write a
   placeholder idea.
3. There is no tracker, so there is no issue-metadata block (type, status, priority, reporter,
   assignee, labels, components, fix versions, linked issues) and no comment thread — Step 4's
   `$METADATA` and `$COMMENTS_RENDERED` each render their no-tracker line instead (see Step 4)
   rather than inventing values.

**`"jira-mcp"`:**
0. Adapter-unusable check first, per config.md's `jira-mcp` contract: if `tracker.mcpToolPrefix`
   is empty, or the `<tracker.mcpToolPrefix>jira_get_issue` tool is not connected/reachable,
   report the failure, then:
   - if `<specs.dir>/<TICKET_ID>/idea.md` already exists, fall back to it — report that the
     existing file is being used in place of a tracker fetch, and stop (do not overwrite it);
   - otherwise, stop and ask the user how to proceed (fix the tracker connection, or re-invoke
     with a description-file argument to bootstrap the idea by hand).
   Do not attempt step 1 when this check fails.
1. Call `<tracker.mcpToolPrefix>jira_get_issue` with `issueIdOrKey: TICKET_ID`. Capture
   `fields.summary`, `fields.description`, `fields.status.name`, `fields.priority.name`,
   `fields.labels`, `fields.components` (list of `{ name }`), `fields.reporter.displayName`,
   `fields.assignee.displayName`, `fields.issuetype.name`, `fields.fixVersions` (list of
   `{ name }`), `fields.issuelinks` (direction + linked issue key + relationship type). This is a
   connected-server API error (404, 401, network failure mid-call) — distinct from step 0's
   unreachable-tools case — so report the error and terminate without writing any files; there is
   no local-file fallback here.
2. Call `<tracker.mcpToolPrefix>jira_get_issue_comments` with `issueIdOrKey: TICKET_ID,
   maxResults: 100, startAt: 0`; if the response's `total` exceeds `startAt + comments.length`,
   keep paginating with `startAt += 100`. For each comment capture `author.displayName`,
   `created`, `body`.

**`"github-issues"`:**
1. Fetch the issue via `gh issue view <TICKET_NUM> --json title,body,state,labels,assignees` in
   the host repo. On error (`gh` not installed/authenticated, issue not found), report the error
   and terminate without writing any files.
2. Fetch comments via `gh issue view <TICKET_NUM> --json comments`; each comment carries
   `author.login`, `createdAt`, `body`.
3. There is no Jira-style type/priority/fix-versions/linked-issues metadata — Step 4's `$METADATA`
   omits those fields entirely (see Step 4) rather than rendering a placeholder for each.

### Step 3: Translate to `language.docs`

Translate the title, description/body, and every comment body into `language.docs`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`). Apply unconditionally — content already in that
language passes through unchanged (do not add machine-translation tags or notes). Under
`tracker.adapter: "none"`, this step only applies if the gathered description is not already in
`language.docs`.

**Preserve verbatim** (do not translate or rewrite): code blocks (fenced and inline), URLs and
link targets, ticket keys, image references and attachments, file paths, variable names,
identifiers, Markdown structure (headings, lists, tables).

**Convert Jira markup to standard Markdown** where feasible (`"jira-mcp"` only):

- `{code:lang}...{code}` → fenced code block with language tag
- `{noformat}...{noformat}` → fenced code block (no language)
- `{quote}...{quote}` → blockquote (`> ...`)
- `h1.` / `h2.` / `h3.` heading prefixes → `#` / `##` / `###`
- `*bold*` → `**bold**`, `_italic_` left as-is
- `||header||header||` table rows → standard Markdown tables

If the description/body is empty or absent, set `SUMMARY` to the translated title and
`MOTIVATION` to `_(to be filled)_`.

### Step 4: Render the template

Read `${CLAUDE_PLUGIN_ROOT}/skills/generate-idea/assets/templates/idea.template.md` and substitute placeholders:

| Placeholder | Source |
|---|---|
| `$TITLE` | translated title |
| `$TICKET_ID` | parsed `TICKET_ID` |
| `$METADATA` | see below |
| `$SUMMARY` | first paragraph of the translated description, or the translated title if none |
| `$MOTIVATION` | remaining paragraphs of the translated description, or `_(to be filled)_` |
| `$COMMENTS_RENDERED` | see below |

**`$METADATA`** — under `"jira-mcp"` / `"github-issues"`, one bullet per captured field (omit
fields the adapter has no equivalent for, e.g. fix versions under `"github-issues"`); a
present-but-empty field renders `_(none)_`. Under `"none"`, the single line `_(no tracker
configured — tracker.adapter is "none")_`.

**`$COMMENTS_RENDERED`** — one block per comment, in chronological order:

```markdown
### {author} — {timestamp}

{translated body}
```

If there are no comments (always true under `"none"`), render the literal text `_No comments —
no tracker configured or none posted._`.

Sections that cannot be derived from the ticket (`In Scope`, `Out of Scope`, `Open Questions`)
keep the `_(to be filled)_` placeholder — do not omit them; downstream skills depend on the
structure being stable.

### Step 5: Write the output

Ensure `<specs.dir>/<TICKET_ID>/` exists. Write the rendered content to
`<specs.dir>/<TICKET_ID>/idea.md`.

### Step 6: Update `<specs.dir>/.active_ticket`

Write `TICKET_ID` as the sole content of `<specs.dir>/.active_ticket`, matching `analysis`'s
behavior, so the next workflow step (`generate-vision`, `analysis`) can be invoked without
re-passing the ticket.

### Step 7: Report

Print a concise summary: ticket ID and translated title; output path
(`<specs.dir>/<TICKET_ID>/idea.md`); number of comments included; which sections were filled from
the tracker vs. left as `_(to be filled)_`; a note that `<specs.dir>/.active_ticket` was updated;
and, if applicable, a note that a phase suffix in the input was ignored.

## Rules

- **Worker, not orchestrator.** No agent matches this skill's job — it runs inline, like
  `sync-phases`.
- **Never overwrite without confirmation.** Step 1 is mandatory.
- **Never invent content.** Sections the source (tracker or user) does not provide get the
  `_(to be filled)_` placeholder. Do not extrapolate scope or motivation beyond what the source
  says.
- **Preserve technical artifacts verbatim.** Code, URLs, ticket keys, file paths, and identifiers
  are never translated or rewritten.
- **Translate everything natural-language** into `language.docs`. Do not skip translation just
  because content already looks like it's in that language; verify each block individually.
- **Phase suffix is ignored.** Ideas are ticket-level only; a suffix in the input is parsed down
  to the bare `TICKET_ID` and noted in the report.
- **Fail fast on tracker errors.** If a tracker call fails, do not write a partial file.
- **Paths in output: repo-relative only** — see `${CLAUDE_PLUGIN_ROOT}/docs/path-conventions.md`.
