# The deep-review contract

*Status: draft v0.1 · 2026-10-13*

`deep-review` reviews a branch once and offers its fixes. One `reviewer` dispatch writes two
artifacts — the ticket's `deep-review.md` and a machine report — and `scripts/deep_review.py`
pins the target they are judged against and validates them before any fix is offered. This
document is the single home of that contract: the pinned manifest, the machine report's schema,
the report's exact metadata, finding IDs and task format, and the verification and completeness
rules. The steps live in [skills/artel-deep-review/SKILL.md](../skills/artel-deep-review/SKILL.md); the
writer's rules in [agents/artel-reviewer.md](../agents/artel-reviewer.md); the mechanical half in
`scripts/deep_review.py`.

`<specs.dir>` is the host's configured `specs.dir` ([config.md](config.md)); the target manifest
and the machine report are local run evidence under `.artel/run/<TICKET_ID>/deep-review/`, files
on both spec-store paths ([spec-storage.md](spec-storage.md), ticket-parsing.md §3). The report
itself is the spec document `<specs.dir>/<TICKET_ID>/deep-review.md` on the files path and the
`deep-review` document in kartoteka on the kartoteka path.

## 1. The pinned target

```bash
python3 ${ARTEL_ROOT}/scripts/deep_review.py prepare \
  --ticket <TICKET_ID> --specs-dir <specs.dir> [--branch <BRANCH_NAME>] [--base <BASE>] \
  --output .artel/run/<TICKET_ID>/deep-review/target.json
```

`prepare` is read-only apart from the manifest. It resolves the checkout root, refuses a
requested branch that is not the checked-out branch (with a worktree instruction — it never
silently reviews HEAD), refuses pending source changes outside `.artel/` and the specs dir, and
pins four SHAs plus a fresh run id:

```json
{
  "schema": 1,
  "ticket": "PROJ-12",
  "run_id": "6f9c2d1e-4b3a-4c8d-9e0f-1a2b3c4d5e6f",
  "base": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
  "head": "0123456789abcdef0123456789abcdef01234567",
  "merge_base": "1234567890abcdef1234567890abcdef12345678",
  "branch": "feat/timeout",
  "specs_dir": "specs/.current"
}
```

`base` is the base tip (`--base`, else `origin/HEAD`, else the single agreeing candidate of
`origin/main`, `origin/master`, `main`, `master`, `trunk`); `merge_base` is
`merge-base(head, base)`. The review range is exactly `git diff <merge_base> <head>` — the
three-dot equivalent; the regression guard compares it with `git diff <base> <head>`; every
anchor is checked against the same `merge_base..head` diff.

The specs dir is a dedicated artifact root the host declares in `.artel/config.json` — not a
product directory and not an exemption for source changes. A value that names product code is a
configuration error to report; `prepare` already refuses `.`, absolute paths, paths outside the
checkout, `.git` and the checkout root.

## 2. The machine report (schema 1)

The reviewer writes it to `.artel/run/<TICKET_ID>/deep-review/machine.json` (a local file on
both spec-store paths). `ticket`, `run_id`, `base` and `head` are copied from the manifest:

```json
{
  "schema": 1,
  "ticket": "PROJ-12",
  "run_id": "6f9c2d1e-4b3a-4c8d-9e0f-1a2b3c4d5e6f",
  "base": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
  "head": "0123456789abcdef0123456789abcdef01234567",
  "completeness": "complete",
  "gaps": [],
  "verification": [
    {"kind": "command", "command": "python3 -m unittest tests.test_config",
     "result": "1 passed"},
    {"kind": "evidence",
     "evidence": "verify.commands is not configured — the quality gate is skipped, not green"}
  ],
  "findings": [
    {"id": "DR-001", "kind": "defect", "scope": "diff", "priority": "Warning",
     "file": "config.py", "line": 3,
     "evidence": "config.py:3 now defaults to 60 seconds; README.md:3 still documents 30.",
     "recommendation": "Update README.md:3 to 60 seconds."},
    {"id": "DR-002", "kind": "audit", "scope": "diff", "priority": "Suggestion",
     "file": "config.py", "line": 3,
     "evidence": "The changed default is the only sensitive surface touched; the change is acceptable.",
     "recommendation": "none — audit only."}
  ]
}
```

| Field | Rule |
|---|---|
| `schema` | exactly `1` |
| `ticket`, `run_id`, `base`, `head` | non-empty strings, equal to the manifest's |
| `completeness` | `complete` or `partial` |
| `gaps` | list of non-empty strings; `partial` requires at least one, `complete` requires none |
| `verification` | at least one record (§4) |
| `findings` | list of finding objects, each validated below |

A finding:

| Key | Rule |
|---|---|
| `id` | unique `DR-NNN` (exactly three digits) |
| `kind` | `defect` or `audit` |
| `scope` | `diff` or `repository` |
| `priority` | `Critical`, `Warning` or `Suggestion` |
| `file` | safe repo-relative path |
| `line` | optional positive integer or `null` (never a bool) |
| `evidence`, `recommendation` | non-empty strings |
| `convention_ref` | optional safe repo-relative path: the file that states the host rule or the canonical example the finding cites, and it must exist at the reviewed head |

