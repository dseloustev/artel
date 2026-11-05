---
name: artel-bratishka-status
description: "Use when the person asks what the Bratishka duty queue looks like — the open bot pull requests, their review and QA state, the live testing PR, or what the bot is working on. Read-only; never merges, closes or comments."
argument-hint: "[--offline] [--json]"
---

# Bratishka duty board

Run:

```bash
python3 ${ARTEL_ROOT}/scripts/bratishka.py status --json [--offline]
```

The engine reads `.artel/config.json` (`bratishka`) and the repo, then prints
`{ok, verb, elapsed_ms, data}`. Exit `2` means a configuration/environment error: print the
`error.message` and stop. Exit `0` means `data` has `testing_prs`, `queue`, `live_tasks` and
`next_actions`.

Render, in this order, from `data`:

1. **Testing PR** — each open testing PR, its date, age in days, QA-approval count
   (`qa_approvals`) and how many source PRs are already merged or closed (`source_merged`).
2. **Queue** — the five buckets (`qa-approved`, `needs-qa`, `needs-review`, `bot-working`,
   `blocked`) as short tables: number, ticket, title, review state, QA state, CI/merge state
   (`mergeable`, `merge_state`) and flags. An empty bucket is one line "none", not a table.
3. **Live bot tasks** — initiator and a one-line description; say "dashboard offline" when
   `--offline` or the engine reported no dashboard.
4. **Next actions** — the engine's `next_actions`, each naming a skill.

This skill **writes nothing** and is safe at any time. It never posts, merges or closes.
The approval rules, buckets and flags are in `${ARTEL_ROOT}/docs/bratishka-duty.md`.
