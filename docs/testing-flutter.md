# Testing artel on a Flutter project

A hands-on smoke-test guide: install the plugin from this checkout into a separate Flutter
project and validate the workflow end to end. This is the operator-run counterpart of
[porting-plan.md](porting-plan.md) Phase 6's dry run — Flutter is a deliberately good host, since
the source system artel was ported from ran on Flutter, so every gate has a natural command to
drive it.

Companion docs: [workflow-guide.md](workflow-guide.md) (what each step should do),
[config.md](config.md) (every key used below), [skills-reference.md](skills-reference.md)
(per-skill expectations).

## 1. Prerequisites

- **Claude Code** with plugin support (`/plugin` works).
- **`python3` on PATH** — the hooks and gate scripts require it.
- **Flutter SDK** — `flutter --version` and `dart --version` work in the same shell you launch
  Claude Code from (the hooks inherit that PATH).
- **A Flutter project in a git repo.** A scratch one is best:

  ```bash
  flutter create artel_smoke
  cd artel_smoke
  git init && git add . && git commit -m "chore: scaffold"
  git checkout -b develop   # optional; any non-default working branch setup is fine
  ```

- **Optional but recommended: an `origin` remote** (a scratch GitHub repo works). The
  orchestrators' checkpoint commits **push to `origin`** after the approval pause; without a
  remote, expect the push step to stop-and-ask — that stop is itself correct behavior, but a
  remote lets you see the full loop. `gh auth login` if you also want to exercise the
  `github-cli` PR step and/or the `github-issues` tracker.

## 2. Install from the local checkout

In the Flutter project's Claude Code session:

```
/plugin marketplace add <path-to-this-artel-checkout>
/plugin install artel@artel
```

**Expected:** install succeeds; typing `/artel:` offers the namespaced skills
(`/artel:feature-development`, `/artel:dev`, `/artel:setup`, `/artel:analysis`, …). If the
skills don't appear, restart the session once — hooks and skills register at session start.

## 3. Configure for Flutter

Run `/artel:setup` and answer the interview, or write `.artel/config.json` yourself. A known-good
Flutter configuration for a local-only test (no tracker, GitHub for PRs):

```json
{
  "version": 1,
  "ticket": {
    "projectKey": "FLT",
    "pattern": "^(?:{projectKey}-)?(\\d+)(?:-p?(\\d+))?$",
    "phaseSuffix": true
  },
  "tracker": {
    "adapter": "none",
    "mcpToolPrefix": ""
  },
  "vcs": {
    "adapter": "github-cli",
    "mcpToolPrefix": ""
  },
  "verify": {
    "commands": [
      "flutter analyze",
      "dart format --output=none --set-exit-if-changed .",
      "flutter test"
    ],
    "fast": "dart analyze {files}",
    "surface": ["lib/**/*.dart", "test/**/*.dart", "!**/*.g.dart", "!**/*.freezed.dart"]
  },
  "setup": {
    "commands": ["flutter pub get"]
  },
  "language": {
    "docs": "en",
    "pr": "en"
  },
  "design": {
    "figma": false
  },
  "specs": {
    "dir": "specs/.current",
    "releases": "specs/releases"
  },
  "runtime": {
    "surface": ["lib/**/*.dart"]
  }
}
```

Notes on the choices:

- **`runtime.run` is deliberately unset** for the first pass, so the `RUNTIME_OK` gate records
  `skipped (not configured)` — honest degradation is part of what you're testing. Don't put
  `flutter run` there later: `run-app` treats the command as a bounded, self-terminating black
  box whose exit code is the verdict, and `flutter run` never exits. When you do want the gate
  armed, use something that builds-and-exits, e.g. `"run": "flutter build apk --debug"` (or
  `flutter build macos --debug`), or an integration-test command.
- **`verify.fast` carries `{files}`** so the per-edit hook analyzes only what changed;
  `verify.surface` keeps generated files out of the hooks' sight.
- **`tracker.adapter: "none"`** keeps the whole test local — the ticket description comes from
  a file you write. Switch to `"github-issues"` later to exercise the tracker path.

**Expected after setup:** `.artel/config.json` exists; `.gitignore` gained `.artel/run/` and
`.artel/context/`; the report says which gates are armed (verify) and which will record
`skipped` (runtime, design).

## 4. Smoke tests

Run these in order — later ones reuse earlier artifacts. Between scenarios, `git status` should
only ever show what the scenario says it changes.

### 4.1 Spec stage à la carte

Write a small description file, e.g. `ticket.md`:

> Add a "reset" button to the counter page that sets the counter back to zero. Add a widget
> test proving it.

Then seed the ticket and run the PRD interview:

```
/artel:generate-idea FLT-1 ticket.md
/artel:analysis FLT-1
```

**Expected:** `generate-idea` (under `tracker.adapter: "none"`) builds
`specs/.current/FLT-1/idea.md` from your description and sets `specs/.current/.active_ticket`
to `FLT-1`. Running it standalone here exercises the skill on its own; the pipeline's gate 0
would seed the same file itself (4.5 covers that path).
`analysis` then explores the project, interviews you in batches of ≤4 questions, and writes
`specs/.current/FLT-1/prd.md` with `Status: PRD_READY`. Nothing outside `specs/.current/`
changes.

### 4.2 Dry run of the full pipeline

```
/artel:feature-development FLT-1 --dry-run
```

