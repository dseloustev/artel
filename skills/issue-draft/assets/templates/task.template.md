<!-- issue-draft task template. The template is the description's skeleton. Each block's
     rule is the HTML comment directly before its slot (or its first table row): the first
     word is required, keep or optional, the rest says what fills it. Comments never appear
     in the output. Headings and table labels render in language.pr, markup in the
     tracker.adapter dialect. -->

## Task description:
<!-- required. The concrete engineering task and the problem it solves, phrased as work to
     do, not as a recap. Name repository, component, platform or integration only when the
     source does. -->
$DESCRIPTION

## Technical details:
<!-- keep. Implementation approach, affected components, constraints, dependencies — only
     what the source, the retrieved facts or the author's answers state. Retrieved facts in the
     team's citation style (the skill's §6). A suspected cause is marked unconfirmed. -->
$TECHNICAL

<!-- keep, table. All four rows, always, in this order; labels bold. A row with no data keeps
     an empty value cell. Platform: the platforms the source names, else All (verbatim).
     Link rows: every link the source gives, one per line inside the cell as
     "<what it is>: <url>". Each link lands in exactly one row: Figma links in Figma, Notion
     links in Notion, every other link in URLs. -->
| **Platform** | $PLATFORM |
| **URLs** | $URLS |
| **Figma** | $FIGMA |
| **Notion** | $NOTION |

## AC:
<!-- keep. Bullets: acceptance criteria — what to verify, which functionality is affected.
     Only criteria the source or the author's answers state. -->
$ACCEPTANCE

## Additional:
<!-- keep. Bullets: testing instructions the AC do not cover; who to ask, who reviews, a
     deadline — each only when the source states it; other notes from the source. -->
$ADDITIONAL

<!-- optional. "Source: <repo-relative path>" only when the input was a file. -->
$SOURCE
