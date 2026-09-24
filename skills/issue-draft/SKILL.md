---
name: issue-draft
description: "Use when the user wants a text description, a pasted ticket or a local .txt/.md file turned into a Jira-ready task, bug report or epic, in any language — e.g. draft an issue from this, write these notes up as a ticket, format this bug, describe this epic. Writes a summary plus a type-specific templated description to a local file; gathers context from kartoteka, the tracker, Figma and the host code, and asks about remaining gaps first. Draft only — never posts to a tracker."
argument-hint: "<text | file-path> [--type task|bug|epic] [--local]"
model: opus
---

# Drafting a Jira issue

Turn free text — or a local `.txt`/`.md` file — into a **summary** and a **description that
follows the template for its issue type** (task, bug or epic), saved to one local file. The
description block pastes into the tracker's description field as-is. Creating or submitting
the issue is out of scope: this skill writes a file and posts nothing.

The written file is in `language.pr` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`: "PR title and
body, tracker comments, **drafted issues**"), in the markup dialect the destination renders
(§0) — what you say in the conversation (questions, the report, the abort line) follows the
language of the user's own words, or `language.pr` when the invocation carries none beyond the
source. Retrieved facts are paraphrased in `language.pr` too, attributed (§6).

`--type task|bug|epic` flag: fix the issue type instead of letting §2 pick it.

`--local` flag: draft without the network — no kartoteka, no tracker, no Figma; only the host
code is read (§0, §4).

**Worker that delegates retrieval.** Drafting runs inline, because it talks to the user (§5),
like `sync-phases`, `generate-idea` and `knowledge`. The one thing it hands off is retrieval:
the `issue-scout` agent (§4) reads kartoteka, the tracker, Figma and the host code and returns a
fact sheet. The scout consumes the read-side contract
`${CLAUDE_PLUGIN_ROOT}/docs/knowledge-consultation.md` — §1 (gate, resolved here in §0), §2
(calls) and §5 (injection rule) — and declares its deviations in its own body: an error ends
that source, not the draft; nothing is recorded under `<specs.dir>`; a larger budget; retrieved
facts are paraphrased with attribution rather than quoted verbatim (§6).

## When to use

- The user gives a task, a bug report, an epic idea, a pasted conversation or a pasted ticket
  and wants it shaped into an issue.
- The user points at a local `.txt` or `.md` file to convert into an issue.
- The user asks for a drafted issue, an issue file, or a write-up "ready to file".

## What you produce

One file with two blocks:

- `=== SUMMARY ===` — max 255 characters, concrete engineering-task wording, in `language.pr`.
- `=== DESCRIPTION (<type>, <dialect label>) ===` — the type's template (§6) rendered in
  `language.pr` and the resolved dialect. It holds the issue and nothing else: open questions
  and notes to the author go to the report (§8), never into the description.

No priority, assignee, labels, story points, sprint or deadlines — unless the input explicitly
states them. The type picks the template and is named in the block header; it is not a
tracker field this skill sets. One draft per invocation.

## 0. Resolve configuration

Read from `.artel/config.json` (`${CLAUDE_PLUGIN_ROOT}/docs/config.md`): `language.pr`,
`tracker.adapter`, `tracker.mcpToolPrefix`, `design.figma`, `knowledge.adapter`,
`knowledge.project`, `ticket.pattern`, `ticket.projectKey`. Resolve:

- **Dialect** — `Jira wiki` when `tracker.adapter` is `"jira-mcp"`; `Markdown` when it is
  `"github-issues"` or `"none"`. The label goes into the description block's header. Jira
  wiki: read `${CLAUDE_PLUGIN_ROOT}/skills/issue-draft/references/jira-wiki-markup.md` before
  rendering. Markdown: the cheat-sheet at the end of this file.
- **Template** — once §2 has picked `<type>`: `.artel/templates/issue-draft-<type>.md` in the
  host repo when that file exists; else `.artel/templates/issue-draft.md` (a single host
  override, used for every type); else
  `${CLAUDE_PLUGIN_ROOT}/skills/issue-draft/assets/templates/<type>.template.md`. The
  template's HTML comments are its block rules (§6); a host override keeps that convention.
  A host override that marks no block `required` is used anyway, and the report says so.
- **Sources** — which of the scout's four sources are on for this run (§4):
  - *kartoteka* — the consultation contract's §1 table with `--local` as its first row, and the
    `knowledge.project` precondition resolved before the table: adapter `kartoteka` with the key
    empty or outside `^[a-z0-9][a-z0-9-]*$` is a configuration error — kartoteka is off, and
    the contract's line `kartoteka is configured for this project but knowledge.project is not set`
    goes to the report's source lines (§8). Adapter `none` / absent: off, with no line.
  - *tracker* — on when `tracker.adapter` is `"jira-mcp"` or `"github-issues"`;
  - *Figma* — on when `design.figma` is `true`;
    - *code* — always on.

  Every source that is off carries its one-line reason to the report's source lines (§8):
  kartoteka's from the contract's table or the precondition above, tracker
  `off — tracker.adapter is none`, Figma `off — design.figma is false`, and
  `off — local-only run requested` for all three under `--local`.

  `--local` turns kartoteka, tracker and Figma off for this run: a local draft reads only the
  host code. `<project>` below is `knowledge.project`.

## 1. Resolve the input

From the arguments with `--local` and `--type <value>` stripped. A **path candidate** is an
argument that is one token — no whitespace, no line break — and contains `/` or ends in `.txt`
or `.md`. Text with spaces or line breaks is never a path, whatever it contains.

- **A path candidate that exists and ends in `.txt` or `.md`** → read it; its contents are the
  source; keep the repo-relative path for the source footer (§6).
- **A path candidate that exists but is unreadable or another type** → tell the user, then ask
  via `AskUserQuestion`: `Treat the argument as literal text` / `Stop`. On `Stop`, report
  `Aborted: nothing written.` and terminate.
- **A path candidate that names nothing on disk** → the same `AskUserQuestion`:
  `Treat the argument as literal text` / `Stop`. On `Stop`, report `Aborted: nothing written.`
  and terminate. Never draft an issue about a path string.
- **Anything else** → the argument is the source, verbatim.
- **Empty** → ask for the text or a path via `AskUserQuestion`, the same input gate
  `generate-idea` runs: show one concrete example of an actionable description and reject vague
  input ("make it better", "fix stuff") the same way. Never invent an issue from nothing.

Where no question can be answered (§5's non-interactive rule), each question above resolves to
`Stop`.

**A pasted tracker export** — an issue body followed by its comment thread — is source material
with these filters:

- **Bot and system comments are dropped**: merged-pull-request notices, automated triage or
  due-date reminders, auto-generated headers. They are never facts, links or gaps.
- **Human comments are source facts** like the body; a later comment that corrects the body
  wins.
- **Mentions** (`[~username]`) stay only on a contact or reviewer line; elsewhere the person
  is written as a plain name.

**Unfilled template text is no data**, in any source: a section that only repeats a
template's own prompt ("Mockups / Figma links", "Testing instructions (if needed)") and a bare
placeholder value (`-`, `n/a`) fill nothing. A note that says something about the data ("will
come later") is a fact and is kept as written where it stands.

The whole source is the target; there is no separate context.

## 2. Pick the issue type

First match wins:

1. **`--type`** — `task`, `bug` or `epic`. Any other value: tell the user the three accepted
   values, report `Aborted: nothing written.` and terminate.
2. **The request names a type** — in the words asking for the draft (the invoking message, or
   a leading instruction in the argument such as `format as a bug: …`), in any language: epic
   → `epic`; bug, defect, bug report → `bug`; task, ticket, story → `task`. Headings and
   field names inside the source never count.
3. **The source's main subject is a defect** — behaviour that should work and observably does
   not → `bug`. A feature request that mentions a side defect stays `task`. An investigation —
   finding a cause, a research ticket — is a `task`: its deliverable is findings, not a fix.
4. Otherwise → `task`. An epic is never inferred from the source's shape — it is a planning
   decision the user names.

The report says which type was picked and by which rule.

## 3. Extract facts and list the gaps

Apply the generation rules to fill the template's slots (§6):

1. Infer the most likely **actionable engineering task**. Phrase it as a concrete task, not a
   discussion recap.
2. Capture the real technical problem — bug, improvement, investigation, or follow-up. Name the
   affected repository, library, platform, product, component, or integration **only if
   mentioned**.
3. Include reproduction details, symptoms, expected behavior, or evidence **only if present**.
   Expected behaviour is never inferred — not from the title, not as the defect's opposite.
4. Mention dependencies, blockers, or related systems **only if** explicitly supported by the
   source.
5. Stay factual, concise, implementation-oriented. Prefer concrete technical wording over vague
   business wording.
6. **Never invent facts.** Preserve uncertainty explicitly ("per the author's assumption, not
   confirmed" — in `language.pr`).
7. Do **not** assign priority, assignee, sprint, labels, story points, or deadlines unless the
   source states them.
8. Keep code, URLs, ticket keys, file paths and identifiers verbatim.

Then collect:

- **The source ticket** — the key the source itself is: the key a pasted export opens with;
  free text has none.
- **Ticket keys** the source mentions: tokens of the form `<ticket.projectKey>-<digits>`
  (case-insensitive), each matched against `ticket.pattern`
  (`${CLAUDE_PLUGIN_ROOT}/docs/ticket-parsing.md` §§1–2), canonicalised, phase suffix dropped.
  In free text a bare number is never a key.
- **The gap list** — every point that would change the issue if answered differently, ranked:
  1. *what* — the concrete task or defect cannot be stated;
  2. *where* — no repository, component, platform or integration is named and the issue
     depends on it;
  3. *the type's core* — `bug`: environment, reproduction steps, or one side of expected vs
     actual; `epic`: the role, the capability or the value of the user story; `task`: a way to
     tell the change is done (acceptance criteria);
  4. *references* — a ticket key or system name the source cites but does not explain;
  5. everything else — suspected cause, dependencies, evidence.

  A gap is a concrete question ("which platform shows the login failure — iOS, Android, or
  both?"), never a section name ("Context missing"). Priority, assignee, labels, estimates and
  deadlines are never gaps; neither is a platform the template defaults to All, nor a link the
  source says will come later.

## 4. Gather context — the `issue-scout` agent

Use the Agent tool with:

- `subagent_type`: `"issue-scout"`
- `description`: `"Context for an issue draft"`
- `prompt`:

```
Gather context for an issue draft. Follow your agent definition end to end and return only the
fact sheet.