### Anchors

| Finding | Anchor rule (against `merge_base..head`) |
|---|---|
| `diff` with `line` | the line falls in a new-side hunk |
| `diff` without `line` | the file is touched on either side — added, modified, deleted, or either side of a rename |
| `repository` with `line` | the line is inside the file at head and **not** inside a changed hunk |
| `repository` without `line` | the file is a blob at head |

A guessed line is worse than none: the checker fails it and names the file's real hunk ranges.
The pinned range is the only truth — never the working tree, never a moved HEAD.

### Counts and tasks

- **Counted findings** are `kind: defect` with `scope: diff`, at every priority. Audits and
  repository-scoped defects are never counted.
- **Actionable findings** are the counted `Critical` and `Warning` ones. Each gets exactly one
  task in the report's `## Proposed fixes`; `Suggestion`s, audits and repository findings never
  become tasks.

## 3. The report (`deep-review.md`)

### Header and metadata

The report opens with the document header ([spec-storage.md](spec-storage.md) §3.2), then four
body metadata lines, each exactly once and matching the manifest:

```markdown
---
type: deep-review
ticket: PROJ-12
produced_by: artel:reviewer
---

**Review run:** 6f9c2d1e-4b3a-4c8d-9e0f-1a2b3c4d5e6f
**Reviewed base:** 9f8e7d6c5b4a39281706f5e4d3c2b1a098765432
**Reviewed head:** 0123456789abcdef0123456789abcdef01234567
**Review completeness:** complete
```

### Sections

`## Proposed fixes` is the report's last `##` section. The finding sections are matched by their
exact titles — the canonical form or its one allowed parenthetical:

| Section | Holds |
|---|---|
| `## Critical Issues` / `## Critical Issues (must fix)` | counted Critical diff defects |
| `## Warnings` / `## Warnings (should fix)` | counted Warning diff defects |
| `## Suggestions` / `## Suggestions (consider improving)` | counted Suggestion diff defects |
| `## PR Compliance` | optional, when the dispatch carried a PR description |
| `## Pre-existing issues` / `## Pre-existing issues (out of diff)` | repository-scoped defects |
| `## Accepted audits` | audits |
| `## Proposed fixes` | the two task blocks, last |

Every machine finding appears exactly once as a `###` heading in its section:

- diff defects: `### DR-NNN (<Priority>): <title>`
- repository defects: `### DR-NNN (<Priority>): <title>` under `## Pre-existing issues`
- audits: `### DR-NNN (audit): <title>` under `## Accepted audits`

A heading without a machine finding, a duplicate heading, a finding without a heading, and a
finding in the wrong section or with the wrong parenthetical are all errors. An empty category's
section may be absent, or read `- none`.

### Tasks

```markdown
## Proposed fixes

### Tasks (Critical)

- none

### Tasks (Warning)

- [ ] **Task 1 (Warning): update the documented timeout**
  - Update `README.md:3` to state 60 seconds.
  - Finding: DR-001
  - Acceptance criteria:
    - README.md states 60 seconds and no longer states 30 seconds.
```

- Exactly one `### Tasks (Critical)` and one `### Tasks (Warning)` block, and no other `###`
  heading inside `## Proposed fixes`.
- `- [ ] **Task N (<Priority>[, behavior]): <title>**`, numbered `1..N` within its block,
  unchecked. The `behavior` marker belongs to a behavioural defect; a documentation-only fix
  carries none.
- Every task's body carries exactly one `Finding: DR-NNN` and an `Acceptance criteria:` line.
- One task per actionable finding, no extra tasks. A block with no findings reads exactly
  `- none`.
- No checkbox anywhere else in the report — preamble, another section or between the blocks
  included. Fenced code blocks are ignored when the report is scanned.

### The completion line

The reviewer returns one line, and the skill passes it to the checker unchanged:

    Deep review: <c> Critical, <w> Warning, <s> Suggestion — <specs.dir>/<TICKET_ID>/deep-review.md

`<c>`, `<w>` and `<s>` are the counted diff defects, and they must equal the copied task blocks
for Critical and Warning; `<path>` must be the report's real path. A line that disagrees with the
validated artifacts fails the check — the counts a run offers from are the validated ones, never
the line as written.

### A complete example

