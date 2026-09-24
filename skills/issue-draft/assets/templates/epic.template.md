<!-- issue-draft epic template. The template is the description's skeleton. Each block's
     rule is the HTML comment directly before its slot (or its first table row): the first
     word is required, keep or optional, the rest says what fills it. Comments never appear
     in the output. Headings and table labels render in language.pr, markup in the
     tracker.adapter dialect. -->

<!-- optional, no heading. The epic's goal and scope in a short paragraph, as the source
     states them. Plain text above the separator. -->
$INTRO
----
<!-- keep, table. All six rows, always, in this order; labels NOT bold, each label keeps
     its bracketed hint. The first three rows are a user story; each value is a first-person
     phrase that opens with its row's lead-in: "I as <role>", "Want <capability>",
     "So that <value>". The role keeps the source's own word for it. Never invent the role,
     the capability or the value: an unstated one leaves its cell empty. When the source
     means end users without naming a role, the role is User. Platform: the platforms the
     source names, else All (verbatim). Notion and Figma: every such link, one per line
     inside the cell as "<what it is>: <url>"; a note the source gives instead ("will come
     later") is kept as written. -->
| As [user role] | $ROLE |
| I want [what they want to do] | $CAPABILITY |
| So that [what value it brings] | $VALUE |
| Platform | $PLATFORM |
| Notion | $NOTION |
| Figma | $FIGMA |

## Acceptance Criteria:
<!-- keep. Heading: "Acceptance Criteria" stays in English; when language.pr is not English,
     its translation follows in parentheses before the colon. Bullets: what must be delivered
     for the epic to count as done — edge cases, UX, errors, behaviour across scenarios — only
     as the source or the author's answers state. -->
$ACCEPTANCE

## Additional:
<!-- keep. Bullets: API endpoints and other links as "<what it is>: <url>", testing
     instructions, scope examples, definition of done, other notes from the source. -->
$ADDITIONAL

## Related:
<!-- optional. One bullet per kartoteka hit that bears on the epic: identifier, title,
     source, date, status. Copy the ⚠ NON-CURRENT marker exactly. Never a bullet without an
     identifier. -->
$RELATED

<!-- optional. "Source: <repo-relative path>" only when the input was a file. -->
$SOURCE
