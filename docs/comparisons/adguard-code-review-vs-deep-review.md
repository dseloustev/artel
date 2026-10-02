# AdGuard `code-review` vs artel `deep-review`

A comparison of two review systems, written 2026-10-02, and the source of the review-evidence
discipline artel adopted the same day ([design.md](../design.md), decision log 2026-10-02).

## Provenance

The AdGuard plugin lives in a **private** repository
(`github.com/AdGuardSoftwareLimited/ai-plugins`, `plugins/code-review`); an unauthenticated
fetch returns 404. The analysis below is against a local clone of **v0.1.0 (commit `2cafd82`,
2026-09-28)** — re-clone with the corporate credentials for anything newer. The repo carries
no licence file, so only mechanics were adapted into artel; all prose is written fresh.

The artel side is `skills/deep-review/` with its two agents and contracts:
`agents/reviewer.md`, `agents/review-forecaster.md`, `docs/review-forecast.md`.

## TL;DR

They solve different halves of the problem. AdGuard's is a **self-contained review engine** —
evidence-first findings, anchor validation, convention/architecture/security passes, 23
language packs, no ecosystem dependencies. artel's is a **review-and-fix pipeline** — dispatch
a reviewer, forecast the human review from kartoteka precedents, write `deep-review.md`, turn
fixes into tasklist and queue work, apply them, re-verify. AdGuard's review *mechanics* are
deeper; artel's *workflow integration, precedent forecasting and fix loop* have no counterpart
there.

## At a glance

| | AdGuard `code-review` | artel `deep-review` |
|---|---|---|
| What it is | Senior-engineer review protocol (one self-contained skill) | Orchestrator: review → precedent forecast → optional fix application |
| Trigger | Auto-discovered skill; natural language | `/artel:deep-review [ticket] [branch] [pr-link] [--local]` + router |
| Unit of work | Working copy / range / branch / commit / PR-MR id | A ticket's branch (ticket-wide; phase suffix discarded) |
| Architecture | Single reasoner, Phases 0–3, 23 language packs, 1 validator script, 3 execution modes | Skill orchestrator + 2 agents + 4 contract docs |
| Output | Human report (verdict + findings + fix list + verification), opt-in JSON, optional PR comment | `deep-review.md` (verbatim comments + 2 tables + fixes + record) → tasklist → queue → implemented fixes |
| Depends on | Only the repo + its toolchain (`gh`/`glab` optional) | `.artel/config.json`, git, optional kartoteka MCP, spec store, agents, task queue |
| Maturity | v0.1.0, CI lints markdown + ruff; manual behaviour test; an experiment beside their production `ocr` lane | Contract-pinned pytest suite, hook tests |

## Dimension by dimension

| Dimension | AdGuard `code-review` | artel `deep-review` | Edge |
|---|---|---|---|
| **Review depth** | Hunk-by-hunk evidence-first pass + architecture pass + verification pass, each with a dedicated reference | `reviewer` agent: 3 lenses (convention/architecture/security), regression guard, PR compliance, `verify.fast` | AdGuard |
| **Finding contract** | Every finding cites exact `file:line` + observed failure or violated contract; unverified findings labelled; no named failure path → no finding | Standalone output asks for priority sections + fix examples; `findings.json` carries `file`/`line`, but no evidence requirement or unverified labelling | AdGuard |
| **Anchor integrity** | `validate-findings.py` mechanically checks anchors against real new-side hunks | No mechanical check (before 2026-10-02); bad anchors flowed into table attachment and citations | AdGuard |
| **Noise control** | Prohibited list (praise, diff restatement, speculation, nitpicks); linter-owned style excluded; out-of-diff routed; cross-file consolidation; P3 omitted by default | `verify.fast` findings never re-reported; NON-CURRENT threads uncounted; budget/record honesty; no out-of-diff route (before 2026-10-02) | AdGuard (review), artel (forecast) |
| **Conventions** | Mandatory conventions phase; "repo already does X in `<file>` — align with it"; must cite an existing example; three-ways → ask which is canonical; capped at P2 | `convention-fit` lens reads host CLAUDE.md/style guides; weaker backing and phrasing rules | AdGuard |
| **Architecture** | Dedicated reference: boundaries, layering, six decay risks, speculative abstraction, scope creep | `architecture-fit` lens: layers, DI pattern, signature parity, infra leakage | AdGuard |
| **Security** | Universal taxonomy with signal + confirm-check pairs | Security lens with an enforced coverage guarantee (sensitive-path diff line ⇒ high finding or audit entry) + sensitive-paths policy | Tie — complementary |
| **Large diffs** | Concrete triage: skip/skim/full, risk-first, consolidation, coverage statement, staged passes, split recommendation | One sentence: "reviewed in passes, and the report says so" (before 2026-10-02) | AdGuard |
| **Verification** | Runs the repo's own commands; records exact commands/outcomes/gaps; never implies a run; can cite runner CI | Hard gate before review and after fixes; `verify.fast` in the agent; no per-finding verification record | Tie — artel's gates, AdGuard's honesty protocol |
| **Intent context** | Stated purpose from commit/branch/PR description | Ticket docs (idea/PRD/plan/tasklist) + PR Compliance section | artel |
| **Precedent / forecasting** | None — stateless; every run starts cold | kartoteka: units, classification, weighted smoothed pass rate, cited precedents, budget, freshness record | artel |
| **Fix workflow** | Report + fix list; optional manual PR posting | `deep-review.md` → `## Code Review Fixes` → task queue → implementer per task → re-verify | artel |
| **Output artifact** | Human report + opt-in JSON; template asset; portable | Schema'd `deep-review.md`; formula and budget single-home; versioned on the kartoteka path | artel (richer), AdGuard (portable) |
| **Portability** | Self-contained; modes dispatched by tool presence; host CLIs confined to one file | Coupled to the artel ecosystem | AdGuard |
| **State / idempotency** | Stateless; re-run = fresh review | Overwrite prompt; kartoteka versioning; no re-review loop | Tie |
| **Language / domain rules** | 23 on-demand packs, source-cited, drift-is-a-bug maintenance rule | Host conventions docs + optional code-symbol index; no packs | AdGuard |
| **Tests / maintenance** | markdownlint + ruff CI; manual behaviour test | Contract-pinned pytest suite; formula/budget single-home; mirror tests | artel |