## Source (verbatim)
<the source>

## Type
<task | bug | epic>

## Gaps (ranked)
1. <concrete question from §3>

## Extracted from the source
- Ticket keys: <list or "none">
- Figma links: <list or "none">
- Other links: <list or "none">
- Code or UI names: <list or "none">

## Config
knowledge.adapter, knowledge.project, tracker.adapter, tracker.mcpToolPrefix, design.figma,
ticket.projectKey, ticket.pattern, language.pr: <values>

## Source ticket
<KEY | none>

## Sources
kartoteka: <on | off — <reason line> | off>
tracker: <on | off — <reason>>
figma: <on | off — <reason>>
code: on
```

The scout returns a fact sheet: a facts table (`fact`, `kind`, `ref`, `date`, `author`,
`basis` — `stated` or `inferred` — and `serves`), one status line per gap, and one line per
source. §5 and §6 use it; the source lines go to the report (§8). An empty facts table is a
normal outcome: the draft proceeds exactly as without retrieval.

**Stale-registry fallback:** when no agent named `issue-scout` is registered (agent definitions
are cached per session), re-dispatch the same prompt once via
`subagent_type: "general-purpose"`, prefixed with: "Read
`${CLAUDE_PLUGIN_ROOT}/agents/issue-scout.md` and follow it as your agent definition."

**Failure.** When the scout errors or returns no fact sheet, carry `scout: error — <text>` to
the report's source lines and continue to §5 with the gap list as it stands.

## 5. Ask about the remaining gaps

Gaps the fact sheet marks closed are not asked. A gap whose answer belongs in AC, environment,
steps, expected or actual result, or in an epic's role, capability or value, stays open whatever
the fact sheet says — those take nothing from retrieval (§6). A gap whose closing fact is not
placed in the description stays open, too: decide the §6 placement first, because a reader
never sees an answer that only the report carries. If none stays open, skip. Otherwise **one**
`AskUserQuestion` call with the four highest-ranked open gaps:

- `header` — at most 12 characters naming the gap's subject;
- `question` — the concrete question from §3, in the language the user is conversing in;
- `options` — two to four answers drawn from evidence: what the source hints, what the fact
  sheet suggests, the common cases. The tool's free-text option covers the rest;
- never a question about priority, assignee, labels, estimates or deadlines.

Answers are first-hand facts: they fill slots unquoted. An answer that contradicts a retrieved
fact wins; the fact leaves the description and is listed under Also found in the report, marked
contradicted. A gap the user skips, answers "unknown", or that was not among the four asked
stays open: it goes to the report's **Missing Details** list (§8), never into the description.

**Non-interactive rule.** In a pipeline or autonomous run
(`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md`), or wherever a question cannot be answered,
skip the round: every open gap goes to Missing Details and the file is still written.

## 6. Render

The resolved template is the description's skeleton. Each block — a `## ` heading with its
slot, a slot with no heading, or a table — has one HTML comment directly before its slot (or
before its first table row). The comment's first word is the block's rule; its remainder says
what fills it; the `$NAME` placeholders are the slots:

