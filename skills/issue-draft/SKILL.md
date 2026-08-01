---
name: issue-draft
description: Use when the user wants to turn a text description, a chat/message excerpt, or a referenced local file into a tracker-ready issue draft — e.g. "draft an issue from this", "turn this into a bug report", or "write up this conversation as an issue". For producing an issue-ready draft file (summary + description), not for posting to a tracker.
model: opus
---

# Generating issue drafts

Turn a free-text description — or the contents of a referenced local file — into a tracker-ready
issue draft, and save it to a local file. Creating or submitting the issue in a tracker is out of
scope: this skill only produces the issue text, in `language.pr`
(`${CLAUDE_PLUGIN_ROOT}/docs/config.md`: "PR title and body, tracker comments, **drafted issues**"),
in whichever markup dialect matches the eventual destination.

## When to use

- The user gives a bug report, task, idea, or conversation excerpt and wants it shaped into an
  issue.
- The user points at a local file (`.txt`, `.md`, or a Slack-export `.json`) to convert into an
  issue.
- The user asks for a drafted issue, an issue file, or a write-up "ready to file".

## What you produce

A single file containing:

- **summary** — max 255 characters, concrete engineering-task wording, in `language.pr`.
- **description** — in `language.pr`, in the markup dialect resolved below.

No priority, assignee, labels, story points, issue type, or deadlines — unless the input
explicitly states them.

## Markup dialect

The description's markup follows `tracker.adapter` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`) — the
dialect the eventual destination actually renders, not a fixed choice:

- **`"jira-mcp"`** → Jira wiki markup (see cheat-sheet below).
- **`"github-issues"`** or **`"none"`** → standard Markdown.

## Workflow

1. **Resolve the input** (see "Input resolution"). Get the source text and decide single vs.
   multiple-suggestions mode and the output path.
2. **Normalize the source** into TARGET (the actionable thing) and optional CONTEXT (background).
3. **Generate** the issue(s) following "Generation rules" — in `language.pr`, in the resolved
   markup dialect.
4. **Write** the result to the output file using "Output format".
5. **Report** the output path to the user and print the generated summary (summaries, in multiple
   mode).
6. **Self-verify** against the checklist before declaring done.

## Input resolution

From the user's invocation and the surrounding request, determine three things:

- **Source:**
  - If the request references a path that exists on disk, read that file and use its contents as
    the source. Supported: `.txt`, `.md`, `.json` (Slack export).
  - If a referenced path exists but is unreadable or an unsupported type, tell the user and ask
    whether to treat the argument as literal text instead.
  - Otherwise, treat the user's descriptive text as the source directly.
  - If neither is present, ask the user for the text or a file path. Do **not** invent an issue
    from nothing.
- **Mode:**
  - **Single** (default) — one issue.
  - **Multiple suggestions** — when the user asks for options, alternatives, or a number of
    variants (in any language). Produce 2–5 variants.
- **Output path:**
  - If the user gives one, use it.
  - Else default to `issue-draft-<slug>.md` in the current working directory, where `<slug>` is a
    short ASCII/transliterated slug of the summary (e.g. `issue-draft-login-button-ios.md`).

### Normalizing a Slack-export `.json`

- It is typically an array of message objects (fields like `text`, `user`/`name`, `ts`). Sort by
  `ts` ascending (oldest first).
- Treat the **newest (last in sorted order) / most actionable** message as TARGET; the rest is
  CONTEXT.
- If no message is clearly the target, use the **last (most recent)** message and note the
  ambiguity under the "Missing details" section (see below).

For non-JSON sources (plain text, `.md`, `.txt`), treat the **entire source as TARGET**; there is
no separate CONTEXT.

## Generation rules (follow exactly)

1. Infer the most likely **actionable engineering task** from the source. Phrase it as a concrete
   task, **not** as a discussion recap.
2. Capture the real technical problem — bug, improvement, investigation, or follow-up. Include
   affected repository, library, platform, product, component, or integration **only if
   mentioned**.
3. Include reproduction details, symptoms, expected behavior, or evidence **only if present** in
   the source.
4. Mention dependencies, blockers, or related systems **only if** explicitly supported by the
   source.
5. Stay factual, concise, implementation-oriented. Prefer concrete technical wording over vague
   business wording.
