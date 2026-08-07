# Phase 5 — hooks and gates: design

*Status: approved · 2026-08-07*

Design for [porting-plan.md](../../porting-plan.md) Phase 5: port the source project's five
Python quality-gate hooks (`../adguard-wallet/.claude/hooks/`), wire them via `hooks/hooks.json`,
and resolve [design.md](../../design.md) open question 1 (the deterministic CLI). Approach:
**faithful port with mapped substitutions**, matching the Phase 2–4 precedent — the hooks' logic,
caps, and fail-open discipline port verbatim; everything Dart/wallet-specific (the `agent.dart`
CLI, the `is_code_dart` file filter, spec-trail state paths, the wallet sensitive-paths policy)
maps to config lookups and artel path conventions.

## Deliverables

- `scripts/verify.py` — JSON-envelope wrapper over the configured verify commands.
- `scripts/plan_check.py` — plan-anchor checker; the exact tool Gate 3.5 in
  `skills/feature-development/SKILL.md` already invokes.
- `hooks/hooks.json` — event wiring with `${CLAUDE_PLUGIN_ROOT}` paths.
- `hooks/hook_common.py` + five ported hooks: `session_baseline.py`, `fast_verify_post_edit.py`,
  `stop_gate.py`, `verify_stop_gate.py`, `sensitive_guard.py`.
- `hooks/sensitive-paths.json` — shipped default sensitive-paths policy.
- One new config key: `verify.surface`, plus the `{files}` placeholder rule
  ([config.md](../../config.md)).
- `tests/` — stdlib `unittest` suite over the pure-function core.
- Doc updates: config.md, autonomous-run.md §10, design.md (open question 1 + decision log),
  porting-plan.md (checkboxes + map row), skills/setup interview additions, CHANGELOG.

## Decisions (with rationale)

1. **Open question 1 resolved: Python rewrite of `verify` + `plan-check`; `codegen` dropped.**
   `plan_check.py` was effectively pre-decided — the shipped feature-development Gate 3.5 already
   calls `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan_check.py --plan <path> --strict`. `verify.py`
   ships because the hooks need one deterministic contract (exit 0/1/2 + envelope + finding keys)
   over arbitrary configured commands. The source `codegen` verb is **not** ported: its
   auto-detection rules (freezed/arb/API annotations) are inherently Dart-specific, and
   `setup.commands` already covers install/codegen generically. The porting-plan map row updates
   accordingly.
2. **Finding keys are digit-stripped output lines.** A key = a non-empty output line of a red
   stage, ANSI-stripped, digit-stripped, whitespace-collapsed, deduped, prefixed `s<index>:`,
   capped at 200 per stage. Digit-stripping makes keys stable against both shifting line numbers
   and timing noise ("Done in 3.2s"), at the accepted cost of deduping same-rule-same-file
   findings — for a blocking gate, fewer false blocks beats per-finding granularity. Keys are
   computed once, in `verify.py`; hooks consume them from the envelope and never re-derive.
