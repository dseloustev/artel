---
name: artel-skill-author
description: "Use when a skill is to be added to, changed in or removed from this plugin — walks the contract (frontmatter legality, orchestrator or worker, the three-section convention, references and assets, what the description must say) and finishes the integration a new skill silently needs: skills-reference entry, router row, changelog, doc-contract test. Never ships a skill that is not routed and referenced."
argument-hint: "<skill-name | what the skill should do> [--new] [--remove]"
---

# Author a skill

A skill is cheap to write and expensive to finish: the frontmatter has to stay inside what the
build and the host accept, the body has to obey this plugin's conventions, and a new folder is
**not discoverable** until it is routed, referenced and tested. This skill carries all of it so the
next author does not have to rediscover it.

It edits this plugin's `skills/`, `docs/skills-reference.md`, `skills/artel-using-artel/SKILL.md`
and `CHANGELOG.md` — nothing else, and never a host project.

## Prerequisites

1. **This is the plugin checkout** — `skills/`, `docs/skills-reference.md` and `AGENTS.md` are at
   the root. Anywhere else, this skill has nothing to author.
2. **The name is legal** — `^[a-z0-9]+(-[a-z0-9]+)*$`, with the `artel-` prefix
   (`tests/test_build_opencode.py`, `VALID_NAME`). A family name follows its family:
   `artel-bratishka-<action>` for the duty, `<ticket-family>-<action>` otherwise.
3. **A clean tree** — `git status --porcelain` is empty, so the change is reviewable as one diff.

## 1. Orchestrator or worker?

Decide before writing a line, because it shapes the whole body
(`skills/README.md`):

- **Orchestrator** — it resolves context, dispatches an agent through `subagent`, and reports. The
  default, and the rule for anything a stage of the pipeline does. It never inlines the agent's
  work, and its body cites the agent contract instead of restating it.
- **Worker** — no agent matches the job, so it runs its documented procedure inline. Say so in the
  body's opening paragraph, the way `sync-phases`, `generate-idea` and `artel-acceptance` do.

## 2. The frontmatter contract

Everything here is asserted by `tests/test_build_opencode.py`; a violation fails the build.

| Field | Rule |
| --- | --- |
| `name` | Equals the folder name, exactly |
| `description` | One line, ≤ 1024 characters. It is the whole trigger surface |
| `argument-hint` | Present when the skill takes arguments; copied verbatim into the reference's `Invocation` field |
| `metadata` → `opencode/autoinvoke` = `false` | **Only** for a manual-only skill — one that must never be picked from natural language. Registered and loadable by id, absent from the model's list |
| `model:` | Never. A skill declares no model; dispatches resolve theirs at call time |
| the Claude-only auto-invocation switch | Never. The build drops it and a test forbids it |

Do not invent a key to look like enforcement: an unknown frontmatter key is stored inert, so it
documents a rule that nothing applies.

## 3. What the description has to say

Three things, in this order, and no fourth:

1. **When** — the situation, in the words a person would actually use.
2. **What it produces** — the artifact or verdict, not the procedure.
3. **What it never does** — a negative clause (`Never posts on a merged PR`, `Never reviews or merges
   a pull request`). This is the cheapest guard in the plugin: it stops the skill being picked for
   the neighbouring job.

English, project-agnostic, no ticket keys or host paths. A manual-only skill opens with
`MANUAL ONLY — use only when the user explicitly asks for it`, because the description survives
into the place a human reads.

## 4. The body

- **Project-agnostic.** No ticket keys, host paths, MCP tool names or language-specific commands —
  they come from `.artel/config.json` (`${ARTEL_ROOT}/docs/design.md`, genericization strategy).
- **Cite, never restate.** A contract belongs to `docs/`, the skill names the heading
  (`## Testing PR`) or the `§N`. Two copies of a rule drift, and the doc-contract tests only guard
  the one in `docs/`.