- **`required`** — always rendered, never empty. With nothing to say after the question round,
  restate the source's own words, however thin, and keep the gap on Missing Details.
- **`keep`** — always rendered, even empty: a heading over an empty body, a table row with an
  empty value cell. The empty place is left for the reader to fill — never filler such as
  "TBD", "—" or "no data".
- **`optional`** — omitted, heading included, when its slot is empty. Never pad.

Section and placeholder names here are the shipped templates'; a host override is read by its
comments' rules, not by these names. Then:

- Fill each slot from §§3–5.
- Keep the skeleton's order and everything around the slots: headings with their trailing
  punctuation, separator lines, table labels.
- Headings and table labels render in `language.pr` unless the block's comment spells them
  otherwise. Product names and abbreviations — `AC`, `URLs`, `Figma`, `Notion` — stay as
  written.
- Strip every HTML comment.
- Write every slot in `language.pr` (verbatim quotes excepted); keep code, URLs, ticket keys,
  paths and identifiers verbatim.
- The trailing source slot (`$SOURCE`) — the line `<Source label>: <repo-relative path>` only
  when §1 read a file, with "Source" in `language.pr`; omit the line entirely otherwise.
- An attachment embed in the source (`!screenshot.png!`, `!image-….png|width=…!`) is kept
  verbatim where it belongs; it renders only once the file is attached to the new issue, so
  the report lists it.