6. **Never invent facts.** Preserve uncertainty explicitly (e.g. "per the author's assumption, not
   confirmed" — rendered in `language.pr`).
7. If the source is ambiguous, still produce the best possible draft, but list the gaps under the
   "Missing details" heading.
8. Do **not** assign priority, assignee, sprint, labels, story points, issue type, or deadlines
   unless the source explicitly states them.
9. **Write everything in `language.pr`** (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`), even when the
   source is in another language — headings included, since the draft is meant to be read (and
   filed) by a `language.pr`-speaking team as-is.
10. **summary** ≤ 255 characters (single mode). In multiple-suggestions mode, each title ≤ 100
    characters.

Build the **description** from the sections that apply (omit sections with no content — do not
pad), each heading rendered in `language.pr` and in the resolved markup dialect:

- **Description** — what the task is.
- **Context** — relevant background, if present.
- **Reproduction Steps** — numbered, for bugs, if present.
- **Expected Behavior** / **Actual Behavior** — for bugs, if present.
- **Suspected Cause** — only if the source suggests one (mark as unconfirmed).
- **Missing Details** — explicit bullet list when the source is ambiguous.

## Output format

### Single mode

```text
=== SUMMARY ===
<summary, ≤255 characters, in language.pr>

=== DESCRIPTION (<dialect label>) ===
<Description heading>
...
<Missing Details heading>
* ...

<Source label>: <path or link>   ← add ONLY if the source was a file/link; omit the line entirely for plain text
```

`<dialect label>` is `Jira wiki` when `tracker.adapter` is `"jira-mcp"`, otherwise `Markdown`.
`<Source label>` is the `language.pr` translation of "Source" (e.g. "Source:" for English) — the
whole file is in `language.pr`, this footer is no exception.

The source-footer line is included **only** when the source was a file path or the input contained
an explicit link; for plain inline text, omit it (do not write the placeholder line at all).

### Multiple-suggestions mode

Numbered blocks, one per variant:

```text
=== SUMMARY 1 ===
<variant 1 title, ≤100 characters>

=== DESCRIPTION 1 (<dialect label>) ===
...

=== SUMMARY 2 ===
<variant 2 title, ≤100 characters>

=== DESCRIPTION 2 (<dialect label>) ===
...
```

If the source was a file or link, append a single source-footer line after the last variant block
(not once per variant).

## Markup cheat-sheet

**Jira wiki** (`tracker.adapter: "jira-mcp"`):
- Headings: `h2.`, `h3.` (with the trailing dot and a space).
- Bullet list: lines starting with `* `. Numbered list: lines starting with `# `.
- Bold: `*text*`. Italic: `_text_`. Inline code: `{{text}}`.
- Code block: `{code}` … `{code}` (or `{code:java}`); preformatted: `{noformat}` … `{noformat}`.
- Do **not** use Markdown headings (`##`) or Markdown code fences inside the description — Jira
  will not render them.

**Markdown** (`tracker.adapter: "github-issues"` or `"none"`):
- Headings: `##`, `###`.
- Bullet list: lines starting with `- `. Numbered list: `1.`, `2.`, …
- Bold: `**text**`. Italic: `_text_`. Inline code: `` `text` ``.
- Code block: fenced with ` ``` `.

## Self-verify before finishing

- [ ] Output is entirely in `language.pr` (headings included).
- [ ] Summary ≤ 255 chars (single) / each title ≤ 100 chars (multiple).
- [ ] Description uses the markup dialect resolved from `tracker.adapter` — never a mismatched
      dialect (e.g. Jira wiki when the adapter is `"github-issues"`).
- [ ] No invented facts; uncertainty is marked; gaps are under the "Missing details" heading when
      the source was ambiguous.
- [ ] No priority / assignee / labels / issue type added unless stated in the source.
- [ ] Source-footer line is present when the source was a file path or link.
- [ ] Source-footer line is absent when the source was plain inline text.
- [ ] File written; path and summary reported to the user.

## Rules

- **Worker, not orchestrator.** No agent matches this skill's job — it runs inline, like
  `sync-phases` and `generate-idea`.
- **Draft only.** This skill never calls a tracker's issue-creation API — `tracker.adapter` governs
  the destination markup dialect and terminology only, not whether this skill submits anything.
- **Paths in the report: repo-relative only**, when the output file landed inside the repo — see
  `${CLAUDE_PLUGIN_ROOT}/docs/path-conventions.md`.
