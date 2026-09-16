---
name: using-artel
description: "Use when starting any conversation in an artel-configured repo — checks whether the request is ticket, feature, queue or knowledge work and routes it to the matching /artel: skill before any other response or action, including clarifying questions."
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task — an artel agent (analyst,
researcher, planner, implementer, reviewer, …) or any other — ignore this skill. Routing is the
main session's job; yours is the task you were given.
</SUBAGENT-STOP>

This repo runs artel: a spec-driven feature workflow where a ticket goes in and a pull request
comes out. The host status lines injected with this block say whether `.artel/config.json`
declares kartoteka and which ticket is in flight.

## The rule

Before any response or action — including clarifying questions, reading files, or exploring
the codebase — decide whether the request is **artel's domain**: ticket work, a pipeline stage,
the task queue, or the project's institutional knowledge. If it is, invoke the matching
`/artel:` skill from the tables below, announce "Using `/artel:<name>` to <purpose>", and
follow it. The skill resolves the ticket, reads the config and asks its own questions; do not
ask them first.

If the request is not artel's domain, this skill does not apply — proceed as you otherwise
would, with whatever process skills the host has.

## Routing

No ticket argument → the skill reads `<specs.dir>/.active_ticket` (the status line above names
it). `<ticket>-<N>` is a phase-scoped run.

**Entry points**

| The user wants… | Run |
|---|---|
| a ticket taken from idea to pull request, one approval pause | `/artel:feature-development <ticket> [description-file] [--mode=…] [--dry-run] [--local]` |
| just the approved work plan, no implementation | `/artel:feature-development <ticket> --dry-run` |
| a small change implemented, reviewed and runtime-checked, no PRD/QA/docs | `/artel:dev <ticket> [description-file] [--mode=…]` |
| the next phase of a phased ticket | `/artel:feature-development <ticket>-<N>` or `/artel:dev <ticket>-<N>` |
| an interrupted run resumed | re-invoke the same entry-point command |
| artel configured or reconfigured for this repo | `/artel:setup` |

**Pipeline stages, à la carte**

| The user wants… | Run |
|---|---|
| the ticket imported from the tracker into `idea.md` | `/artel:generate-idea <ticket> [description-file]` |
| the Figma design analysed (flow, screen mapping) | `/artel:figma-analysis <ticket> [figma-url]` |
| the PRD interview | `/artel:analysis <ticket> [description-file] [--local]` |
| the technical vision document | `/artel:generate-vision <ticket> [idea-file]` |
| research on the codebase for the ticket | `/artel:researcher <ticket> [--local]` |
| the architecture and implementation plan | `/artel:planner <ticket>` |
| the plan broken into a tasklist | `/artel:tasklist <ticket>` (from plan) · `/artel:generate-tasklist <ticket> [idea-file] [vision-file]` (from idea + vision) |
| the next open task implemented | `/artel:implementer <ticket> [--local]` |
| a quick verify loop on the paths just touched | `/artel:inner-loop [paths]` |

**Quality gates and close-out**

| The user wants… | Run |
|---|---|
| the ticket's changes reviewed | `/artel:run-reviewer <ticket>` |
| a branch reviewed and its review outcome forecast from kartoteka precedents | `/artel:deep-review <ticket> [branch] [pr-link] [--local]` |
| the app built and launched (gate or inspection) | `/artel:run-app [--gate]` |
| the running app driven through its UI | `/artel:drive-app` (needs `/artel:add-automation` first) |
| a QA plan and report | `/artel:qa <ticket> or R-<release>` |
| to know which gates have passed | `/artel:validate <ticket> or R-<release>` |
| documentation updated for the ticket's work | `/artel:docs-update <ticket>` |
| a PR description / the PR opened | `/artel:pr-description <ticket>` · `/artel:pr-create <ticket>` |
| a single PR comment addressed | `/artel:address-pr-comment <pr-comment-url>` |

**Utilities**