## Pros and cons

### AdGuard `code-review`

**Pros**

- The review itself is the product: evidence-first, anchor-validated, anti-noise,
  convention-grounded — the strictest finding contract of the two.
- `validate-findings.py` makes a whole class of review error (a wrong anchor) mechanically
  catchable instead of a matter of trust.
- Out-of-diff routing (explicit `scope`, never affecting the verdict) is a clean answer to a
  real reviewer problem.
- The report is optimised for action: verdict → findings → fix list → verification section.
- Self-contained and portable; mode dispatch needs no config.
- Source-cited language packs with an explicit maintenance contract.

**Cons**

- One reasoner, no cross-check of findings; the dedupe "arbiter" lives outside the skill.
- No history: every run starts cold; re-review = full re-run.
- Platform tool names (`report_findings`, `code_review`) leak into a design that claims
  host-agnosticism.
- AdGuard corporate assumptions are baked into Phase 0 and the packs — noise elsewhere.
- v0.1.0: one manual test path, no e2e; packs hand-maintained against private sources.
- PR posting is thin/manual: no review-event submission, no duplicate suppression.

### artel `deep-review`

**Pros**

- Unique forecasting layer: kartoteka precedents with citations, smoothing, reviewer weights,
  budget caps, and a record that distinguishes *nothing filed* / *index cold* / *did not look*.
- Full loop: gate → review → forecast → tasklist → queue → implement → re-verify. AdGuard
  stops at a report.
- Clean separation: orchestrator never judges; one seat per agent; model tiering.
- Contract-pinned by tests; storage abstraction with versioning; mirror hook.
- Ticket intent awareness (PRD/plan/tasklist) + PR compliance; regression guard via
  three-dot vs two-dot diff — something AdGuard does not do.
- Honest failure modes: forecast-off always recorded; no invented precedents.

**Cons**

- Review mechanics were shallower than AdGuard's: no evidence contract, no anchor validation,
  no large-diff triage, weaker convention and anti-noise rules (the first three addressed on
  2026-10-02).
- No route for out-of-diff defects (addressed on 2026-10-02).
- No per-finding verification record; the forecaster trusts the reviewer.
- No PR interaction (by design) and no re-review loop.
- Forecast only works on Bitbucket-backed kartoteka indexes; constants are acknowledged
  placeholders.
- Heavy ecosystem coupling — the review protocol cannot be reused standalone.

## Suggestions, and what happened to them

### Adopted 2026-10-02

1. **Evidence contract for every finding** — exact `file:line` + the observed failure or
   violated contract; reading-only correctness claims marked; no named failure path → no
   finding. Landed in `agents/reviewer.md`, `## Evidence and anchoring`.
2. **Mechanical anchor validation** — `scripts/validate_findings.py` checks anchors and
   `scope` against the reviewed diff's new-side hunks; the reviewer runs it before returning.
3. **Large-diff triage** — classify skip/skim/full, read risk-first, consolidate repeated
   patterns, state coverage, staged passes at commit boundaries, recommend splitting past
   ~1000 lines. Landed in `agents/reviewer.md`, `## Large changes`.
4. **Out-of-diff routing** — `"scope": "repository"` for defects in unchanged code; a
   **Pre-existing issues (out of diff)** report section; never a fix task, a forecast unit or
   a counted finding. Landed in the reviewer, `docs/review-forecast.md` §2 and the
   forecaster's template.

### Still open (not adopted)

- **Convention protocol tightening** — exact phrasing, must-cite-an-existing-example,
  linter-owned exclusion, three-ways → ask which is canonical.
- **Anti-noise list and "working is not clean"** — explicit prohibitions; fragile-but-working
  constructs reportable as maintainability findings.
- **Per-finding verification record** — exact commands/outcomes/gaps in the report, carried
  into `deep-review.md`.
- **Security taxonomy** — signal + confirm-check pairs behind the existing security lens.
- **Modernity ladder** — modern idiom for new code, with corporate docs and repo pattern
  taking precedence.
- **Fix list** — one line per definite issue for quick triage.

### What not to copy

- **Three-mode dispatch** (`report_findings`/`code_review` tool sniffing) — coupled to their
  runner's tool names; artel's orchestrator/agent split is cleaner.
- **23 corporate language packs** — AdGuard-specific authority; artel is multi-host by design.
- **PR posting** — artel is deliberately read-only on the VCS host; `address-pr-comment` and
  `pr-create` own that surface.
- **Self-contained single-file design** — artel's skill/agent/contract split is what makes
  forecasting and fix application possible.
- **Confidence scoring** — AdGuard rejects it; do not reintroduce it.

## Shared weaknesses

Both are single-reasoner by design (AdGuard: "do not delegate the analysis to subagents";
artel: "no second reviewer"), and both are stateless with no re-review loop. AdGuard punts
re-review to its runner; artel punts to re-running the skill. Neither independently verifies
that a finding is real beyond mechanical checks — in artel, the anchor validator closes only
the mechanical half.
