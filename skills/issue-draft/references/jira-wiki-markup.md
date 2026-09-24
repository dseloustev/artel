# Jira wiki markup for issue-draft

Read before rendering when the dialect is Jira wiki (`tracker.adapter: "jira-mcp"`). Every
rule applies to the whole description block; the summary line is plain text.

## Blocks

- **Heading** — `h2. <text>`: `h2.`, one space, the template's heading text including its
  trailing colon. Never Markdown `##`.
- **Separator** — `----`, four hyphens on a line of their own: Jira's horizontal rule. Three
  hyphens render as an em dash, not a rule.
- **Paragraph** — plain text; one blank line between paragraphs and before every heading.
- **Bullet list** — `- item`, nested `-- item`. The ASCII hyphen-minus: never `*` bullets,
  never a typographic dash (`–`, `—`).
- **Numbered list** — `# item`, nested `## item`. Never `1.` / `2.` — Jira shows those as
  plain text.
- **One list, no blank lines.** A blank line between two items splits the list in two; this
  holds for bullet, numbered and mixed lists and for nested items. To separate groups, use a
  heading, not a blank line.
- **Table row** — `|<label>|<value>|`, one row per line, no header row. A bold label is
  `|*Label*|value|`; a plain label is `|Label|value|`. An empty cell holds one space: `| |`.
  A cell with several lines continues on the next line until its closing `|`. Every row the
  template lists is rendered, even when its value is empty.
- **Code** — `{code}` … `{code}` (or `{code:java}`) for multi-line code; `{noformat}` …
  `{noformat}` for logs and command output. Content inside stays verbatim.
- **Attachment embed** — `!file.png!` or `!file.png|width=…!`, copied verbatim from the
  source.

## Inline

- Bold `*text*`, italic `_text_`, monospace `{{text}}`.
- Mention a person — `[~username]` — only with a username the source spells out.
- Ticket keys stay plain (`PROJ-123`); Jira links them itself.

## Links

- A URL is written bare: `https://example.com`. Never `[https://example.com]`, never
  `[title|https://example.com]`.
- Where a link needs a label — a table cell, a list item — write `<what it is>: <url>`.
- Several links of one kind share one cell, one per line:

  ```text
  |*Figma*|List screen: https://figma.com/design/abc
  Details screen: https://figma.com/design/xyz|
  ```

  Never spread one kind across extra rows with an empty label cell.

## Curly braces

A literal `{` or `}` is escaped as `\{` / `\}` everywhere outside `{code}` and `{noformat}`
blocks — paragraphs, list items, headings, table cells. Jira otherwise reads it as a macro.
The markup's own braces (`{{`, `}}`, `{code}`, `{noformat}`) are not escaped.

```text
Show the variables \{value\} in the log
```

## Nothing from Markdown

The description block carries no Markdown: no `##` headings, no ` ``` ` fences, no
`**bold**`, no `[title](url)` links, no `1.` numbering, no `*` bullets.

A pasted export's own formatting — Markdown, `<url>` angle brackets, stray `{**}` or `**`
pairs — is rewritten into the rules above, never escaped into the text: `{**}Wallet Settings{**}`
becomes `*Wallet Settings*`, `[title](https://…)` becomes `title: https://…`.