| The user wants… | Run |
|---|---|
| a branch set up for a ticket | `/artel:init-branch <ticket>` |
| the project moved to a different VCS platform | `/artel:set-home <repo-url>` |
| Bitbucket's still-open pull requests recreated on GitHub after set-home | `/artel:migrate-prs [pr-id ...]` |
| merge conflicts resolved | `/artel:merge-conflicts [branch]` |
| phase status synced between tasklist and phase files | `/artel:sync-phases <ticket>` |
| a report or quiz on what happened on a branch | `/artel:change-digest [ticket]` |
| a text or a local file turned into a templated, Jira-ready issue description | `/artel:issue-draft` |
| the UI-automation scaffold applied / removed | `/artel:add-automation` · `/artel:remove-automation` |
| the working context saved / restored | `/artel:save-context` · `/artel:restore-context [ticket]` |
| `AGENTS.md` files created or repaired | `/artel:agents-md-generator` |

**Kartoteka — institutional knowledge and the task queue**

Only when the status line says `knowledge.adapter: kartoteka` and its `project` is a name, not
`NOT SET`. With `none`, say that the index is not declared for this project and point at
`/artel:setup`; with `project NOT SET`, say that `knowledge.project` is missing and point there
too — every kartoteka call needs it. A `knowledge.tokenEnv: … (NOT SET …)` line does not stop
routing: mention it once — the daemon may have `[auth]` on, and then the mirror hook's writes
will be refused until that variable is exported and the MCP registration carries the same token
(`--header`; config.md) — and route as usual.

| The user wants… | Run |
|---|---|
| to know what was decided or discussed about something | `/artel:knowledge <query>` |
| everything filed under a ticket (Jira, PRs, notes, its trail) | `/artel:knowledge <ticket> [--artifacts]` |
| to know whether the index is fresh | `/artel:knowledge status` |
| the ticket's iteration queue: rows, who holds what, whether it is stalled | `/artel:tasks list [ticket] [--status …]` |
| a task added to the ticket | `/artel:tasks add <ticket> "<title>" --iteration N` |
| a task marked done or blocked | `/artel:tasks done <id>` · `/artel:tasks block <id>` |
| a task a dead agent left held released | `/artel:tasks release <id>` |

**Code navigation — the AST index**

Only when the status line says `ast-index: on PATH` — the hook checks the CLI, which is what
the index runs on; if the `/ast-index:` skills are not installed in this session, run the CLI
directly (`ast-index search`, `usages`, `callers`, …). Then the index is the first tool for any
code search — before grep, ripgrep or the
Search tool — and its result is the complete answer; grep only for regex patterns, string
literals, comment text, or after an empty result.

| The user wants… | Run |
|---|---|
| a class, symbol or file found, or its usages / implementations / callers | `/ast-index:ast-index` |
| the project's structure, conventions, frameworks or module dependencies | `/ast-index:ast-index` |
| the index created for this checkout, or it reports "Index not found" | `/ast-index:initialize` |
| the index refreshed after a pull or merge | `ast-index update` (per `/ast-index:ast-index`) |

With `not on PATH`, code navigation is not routed here — use the ordinary tools.

## Precedence

1. The user's instructions (`CLAUDE.md`, a direct request) win over these tables.
2. For artel's domain, the `/artel:` skill wins over generic process skills. An entry point
   **is** the process: `feature-development` interviews, plans and pauses for approval; `dev`
   confirms a work list. Do not run brainstorming or plan-writing skills in front of them —
   that is the interview twice.
3. Everything else is not routed here.

Agents (`analyst`, `implementer`, `reviewer`, …) are never dispatched from here. Every one has
a skill that resolves ticket context first; use the skill.

## Red flags

| Thought | Reality |
|---|---|
| "I'll read `tasklist.md` to see what's next" | With the adapter on, the queue is authoritative for iteration work — `/artel:tasks list`; the file still owns the review/runtime/verify-fix and Final Verification sections. |
| "I'll call `search_knowledge` myself" | `/artel:knowledge` keeps the search budget, the citations and the ⚠ NON-CURRENT markers. |
| "I'll `task_create` it directly" | `/artel:tasks add` keeps `tasklist.md` and the queue in step; a bare row breaks promotion. |
| "I'll grep for that class" | With the index on PATH, `/ast-index:ast-index` answers in milliseconds; grep is for regex, literals and comments. |
| "This change is small, I'll just implement it" | Small is what `/artel:dev` is for — gates included. |
| "I'll brainstorm first, then run the pipeline" | The pipeline interviews. Run it. |
| "I'll ask a clarifying question first" | The skill asks its own. Route first. |
| "I remember what this skill does" | Skills change. Invoke it and read it. |