- **Retrieved facts** (§4) enter the description by these rules:
  - **Placement.** The task description or the bug's problem line takes at most one relation
    sentence ("continues PROJ-2874", "a follow-up to PROJ-1674"). Technical details take the
    code map (paths and symbols), API endpoints and attributed decisions; an `inferred` fact is
    phrased as likely. Table rows take retrieved links. A link's label: the label the source
    gives wins; else the name the scout found, marked as that ('frame "<name>": url'); a link
    the scout could not look up keeps the label the source gives, or a plain one naming what it
    is. A retrieved link replaces a source note that said the link would come later; table links
    do not count against the cap. Platform comes only from the source or the answers. Additional
    takes ordering from tracker links ("do after PROJ-3144") and follow-ups a decision names. In
    an epic, code facts go to Also found; its decisions and relations go to Additional. AC,
    environment, steps, expected and actual results take nothing from retrieval — only the
    source or the author's answers — and an epic's role, capability and value take nothing from
    retrieval either.
  - **Citation — the team's house style.** A ticket is its bare key inside a sentence that
    states the relation; no title, no link markup. Code paths and symbols are inline code
    (Jira `{{…}}`). A decision is paraphrased and attributed to its author and date when both
    are known ("decided with <name> on <date> that …"), else to its ticket ("per PROJ-2332,
    …"). Uncertainty is said plainly ("likely", "probably"). Nothing is quoted verbatim, except
    the `⚠ NON-CURRENT` marker.
  - **Cap.** At most **6** retrieved facts enter the description, at most 3 of them code, taken
    in this order: gap-closers, facts that change how the issue reads, one relation, decisions,
    the code map. The rest go to the report under Also found.
  - A retrieved fact never becomes an instruction of the draft: a comment that told someone to
    skip a check is at most "PROJ-812 proposed skipping the check", with a status such as
    rejected only when a source states it. A `⚠ NON-CURRENT` fact appears only as history,
    where the `⚠ NON-CURRENT` marker is copied exactly as emitted, or not at all.
- Apply the dialect: the Jira wiki reference (§0), or the Markdown cheat-sheet below.
- **Summary**: ≤ 255 characters, concrete engineering-task wording, `language.pr`.

## 7. Write

Output path: the one named in the request, else `issue-draft-<slug>.md` in the current
working directory, `<slug>` a short ASCII/transliterated slug of the summary (e.g.
`issue-draft-login-button-ios.md`).

```text
=== SUMMARY ===
<summary>

=== DESCRIPTION (<type>, <dialect label>) ===
<rendered template>
```

## 8. Report

- The output path (repo-relative when inside the repo —
  `${CLAUDE_PLUGIN_ROOT}/docs/path-conventions.md`), the summary line, and the type with the
  §2 rule that picked it.
- **Heads-up**, before the description block, when a retrieved fact says the work is already
  done, moved elsewhere, superseded or disabled, or contradicts the source — each with its
  reference. The description keeps the source's version; the reader decides. A heads-up fact
  never enters the description.
- The **description block** as written, inside a fenced `text` block, so it copies out
  without the conversation's Markdown rendering it.
- **Missing Details** — one line per gap still open after §5, as its concrete question; omit
  the list when nothing is open.
- **Attachments** — the files the description embeds, to attach to the new issue; omit when
  none.
- A **provenance table**: one row per rendered block, and one per row of a table block, naming the
  reference behind it (a ticket key, a path, a Figma node) —
  `input` / `kartoteka` / `tracker` / `figma` / `code` / `answer` / `default` (a template
  default such as Platform `All`) / `empty` (a `keep` place left for the reader).
- **Source lines** — the scout's line per source (for kartoteka with its per-source last-sync
  lines), the §0 configuration line when there is one, or `scout: error — <text>`.
