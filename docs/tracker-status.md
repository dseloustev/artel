# Tracker status intents

How a status change is resolved on any tracker, for the workflows that move a ticket as they work
it: `artel-bratishka-delegate` moves it twice per issue, and every move is resolved at run time
from the transitions the tracker offers from the issue's current status.

Every project defines its own workflow: names, locales and available paths differ, and the same
name can mean different steps in two projects. Treat the names below as **aliases and examples**,
never as constants.

## The two intents

Think in intents, not names.

| Intent | Moment | Config key |
| --- | --- | --- |
| `start` | work has begun, before anything is spawned | `bratishka.delegate.startStatus` |
| `handoff` | the work finished and its pull request is open | `bratishka.delegate.handoffStatus` |

Both keys are empty by default, which means "resolve from the aliases". A value in the key is an
**override** and is matched exactly.

## Aliases

Match case-insensitively after trimming and collapsing whitespace, against the transition's
`to.name` first, then its `name`.

**`start`** — `in progress`, `in development`, `in work`, `doing`, `start progress`, `start work`;
`в работе`, `в процессе`, `в разработке`, `начато`, `выполняется`.

**`handoff`** — `code review`, `in review`, `ready for review`, `ready for code review`, `review`,
`pr review`; `на ревью`, `на код-ревью`, `код-ревью`, `ревью`, `на проверке`, `на рассмотрении`.

Precedence, highest first: exact equality with `to.name`; exact equality with the transition
`name`; a whole-word occurrence inside `to.name`, then inside `name`. Prefer a forward move over a
rollback when two candidates remain.

## Resolution

```text
resolve(intent, transitions, override):
    if override is set:
        candidates = transitions whose to.name or name equals it, case-insensitively
        if candidates is empty        -> STOP: show every available transition and ask
        if candidates target >1 status -> STOP: show them and ask
        return the candidate
    candidates = transitions whose to.name or name matches the alias list
    if exactly one distinct target status -> return the candidate
    if more than one distinct target      -> STOP: show them and ask
    STOP: show every available transition and ask which status to use
```

Rules:

- **Resolve on every run.** The available transitions depend on the current status, and the same
  project can offer different paths from different statuses.
- **Never use a transition identifier.** Identifiers are per-instance and per-status; the name is
  resolved from the transitions the tracker just returned.
- **An override never falls back to the aliases.** A typo must surface as a stop, not as a silent
  move to another status.
- **Never pick a candidate silently** when two statuses match. Ask.

## Outcomes

| Situation | Action |
| --- | --- |
| Already in the resolved status | Skip the move and continue |
| In the `handoff` status while resolving `start` | Stop and ask: moving backwards must be deliberate |
| The chosen transition reads like a rollback (`back …`, `reopen`, `return …`, `вернуть …`, `назад`) | Confirm before applying it |
| No review-like status exists for `handoff` | Stop, show the transitions, ask whether to skip the move or which status to use |
| The status category is `done` | Stop and ask whether to reopen; never reopen on your own |
| The move succeeded but the status did not change | Report the mismatch and stop for that issue |

An issue that stops here is `needs-decision` and does not stall the rest of the batch.

## Done categories

Prefer `fields.status.statusCategory.key` — `new`, `indeterminate` or `done` — which is
locale-independent. Where it is missing, read the status name: `done`, `closed`, `resolved`,
`cancelled`, and their translated forms.

To reopen on request, resolve a transition whose target category is not `done` and whose name
reads like a reopen (`reopen`, `back …`, `to do`, `открыть`, `вернуть …`); ask when more than one
candidate matches.

## Queries

A query language that filters by status (JQL, for one) accepts the project's **canonical** status
names, which are not always the names an issue response returns. Take the canonical name from the
same place the transitions came from, and scope every query to the issue's project key. Status
names are per-project, so never reuse a name across two projects.

## Boundaries

- This file fixes the **resolution** only. Which statuses mean start and handoff for a given
  project is `.artel/config.json`'s business, and what a workflow does with the move is the
  calling skill's.
- A move is a write to the tracker: it is reported with the transition's from and to names, and it
  is skipped — never guessed — when the project does not offer it.
