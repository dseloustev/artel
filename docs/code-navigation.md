# Code navigation

How artel agents and skills find code: which tool answers a question about the
host codebase, and what to do when the fast one is not there.

Referenced by `agents/analyst.md`, `agents/researcher.md`, `agents/planner.md`,
`agents/implementer.md`, `agents/reviewer.md`, `agents/tech-writer.md`,
`agents/vision-writer.md`, `agents/figma-analyst.md`,
`skills/agents-md-generator/SKILL.md` and `skills/merge-conflicts/SKILL.md`.
Spelled here once because all ten need the identical rules, and §3 and §5 are
ones where two copies drifting apart is wrong rather than untidy.

**This is the query side.** Refreshing an index after implementation is a
separate, optional host hook — `docs/orchestrator-common.md` §1 — with no
mechanics defined by the plugin. This file is about reading one that exists.

**This file names one tool.** Everywhere else in the plugin a code-symbol index
is "the host's optional code-symbol index", because artel must run against a
host it knows nothing about (`CLAUDE.md`, "genericize"). An index-first rule is
worthless without the commands that make it actionable, though, so the concrete
names live here — and only here, in the two sections that are about mechanics:
§1's probe and §2's command table. **The rules — §3, §4, §5 — name no tool**,
which is the line that keeps this portable: a host with a different index
rewrites the first two sections and keeps the last three verbatim.

## 1. Whether an index is available

One input, probed once per run:

| `command -v ast-index` | Behavior |
|---|---|
| found | **Use the index** — §2 for the commands, §3 for the rule |
| not found | Glob/Grep/Read are the whole toolkit. Record nothing |

The probe is presence only. Do not run a search to find out whether search
works, and do not build an index that is not there — creating one is the user's
call (`/ast-index:initialize`, or the host's own step), never an agent's.

**Absence is silent.** A host that has not wired up an index is not
misconfigured, and an agent must not report the index as missing, ask for it, or
degrade its own output because of it. Glob and Grep answer every question below,
more slowly. This is the same shape as the knowledge gate
(`docs/knowledge-consultation.md` §1) with one difference: there is no config
key. An index is project-agnostic — it reads whatever checkout it is pointed
at — so its presence on `PATH` is the whole gate, and an undeclared one is safe
to use.

When the `ast-index` plugin's skill is loaded in the session, route through
`/ast-index:ast-index` and let it pick the command. When it is not, run the CLI
directly with Bash. The probe above answers both cases: the CLI is what the
skill drives.

## 2. Reference implementation

`ast-index` — a local index over the checkout, ~1-10ms per query against
grep's 200ms-3s.

| The question | The command |
|---|---|
| Where is this class / symbol / file? | `search <name>` · `class <name>` · `symbol <name>` · `file <name>` |
| Everything about this area, in one call | `explore <terms>` (add `--rwr` for callers/subclasses) |
| Who uses this? What breaks if I change it? | `usages <name>` · `refs <name>` |
| Who calls this function? | `callers <name>` · `call-tree <name> --depth N` |
| What implements this interface? What extends this class? | `implementations <name>` · `hierarchy <name>` |
| What is in this file? | `outline <path>` · `imports <path>` |
| What is this project's shape? | `map` · `map --module <path>` |
| What architecture, frameworks and naming does it use? | `conventions` |
| What do the modules depend on? | `deps` · `dependents` · `unused-deps` · `api <module>` |
| Which symbols does this branch touch? | `changed --base <default-branch>` |
| The index looks stale (§4) | `update` — incremental, changed files only |

Add `--fuzzy` when the exact name is unknown, `--module <path>` / `--in-file
<path>` to scope, `--limit N` to cap, `--format json` only when parsing the
output programmatically — the plain text is for reading.

`ast-index help <command>` is authoritative; this table is the subset artel's
stages actually need. `update` is the only index-*management* command on it: rebuilding,
clearing and scheduled refreshes are the host's, not an agent's
(`docs/orchestrator-common.md` §1).

## 3. The index-first rule

**With an index available, it is the first tool for any code search** — before
Grep, Glob or the Search tool.

**Its result is the complete answer.** Do not re-run the same question through
grep "for completeness". A second pass that finds nothing new cost a minute and
proved nothing; a second pass that finds something means the index is stale, and
§4 is how that is handled.

Reach for Grep when the question is not about symbols:

- **regex** — the index matches literally
- **string literals inside code** — `"could not connect"`
- **comment text**
- after the index returns **empty**

`skills/merge-conflicts/SKILL.md` Phase 5 is the worked example: scanning the
tree for `<<<<<<<` is a literal scan, so it is Grep's job and stays Grep's job,
in the same skill whose Phase 3 resolves moved symbols through the index.

## 4. Staleness

The index reflects the last time it was built or updated. A checkout that has
moved since — a pull, a merge, a branch switch, another agent's commits — can
answer from a stale picture.

**On an unexpected miss, update once and retry before believing it.** A symbol
the plan or the diff says exists, that the index does not return, is a stale
index at least as often as a wrong reference: run the incremental update (§2)
and ask again. Only a miss that survives the retry is evidence the symbol is
not there.

That is one retry, not a loop, and it is the same rule
`scripts/plan_check.py` already implements for the `PLAN_GROUNDED` gate.

Refreshing after your own implementation work is a different thing and not your
call — `docs/orchestrator-common.md` §1.

## 5. Grounding

**Never cite a symbol or path you have not resolved.** Not from memory, not from
a plausible naming convention, not because a sibling file has one. Resolve it —
§2 — or do not write it.

This is the rule the pipeline's anti-hallucination gates rest on, and every
stage states its own consequence for breaking it:

- `planner` — a `ref:`/backticked-path claim that does not resolve fails
  **PLAN_GROUNDED** (`scripts/plan_check.py`). Anything the plan will create is
  declared `new:` or `(new file)`; an undeclared new path reads as a
  hallucinated reference.
- `implementer` — a plan anchor that does not resolve is a **Major deviation**:
  halt and report per `docs/deviation-protocol.md`. It is never something to
  invent a target for.
- `vision-writer`, `tasklist-writer` — every cited path is confirmed before it
  is written; the KISS "reuse, then cite" rule is a grounding rule.
- `reviewer` — a finding that names a symbol names one that exists. A layer
  violation asserted against a class nobody can find is noise in a gate a human
  trusts.

The index makes this cheap enough that there is no excuse for the shortcut.
Without one it is still the rule — Grep is slower, not optional.
