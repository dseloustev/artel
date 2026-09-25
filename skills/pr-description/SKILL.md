---
name: pr-description
description: "Generate a short, plain-language PR description for a ticket, styled to match recent merged PRs"
argument-hint: "[ticket-id]"
model: sonnet
---

## Ticket Resolution

Parse `$0` into `TICKET_ID`, `TICKET_NUM`, `PHASE_NUM` per `${CLAUDE_PLUGIN_ROOT}/docs/orchestrator-common.md` §2 and `${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2. If `$0` is empty, read the first non-empty line of `<specs.dir>/.active_ticket`; if no identifier is available, error with "Error: No ticket specified. Provide a ticket ID as a parameter or set it in <specs.dir>/.active_ticket" and terminate.

`PHASE_NUM` is parsed for input compatibility (so a phase-suffixed identifier is accepted) but is **not** used here — the PR description always covers the whole branch.

### Base branch

Determine the repo's default branch first (e.g. via `git symbolic-ref refs/remotes/origin/HEAD`, or ask if ambiguous — the same resolution `${CLAUDE_PLUGIN_ROOT}/agents/reviewer.md`'s standalone mode uses) and use it as `BASE_BRANCH` below.

## Pipeline invocation

`feature-development` invokes this skill automatically at run completion — after `validate` reports all gates green, before `run-state.json` is marked complete (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §9). Pipeline behavior is identical to manual invocation, with one deliberate exception to skip-if-exists (§9 itself): an existing `pr-description.md` is **always regenerated**, never skipped — the branch diff is the input, so a file written earlier in the run is stale by definition. This skill never calls `AskUserQuestion`; tracker/VCS failures degrade gracefully (fallback template) and must never loop, escalate, or block the run.

## Pre-flight (run in this skill, not in the agent)

The skill is an orchestrator — it gathers inputs, then delegates writing to the `tech-writer` agent. Collect the following directly:

### 1. Ticket summary

Branch on `tracker.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`):

- **`"jira-mcp"`**: call `<tracker.mcpToolPrefix>jira_get_issue` with `issueIdOrKey: TICKET_ID`. Capture `summary`, `description`, any acceptance-criteria field, plus `status`, `issuetype`, `priority`.
- **`"github-issues"`**: call `gh issue view <TICKET_NUM> --json title,body,state,labels` in the host repo. Capture the same shape (title → summary, body → description, state, labels).
- **`"none"`**: no tracker to call — skip this step; the local docs below are the sole source.

The content may be in any language — pass it through as-is; the agent writes the final document in `language.pr`. If the call fails (auth, network, missing ticket) or the adapter is `"none"`, record `TICKET_SUMMARY_UNAVAILABLE` and continue — do not abort.

### 2. Local docs

Read each of the following with `Read` if the file exists; skip silently otherwise:

- `<specs.dir>/<TICKET_ID>/idea.md`
- `<specs.dir>/<TICKET_ID>/vision.md`
- `<specs.dir>/<TICKET_ID>/tasklist.md`
- `<specs.dir>/<TICKET_ID>/review.md` — only its `## Manual checks outstanding` section (the
  reviewer's list of checks nobody has run yet)

### 3. Git data

Run via `Bash` (one block, sequential):

```bash
BASE_BRANCH=<resolved above>

git branch --show-current
git log "$BASE_BRANCH"..HEAD --oneline
git diff "$BASE_BRANCH"...HEAD --stat
git diff "$BASE_BRANCH"...HEAD
```

Capture each output. The three-dot `...` diff (merge-base) is deliberate: after the base branch advances or is merged into the ticket branch, a two-dot diff would include reversed upstream changes that have nothing to do with the ticket. If the host repo marks any files as generated (analyzer/linter exclusion lists, generated-file headers) or has lockfiles, exclude them from the diff and stat with `:(exclude)` pathspecs — they are noise, not ticket content; don't invent a fixed exclusion list where the host has none. The diff may be large — pass it to the agent verbatim; do not truncate here.

### 4. Style sample (3–5 recent merged PRs)

Branch on `vcs.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`):