- **Operational skills carry three sections** (`skills/README.md`):
  `## Prerequisites` (one line per dependency, proven by a single call, remedy not fallback),
  `## Hard rules` (prohibitions only, one `NEVER` per line), `## Common mistakes` (a two-column
  mistake → fix table). Any skill that writes, merges, closes or spawns has them;
  `tests/test_bratishka_docs.py` shows the shape.
- **The `${ARTEL_ROOT}` token**, never an absolute path, for anything the skill points at.

## 5. references/ and assets/

Add them only for the reason they exist:

- **`references/`** — detail a *conditional* path needs: a tool contract, a grammar, a
  troubleshooting tree. Never move mandatory procedure there: a run that needs it would have to
  guess whether to read it (`${ARTEL_ROOT}/docs/design.md`, the 2026-11-16 entry).
- **`assets/`** — verbatim material the skill hands over or fills: a prompt, a template, an HTML
  report. The body names the file and never inlines its content.

Every `.md` beside a `SKILL.md` is baked into the installed copy, and every `${ARTEL_ROOT}`
pointer into a skill's directory must resolve there (`tests/test_build_opencode.py`). A
non-Markdown asset is copied as-is.

## 6. The integration — the part that is always forgotten

A skill works locally without any of this and is still invisible in the plugin. Do all of it:

1. **`docs/skills-reference.md`** — one `### <route-name>` entry with its six fixed fields
   (`Purpose`, `Invocation`, `Reads`, `Writes`, `Pauses`, `Notes`), placed in workflow order, not
   alphabetically. `Invocation` is the frontmatter hint verbatim:
   `` `- **Invocation:** `/artel-<route> <hint>`` ``. Under `## Bratishka duty` the heading drops
   the family prefix (`### status` for `artel-bratishka-status`).
2. **The router** — `skills/artel-using-artel/SKILL.md` names the new route, and names nothing that
   does not exist (`tests/test_using_artel_docs.py`, both ways). **The router is capped at 10 KiB
   because every session pays for it**: a new route usually means shortening a neighbouring
   sentence, not appending.
3. **`CHANGELOG.md`** — one bullet under `## [Unreleased]`, opening with a bold sentence that says
   what changed and continuing with why it matters (or why the old behaviour was wrong).
4. **A test** — pin the one thing a later edit could silently break: a heading, an invocation line,
   a config key the skill reads, the section it must carry. A new `tests/test_<name>_docs.py`, or a
   class in an existing contract test.
5. **The suite** — `python3 -m unittest discover -s tests -p 'test_*.py'`. It takes minutes; run it
   rather than assuming.

## 7. Removing a skill

The reverse of step 6, in order: the folder, its `skills-reference` entry, its router row, any test
that names it, and a `### Removed` changelog entry. Then the suite — the router test and the
reference test both fail if one step is missed, which is the point of having them.

## 8. Report

State what was created or changed, the sections the body carries, the four integration points and
what each one now says, and the suite's result. Never report a skill as shipped while any of the
four is missing.

## Hard rules

- NEVER exceed the router's 10 KiB cap: shorten, do not append.
- NEVER leave a skill unreferenced, unrouted or untested — those are the three silent failures.
- NEVER add `model:` or the Claude-only auto-invocation switch, and never invent a frontmatter key.
- NEVER write a description without a negative clause or longer than 1024 characters.
- NEVER put a project literal in a body, an asset or a description.
- NEVER move mandatory procedure into `references/`.
- NEVER restate a contract that `docs/` owns; cite its heading.
- NEVER delete a skill without removing its entry, its route and its tests in the same change.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Adding the folder and stopping there | The four integration points of step 6 |
| Appending a router row and pushing past 10 KiB | Shorten a neighbouring sentence first |
| Copying a rule out of `docs/` into the body | Cite the heading; two copies drift |
| A description that only says what it does | Add when to use it and what it never does |
| The Claude-only auto-invocation switch for a manual-only skill | The `metadata` → `opencode/autoinvoke` flag |
| Splitting a long skill because it is long | Move conditional detail only; procedure stays |
| An `### ` heading that repeats the `artel-` prefix | The reference uses the route name, not the folder name |
| Skipping the suite because it is slow | It is the only check that the bake, the router and the reference agree |
