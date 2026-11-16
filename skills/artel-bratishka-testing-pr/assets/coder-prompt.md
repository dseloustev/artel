# Coder-prompt override

Point `bratishka.spawn.promptFile` at a copy of this file to replace the coder prompt the testing
PR is built with. The engine renders the text and sends it **verbatim** as the spawned task's
prompt, so the task sees only what is here.

The placeholders below are the entire vocabulary. Any other braced token is refused as
`invalid_config` before anything is spawned — a typo never reaches the bot as literal text. Write
`{{` and `}}` for literal braces.

| Placeholder | Filled with |
| --- | --- |
| `{repo}` | the repository the plan resolved, `owner/name` |
| `{repo_line}` | the ready-made `Repository: …` line, or the checkout's origin |
| `{base_ref}` | the plan's base branch |
| `{date}` | today, `YYYY-MM-DD` |
| `{title}` | the testing PR's title, from `testingPr.titleTemplate` |
| `{branch}` | the testing branch, from `testingPr.branchTemplate` |
| `{tasks}` | the source-PR blocks: ticket, number, head branch, QA notes, platforms |
| `{run_id}` | the run marker |

`Run id: {run_id}` must stay in the text. `testing-pr-verify` finds the spawned task by that
marker alone, so a prompt without it cannot be correlated with its pull request.

## Template

```markdown
# Daily testing PR — {date}

{repo_line}
Base: `origin/{base_ref}`
Date: {date}

Create the daily testing PR that bundles the source PRs below.

{tasks}

Steps:

1. Create `{branch}` from `origin/{base_ref}`. Never push to the base branch.
2. Merge only the listed head branches into it, in the order shown. A conflict stops the task;
   never force a resolution, never rebase, never edit a source branch.
3. Open one pull request against `{base_ref}` with the exact title `{title}` and head `{branch}`.
4. Do not merge the testing PR and do not touch the source PRs.
5. Return the pull request URL.

Run id: {run_id}
```