- **`"github-cli"`**: `gh pr list --state merged --limit 5 --json number,title,body` in the host repo.
- **`"bitbucket-mcp"`**: derive `projectKey`/`repositorySlug` from `git remote get-url origin` (Bitbucket Server clone-URL shape: `.../<projectKey>/<repositorySlug>.git`) — do not hardcode them. If the remote is missing or its URL doesn't match the expected shape, stop and ask. Call `<vcs.mcpToolPrefix>bitbucket_list_repo_prs` with that `projectKey`/`repositorySlug`, state filter `MERGED`, ordered newest first, limit 5. (Verify the exact parameter names by reading the tool's input schema before the first call — MCP servers occasionally rename fields.) For each PR ID returned, call `<vcs.mcpToolPrefix>bitbucket_get_pr` to retrieve the title and full description body.

Drop any PR with an empty/whitespace-only description. Keep the first 3–5 non-empty ones. Concatenate them into a single string `STYLE_SAMPLE`, separating each PR with a `---` line. Prefix each PR with its title and ID, e.g. `### PR #123 — Some title\n\n<body>`.

If the fetch fails, set `STYLE_SAMPLE` to empty and continue — the agent will fall back to the legacy template described below.

## Delegate to `tech-writer` agent

**Spec store.** Before dispatching, read the ticket's storage decision:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py decision <TICKET_ID>`. `fresh: true` → use
its `store` and `reason`. Anything else → resolve per `${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md`
§2.1, which may ask the user — except while `.artel/run/<TICKET_ID>/run-state.json` has
`run_active: true`: then return `STORE_UNAVAILABLE: <record>` to your caller and stop. Every
dispatch prompt in this skill carries the result verbatim, as `**Spec store:** kartoteka` or
`**Spec store:** files (<reason>)`. An agent's `STORE_UNAVAILABLE` return goes back to your caller
unchanged. This skill's own reads, existence checks and writes of spec documents follow §4.1 and
§4.2 — an existence check is `spec_store.py exists <path>` (exit 0 present, 3 absent).

Make a single `Agent` call:

- `subagent_type`: `"tech-writer"`
- `description`: `"PR description for <TICKET_ID>"`
- `prompt`: built from the captured inputs using the template below.

```
You are writing a short, plain-language PR description for ticket <TICKET_ID>. A finished body runs about 10–20 non-empty lines (~70–200 words) — the scale of the PRs in the style sample below.

## Context

- **Ticket ID:** <TICKET_ID>
- **Ticket Number:** <TICKET_NUM>
- **Base Branch:** <BASE_BRANCH>
- **Current Branch:** <CURRENT_BRANCH>
- **Output File:** `<specs.dir>/<TICKET_ID>/pr-description.md`

## Inputs (all inlined below — do not re-fetch)

### Ticket summary
[Either the captured summary/description/acceptance/status/issuetype/priority, or the literal string "TICKET_SUMMARY_UNAVAILABLE" if no tracker call succeeded. The text may be in any language — the final PR description must be in <language.pr>.]

### Local docs
- **idea.md:** [contents, or "(not found)"]
- **vision.md:** [contents, or "(not found)"]
- **tasklist.md (for <TICKET_ID>):** [contents from <specs.dir>/<TICKET_ID>/tasklist.md, or "(not found)"]

### Git data
- **Commit log (<BASE_BRANCH>..HEAD):**
  [git log output]
- **Diff stat:**
  [git diff --stat output]
- **Full diff:**
  [git diff output]

### Style sample (3–5 recent merged PRs)
[STYLE_SAMPLE — multiple PR descriptions separated by `---`, or the literal string "STYLE_SAMPLE_EMPTY" if the VCS fetch failed or returned no usable bodies.]

## How to write the PR description

1. **Derive the structure and length from the style sample.** Read the sampled PR descriptions and identify the recurring elements: which section headings appear in most of them, in what order, what tone (terse vs. narrative), and how long they run. Reproduce that. Do **not** impose a template that isn't reflected in the sample. The longest PR in the sample is the ceiling for your draft — if the draft comes out longer, cut detail until it fits.

2. **Markup.** Use bold-text headings on their own line, matching the sample's own heading set and phrasing — **not** markdown `#` / `##` headings. Do **not** include an H1 title in the body; the PR title is a separate field. The file opens with the document header (`${CLAUDE_PLUGIN_ROOT}/docs/spec-storage.md` §3.2) — `type: pr-description`, `produced_by: artel:tech-writer` — which `pr-create` drops before posting; the body starts after it. Reproduce any recurring sub-bullet convention (e.g. italic major/minor-changes labels) exactly as the sample uses it.