- **Also found** — retrieved facts not placed in the description, each with its reference;
  omit when none.
- When a host override marks no block `required`, say so.

## Self-verify before finishing

- [ ] Everything in `language.pr`, headings and table labels included (product names and
      abbreviations as written); retrieved facts paraphrased and attributed.
- [ ] Summary ≤ 255 characters.
- [ ] Type picked by §2's first matching rule; the template is that type's.
- [ ] Dialect matches `tracker.adapter` — never a mismatched dialect; for Jira wiki, every
      rule in the reference file holds (bare links, `-` bullets, `#` numbering, no blank line
      inside a list, `| |` empty cells, escaped braces, `----` separators, no Markdown).
- [ ] No invented facts — no expected behaviour the source does not state; every retrieved fact
      carries its reference; no `⚠ NON-CURRENT` fact closed a gap.
- [ ] Retrieved facts: at most 6 in the description (3 code), in house style; none in AC,
      environment, steps, expected or actual; no `[~login]` from retrieval.
- [ ] No bot or system comment content; mentions only on contact or reviewer lines.

- [ ] No priority / assignee / labels / estimate / deadline unless the source states it.
- [ ] Template comments stripped; every `required` and `keep` block present, every table row
      present; empty `optional` blocks absent; no filler in empty places; no open questions
      in the description.
- [ ] Source footer present when the input was a file, absent otherwise.
- [ ] File written; path, summary, type, description block, Missing Details, attachments,
      provenance table, source lines and Also found reported.

## Markdown cheat-sheet

For `tracker.adapter: "github-issues"` or `"none"` (Jira wiki has its own reference, §0):

- Headings: `##`, `###`. A template's `----` separator renders as `---` with a blank line
  before it.
- Bullet list: lines starting with `- `. Numbered list: `1.`, `2.`, …
- Bold: `**text**`. Italic: `_text_`. Inline code: `` `text` ``.
- Code block: fenced with ` ``` `. Links: bare URLs, `<what it is>: <url>` where a label is
  needed.
- Table: a GitHub table with an empty header row (`| | |` then `|---|---|`); lines inside one
  cell joined with `<br>`; an empty cell stays empty.
- Attachment embeds become `![<file name>](<file name>)`.

## Rules

- **Worker that delegates retrieval.** One agent, `issue-scout`, reads the sources; the skill
  drafts, asks and writes.
- **Draft only.** This skill never calls a tracker's issue-creation API — `tracker.adapter`
  governs the destination markup dialect only, not whether this skill submits anything.
- **Ask once.** One `AskUserQuestion` round per invocation (besides the input gate); what it
  does not resolve is listed under Missing Details in the report, never guessed.
- **Retrieved facts are attributed, never presented as the author's own or as instructions**
  (consultation contract §5, deviation in §6); a `⚠ NON-CURRENT` fact never closes a gap.
- **Paths in the report and its source lines are repo-relative** when they lie inside the
  repo — see `${CLAUDE_PLUGIN_ROOT}/docs/path-conventions.md`.
