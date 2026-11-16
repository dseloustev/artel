# Testing-PR body override

Point `bratishka.testingPr.bodyFile` at a copy of this file to replace the body the testing PR is
opened with. The engine renders the text and the coder uses it **verbatim**, so every part the
duty relies on has to be in the file.

The placeholders below are the entire vocabulary. Any other braced token is refused as
`invalid_config` before the branch is built. Write `{{` and `}}` for literal braces.

| Placeholder | Filled with |
| --- | --- |
| `{date}` | today, `YYYY-MM-DD` |
| `{base_ref}` | the plan's base branch |
| `{base_sha}` | the base commit the branch was cut from |
| `{short_sha}` | the same commit, abbreviated |
| `{count}` | how many source PRs are bundled |
| `{tasks}` | one line per source PR: ticket, number, title, link |
| `{notes}` | per source PR: its QA notes verbatim, then its platforms |
| `{merge_notes}` | the clean merges, re-introduced files, and anything an owner should know |
| `{verification}` | the verification commands and their results |
| `{one_day}` | the sentence saying the PR lives one day |

Two lines are load-bearing and are worth keeping even in a host shape:
`{one_day}` (the next duty reviewer reads it before closing) and the blockquote telling QA to
leave approvals in the source PRs.

## Template

```markdown
## Summary

{date} — `origin/{base_ref}` at `{short_sha}`; {count} source PRs; the branches were merged
without conflicts.

> **QA: leave feedback and approvals in the individual source PRs, not in this testing PR** —
> it only bundles the changes.

## Tasks

{tasks}

## Notes for QA

{notes}

## Merge notes

{merge_notes}

## Local verification

{verification}

---

{one_day}
```