3. **Title** (for the PR title field, not the body). Use the ticket summary as the source, translated to `<language.pr>` if it is in another language. If the sampled PRs prefix titles with `<TICKET_ID>` or similar, match that convention; otherwise omit the prefix. Keep the title under 80 characters. Do not put it inside the output `.md` file as an H1 — the body should start with the style sample's own first heading.

4. **Language.** Body must be `<language.pr>`. Translate any content in another language (e.g. tracker fields, local docs, code comments paraphrased in prose) into it. Keep code identifiers, file paths, class/method names, branch names, and commit hashes verbatim — do not transliterate them.

5. **Reader contract.** The description is written for a QA engineer and a reviewer who have not opened the code and will not read the ticket docs. Every sentence states visible behavior, user impact, or a check the reader can perform. A bullet may name the single class/package/model that is the subject of its change — that is the only place code identifiers appear. Everything about *how* the change works — measurements, constants, internal callbacks and lifecycle events, rejected alternatives, design rationale — lives in the diff; the description states what changed and why.

6. **Section contract** — what each section is, using the style sample's own headings:
   - **Summary** — 1–3 sentences: what was broken or missing, and how the app behaves now. If the PR targets a non-default base branch, one extra line names it.
   - **Major changes** — 0–2 one-line bullets: only changes a reviewer must know about before reading the diff (behavior change, schema bump, new dependency). State "None." when there are none.
   - **Minor changes** — 1–4 one-sentence bullets. State "None." when there are none.
   - **QA notes** — 1–6 numbered scenarios: where in the app + what to do + what should be visible; platform-specific caveats live here. A deliberate behavior change that QA might file as a bug gets one line starting "Not a bug: …". Every entry of `review.md`'s `## Manual checks outstanding` becomes one more numbered scenario, marked as not yet run.
   - **Platforms tested** — the checklist, or a single "any platform" line for platform-independent changes.

7. **Accuracy.** Every factual claim must be verifiable from the inlined diff or the inlined docs. Do not invent rationale or behavior. If something cannot be verified, omit it.

### Fallback structure (use ONLY if STYLE_SAMPLE_EMPTY or the sample is too short to derive a structure)

The headings below are illustrative — render them, like the rest of the body, in `<language.pr>`.

```
**Summary**

1–3 sentences: what was broken or missing — and how the app behaves now.

**Detailed description**

- _Major changes_
  - 0–2 one-line bullets: only what the reviewer must know before reading the diff (behavior change, schema migration, new dependency). "None." if there are none.

- _Minor changes_
  - 1–4 one-sentence bullets: related touch-ups. "None." if there are none.

**QA notes**

1–6 numbered scenarios: where in the app + what to do + what should be visible. Platform-specific caveats go here too. A deliberate behavior change QA might mistake for a bug gets its own line: "Not a bug: …". The reviewer's `## Manual checks outstanding` entries follow, marked as not yet run.

**Platforms tested**
- [Platform 1]: done / not done
- [Platform 2]: done / not done

(Or a single line "Any platform" when the change is platform-independent — also an accepted form.)
```

## Output

Write the completed PR description to `<specs.dir>/<TICKET_ID>/pr-description.md`. Report a one-line summary of where you wrote it and which structural source you used (style sample vs. fallback template).
```

## Rules

- The skill is an **orchestrator**. It must not write `<specs.dir>/<TICKET_ID>/pr-description.md` itself — only the `tech-writer` agent does.
- Every external dependency (tracker, VCS style sample, local docs) is optional and degrades gracefully. The skill always produces a usable PR description as long as the git diff is available.
- Do not cache the style sample — fetch fresh on every run, per design.
- Do not include files the host marks as generated, or lockfiles, in the diff handed to the agent.
