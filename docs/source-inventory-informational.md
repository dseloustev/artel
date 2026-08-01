# Source inventory — informational skills (reference & how-to)

*Compiled 2026-08-01 from the sibling checkout `../adguard-wallet/.claude/`.*

Everything here is **knowledge, not process**: reference and how-to skills an agent consults
*while* developing — conventions, framework guidance, tool recipes. None of them orchestrate
agents or drive the ticket→PR pipeline. The workflow/task-execution list lives in
[source-inventory-workflow.md](source-inventory-workflow.md). Note: `flutter-inner-loop` is
deliberately *not* here — despite the prefix it is a pipeline verify stage (see the workflow
list).

Decision 2026-08-01 (see [design.md](design.md#decision-log)): these skills seed the
**likbez** companion plugin (separate repo), to be extended later from the internet. The
contracts & operator docs listed at the bottom are the exception — they document the workflow
system and ship with **artel**.

## Architecture & conventions

- **`flutter-architecture`** — Deep architecture contract: MVVM, layered UI/Data/optional
  Domain, feature-first vs layer-first selection, repositories/services/DI, Result & Command
  patterns; for reviews, reports layer violations before style advice.
- **`flutter-apply-architecture-best-practices`** — The official layered-approach primer (lean
  Views, ChangeNotifier ViewModels, Repository/Service data layer, hybrid `lib/data|domain|ui`
  structure) for structuring or refactoring a project.
- **`flutter-conventions`** — The wallet's convention checklist for what DCM/analyzer do *not*
  enforce; loaded by the implementer before coding and by the reviewer's convention lens
  (agents Read it by path since subagents don't inherit skills). Manual/composed only.

## UI & layout

- **`flutter-adaptive-ui`** — Constraint-based adaptive design: compact/medium/expanded
  breakpoints (600/840), `MediaQuery.sizeOf` vs `LayoutBuilder`, state preservation across
  resize/fold, Capability/Policy patterns for platform branching. Ships starter assets.
- **`flutter-build-responsive-layout`** — Narrower layout-technique guide: measuring available
  space, constraints-down/sizes-up, `Expanded`/`Flexible`, lazy `.builder` lists.
- **`flutter-fix-layout-issues`** — Diagnostic map from layout error signatures ("RenderFlex
  overflowed", unbounded viewport height, …) to conditional fixes, verified via hot reload.
- **`flutter-animations`** — Picking the smallest sufficient animation model (implicit /
  explicit / Hero / staggered / physics) with controller lifecycle discipline.
- **`flutter-add-widget-preview`** — How to add `@Preview` widget previews (valid targets,
  MultiPreview, previewer limitations).
- **`flutter-duit-bdui`** — Backend-driven UI with `flutter_duit`: version-first (public API
  changed across majors), `XDriver.remote` vs `.static`, custom widgets and capability
  delegates only when built-ins can't.

## Navigation & routing

- **`flutter-navigation`** — Navigation as user state: choose the smallest routing model
  (`Navigator` for local flows, `go_router` for deep links/web/redirects/shells), model route
  data deliberately, preserve URLs/tab state/back behavior.
- **`flutter-setup-declarative-routing`** — Bootstrap guide for `MaterialApp.router` +
  `go_router` (ShellRoute, URL strategy, platform deep-link setup).

## Networking & serialization

- **`flutter-networking`** — Networking contract: adapt to the project's existing client stack
  (http/Dio/Retrofit/Chopper), typed decoders, explicit timeouts and status handling, services
  own endpoints / repositories own data policy; review lens for leaked clients, unsafe token
  storage, UI-thread parsing.
- **`flutter-use-http-package`** — Narrow how-to for the `http` package: platform
  permissions/entitlements, request execution rules, background parsing.
- **`flutter-implement-json-serialization`** — Manual `fromJson`/`toJson` with `dart:convert`,
  `compute()` offloading for large parses, throw-don't-return-null on HTTP errors.

## Localization

- **`flutter-internationalization`** — The l10n decision guide: gen-l10n by default, ARB
  management, plural/select, RTL, migration off `package:flutter_gen`, generation
  troubleshooting.
- **`flutter-setup-localization`** — The initial bootstrap checklist (`flutter_localizations`
  + `intl`, `generate: true`, `l10n.yaml`, MaterialApp wiring).

## Persistence

- **`flutter-drift`** — Routed Drift-in-Flutter reference: `drift_flutter` setup, type-safe
  tables/queries/streams, `drift_dev make-migrations`, Riverpod/StreamBuilder wiring, per-topic
  reference files.
- **`dart-drift`** — The non-Flutter sibling (CLI/server/desktop): SQLite via
  `drift/native.dart` or PostgreSQL via `drift_postgres`, same "never write Drift from memory,
  route to references" principle.

## Testing & quality

- **`flutter-testing`** — Umbrella testing reference: choose the correct layer (unit / widget
  / integration / plugin-channel mocking), golden and accessibility checks, flakiness, CI
  commands; routes to per-topic references.
- **`flutter-add-widget-test`** — `WidgetTester`/`Finder`/`Matcher` walkthrough with a 9-step
  checklist from `pumpWidget` to a run-fix-rerun loop.
- **`flutter-add-integration-test`** — Setting up `integration_test`, exploring the live app
  via Dart MCP tools, then converting the actions into permanent test files.
- **`dart-add-unit-test`** — Unit-test structure and conventions: `test/` mirroring `lib/`,
  `group()`/`test()`/`expect()`, setUp/tearDown, `dart test` vs `flutter test`.
- **`dart-generate-test-mocks`** — Mockito + build_runner mocks: `@GenerateNiceMocks`,
  stubbing/verifying, `thenAnswer` for async.
- **`dart-collect-coverage`** — Coverage collection and LCOV reports, incl. `coverage:ignore`
  directives.
- **`dart-migrate-to-checks-package`** — Assertion migration table from `package:matcher`
  `expect(...)` to `package:checks` `check(...)`.
- **`dart-run-static-analysis`** — `analysis_options.yaml` configuration, suppression
  mechanisms, `dart analyze` + `dart fix --apply` workflows.

## Runtime & debugging

- **`flutter-runtime`** — The 12 Dart MCP runtime tools and the two connection workflows
  (direct `launch_app` vs VS Code DTD — this project needs the latter because of
  `--dart-define-from-file`); hot reload, widget tree inspection, log reading.
- **`dart-fix-runtime-errors`** — Type-soundness/null-safety knowledge plus the
  locate-fix-verify loop for runtime and analyzer errors.

## Language & tooling

- **`dart-use-pattern-matching`** — Dart 3 pattern-matching idiom: which pattern kind for
  which job, switch statement vs expression.
- **`dart-build-cli-app`** — CLI construction: `bin/` vs `lib/src/`, ArgParser vs
  CommandRunner, POSIX exit codes, CLI integration testing, compilation/distribution.
- **`dart-resolve-package-conflicts`** — Dependency management: single-version rule, caret
  constraints, reading `dart pub outdated`, conflict-resolution workflows.

## Contracts & operator docs (not skills, same informational role)

These documents serve the same "assist the agents" purpose and are cited by section number
throughout the system. **They ship with artel** (they document the workflow), except
`rules/ast-index.md`, which goes to likbez:

- **`docs/autonomous-run.md`** — The machine-facing contract for autonomous runs:
  `run-state.json` schema, question bundling, AFK/HITL tags, capped loops, completion gate,
  Stop-hook interplay, modes, journal, checkpoint commits, phase traversal.
- **`docs/orchestrator-common.md`** — Shared procedures for the two orchestrator skills
  (ticket resolution, phase-aware paths, `.active_ticket` upkeep, skip-if-satisfied gates,
  the never-write-code-yourself rule).
- **`docs/skills-reference.md`** — Lookup table: one entry per skill with Purpose /
  Invocation / Reads / Writes / Pauses / Notes.
- **`docs/workflow-guide.md`** — The narrative operator guide for humans: quickstart table,
  concepts, end-to-end walkthrough, recipes, troubleshooting.
- **`agents/docs/ticket-parsing.md`** — Canonical ticket-ID grammar (`AW-NNNN[-P]`), the
  `specs/.current/{TICKET}/` layout, phase-aware path resolution, refuse-and-ask rule,
  `.active_ticket` format.
- **`agents/docs/deviation-protocol.md`** — How implementation-time divergences are classified
  (Minor/Major), recorded in `implementation-notes.md`, escalated, and verified by review.
- **`agents/docs/path-conventions.md`** — Repo-relative paths only inside `specs/` artifacts
  (they are committed; absolute paths leak local layout).
- **`rules/ast-index.md`** — Policy making `ast-index` the mandatory first tool for code
  search, with a verbatim block to paste into subagent prompts.