**Expected:** the chatty head runs (PRD already exists → skipped; vision checkpoint; silent
research + plan; the `plan_check.py` grounding gate; tasklist), then the approval-pause
*presentation* appears — plan summary, tasks, HITL tags, open questions — and the run **stops**.
`ls .artel/run/FLT-1/` shows **no `run-state.json`** (a dry run never arms; `open-questions.md`
may exist). Plan and tasklist sit under `specs/.current/FLT-1/`.

### 4.3 Hooks: fast verify + stop gate

Introduce an analyzer error in `lib/main.dart` (e.g. an unused variable with a typo'd type) via
a normal edit request to Claude.

**Expected:** after the edit, the PostToolUse hook surfaces the analyzer findings in-session.
Ask Claude to end the turn / finish up — the verify stop gate should **block completion**,
naming the findings introduced this session (pre-existing findings are baselined and never
block). Fix the error → the next stop passes. State appears under `.artel/run/.hooks/`
(`baseline-<session>.json`, `stopblocks-<session>.json`); deleting the baseline file is the
documented escape hatch.

### 4.4 Lean loop end to end

```
/artel:init-branch FLT-1
/artel:dev FLT-1
```

**Expected:** `init-branch` (an optional convenience — the pipelines run on any feature branch)
creates `feature/FLT-1` off the detected default branch and runs `flutter pub get`
(`setup.commands`). `dev` presents the tasklist from 4.2 for its one work-list confirmation
(Confirm / Adjust — the pause happens even though the tasklist already exists), arms the run
(`.artel/run/FLT-1/run-state.json` with `run_active: true`), makes the work-list checkpoint
commit, then silently implements, reviews (`review.md`, `## Code Review Fixes` on findings),
records the runtime gate as `skipped (not configured)`, and closes with the phase checkpoint:
`flutter analyze` + format + tests green → commit + push. Without an `origin`, the push
stops-and-asks — the checkpoint procedure never forces and never self-repairs remotes; a
stop-and-ask there is correct behavior, not a failure. The final report lists tasks, deviations (`none` expected),
counters, checkpoint commits, and the journal path. During the armed run, a premature
session-end attempt is blocked by the run stop gate.

### 4.5 Full pipeline to the PR gate

Reset the scratch repo (or make a second ticket `FLT-2` with a new description) and:

```
/artel:feature-development FLT-2 ticket2.md
```

(No manual `generate-idea` this time — passing the description file lets gate 0 seed `idea.md`
itself, which is the path 4.1 skips.)

**Expected:** the same head as 4.2 but the pause is live — **Approve** it. The run arms, makes
the planning checkpoint, implements, reviews, skips runtime, runs QA (`qa.md` verdict), updates
docs/CHANGELOG, validates all gates, regenerates `pr-description.md`, and pauses at the PR gate
("Open the PR now?"). With `gh` authenticated and an `origin` on GitHub, approving opens a real
PR; otherwise choose "Skip — I'll do it manually" and confirm the run still closes cleanly
(`run-state.json` flips to `completed: true`, `run_active: false`; `run-journal.md` has the
completion entry).

### 4.6 The gate scripts standalone

From the host repo root (find the plugin root via `/plugin` — the installed path under
`~/.claude/plugins/`):

```bash
python3 <plugin-root>/scripts/verify.py --fast --files lib/main.dart
python3 <plugin-root>/scripts/verify.py
python3 <plugin-root>/scripts/plan_check.py --plan specs/.current/FLT-2/plan.md --strict
```

**Expected:** each prints a one-line JSON envelope; exit `0` when clean, `1` with findings
listed, `2` only for environment errors (e.g. Flutter not on PATH). `verify.py --files`
substitutes the paths into `verify.fast`'s `{files}` token.

## 5. What "pass" looks like

| Check | Where |
|---|---|
| Skills appear under the `artel:` namespace | `/artel:` completion |
| Config + `.gitignore` entries written once, by `setup` | `.artel/config.json`, `.gitignore` |
| Spec trail only under `specs/.current/<TICKET>/` | 4.1–4.5 |
| Run state only under `.artel/run/` (gitignored) | 4.2, 4.4, 4.5 |
| Nothing written into the plugin install/cache directory | `~/.claude/plugins/` unchanged |
| Unconfigured gates report `skipped`, never green | runtime + design in every report |
| Dry run never writes `run-state.json` | 4.2 |
| Verify stop gate blocks only findings you introduced | 4.3 |
| Checkpoint commits only on the feature branch, never forced | 4.4, 4.5 |
| PR-gate decline still closes the run cleanly | 4.5 |

## 6. Cleanup

```
/plugin uninstall artel@artel
/plugin marketplace remove artel
```

Then delete the scratch project, or keep it — `.artel/config.json` is inert without the plugin.

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/artel:*` skills missing after install | Restart the Claude Code session; confirm `/plugin` lists artel as installed. |
| Hooks silent in 4.3 | Hooks are inert until `.artel/config.json` exists and `verify.fast` is non-empty; also check `python3` is on the PATH Claude Code runs with. |
| `verify.py` exits `2` with `command_not_found`/`spawn_failed` | `flutter`/`dart` not on the hook shell's PATH — launch Claude Code from a shell where they resolve. |
| Checkpoint stops on "push rejected / no origin" | Expected without a remote — add a scratch `origin` or accept the stop-and-ask. |
| Runtime gate hangs | You configured a non-terminating `runtime.run` (e.g. `flutter run`) — use a build-and-exit command; see §3. |
| Every gate `skipped` | That's the inert default config — arm gates via `verify.commands` etc. (config.md). |

Findings that look like plugin defects (rather than environment issues) belong in
[porting-plan.md](porting-plan.md) Phase 6 notes or a GitHub issue on the artel repo.