```markdown
---
type: deep-review
ticket: PROJ-12
produced_by: artel:reviewer
---

**Review run:** 6f9c2d1e-4b3a-4c8d-9e0f-1a2b3c4d5e6f
**Reviewed base:** 9f8e7d6c5b4a39281706f5e4d3c2b1a098765432
**Reviewed head:** 0123456789abcdef0123456789abcdef01234567
**Review completeness:** complete

## Critical Issues (must fix)

- none

## Warnings (should fix)

### DR-001 (Warning): README.md documents the old timeout

`config.py:3` now defaults to 60 seconds; `README.md:3` still says 30.

## Suggestions (consider improving)

- none

## Pre-existing issues (out of diff)

- none

## Accepted audits

### DR-002 (audit): the changed default is the only sensitive surface touched

The value is read through one named constant; no other surface changed. The change is acceptable.

## Proposed fixes

### Tasks (Critical)

- none

### Tasks (Warning)

- [ ] **Task 1 (Warning): update the documented timeout**
  - Update `README.md:3` to state 60 seconds.
  - Finding: DR-001
  - Acceptance criteria:
    - README.md states 60 seconds and no longer states 30 seconds.
```

## 4. Verification and completeness

Each `verification` record is one of exactly two shapes — no other keys, and no numeric
confidence anywhere:

| `kind` | Keys |
|---|---|
| `command` | `command` (the exact command) and `result` (its outcome) |
| `evidence` | `evidence` (what was inspected or supplied) |

A review records at least one. An unconfigured `verify.commands` is recorded as skipped — an
`evidence` record saying so, never a passed command and never a green gate; a missing optional
gate does not make the review partial.

`completeness` is about **review coverage**, not gate status: what was read in depth, what was
skimmed, what was left unreviewed. A review that could not cover the change — a timeout, an
unreached area — is `partial`, lists its gaps in `gaps`, and stops the hand-off: the checker
exits `1`, and the skill displays the gaps instead of offering fixes.

## 5. Validating (`check`)

Files path:

```bash
python3 ${ARTEL_ROOT}/scripts/deep_review.py check \
  --target .artel/run/<TICKET_ID>/deep-review/target.json \
  --findings .artel/run/<TICKET_ID>/deep-review/machine.json \
  --report <specs.dir>/<TICKET_ID>/deep-review.md \
  --summary '<the completion line>'
```

Kartoteka path — the stored document goes by pipe, never through a file:

```bash
set -o pipefail; python3 ${ARTEL_ROOT}/scripts/spec_store.py get <specs.dir>/<TICKET_ID>/deep-review.md | python3 ${ARTEL_ROOT}/scripts/deep_review.py check \
  --target .artel/run/<TICKET_ID>/deep-review/target.json \
  --findings .artel/run/<TICKET_ID>/deep-review/machine.json \
  --report - --summary '<the completion line>'
```

`--report-path` is only needed when the logical report path differs from the manifest-derived
`<specs.dir>/<TICKET_ID>/deep-review.md` (spec-storage.md §4.2's pipe rule).

An empty or whitespace-only report is **unprocessable input**, not a malformed artifact: the
checker answers exit `2` with an `empty_input` reason. On the kartoteka path that is a failed
`get` — the store's own error is the cause — and on the files path the reviewer wrote nothing.
A nonempty report that lacks the header is different: exit `1`, correctable by the reviewer.

Exit codes:

| Exit | Meaning |
|---|---|
| `0` | valid and complete |
| `1` | inconsistent, invalid or partial — the envelope's `errors` say why |
| `2` | the tool cannot evaluate its inputs: usage, unreadable or empty files, an invalid manifest, a target that no longer resolves |

In one sentence: exit `0` is a validated review, exit `1` is a review to fix or expose, exit `2`
is a target or input the tool cannot judge.

The checker also re-confirms the checkout still matches the pin (branch, HEAD, no pending source
changes, `merge-base(head, base)`) and writes nothing.

On success the one-line JSON envelope carries `data`: `schema`, `ticket`, `run_id`, `branch`,
`base`, `head`, `merge_base`, `completeness`, `gaps`, `counts` (`Critical`/`Warning`/
`Suggestion`), `tasks` (`Critical`/`Warning`), `findings` with its two explicit dimensions
`by_kind` (`defect`/`audit`) and `by_scope` (`diff`/`repository`), `verification` (the record
count), and `report` (the repo-relative path). `data.counts` and `data.tasks` are what the skill
offers from.

**What the checker proves:** the artifacts' structure, identities and anchors — the metadata and
SHA links, the heading/ID/task correspondence, and that every line anchor falls in the pinned
diff. **What it does not prove:** never that a finding is true. Semantic truth stays the
reviewer's, and the reader's judgement.

## 6. The hand-off

- A validated `complete` review offers its `### Tasks` blocks: the skill copies the chosen
  block(s) under `### deep-review-<YYYY-MM-DD>` into the ticket's `## Code Review Fixes`
  ([task-queue.md](task-queue.md) §6), records the rows on the queue path, and runs one
  implementer per task.
- A `partial` or otherwise failing review stops before any copy or implementer. One artifact
  retry is shared by a missing artifact and an invalid one: the same reviewer, once, with the
  same prompt and model plus the checker's errors — at most two reviewer attempts per run; the
  skill never edits the report or the machine report itself. Unprocessable input (an empty
  report, `empty_input`) and checkout drift are never retried.
- Accepted audits never count and never become tasks; repository findings stay pre-existing
  issues; a `Suggestion` never becomes a task.