3. **Default sensitive-paths policy: secrets + gate config + CI/CD.** Two `full-gates`
   categories — secrets/credentials and gate self-protection (an armed run must not rewrite its
   own gates or the host's hook wiring) — plus one `plan-gate` category for CI/CD pipeline files.
   Small, defensible, near-universal; broader nets (migrations, lockfiles, infra) match too many
   innocent paths across ecosystems.
4. **New `verify.surface` config key + `{files}` placeholder.** `verify.surface` (optional array
   of fnmatch globs; `!`-prefixed patterns exclude; absent → every changed file counts) replaces
   the source's hardcoded `is_code_dart` filter. `verify.fast` / `verify.commands` entries may
   contain `{files}`, replaced with the space-joined shell-quoted changed paths; without the
   placeholder the command runs unscoped. The source behavior stays expressible:
   `["lib/**/*.dart", "!*.g.dart", "!*.freezed.dart"]` + `"… verify --fast --paths {files}"`.
   Deliberately not reusing `runtime.surface`: a project's runtime surface and lintable surface
   are different sets.
5. **Two independent layers, subprocess boundary between them (Approach A).** The scripts are
   standalone CLIs reading `.artel/config.json` themselves; the hooks treat `verify.py` exactly
   as the source hooks treated the Dart CLI — a subprocess with an envelope contract and a
   timeout. ~15 duplicated config-reading lines are accepted: subprocess isolation gives hooks
   reliable timeouts, an exception in the verify layer can never take down a hook that must fail
   open, and the scripts stay human-readable top to bottom.
6. **Hook state lives under `.artel/run/.hooks/`.** Session baselines
   (`baseline-<session>.json`) and verify-stop counters (`stopblocks-<session>.json`) —
   dot-prefixed so it can never collide with a ticket dir, already inside the gitignored tree.
   The per-ticket stop-gate counter stays at `.artel/run/<TICKET>/.stop-gate-blocks` for source
   parity.
7. **Policy override is wholesale, not merged.** `sensitive_guard.py` loads
   `.artel/sensitive-paths.json` when present, else the plugin's `hooks/sensitive-paths.json`.
   No merge semantics: the effective policy is always exactly one readable file. The setup
   interview offers to scaffold the host file from the shipped defaults.

## `scripts/verify.py`

Invocation: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py [--fast] [--files a,b,c]
[--timeout N]`, run from the host repo root; config from `.artel/config.json`.

- `--fast` → single stage from `verify.fast`; default mode → `verify.commands` in order,
  stopping at the first red stage (config.md's documented gate rule).
- `{files}` substitution per decision 4. A present-but-blank `--files` value is
  `invalid_argument` (exit 2) — the source's anti-footgun rule: blank must never silently widen
  a scoped call.
- No commands configured → `{"skipped": true}` in `data`, exit 0. The *caller* journals
  "skipped"; per config.md an empty gate is never reported green.
- Envelope: the source's one-line-JSON contract, three shapes keyed by `ok`/`error` — exit 0
  success, 1 findings, 2 environment error. Exit-2 classification for arbitrary commands: spawn
  failure, stage timeout, or stage exit code 126/127 → environment error (fix the toolchain,
  never the code); any other non-zero stage exit → findings.
- `data.stages` is an **array** (generic commands have no stage names):
  `{"command", "exit_code", "ok", "keys", "tail"}` — `tail` is the last ~2000 chars of raw
  combined output for humans; `keys` per decision 2.
- Per-stage timeout via `--timeout` (seconds, default 240); hooks pass their own budgets
  (baseline 100, per-edit 120, stop gate 240) exactly as the source did.

## `scripts/plan_check.py`

Invocation: `--plan <path> [--strict]` — matching Gate 3.5 as shipped.

- Anchor grammar ports unchanged: `ref:<value>` / `new:<value>` tokens (value charset
  `[A-Za-z0-9_$./-]+`, trailing punctuation trimmed), dedupe by `kind:value`.
- Genericized implicit refs: the backticked-path rule drops the Dart root whitelist
  (`lib|test|integration_test|packages`); any backticked repo-relative token containing `/` and
  ending in a file extension is an implicit `ref:`. Both suppression rules port verbatim: a line
  carrying a `new:` anchor or the `(new file)` marker suppresses that line's backticked paths;
  an exact-value `new:<path>` anywhere suppresses that path document-wide.
- Resolution: `/`-containing values → filesystem existence (file or directory). Bare symbols →
  `ast-index symbol <name> --format json` when the binary is on PATH (non-empty JSON array =
  hit; member refs `Foo.bar` resolve on `Foo`; one `ast-index update` retry for stale-index
  recovery, then a final re-check — no further retries), else fallback `git grep -l -w <name>`
  over tracked files (any hit = resolved). The anti-hallucination check keeps working in repos
  without ast-index.
- `data` keeps the source shape: `{"checked", "resolved", "new_declared", "unresolved":
  [{"ref", "reason"}]}` with reasons `"file not found"` / `"symbol not found"`. Exit 0 clean or
  unresolved-without-`--strict`; exit 1 unresolved with `--strict`; exit 2 `plan_not_found` /
  `invalid_argument` / internal error.

Deliberate non-goal: the Phase-3 skill bodies (`inner-loop`, `validate`, …) keep running
`verify.commands` directly as ported — `verify.py` is the hooks' engine and an à-la-carte CLI,
not a forced migration.

## `hooks/hook_common.py`

Stdlib-only, Python 3.9-compatible (no PEP 604 annotations — macOS may ship 3.9).

- `read_hook_input()`, `relpath_from_tool_input()` — verbatim ports.
- `load_config()` — tolerant read of `.artel/config.json`; missing/unparseable → `{}`.
- `is_verifiable(path)` — replaces `is_code_dart`: fnmatch against `verify.surface`
  (repo-relative; match ≥1 positive and 0 `!`-negative patterns); key absent → every file
  counts.
- `changed_files()` — port of `changed_dart_files`: `git diff --name-only HEAD` merged with
  `git status --porcelain` (rename lines resolve to the new path), filtered by `is_verifiable`
  and still-exists.
- `run_fast_verify(files, timeout)` — subprocess to `scripts/verify.py --fast`; plugin root
  derived from `__file__` (hooks and scripts are siblings inside the plugin install — no
  env-var dependency); parses the last stdout line as the envelope; returns
  `(exit_code, envelope)`.
- `finding_keys(envelope)` — reads `data.stages[*].keys` from the envelope; no re-derivation.
- `resolve_active_ticket()` — reads `<specs.dir>/.active_ticket`
  ([ticket-parsing.md](../../ticket-parsing.md) §6; default `specs/.current/`), parses line 1
  with `ticket.pattern` (`{projectKey}` substituted, case-insensitive), returns the **base**
  ticket ID with any phase suffix stripped — `.artel/run/<TICKET>/` is always ticket-top-level
  per [autonomous-run.md](../../autonomous-run.md)'s "Host-writable state" section.
- `STATE_DIR = .artel/run/.hooks/` per decision 6.

## The five hooks

Logic, caps, and comments port unchanged unless noted.

| Hook | Event | Deltas from source |
|---|---|---|
| `session_baseline.py` | SessionStart | Keys from the envelope; exit-2 → baseline left absent for the stop gate's lazy first-sight capture (unchanged rule) |
| `fast_verify_post_edit.py` | PostToolUse `Edit\|Write\|MultiEdit` | File gated by `is_verifiable`; exit 1 → `additionalContext` listing red stages' `tail` lines (cap 10) — raw output, since generic findings have no `file:line:rule` shape; exit 0/2 → silent, never blocks |
| `stop_gate.py` | Stop | `resolve_active_ticket()`; state at `.artel/run/<TICKET>/run-state.json`, counter `.artel/run/<TICKET>/.stop-gate-blocks`; `MAX_CONSECUTIVE_BLOCKS = 5`, `WALL_CLOCK_HOURS = 3`, allow-conditions verbatim; block-reason text names the artel paths |
| `verify_stop_gate.py` | Stop | Mechanics verbatim: counter reset on clean/no-changes, allow-with-stderr on exit 2, lazy first-sight baseline, new-keys diff, `MAX_CONSECUTIVE_BLOCKS = 2` with the latch-at-cap behavior and its phase-lock comment; block reason shows ≤10 new keys + the exact re-run command |
| `sensitive_guard.py` | PreToolUse `Edit\|Write\|MultiEdit` | Arming (active + fresh run-state, 3h), `MODE_RANK`, floor comparison, `TASKLIST_READY` requirement verbatim; run-state from `.artel/run/`; policy per decision 7 |

## `hooks/sensitive-paths.json` (shipped defaults)

fnmatch on repo-relative paths — `*` crosses `/` in fnmatch, so patterns stay short:

- `secrets` (`full-gates`): `.env*`, `*/.env*`, `*.pem`, `*.key`, `*.p12`, `*secret*`,
  `*credential*`, `*id_rsa*`
- `gate-config` (`full-gates`): `.claude/*`, `.artel/config.json`, `.artel/sensitive-paths.json`
- `ci-cd` (`plan-gate`): `.github/workflows/*`, `.gitlab-ci.yml`, `Jenkinsfile`, `.circleci/*`,
  `azure-pipelines.yml`, `bitbucket-pipelines.yml`

## `hooks/hooks.json`

Five registrations mirroring the source's events and timeouts: SessionStart →
`session_baseline.py` (120s); PostToolUse `Edit|Write|MultiEdit` → `fast_verify_post_edit.py`
(150s); Stop → `stop_gate.py` (60s) then `verify_stop_gate.py` (300s); PreToolUse
`Edit|Write|MultiEdit` → `sensitive_guard.py` (30s). Each command:
`cd "$CLAUDE_PROJECT_DIR" && python3 "${CLAUDE_PLUGIN_ROOT}/hooks/<name>.py"` — the `cd` guard
pins the cwd to the host repo root, which every path in the system assumes.

## Failure policy (stated invariant)

Every hook fails **open**: top-level `try/except → exit 0` with a stderr note in all five;
verify environment errors (exit 2) always allow; missing config, missing state files, and
unparseable JSON all allow. The only blocking behaviors in the layer are `verify_stop_gate`'s
bounded new-findings block and `sensitive_guard`'s floor deny — both disarmed when no run is
armed.

## Testing

`tests/` runnable with `python3 -m unittest discover tests`, subprocess-free (the pieces under
test are pure functions): key normalization (ANSI/digit stripping, collapse, dedupe, caps);
exit classification (126/127/timeout → 2, other non-zero → 1); `{files}` substitution and
blank-`--files` rejection; anchor extraction with both suppression rules; `is_verifiable` glob
semantics including `!` negation; active-ticket resolution with phase-suffix stripping.

## Doc and skill updates (landing with the code)

- [config.md](../../config.md): `verify.surface` key, the `{files}` placeholder rule, and
  `.artel/sensitive-paths.json` in "Purpose and location".
- [design.md](../../design.md): open question 1 marked decided; decision-log entries for
  decisions 1–4, 6, 7 above.
- [porting-plan.md](../../porting-plan.md): Phase 5 checkboxes; `tools/agent/` map row updated
  (codegen dropped — `setup.commands` covers it).
- [autonomous-run.md](../../autonomous-run.md) §10: the "(Phase 5)" forward reference replaced
  with the concrete policy file locations.
- `skills/setup/SKILL.md`: interview gains an optional `verify.surface` question and the
  sensitive-policy scaffold offer.
- `CHANGELOG.md` entry under `[Unreleased]`. *(Erratum vs. the approved draft, which said to
  bump the plugin version now: the repo accumulates all pre-release work under `[Unreleased]`
  at version `0.1.0` and tags `v0.1.0` in Phase 6 — a mid-stream bump would be the first and
  only one of its kind.)*

## Out of scope

- Migrating Phase-3 skill bodies to call `verify.py` (they keep running config commands
  directly).
- The `codegen` verb (decision 1) and any ast-index shipping (likbez's domain — `plan_check.py`
  only *uses* ast-index opportunistically when present).
- Phase 6 items: end-to-end dry run, operator docs, publish/tag.
