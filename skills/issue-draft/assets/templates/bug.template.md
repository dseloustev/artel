<!-- issue-draft bug template. The template is the description's skeleton. Each block's
     rule is the HTML comment directly before its slot: the first word is required, keep or
     optional, the rest says what fills it. Comments never appear in the output. Headings
     render in language.pr, markup in the tracker.adapter dialect. -->

<!-- required, no heading. One or two sentences stating the defect: what is broken and
     where. Plain text above the separator, never a heading. -->
$PROBLEM
----

## Environment:
<!-- keep. Device / OS / app build, exactly as the source states them. -->
$ENVIRONMENT

## Steps to reproduce:
<!-- keep. Numbered list; only steps the source states; a sub-step one level deeper. -->
$STEPS

## Expected result:
<!-- keep. What should happen, only as the source states it — never inferred from the title
     or written as the defect's opposite. Left empty otherwise. -->
$EXPECTED

## Actual result:
<!-- keep. What happens instead. Error text and log lines verbatim; attachment embeds
     (screenshots) kept as the source has them. -->
$ACTUAL

## Technical details:
<!-- optional. Only when the source, the retrieved facts or the author's answers give
     technical content: suspected cause (always marked unconfirmed), logs, affected code, links
     as "<what it is>: <url>", one per line. Retrieved facts in the team's citation style (the
     skill's §6). -->
$TECHNICAL

<!-- optional. "Source: <repo-relative path>" only when the input was a file. -->
$SOURCE
