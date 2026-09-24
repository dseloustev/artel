# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`issue-draft` gathers context through a new `issue-scout` agent.** Before the question
  round, a read-only agent (`agents/issue-scout.md`) reads kartoteka (`index_status` 1 ·
  `related` ≤ 5 · `search_knowledge` ≤ 10), the tracker (≤ 6 issues: the tickets the source
  names, their parent and links), Figma (≤ 6 links: real frame names as link labels) and the
  host code (≤ 20 lookups through `docs/code-navigation.md`), and returns a fact sheet — each
  fact `stated` or `inferred`, with its reference. Gaps a stated fact answers are not asked. At
  most eight retrieved facts enter the description, in the team's citation style (bare ticket
  keys in relation sentences, inline code, decisions attributed to a person and date); none
  enter AC; the rest are reported under **Also found**, beside one status line per source.

### Changed

- `issue-draft`'s `--local` now leaves only the host code on: no kartoteka, no tracker, no
  Figma.
- Retrieved facts are paraphrased with attribution instead of quoted verbatim — a deviation
  from `docs/knowledge-consultation.md` §5 declared for `issue-scout`; ⚠ NON-CURRENT still
  travels verbatim and retrieved imperatives never become directives.

### Removed

- The Related section of the `issue-draft` templates: related tickets are cited inline.

## [0.19.0] - 2026-09-24

### Added

- **`issue-draft` drafts tasks, bug reports and epics, each to its own template.** Three
  templates (`skills/issue-draft/assets/templates/{task,bug,epic}.template.md`) follow the team
  Jira formats: a task's description, technical details, Platform/URLs/Figma/Notion table, AC
  and Additional; a bug's one-line problem above a rule, then environment, steps, expected and
  actual result; an epic's user-story table and acceptance criteria. The type comes from the
  new `--type task|bug|epic` flag, else the words of the request, else a defect as the
  source's main subject, else task — an epic is never inferred. Host overrides go per type at
  `.artel/templates/issue-draft-<type>.md`; the single `.artel/templates/issue-draft.md` still
  applies to every type that has none.
- **A `keep` block rule.** Besides `required` and `optional`, a template block can be `keep`:
  always rendered, left empty for the reader when there is nothing to say — the team formats'
  fixed headings and table rows.
- **Jira wiki markup reference** (`skills/issue-draft/references/jira-wiki-markup.md`): bare
  links (`<what it is>: <url>` in cells, several of one kind in one cell), `-` bullets, `#`
  numbering, no blank line inside a list, `| |` empty cells, escaped braces, `----` rules,
  attachment embeds kept verbatim, and an export's own formatting rewritten, never escaped.
- The report carries the description block ready to copy, the Missing Details list and the
  attachments to re-attach.

### Changed

- **Open questions leave the description.** What the question round did not resolve is listed
  under Missing Details in the report; the drafted description holds only the issue.
- **Pasted tracker exports are filtered**: bot and system comments are dropped, `[~user]`
  mentions stay only on contact or reviewer lines, and unfilled template text or a bare `-` /
  `n/a` counts as no data.
- The skill text is English only; drafts stay in `language.pr`.
- The description block header names the type: `=== DESCRIPTION (<type>, <dialect>) ===`.

### Fixed

- **`issue-draft` no longer takes pasted text for a path.** Only a single token with no
  whitespace is a path candidate; any pasted URL used to trigger the "treat as text / stop"
  question. A path question that cannot be answered resolves to stop.
- **Bare numbers are no longer ticket keys.** In free text only `<projectKey>-<digits>` counts;
  build numbers, Figma node ids and dates used to match `ticket.pattern`.
- Expected behaviour is never inferred from a title or as the defect's opposite.

### Removed

- `skills/issue-draft/assets/templates/description.template.md`, the universal template, and
  its `$MISSING` slot.

### Upgrading

- A host override written against the universal template still renders, with two changes:
  move the comment of the trailing `$SOURCE` slot **above** the slot (every slot's rule is now
  the comment before it), and drop the Missing Details section — open questions go to the
  report. To override one type only, save the file as `.artel/templates/issue-draft-<type>.md`.

## [0.18.0] - 2026-09-24

### Added

- **Named quality gates in the runner.** `scripts/verify.py task --files …` runs `verify.fast`
  on the changed paths and a new scoped test command, `verify.test`, on the test files among
  them (`verify.testSurface` selects them). `scripts/verify.py checkpoint --ticket …` runs
  `verify.commands` and, against the baseline the orchestrator records once per run
  (`--record-baseline`, `.artel/run/<TICKET_ID>/verify-baseline.json`), reports only findings
  that are new — so a host whose default branch is already red stops turning every
  checkpoint red, and a red-on-baseline stage no longer hides the test stage behind it.
  `verify.baseline: false` turns the compare off. The contract is `docs/gates.md`. The inner
  loop and the orchestrators call them by name (see *Changed* below).

### Changed

- **The task loop runs the task gate, not the whole-tree gate.** The implementer's inner loop
  runs `verify.fast` on the changed paths and `verify.test` on the tests it touched; the full
  `verify.commands` gate runs at every phase checkpoint, compared against the baseline the run
  records once at arm time, and nowhere else. The three evaluated runs had run the suite per
  task for nothing: it never found a task-owned defect. Contract: `docs/gates.md`.
- **The checkpoint gate is baseline-aware.** Only findings the branch introduced turn it red;
  a stage red on pre-existing findings is journaled as `baseline_red` and no longer hides the
  test stage behind it.
- **The completion gate is the orchestrator's own checklist** of eight facts it already holds;
  `validate` is no longer dispatched. The final gate is the last checkpoint unless a
  `verify.surface` file changed since.
- **Docs run once per ticket**, on the last phase before its checkpoint commit, instead of once
  per phase.
- **The reviewer writes `## PRD acceptance criteria` and `## Manual checks outstanding`**, and
  `pr-description` copies the latter into the PR's QA notes.
- **Fix-section parents close with their last child** in the kartoteka queue (and reopen on
  append), so a finished ticket reads fully done — the section rows used to be permanent
  labels.

### Removed

- **The QA gate** (gate 9, `RELEASE_READY`, `MAX_QA_ROUNDS`): twelve verdicts in twelve were
  positive and no run ever took its fix round. `/artel:qa` stays à la carte.
- **The `## Final Verification` section** of generated tasklists and any planner-written
  "run the gates" task: the gate is the orchestrator's, and the copies drifted. Older tasklists
  still parse and mirror.
- **The `validate` dispatch** at completion — the `validator` agent no longer runs inside the
  pipeline. `/artel:validate` stays à la carte and now reports `CHECKPOINT_OK` instead of
  `RELEASE_READY`.

## [0.17.0] - 2026-09-24

**Requires kartoteka 0.44.0** when `knowledge.adapter` is `"kartoteka"`. Read **Upgrading**
before the first run.

### Added

- **Spec images live in kartoteka too.** With `knowledge.adapter: "kartoteka"`, image files in a
  ticket's trail are uploaded to kartoteka 0.44.0's new attachment store and moved out of the
  trail: `figma-analysis`'s `design/` screenshots, runtime screenshots under `runtime/`, and any
  `*.png`, `*.jpg`, `*.jpeg`, `*.gif` or `*.webp`.
  - **They travel with the documents that embed them.** Another worktree, another machine or a
    teammate gets them too, and the dashboard shows `design-analysis.md` with its images inline.
  - **Producers don't change.** They write images where they always have. Orchestrators sweep them
    in with `spec_store.py image sync` at the end of design analysis, before each checkpoint
    commit, in `pr-create`, and at completion.
  - **Images are never staged on the kartoteka path**, so pull requests stop carrying
    screenshots.
  - **Agents view an image with `spec_store.py image fetch <logical path>`.** It downloads into a
    disposable cache under `.artel/run/<TICKET_ID>/images/`.
  - **An unaddressable or oversized image is `skipped`.** One whose name is outside kartoteka's
    path grammar — for example macOS's default "Screenshot … at ….png", which contains spaces
    — or which is larger than 5 MiB, is neither stored nor committed and stays only in the
    worktree. The final report names it; rename or shrink it, then sweep again.
  - Contract: `docs/spec-storage.md` §4.6.
- **`/artel:migrate-specs` moves committed images in**, with the same guarantees as documents:
  - each image's hash is compared with every stored version;
  - a conflict shows sizes, hashes and both images instead of a diff;
  - a local copy is deleted only once kartoteka verifiably holds it;
  - it also moves images from `save-context` copies (`.artel/context/tickets/<TICKET_ID>/spec-trail/`).

### Changed

- **The storage decision also requires kartoteka's attachment store.** A daemon older than 0.44.0
  is reported as unavailable:
  `the kartoteka daemon predates attachments (0.44.0); upgrade it`.
- **Reading an image's old path is refused with a hint.** On the kartoteka path, when no local
  copy exists at an image's path under the trail, the guard names `image fetch`
  (`hooks/spec_store_guard.py`, now also on `Read`).
- **A failed sweep never pauses a run.** Its images stay local and untracked, are retried at the
  next sweep point, and are listed in the final report with the `image sync` command.
  - In the rare case where an image can be neither verified nor put back (`image sync` exits `2`
    with kind `unrecoverable`), its bytes are set aside as `<cache file>.unverified`.
  - The run still does not pause, and the whole message, naming both paths, goes into the
    journal and the final report.
- **`restore-context` no longer restores images on the kartoteka path.** They are excluded
  alongside the spec documents; both stay in the context store until `/artel:migrate-specs`
  moves them in.
- **OpenCode users re-run `scripts/install-opencode.sh`** to pick up the bridge's new `read`
  hook.

### Upgrading

1. Upgrade kartoteka to 0.44.0, run `kartoteka migrate`, and restart the daemon.
   - Until kartoteka is upgraded, every run on the kartoteka path, and `/artel:migrate-specs`,
     stops at the storage decision with the line above.
   - A 0.44.0 daemon whose database was not migrated refuses to start, which artel reports as
     `kartoteka is unreachable at …`, so run `kartoteka migrate` first.
2. Move trails that have committed images in with `/artel:migrate-specs <TICKET_ID>`, or
   `--all`. A headless run stops at start while a trail still holds committed images, as it does
   for committed documents.

## [0.16.0] - 2026-09-23

**Requires kartoteka 0.43.0** when `knowledge.adapter` is `"kartoteka"`. Read **Upgrading**
before the first run.

### Added

- **kartoteka is the spec store.** With `knowledge.adapter: "kartoteka"`, every spec document —
  `idea.md` through `pr-description.md`, ticket-wide and phase-scoped — is read and written in
  kartoteka's artifact store and nowhere else: nothing under `<specs.dir>`, nothing in a commit
  or a pull request. Agents use the MCP tools (`artifact_get`, `artifact_put`, and kartoteka
  0.43.0's `artifact_patch` for in-place edits such as ticking a box). Scripts receive
  documents by pipe from the new `scripts/spec_store.py`, so a document never passes through an
  orchestrator's context. `.active_ticket` and gate evidence stay on disk. Contract:
  `docs/spec-storage.md`.
- **Nothing is saved locally without asking.** When kartoteka cannot be reached a run asks
  first — Retry / Work locally for this run / Abort. Mid-run it pauses
  (`pause_reason: "store-unavailable"`) and offers to keep an unsaved document locally until
  kartoteka is back; resuming uploads it and removes the copy. Headless runs follow the new
  `specs.onUnavailable` (`"abort"` by default, or `"local"`).
- **`/artel:migrate-specs`** moves local trails in — one ticket, several, or `--all`, including
  `save-context`'s copies. Each file's hash is compared with every stored version: missing and
  newer documents are uploaded, a stale copy never overwrites a newer stored one, and a real
  conflict shows its diff and asks keep local / keep stored / skip. Local copies are deleted only
  after kartoteka verifiably holds them, after one confirmation, with `git rm` and an optional
  single commit. `.active_ticket`, evidence and release documents are never deleted.
- **A guard enforces it.** `hooks/spec_store_guard.py` (`PreToolUse` on `Edit|Write|MultiEdit`,
  and the OpenCode bridge) refuses a spec-document file write while kartoteka is the store, and
  names the call to use instead.
- The session's host status gains a `spec store:` line.

### Changed

- **`knowledge.adapter: "kartoteka"` now means kartoteka holds the spec trail.** The mirror hook
  remains for the files path (`--local`, or an approved local run), best-effort as before.
- `tasklist_tasks.py --tasklist -` and `plan_check.py --plan -` read stdin.
- The review-round reset stores a round-0 version instead of deleting `review.md`; earlier
  rounds are the document's previous versions in kartoteka.
- The planning checkpoint skips its commit when only `.active_ticket` changed.
- The kartoteka HTTP client is shared by the mirror hook, the guard and `spec_store.py`
  (`hooks/kartoteka_http.py`).

### Upgrading

- Upgrade the daemon to **kartoteka 0.43.0** and restart it.
- Tickets with a trail on disk: run `/artel:migrate-specs --all` (or per ticket) interactively.
  A run that finds a local trail asks (interactive) or stops (headless).
- Scripts reach kartoteka over HTTP: with `[auth]` on, `knowledge.tokenEnv` must name a
  CLI-minted token (`kartoteka token add`), even where the MCP session signs in with GitHub.
- Headless allowlists need `python3 <plugin-root>/scripts/spec_store.py *`.
- Recommended on the daemon: leave the `tasklist` stage out of `[workspace] index_stages` —
  every ticked box is a version, and each indexed version costs a `[contextualize]` call.

## [0.15.0] - 2026-09-19

### Added

- **Fix-section tasks are recorded in the task queue.** `## Code Review Fixes`, `## Runtime
  Fixes`, `## Verify Fixes` and `## Final Verification` now become kartoteka rows: one parent
  per section (`CRF: Code Review Fixes`, `RTF: Runtime Fixes`, `VF: Verify Fixes`,
  `FV: Final Verification`) and a child per checkbox titled `<CODE> · <source> · <checkbox
  text>`. `/artel:tasks list` shows review, runtime and verify fixes moving `backlog` →
  `in_progress` → `done` instead of reporting a drained queue while hours of fix work run.
  The rows are recorded, never offered: always `backlog` or `done` when mirrored, never
  `ready`, moved by `task_update` alone, so `task_ready` still hands out iteration work only
  and phase-scoped claiming is untouched. The implementer still finds its task by file scan
  and keeps the row current; a missing row is `row not found; file only`, never an error.
  Every writer records its append before the first fix is dispatched: `run-reviewer` (phase
  and per-task review), `deep-review` Step 6 (which used to skip the mirror), the runtime gate
  and the phase checkpoint in `dev` and `feature-development`, and the generation mirror for
  Final Verification. Each batch opens with a `### <source>` heading (`review-r2`,
  `task-gate-007`, `deep-review-2026-09-18`, `runtime-p1-r1`, `checkpoint-r1`,
  `manual-2026-09-19`), because titles are identity: a re-appended task matching an old `done`
  row would come back `done`. The parser warns on a repeated fix title within the file, and the
  mirror warns on an open box that resolves to a `done` row. On a phase-scoped run the phase
  file's fix sections are mirrored too. `--local`, `knowledge.adapter: none` and missing tools
  write no rows: the file alone carries the work. Contract: `docs/task-queue.md` §2, §3, §5,
  §6.
- **`scripts/tasklist_tasks.py` emits `data.sections`** after `data.iterations`. It accepts
  checkboxes directly under the `##` heading (source `tasklist`), keeps nested acceptance
  criteria out of the title and in the description, and parses a file that has fix sections
  but no iterations, such as a deep-review-only tasklist or a phase file. A file that lost its
  iterations still exits 2 `tasklist_malformed` and names why: an `## Iteration`/`## Phase`
  heading that does not parse, or a `## Final Verification` section with no iteration beside it
  outside a phase file. A tasklist with no fix section prints exactly the 0.14.0 output.
- **`/artel:tasks add … --fix CRF|RTF|VF|FV`** adds a task to a fix section, which the skill
  could not do before, under a `### manual-<date>` heading, and records it.
- **`/artel:run-reviewer --local`** and **`/artel:tasklist --local`**, which
  `feature-development` passes on, so a local-only run writes no rows.

### Changed

- **`feature-development` hands `--local` to every step that could write a row.** Gate 4's
  `tasklist` and every implementer dispatch — the review, runtime, QA and checkpoint fix
  rounds and the per-task review's round, not only gate 5's main loop — now carry it. In
  0.14.0 a local-only run still mirrored its iteration rows at gate 4.
- **The empty-queue diagnosis reads iteration children only.** Open fix rows no longer make
  a finished ticket look stalled or send the implementer into a promotion repair with nothing
  to promote. `/artel:tasks list` counts fix rows toward **blocked** and **held** (with no
  holder, since only a claim sets one), never toward **promotion pending** or **drained**, and
  adds a **fix work open** line. `release` refuses a fix row, because releasing sets `ready`.
  `done` finds a fix row's checkbox in `tasklist.md` or a phase file.

### Upgrading

- **Existing tickets' fix tasks take their source from the file as it stands.** A fix task with
  no `### <source>` heading above it in its section gets the source `tasklist`. A legacy `###`
  heading inside a fix section, such as `### Blocking` or `### Tasks`, becomes the source of the
  tasks under it. Nothing needs editing for the next mirror to record them.
- **Rows made by hand before 0.15.0 under other titles are not adopted.** A re-mirror creates
  new rows beside them, because titles are the identity. Close the old ones with
  `/artel:tasks done` or `block`, or give the batch a `### <source>` heading that matches the
  hand-made titles.

## [0.14.0] - 2026-09-17

### Added

- **Worktrees: one ticket, one worktree, one session.** `/artel:move-to-worktree [ticket-id]`
  moves a ticket that is already on its branch into `.claude/worktrees/<name>` and continues the
  session there (`EnterWorktree`), so other sessions can work on other tickets in the main
  checkout at the same time. `/artel:return-from-worktree [ticket-id]` hands the branch back to
  the main checkout and removes the worktree; the branch is kept. Uncommitted work, untracked
  files included, travels by `git stash` and is dropped only after it applied. Artel's config is
  copied, the context store is shared through a symlink, the ticket's run state moves with it,
  hook baselines and stop-block counters are copied newest-wins, and other ignored files follow
  `.worktreeinclude` (default: `.claude/` and `.mcp.json`). Dependencies and generated code are
  never copied: `setup.commands` rebuilds them in whichever checkout the work lands in. Neither
  skill commits, pushes, merges, deletes a branch or uses `--force`, and both ask once before
  anything moves. Hand-back refuses while the main checkout has uncommitted changes, which may
  belong to another session, and an interrupted hand-back finishes when re-run. The mechanics
  are `scripts/worktree.py` (`move-in`, `hand-back [--check]`), which prints one JSON status:
  `ok`, `refused`, `conflict`, `rolled-back` or `error`. A `conflict` or `error` names the kept
  stash, so `git stash apply <stash>` restores the work. The contract is `docs/worktrees.md`. On
  OpenCode, which has no `EnterWorktree`, the skills print `cd <path> && opencode` instead.
  `tests/test_worktree.py` drives the script against throwaway repositories, and
  `tests/test_worktree_docs.py` pins the contract across the skills.

### Changed

- **`init-branch` offers the worktree.** Alongside its branch question it asks whether to work
  here or move to `.claude/worktrees/<name>`. On the stay route (the branch already carries the
  ticket) it asks that question alone. The move runs right after the branch step and before
  `setup.commands`, so dependencies land in the checkout that will use them. A ticket branch
  already checked out in a linked worktree is offered as **Enter worktree `<path>`**, since git
  refuses a second checkout of it. Inside a linked worktree the question is skipped.
- **Operator docs cover worktrees.** `docs/workflow-guide.md` gains a parallel-tickets recipe and
  a `conflict` troubleshooting row. Its list of what writes to the repo now names the worktree
  skills, and its hook inventory now includes the VCS guard, which shipped in 0.13.0.
  `docs/config.md` documents `.artel/` inside a worktree, `.worktreeinclude` and the new
  `setup.commands` consumers. `docs/design.md` records the worktree decision, and its Open
  follow-ups section gains the pending live smoke test, a ticket lock and a worktree listing.
- **The release tag follows the notes.** `docs/design.md` records why `v0.13.0` sits on the
  merge rather than on its `chore(release)` commit. The repo-local `bump-version` skill states
  the rule: the tag points at the tree its version's CHANGELOG section describes.

### Fixed

- **Hooks gate the worktree the session works in.** `hooks.json` starts every hook in
  `$CLAUDE_PROJECT_DIR`, which stays on the main checkout after a session enters a linked
  worktree. The gates therefore read the main checkout's `.artel/` and verified its files while
  the session edited the worktree. The stop gates, fast verify and the sensitive-path guard were
  all affected, and so were plain `claude -w` sessions in an artel host. `hook_common.read_hook_input()` now moves the
  process into the linked worktree that the payload's `cwd` names, but only when that worktree
  belongs to the same repository. Otherwise the hook stays where it started. Every hook reads its
  input before touching `.artel/` (`tests/test_hook_common.py` pins the order). The one exception
  is `using_artel.py`, which reads no input. `relpath_from_tool_input()` now strips the directory
  the hook moved into, so a `cwd` that is a subdirectory of the repo root also resolves
  correctly. A worktree without `.artel/config.json` leaves the hooks inert.

## [0.13.0] - 2026-09-16

### Added

- **The configured VCS platform is now enforced.** A new always-armed `PreToolUse` hook,
  `hooks/vcs_guard.py`, denies any call that writes to a platform other than the one
  `vcs.adapter` (pull requests) or `tracker.adapter` (issues) declares — so a project moved to
  GitHub cannot post a comment or open a PR on Bitbucket, even if a skill body is wrong. Domain
  is derived from the platform (Bitbucket → `vcs.adapter`, Jira → `tracker.adapter`, GitHub →
  both, since it hosts both), not from the tool's name. Foreign *reads* stay allowed, which is
  what lets the new `migrate-prs` skill read the Bitbucket PRs it recreates. An unrecognized verb
  is denied; `guard.extraReadTools` in `.artel/config.json` rescues an unrecognized verb only,
  never a recognized write. `tracker.adapter: "none"` leaves the tracker domain unenforced — no
  declared home means nothing to protect, while an adapter value that is declared but
  *unrecognized* keeps its domain enforced rather than silently unguarding it. The perimeter:
  the `gh` subcommands over pull requests, repos, releases, aliases and `api` (plus `gh issue`
  for the tracker domain), and tools whose **name** carries a platform token — not `git push`,
  not other `gh` subcommands, not direct HTTP. `gh api` is judged on its resolved HTTP method,
  by `gh`'s own rule: GET by default, POST as soon as a parameter is added.
- **`/artel:set-home <repo-url>`** moves a project between platforms: it rewrites `vcs.*` and
  re-points the git remotes together, renaming the old `origin` rather than replacing its URL so
  the open PRs' source branches stay fetchable, and refreshing `refs/remotes/origin/HEAD` (which
  `pr-create` and the reviewer agent use to resolve the default branch). With no argument it
  reports the current home and stops.
- **`/artel:migrate-prs [pr-id ...]`** recreates a Bitbucket project's still-open pull requests
  on GitHub, carrying title, description and branches plus a provenance line. Re-runnable: an
  existing GitHub PR for the branch is reported and skipped. It never writes to Bitbucket, and
  migrates none of review comments, reviewer assignments or PR state — the old PRs are declined
  by hand.

## [0.12.2] - 2026-09-15

### Changed

- **Reviewed against kartoteka 0.33.0–0.35.0: nothing in artel changes.** None of the three
  moves an MCP signature, an HTTP route, a config key or the index identity. 0.33.0 needs
  `kartoteka migrate` (an index on `chunks(project, doc_id)`), 0.34.0 adds an opt-in serve log
  file, and 0.35.0 indexes a workspace artifact without its leading YAML frontmatter block, which
  artel does not write. `docs/kartoteka-requirements.md` is re-stamped against 0.35.0; §3.1
  (`TICKET_KEY`) is still its one open item.
- **`docs/design.md` records the OKF decision and the frontmatter follow-up.** The 2026-09-15
  review that declined the Open Knowledge Format for the spec trail lived only in a gitignored
  prompt file; it is now in the decision log. "Open follow-ups" gains spec-trail frontmatter,
  unblocked by kartoteka 0.35.0, with the two constraints any adoption inherits: a daemon at
  0.35.0 or later before the mirror hook posts the first block, and LF line endings with a
  closing `---` line.

## [0.12.1] - 2026-09-12

### Fixed

- **`init-branch` no longer creates a branch on every run.** It never looked at the current
  branch and matched existing branches by exact name only, so running it on
  `feature/PROJ-2872-adding-accounts` created a second branch, `feature/PROJ-2872`, off the default
  branch and switched to it. It now reads the current branch first: a name carrying this ticket's
  ID (`<ticket.projectKey>-\d+`, the token `pr-create` and `address-pr-comment` already scan for)
  is used as-is with no git change; a name carrying a different ticket's ID stops the skill; a
  name carrying none prompts once — check out an existing branch for the ticket, create one, or
  stay. New branches follow the `feature/<TICKET_ID>[-<N>]-<slug>` shape, the slug coming from the
  tracker summary or the `idea.md` title as short English kebab-case. Declining keeps the current
  branch and runs the rest of the setup there.

## [0.12.0] - 2026-09-06

### Added

- **CI: the suite runs on every push and pull request.** `.github/workflows/tests.yml` runs
  `python3 -m unittest discover -s tests` on Python 3.9, 3.11 and 3.13 — 3.9 being the floor the
  hooks are written to (`hooks/sensitive_guard.py` says so in as many words). Nothing is
  installed and nothing is cached, because the hooks, the gate scripts and the suite are
  stdlib-only by design. Until now the suite ran only when someone cut a release, since
  `bump-version`'s preflight was the one thing that invoked it.
- **The five gate hooks have tests.** `stop_gate`, `verify_stop_gate`, `sensitive_guard`,
  `session_baseline` and `fast_verify_post_edit` — the components that decide whether a run may
  stop and whether a file may be written — had no coverage at all; only `hook_common`,
  `knowledge_mirror` and `using_artel` did. 72 new tests take the suite from 356 to 428 and pin
  the behaviour that is easiest to regress silently: the stop gate's block counter and its
  five allow routes, the verify gate's **latch** at the cap (resetting there re-arms a
  block/pass cycle that can phase-lock against the orchestrator gate and block indefinitely),
  the baseline's refusal to write an empty baseline on an environment error (an empty one makes
  every pre-existing finding look new), the guard's disarm-when-stale rule and the shipped
  `sensitive-paths.json` categories matching the paths they name, and the post-edit hook's
  silence on everything except a red gate.

### Changed

- **`docs/kartoteka-requirements.md` records what has since shipped.** Written 2026-08-31
  against kartoteka 0.27.x, it still described every requirement as open. All of §1 and §2 have
  landed — `parent_id` scoping and `order="created"` in 0.28.0, `expected_version` and
  `artifact_versions`/`artifact_redact` in 0.30.0, per-project namespacing in 0.31.0,
  authentication in 0.32.0 — and so has all of §4. Only §3.1 (`TICKET_KEY`) is still open, and
  it stays conditional. Each subsection carries a **Shipped —** paragraph, the summary table
  gains a state column, and two items are marked shipped-but-unadopted: artel still claims
  unscoped and sorts plan order client-side.
- **`docs/design.md` gains an "Open follow-ups" section.** `docs/superpowers/` is gitignored, so
  a follow-up parked in a spec's "Open questions" left no trace in the repository — several had
  already gone invisible. The section collects them: the non-Dart Phase 6 dry run, store mode
  designed but unbuilt, kartoteka 0.28.0's unused queue parameters, `issue-draft` calibration,
  `deep-review`'s placeholder forecast constants, the unpublished GitHub releases, and the
  provisional license holder.
- **README describes the shipped reality.** The status block said "ported, pre-publish … until
  the repo is public, install from a local checkout" and the install section was headed "once
  published"; the repo has been public and installable through the marketplace path for eleven
  releases. The agent row said "the crew of 12" and omitted `review-forecaster`, which shipped
  in 0.8.0.
- **`docs/task-queue.md` no longer explains a limitation that was lifted.** It said siblings are
  found by title prefix "because `task_list` does not render the parent" — kartoteka 0.28.0
  renders `· parent: #N`. The prefix convention stays; the reason for it is now recorded as a
  choice with an open follow-up rather than a workaround.

### Fixed

- **An untracked (`tracker.adapter: "none"`) pipeline run no longer dies at the vision gate.**
  `feature-development`'s gate 0 invoked `generate-idea` only when a tracker was configured;
  under `"none"` it relied on a `$1` description file or a pre-existing `idea.md` and never
  wrote one. Gate 2 (`generate-vision`) hard-requires `idea.md`, so an untracked run failed with
  `Error: idea file not found` — with or without a description file — unless the operator had run
  `/artel:generate-idea` by hand first. Gate 0 now invokes `generate-idea` under every adapter,
  passing `$0 $1`; the skill already branches on `tracker.adapter` internally, and its
  skip-if-exists preflight leaves resume semantics unchanged. `docs/testing-flutter.md` drops the
  manual pre-seed workaround it carried.

## [0.11.0] - 2026-09-05

### Added

- **`knowledge.tokenEnv`: artel can talk to a kartoteka daemon that requires a bearer token.**
  kartoteka 0.32.0 (E3 phase 1) lets a daemon leave loopback and, with `[auth] enabled = true`,
  refuses every request without `Authorization: Bearer ktk_…`, reads included. The new key names
  the environment variable holding the token — never the value, the config being committed —
  and the mirror hook sends the header from it. A `401` is logged as `reject` when a token was
  sent (revoked or expired; `kartoteka token list` on the daemon host) and as `misconfigured`,
  naming the empty key or the unset variable, when none was; the token never reaches the log. A
  named variable that is unset sends the request unauthenticated, so one committed config serves
  an auth-off loopback daemon and a hosted one; a value that cannot travel in a header, or a
  plaintext `http://` `baseUrl` off loopback while a token is present, is `misconfigured` by
  name and nothing is sent. The `using-artel` host status gains a
  `knowledge.tokenEnv` line (set or not, never the value). The MCP session takes the same
  variable through the client's own expansion — `${KARTOTEKA_TOKEN}` in `.mcp.json`,
  `{env:KARTOTEKA_TOKEN}` in `opencode.json` — documented in config.md and docs/opencode.md;
  `/artel:setup` asks for the name in Round 5 and validates it. Default empty: a daemon with
  kartoteka's defaults is byte-identical to 0.31.0 and nothing changes for it.
  `tests/test_knowledge_mirror.py` pins the header and the two 401 classifications,
  `tests/test_using_artel_hook.py` the status line, and `tests/test_kartoteka_project_docs.py`
  the spellings across the docs.

### Changed

- `docs/kartoteka-requirements.md` §2.1 records that kartoteka 0.32.0 shipped the authentication
  it asked for, with the verified principal recorded beside `author_agent`/`actor` rather than in
  their place. `hooks/README.md`, skills-reference.md and workflow-guide.md list the new key, and
  the mirror hook's trust-boundary comment now covers a hosted `https://` daemon.

## [0.10.0] - 2026-09-04

### Added

- **`issue-draft` drafts against a template, consults kartoteka, and asks once before it
  writes.** The description now follows one self-describing template
  (`skills/issue-draft/assets/templates/description.template.md`; each section's
  `<!-- required|optional … -->` comment is its rule), which a host overrides wholesale at
  `.artel/templates/issue-draft.md`. Before writing, the skill consults the
  institutional-knowledge index under `docs/knowledge-consultation.md` — `index_status`,
  `related` when the source names a ticket key, at most four scoped `search_knowledge` — to
  close gaps from the project's own record, quoted and attributed, with `⚠ NON-CURRENT` hits
  closing nothing and at most five cited hits under a new Related section; then asks the four
  highest-ranked remaining gaps in one `AskUserQuestion` round and lists what it did not ask
  or was not told under Missing Details. `--local` skips the consultation, as everywhere
  else. New optional Acceptance Criteria section, filled only from the source or the answers.
  The report carries a per-section provenance table and the kartoteka footer.
  `tests/test_issue_draft_docs.py` pins the template convention, the call shapes and the
  override path; the skill joins `tests/test_knowledge_consultation_docs.py`'s consulting
  list.

### Changed

- `issue-draft` gains the frontmatter hint `<text | file-path> [--local]`; skills-reference.md,
  config.md (`.artel/templates/issue-draft.md`; `issue-draft` in the `knowledge.*` Consumed-by
  cells) and knowledge-consultation.md (the skill as a consumer with its two declared
  deviations) follow.

### Removed

- **`issue-draft` no longer reads Slack exports or produces variants.** Input is inline text
  or a local `.txt`/`.md` file — the skill reads only what its argument names — and every
  invocation yields one draft. The multi-variant mode existed for a pick-one modal the skill
  never had.

## [0.9.1] - 2026-09-04

### Fixed

- **The task-queue `actor` was a guess.** `agents/implementer.md` and `docs/task-queue.md`
  spelled the claim as `actor="artel@<hostname>"` and named no command to fill the
  placeholder, so the implementer composed a hostname from nothing: one ticket's queue
  carried rows held by three machines, two of which do not exist, and `/artel:tasks list`
  reported them as holders. Both documents now say `<hostname>` is the output of
  `hostname -s`, run in the dispatch rather than recalled; `tests/test_task_queue_docs.py`
  pins the command beside the placeholder in each.

## [0.9.0] - 2026-09-04

**Breaking: `knowledge.project` is required whenever `knowledge.adapter` is `"kartoteka"`.**
kartoteka 0.31.0 namespaces its store and index by project so one daemon can serve several
repositories out of one database, and it now refuses every write that names no project —
the mirror hook's `POST /api/artifacts` answered `422` and was logged as a `reject`,
`task_create` mirrored nothing, `task_ready` handed out no work — and `related()` takes the
project as its first, required parameter. This release is artel's half of that coordinated
change. Add the key beside `adapter` and `baseUrl` (`/artel:setup` now asks for it),
lowercase kebab-case, and register it once in the daemon's database with
`kartoteka project add <name>`: an unregistered name is refused rather than created.

### Added

- **`knowledge.project`** (`docs/config.md`): the kartoteka project this repository's trail,
  queue and consultations belong to. No default, deliberately — kartoteka removed its own
  because a guessed project appends to another project's deliberation trail. The
  `SessionStart` status line shows it beside the URL, and shouts `project NOT SET` when the
  adapter is on and the key is missing.

### Changed

- **Every kartoteka call names the project.** The mirror hook sends it in the request body;
  `task_create` and `task_ready` carry it; `related(<project>, <ticket>)` leads with it; and
  the reads — `search_knowledge`, `index_status`, `task_list`, `artifact_list`,
  `artifact_get` — are scoped to it, so a daemon serving several projects answers for this
  one. That scoping replaces the "kartoteka is single-project" reasoning the docs carried.
- **A missing or malformed project is a configuration error resolved before the gating
  tables**, spelled the same way in `docs/knowledge-consultation.md`, `docs/task-queue.md`
  and `docs/review-forecast.md`: the agents consult nothing and record
  `kartoteka is configured for this project but knowledge.project is not set`, the queue
  takes its fallback path with the same record, the forecast's mode is `off:` with that
  reason, the `knowledge` and `tasks` skills stop with a pointer to `/artel:setup`, and the
  hook logs one `misconfigured` line per mirrorable edit and sends nothing.
- **An unregistered project is recorded distinctly.** The unscoped `index_status()` every
  consultation opens with walks the daemon's registry, so a project it does not list is
  recorded as such rather than as *nothing filed* (a scoped read answers an unregistered
  project with silent zeros). The implementer treats a `Rejected:` naming
  `kartoteka project add` as a fallback trigger and records
  `kartoteka refused knowledge.project as unregistered; continued from tasklist.md`; the
  hook's existing `reject` line already carries kartoteka's message.

## [0.8.0] - 2026-09-02

### Changed

- **`deep-review` is one reviewer pass plus a kartoteka-grounded forecast, in one file.** The
  dual-review flow (two independent `reviewer` dispatches, a merged `review-summary.md` with a
  QA plan, then plan mode) is gone. The skill now dispatches the `reviewer` once, then the new
  `review-forecaster` agent, which groups the branch's diff into change units, looks up
  precedents for each in kartoteka's pull-request and review-thread documents, classifies how
  the reviewer reacted, and writes `<specs.dir>/<TICKET_ID>/deep-review.md`: the reviewer's
  comments verbatim, a table of definite issues, a table of the remaining changes with a pass
  percentage and cited precedents, proposed fixes for changes under the threshold, and a
  record of what was consulted. The forecaster always runs — with `--local`, an adapter other
  than `kartoteka`, or the MCP tools absent, the file still carries the comments and the
  definite issues, and its `Forecast:` line says why. The skill then asks which fixes to apply
  and works them through `## Code Review Fixes` and `Skill: implementer`, not plan mode. The
  contract is `docs/review-forecast.md`; new config keys `review.forecast.threshold` (default
  `70`) and `review.forecast.reviewers` (default empty). `review-claude.md`, `review-second.md`
  and `review-summary.md` are no longer written; the reviewer's report for a deep-review run
  is run-state evidence at `.artel/run/<TICKET_ID>/reports/deep-review-findings.md`, and the
  mirror hook sends `deep-review.md` in place of `review-summary.md`.

## [0.7.2] - 2026-08-28

### Fixed

- **`remove-automation` could leave the scaffold's directory behind.** The skill learned what
  the host's `runtime.scaffold.remove` changed from `git status`, which lists files and never
  directories, and took the command's exit `0` as proof the scaffold was gone. A host command
  that deletes the entrypoint but not its directory — or whose `rmdir` fails because an editor
  or Finder dropped an ignored file there — left the directory on disk under a clean
  `git status`, with a removal commit that still contained "exactly those paths". The skill now
  cross-checks the tree against the paths the `chore: enable agent UI automation` commit added
  (deleting a survivor itself only when it is byte-identical to what the scaffold introduced;
  otherwise stop-and-report) and sweeps the directories the scaffold created for itself once
  their files are gone, removing them when empty or holding only git-ignored entries and
  stopping on untracked work. `/artel:drive-app`'s `drive-observation.md` is spec-trail
  evidence and is deliberately not touched.

## [0.7.1] - 2026-08-26

### Fixed

- **`agents/README.md` was loaded as an agent.** Claude Code registers every `.md` directly
  under `agents/` as an agent, so the directory's own README reached the picker as an agent
  named `README` — no frontmatter, hence an empty description and "All tools" — and was
  offered to the model as a real dispatch target. Present since the first scaffold commit.
  The prose moved to `docs/agents.md`; `agents/` is now definitions only, enforced by the new
  `tests/test_plugin_surface.py` (every `.md` there must carry agent frontmatter whose `name`
  matches its filename). The OpenCode generator already skipped it — only the Claude loader
  did not.
- **OpenCode: the idle-block counter never reset.** `idleBlocks` was incremented on every
  stop-gate block but cleared nowhere: `session.deleted` dropped `routerCache` and
  `childSessions` and left it behind (a small leak), and a *clean* stop did not reset it. In
  a long-lived TUI process, blocks from unrelated stops accumulated until
  `MAX_IDLE_BLOCKS` (10) retired the stop gate for the rest of the session. The counter is
  now consecutive: cleared when both gates pass, and dropped with the session.
- **OpenCode: the installer deleted skills it did not install.** Refresh and `--remove` ran
  `rm -rf "$OC/skills"/artel-*`, which takes any skill of the user's named `artel-…`.
  Both paths now prune exactly the paths recorded in `~/.config/opencode/.artel-install-manifest`
  (so upstream-retired skills are still cleaned); `--remove` on a pre-manifest install falls
  back to the name sweep and announces it. The `cp …/agents/*.md` and `…/commands/*.md` copies
  are also empty-glob safe — an unmatched glob was a `set -euo pipefail` abort.

### Changed

- The OpenCode bridge's `tool.execute.after` order — `knowledge_mirror.py` then
  `fast_verify_post_edit.py`, the reverse of `hooks.json` — is now documented as deliberate at
  both the header map and the call site, and in `docs/opencode.md`: findings leave the handler
  by throwing, which would skip a mirror queued behind them.
- `docs/skills-reference.md`: the OpenCode naming note no longer splits the preamble's bullet
  list in two.

## [0.7.0] - 2026-08-26

### Added

- OpenCode host support. `scripts/build_opencode.py` generates `artel-`-prefixed skills,
  agents and command wrappers (host glossary prepended, install root baked);
  `opencode/plugin/artel.ts` bridges OpenCode's plugin events onto the existing Python
  hooks — edit guard, fast verify, knowledge mirror, session baseline, router injection,
  and the stop gate as an idle re-prompt; `scripts/install-opencode.sh`
  installs/uninstalls into `~/.config/opencode/`. Claude Code operation is unchanged —
  no hook, skill or agent behavior changed: canonical skills, agents and hook code are
  byte-identical. See `docs/opencode.md`.
- Install verification guide (`docs/opencode.md` §Verifying an install) and bridge
  hardening from the final review: failed router-hook runs retry instead of latching
  (diagnostic mirrored to the app log), spawn stdin errors are swallowed, session
  caches clear on compaction/deletion, and generated agent names get the same
  legality guard as skill names.

## [0.6.0] - 2026-08-25

### Added

- **Opt-in per-task review — `review.perTask` (default `false`).** With it on, both
  orchestrators gate every iteration task on its own diff before dispatching the next one
  (`docs/autonomous-run.md` §16): `scripts/review_package.py snapshot` before the implementer,
  `diff` after it, `run-reviewer --task` on the package. The `reviewer` agent's new **task**
  mode grades the diff against that task's acceptance criteria and the implementer's report,
  writes `NNN-<slug>-review.md`, and appends Blocking / Important findings under
  `## Code Review Fixes` — the section the phase review already uses — for one fix round
  (`MAX_TASK_REVIEW_ROUNDS = 1`, counted toward `correction_rounds`); anything left open is the
  phase review's. Task mode never writes `review.md`, bumps the round or runs the lenses.
- **`scripts/review_package.py`.** The implementer does not commit, so a task has no
  `BASE..HEAD`; the script snapshots the working tree (tracked + untracked, `.gitignore`
  honoured) through a temporary index — the real index, HEAD and the checkpoint's explicit
  staging are untouched — and writes a `# Review package` file (stat + diff, ten lines of
  context), printing one line with the file count so the diff never enters the caller's
  context. Exit 2 outside a repo or on an unknown base.

### Changed

- **The implementer's completion is a short contract; the diff goes to a report file.** The
  agent used to return "files changed (with the actual diff)", so every task's diff stayed in
  the orchestrator's context for the rest of the run. It now writes
  `.artel/run/<TICKET_ID>/reports/NNN-<slug>.md` (approach, diff, verify evidence, queue claim,
  deviations in full) and returns under ten lines: task, changed paths, `Report:`,
  `Verify iterations:`, `Deviations:`. Orchestrators journal the path and never open the file.
  New principle in `autonomous-run.md` §1: "Bulk stays in files."
- **`implementer` and `reviewer` agents never spawn subagents.** Review is the orchestrator's
  seat; a worker-spawned reviewer duplicates it at full cost and its verdict counts for
  nothing. The reviewer is also explicitly read-only on the checkout.
- `run-reviewer` accepts `--task "<title>" --report <path> --package <path>`; all three are
  required together and there is no fallback to the ticket review. `setup` offers
  `review.perTask` among the Round-4 extras and validates it as a boolean.

### Unchanged (deliberately)

- Superpowers' subagent-driven development is not adopted as a mode — artel already is one
  (`docs/design.md`, decision log 2026-08-25). Its "rulings, not stalls" rule, same-shape task
  batching, per-dispatch model tiering and brief-file extraction stay out.

## [0.5.0] - 2026-08-25

### Added

- **`docs/code-navigation.md` — the query-side contract for a code-symbol index.** Six agents
  already reached for "the host's optional code-symbol index" and all of them pointed at
  `orchestrator-common.md` §1, which documents only the post-implementation *refresh* hook —
  nothing about reading an index. The new file is that contract, shaped like
  `knowledge-consultation.md`: §1 availability (`command -v`, silently absent, never a
  config key — an index is project-agnostic), §2 the reference implementation and its command
  table, §3 index-before-grep and the literal/regex/comment exceptions, §4 staleness with one
  update-and-retry, §5 the grounding rule the `PLAN_GROUNDED` gate and the deviation protocol
  rest on. It is the second sanctioned exception to the genericization rule and narrower than
  the router's: only §2 names `ast-index`, every agent keeps the generic phrasing and cites the
  file.

### Changed

- **Five more agents and skills now navigate code through the index.** `reviewer` resolves a
  diff's symbols before judging it (`changed --base`, `usages`, `implementations`, `callers` —
  a diff shows changed lines, not what depends on them); `tech-writer` derives key code changes
  instead of grepping for them; `vision-writer` grounds every cited path and uses the index to
  find what to reuse; `agents-md-generator` discovers repo shape, conventions and module
  dependencies from `map` / `conventions` / `deps` rather than a directory crawl;
  `merge-conflicts` Phase 3 locates symbols that moved between the two sides. `analyst`,
  `researcher`, `planner`, `implementer` and `figma-analyst` keep their wording and re-point
  from `orchestrator-common.md` §1 to the new contract.
- `orchestrator-common.md` §1 now says which half it owns: refreshing is the host hook,
  querying is `code-navigation.md`.

### Unchanged (deliberately)

- `qa`, `validator` and `task-planner` read artifacts, not code. `merge-conflicts` Phase 5 keeps
  its Grep — a conflict marker is a string literal, which §3 makes the worked example of when
  *not* to reach for the index.

## [0.4.0] - 2026-08-25

### Added

- **`using-artel`, the session router.** A `SessionStart` hook (`startup|clear|compact`)
  injects a routing table over every skill — plus `knowledge.adapter`, the active ticket and
  whether the `ast-index` CLI is on PATH —
  whenever `.artel/config.json` exists, so "start work on PROJ-123" reaches
  `/artel:init-branch` on turn one and survives compaction. Carries `<SUBAGENT-STOP>`; lists no
  agents; tells the model an entry point is already the process. Inert without a config. With
  the `ast-index` CLI present, code navigation routes to the `ast-index` plugin's skill before
  any grep.
- **`/artel:knowledge` and `/artel:tasks`, the conversational front doors to kartoteka.**
  `knowledge` searches prior decisions, lists what is filed under a ticket, or reports index
  freshness — read-only, under the consultation contract's budget and citation rules. `tasks`
  lists and diagnoses a ticket's queue, adds a task by appending to `tasklist.md` and running
  the existing mirror (so the row carries its iteration prefix, parent and queue order), marks
  done or blocked, and releases a held task after confirmation. Both refuse — with a pointer to
  `/artel:setup` — when `knowledge.adapter` is not `kartoteka`. Neither claims.

## [0.3.1] - 2026-08-24

### Fixed

- **The automation skills' file-list and verify steps.** `add-automation` /
  `remove-automation` derive the scaffold's paths from `git status --porcelain`,
  which collapses a new directory into one `?? dir/` entry — breaking the
  rollback (`rm -f` refuses a directory), the commit's exact-paths check, and any
  `{files}` scope; both now read `--porcelain -uall -z`. Both also ran
  `verify.fast` as a raw string, so a config carrying `{files}` sent the literal
  token to the shell and failed a correct scaffold; they now substitute their own
  changed paths. `add-automation` no longer claims a failed apply leaves nothing
  to roll back, and `remove-automation` gained the default-branch guard its
  counterpart already had, since it commits and pushes.

## [0.3.0] - 2026-08-23

### Added

- **Task queue integration with kartoteka.** `tasklist.md` is mirrored into
  kartoteka's `tasks` table through `task_create`, and `implementer` takes its
  next task from `task_ready` and reports through `task_update` rather than
  scanning for the first `- [ ]`. Iterations become parent rows and checkboxes
  their children; the queue is authoritative for what to work on, while
  `tasklist.md` stays current as the offline fallback. Gated by
  `knowledge.adapter` plus tool presence, with `--local` forcing the fallback —
  no new config key. New `scripts/tasklist_tasks.py` does the parsing and
  contacts nothing; everything reaching kartoteka goes through MCP tools.
  See `docs/task-queue.md` and
  `docs/superpowers/specs/2026-08-22-artel-task-queue-design.md`.

## [0.2.0] - 2026-08-22

### Added

- **Knowledge consultation — the read half of the kartoteka adapter.** With
  `knowledge.adapter: "kartoteka"` and the kartoteka MCP tools in the session, the `analyst`
  consults the institutional-knowledge index before its interview and the `researcher` consults
  it during its scan, so a decision the team already took is neither re-asked nor
  re-litigated. `research.md` gains a **Prior Decisions** section; the PRD gains no new section
  and instead cites into its existing Resolved Questions and Assumptions.

  The contract is `docs/knowledge-consultation.md`, spelled once and referenced by both agents.
  Config declares intent, the session supplies capability, and every disagreement between them
  is reported in the agent's own output rather than failing silently. `--local` on `analysis`,
  `researcher` and `feature-development` forces a knowledge-free run; `dev` does not take it,
  because it invokes neither consulting skill.

  **Nothing here writes.** The `PostToolUse` mirror hook remains the only path from artel into
  kartoteka, and agents read this ticket's own spec trail from disk, never from kartoteka's
  best-effort mirror of it.
- Knowledge mirror: `hooks/knowledge_mirror.py` (`PostToolUse` on `Edit|Write|MultiEdit`) posts
  each deliberation artifact written under `<specs.dir>/<TICKET>/` — `prd.md`, `plan.md`,
  `adr.md`, `review.md` and twelve others — to a kartoteka artifact store, which versions it by
  content hash. New config: `knowledge.adapter` (`"none"` default, `"kartoteka"`) and
  `knowledge.baseUrl`. Additive and best-effort by design: the spec-trail files on disk stay
  primary, the hook never blocks a write and never retries, and every attempt is logged to
  `.artel/run/.hooks/knowledge-mirror.log`. Gate evidence, machine-readable findings, derived
  reports and everything under `.artel/` are deliberately not mirrored.
- Operator docs (Phase 6): `docs/workflow-guide.md` (the narrative operator guide — quickstart,
  concepts, end-to-end walkthrough, recipes, troubleshooting) and `docs/skills-reference.md`
  (per-skill lookup: purpose, invocation, reads/writes, pauses, notes for all 33 skills),
  adapted from the source project's operator docs to plugin reality: `/artel:` command forms,
  config-driven adapters and gates, `.artel/run/` state paths, and the `scripts/verify.py` /
  `scripts/plan_check.py` gate engines in place of the source's Dart CLI.
- Flutter smoke-test guide: `docs/testing-flutter.md` — installing the plugin from a local
  checkout into a separate Flutter project, a known-good Flutter `.artel/config.json`, six
  ordered smoke-test scenarios (spec stage, dry run, hooks, lean loop, full pipeline, gate
  scripts), pass criteria, cleanup, and troubleshooting.

- Project skeleton: plugin manifest, single-plugin marketplace file, repo scaffold
  (`skills/`, `agents/`, `hooks/`, `docs/`).
- Documentation: README, design doc (architecture, genericization strategy, open questions,
  decision log), phased porting plan with source→plugin map, CLAUDE.md working guidance.
- Config contract: `docs/config.md` — the `.artel/config.json` schema (ticket grammar,
  tracker/VCS adapters, verify commands, languages, design toggle, specs dir, runtime commands)
  with annotated defaults, a filled example, and missing-file/precedence rules.
- Ticket-parsing contract: `docs/ticket-parsing.md` — config-driven identifier parsing
  (`ticket.pattern`/`projectKey`/`phaseSuffix`), the spec-trail directory layout, artifact path
  resolution, and the refuse-and-ask write rules, ported and genericized from the source project.
- Autonomous-run contract: `docs/autonomous-run.md` — `run-state.json` schema, question
  collection, AFK/HITL tags, capped loops, modes and risk classification, the run journal,
  headless invocation, checkpoint commits, and phase traversal, ported and genericized with all
  host-writable run state relocated to `.artel/run/`.
- Skill-orchestrator contract: `docs/orchestrator-common.md` — ticket resolution, phase-aware
  artifact paths, the description-file sync procedure, and the checkpoint-commit/autonomous-run
  tie-in, ported and genericized from the source project.
- Deviation-protocol contract: `docs/deviation-protocol.md` — severity classification
  (minor/major), the `implementation-notes.md` format, the escalation handshake, and reviewer
  verification duties, ported and genericized from the source project.
- Path-conventions contract: `docs/path-conventions.md` — the repo-relative-paths-only rule for
  spec-trail artifact content, its rationale, scope, and citation-form conventions, ported and
  genericized from the source project.
- Analysis agents: `agents/analyst.md`, `agents/researcher.md`, `agents/figma-analyst.md` — the
  PRD-interview, codebase-research, and optional Figma design-analysis agents, ported and
  genericized from the source project, with paths resolved via `<specs.dir>`/`<TICKET_ID>` and
  contracts cited via `${CLAUDE_PLUGIN_ROOT}/docs/`.
- Planning agents: `agents/planner.md`, `agents/task-planner.md`, `agents/vision-writer.md`,
  `agents/tasklist-writer.md` — architecture/plan, tasklist breakdown, technical vision, and
  iterative work-plan drafting, ported and genericized from the source project's agent crew.
- Implementation agents: `agents/implementer.md`, `agents/reviewer.md`, `agents/qa.md` — the
  task-by-task implementer (inner-loop and codegen steps forward-referenced to the Phase-3
  `inner-loop` skill, generated-code rule genericized), the dual-mode (ticket/standalone)
  reviewer (project-specific lenses renamed to project-agnostic Review lenses, static analysis
  routed through `verify.fast`, transient-automation and sensitive-surface notes forward-referenced
  to Phase 5), and the QA plan/report agent, ported and genericized from the source project's agent
  crew.
- Validation agents: `agents/validator.md`, `agents/tech-writer.md` — the release/ticket/phase
  gate-checklist validator (gates genericized to config-driven degradation, e.g. `IMPLEMENT_STEP_OK`'s
  `verify.commands` step and `RUNTIME_OK`'s `runtime.run`, forward-referencing the Phase-3 `run-app`
  skill; `AUTOMATION_REMOVED` consistent with the reviewer's transient-automation treatment) and the
  ticket summary/CHANGELOG tech-writer, ported and genericized from the source project's agent crew.
  Completes the 12-agent crew (incl. `figma-analyst`).
- Spec-stage skills: `skills/analysis`, `skills/researcher`, `skills/planner`, `skills/tasklist`
  — the PRD-interview, research, planning, and tasklist-breakdown orchestrators, ported and
  genericized from the source project's à-la-carte skills, invoking the `analyst`, `researcher`,
  `planner`, and `task-planner` agents respectively via the `Agent` tool. Ticket resolution and
  artifact paths follow `docs/orchestrator-common.md`/`docs/ticket-parsing.md`; open-questions
  bookkeeping moves to `.artel/run/<TICKET_ID>/open-questions.md` per `docs/autonomous-run.md`;
  contract references use `${CLAUDE_PLUGIN_ROOT}/docs/`. Folded-in follow-ups from the phase-2
  review: `agents/planner.md`'s design-doc citation now resolves via `${CLAUDE_PLUGIN_ROOT}`;
  `agents/reviewer.md`'s dangling "PR review approval" checkbox reference reworded to match the
  tasklist's actual `## Code Review Fixes` / `REVIEW_OK` mechanism (the source template never had
  such a checkbox); logged the ported-skills' dropped `allowed-tools:` frontmatter decision.
- Generator skills: `skills/generate-idea`, `skills/generate-vision`, `skills/generate-tasklist`
  — the tracker-import, technical-vision, and lean idea+vision-to-tasklist workers, ported and
  genericized from the source project. `generate-idea` stays a procedural worker (no matching
  agent, like `sync-phases`) and now branches on `tracker.adapter` (`"none"` gathers the
  description from an argument or the user, matching `analysis`'s input gate; `"jira-mcp"` and
  `"github-issues"` fetch via `<tracker.mcpToolPrefix>jira_get_issue(_comments)` and the `gh` CLI
  respectively) with content translated to `language.docs` instead of hardcoded English/Jira;
  its idea template moves to `skills/generate-idea/assets/templates/idea.template.md`.
  `generate-vision` and `generate-tasklist` invoke the `vision-writer` and `tasklist-writer`
  agents via the three-phase draft/ask/finalize model, writing `<specs.dir>/<TICKET_ID>/vision.md`
  and `tasklist.md` respectively; cross-references to sibling skills use the installed
  `/artel:<name>` form.
- Implement/verify stage skills: `skills/implementer`, `skills/run-reviewer`, `skills/qa`,
  `skills/validate` — the task-by-task implementer (single-phase autonomous model, deviation
  escalation handshake per `docs/deviation-protocol.md`, verify loop forward-referenced to the
  Phase-3 `/artel:inner-loop` skill), and the one-shot review/QA/gate-check orchestrators
  invoking the `reviewer`, `qa`, and `validator` agents respectively, ported and genericized from
  the source project's à-la-carte skills. Agent-mapping verified against each agent file with no
  disagreements. Folded-in follow-ups: `docs/ticket-parsing.md` §3/§4 now document the reviewer's
  machine-readable `review/findings.json` output (ticket-wide and phase-scoped); introduced a
  `specs.releases` config key (default `"specs/releases"`, `docs/config.md`) and switched
  `agents/qa.md`, `agents/validator.md`, and the `qa`/`validate` skill bodies off the hardcoded
  `specs/releases/` literal, with the decision and rationale logged in `docs/design.md`.
- PR and digest skills: `skills/docs-update`, `skills/pr-description`, `skills/pr-create`,
  `skills/sync-phases`, `skills/change-digest`, `skills/address-pr-comment`, ported and
  genericized from the source project's à-la-carte skills. `docs-update` (dispatches
  `tech-writer`) and `pr-description` (dispatches `tech-writer`, gathering a tracker summary via
  `tracker.adapter`, a git diff, and a style sample of merged PRs via `vcs.adapter` before
  delegating the write) stay orchestrators; `pr-create`, `sync-phases`, and `change-digest` stay
  procedural, matching their source shape (no agent dispatched in source). `pr-create` and
  `pr-description` now branch on `vcs.adapter`/`tracker.adapter` instead of hardcoding a host —
  Bitbucket `projectKey`/`repositorySlug` are derived from `git remote get-url origin` at runtime
  rather than a new config key, since `docs/config.md` has no dedicated key for them.
  `sync-phases` and `change-digest` had no VCS/tracker dependency to genericize in source (pure
  file sync / local `git diff` respectively) and stay adapter-agnostic. `address-pr-comment`
  stays its documented carve-out (no `Agent` delegation — the artifact is a plan-mode plan only
  the main session can author) and now parses both a GitHub and a Bitbucket comment-URL shape
  depending on `vcs.adapter`. Default-branch resolution in `pr-description`, `pr-create`, and
  `change-digest` matches `agents/reviewer.md`'s existing standalone-mode pattern (`git
  symbolic-ref`, no hardcoded `master`/`main` fallback) instead of reintroducing one.
  `change-digest`'s self-contained HTML/quiz template
  (`skills/change-digest/assets/report_template.html`) ports unchanged — it was already
  project-agnostic.
- Renamed skills: `skills/inner-loop` (← `flutter-inner-loop`), `skills/deep-review` (←
  `wallet-review`), `skills/issue-draft` (← `jira-issue-ru`), ported and genericized from the
  source project's à-la-carte skills, consistent with the forward references already committed in
  `agents/implementer.md` and `skills/implementer`. `inner-loop` keeps its bounded
  edit→fast-check→full-gate loop shape and `MAX_VERIFY_ITERATIONS = 4` cap, now driven by
  `verify.fast`/`verify.commands` (config.md) instead of a project-specific CLI — since
  `verify.commands` always runs unscoped from the repo root, the source's separate scoped/unscoped
  final pass collapses into one full-gate step per iteration; environment-error handling keeps the
  exit-`2` convention `agents/implementer.md` already forward-references. `deep-review` keeps its
  dual-independent-reviewer structure and quality-gate/resolve/dispatch/merge/plan-mode steps,
  branches its quality gate and PR-link parsing on `verify.commands`/`vcs.adapter`, and — since
  `agents/reviewer.md`'s standalone mode already runs the full lens set plus the regression guard on
  every dispatch — drops the source's per-reviewer lens duplication, keeping only independence
  (second reviewer never reads the first) as what distinguishes the two dispatches; the legacy
  `review-windsurf.md` compatibility path is dropped (Windsurf mirroring is an explicit non-goal,
  design.md). `issue-draft` keeps the free-text/file/Slack-export input resolution, single vs.
  multiple-suggestions modes, and self-verify checklist; output language moves from hardcoded
  Russian to `language.pr`, and the description's markup dialect now follows `tracker.adapter`
  (Jira wiki under `"jira-mcp"`, Markdown otherwise) since the source's Jira-wiki-only format only
  made sense when the destination was always Jira; issue creation/submission stays out of scope, as
  in the source. Folded-in follow-ups: `docs/ticket-parsing.md` §3/§4 now document the ticket
  `verify/` evidence dir (inner-loop), `change-report.html` and its phase variant (change-digest),
  and `pr-pending.md` (pr-create's identity-check-failure fallback).
- Runtime-gate skills: `skills/run-app`, `skills/drive-app`, `skills/add-automation`,
  `skills/remove-automation` — config-driven adapters ported from the source project's
  Dart/Flutter-specific launcher and driver tooling. `run-app` and `drive-app` treat their
  `runtime.run` / `runtime.drive` commands (config.md) as opaque, self-reporting black boxes — the
  same evidence envelope (`{command, exit_code, output}`) `inner-loop` uses for `verify.commands` —
  recording `runtime/observation.md` / `runtime/drive-observation.md` evidence; `run-app --gate` is
  the only mode `agents/validator.md`'s `RUNTIME_OK` gate treats as authoritative. `add-automation`
  / `remove-automation` run `runtime.scaffold.add`/`remove`, derive the changed paths from
  `git status --porcelain` (the source's fixed Dart file list has no generic replacement), gate on
  `verify.fast`, and commit exactly those paths, consistent with the `AUTOMATION_REMOVED` gate and
  the reviewer's transient-automation carve-out already ported. Dropped as unreplaceable outside a
  Dart/Flutter toolchain: the DTD/MCP connect and observe steps, widget-tree-based finder
  targeting, the wallet unlock procedure and its hard rules, screenshot-per-verification-point
  capture, the seed-phrase secrecy guardrail, and the file-content idempotency/trace checks.
  Task-3 follow-up resolved: restored a one-line optional on-demand-runtime-check hint in
  `skills/implementer/SKILL.md`'s dispatch prompt (`/artel:run-app` via `runtime.run`, explicitly
  not the `RUNTIME_OK` gate) — logged in `docs/design.md`'s decision log. Folded-in follow-up:
  `docs/ticket-parsing.md` §3/§4 now document the ticket `runtime/` evidence dir
  (`observation.md`, `drive-observation.md`) and its phase-scoped variant.
- Ops and utility skills: `skills/init-branch`, `skills/merge-conflicts`, `skills/save-context`,
  `skills/restore-context`, `skills/agents-md-generator`, ported and genericized from the source
  project's utility skills — none dispatch an agent; each keeps its procedural shape per the
  ported-skills' `allowed-tools:`-drop convention. `init-branch` gains real branch-creation
  (`feature/<TICKET_ID>[-<PHASE_NUM>]`, base-branch detection matching `agents/reviewer.md`'s
  standalone-mode convention) that the source skill never had, chains `/artel:restore-context` and
  `/init`, and replaces the source's Dart-toolchain dependency/codegen install step and hardcoded
  `ast-index` calls with the optional host-hook pattern `docs/orchestrator-common.md` §1 already
  defines (no generic equivalent exists for the former, so it is dropped rather than faked).
  `merge-conflicts` keeps its five-phase setup/identify/plan-mode/resolve/verify structure,
  routing its post-resolution check through `verify.commands` instead of a Dart analyzer/formatter
  pair and generalizing the generated-file guardrail beyond `*.dart`/`make g`. `save-context` and
  `restore-context` port as a matched pair (new decision below) with a `docs/`-subset mirror and a
  top-level loose-`specs/`-files mirror dropped — both keyed to fixed source-project filenames with
  no generic equivalent. `agents-md-generator` (a third-party community skill in the source
  project; metadata frontmatter dropped for consistency with the rest of the crew, attribution
  kept as a footer line) ports essentially unchanged — its workflow and both `references/`
  templates were already language/toolchain-agnostic — with its two template reads pointed at
  `${CLAUDE_PLUGIN_ROOT}/skills/agents-md-generator/references/`. New decision: the context store
  `save-context`/`restore-context` read and write moves from the source's user-level,
  cross-project store to `.artel/context/` in the host repo (sibling to `.artel/run/`,
  `docs/config.md` "Purpose and location"), gitignored like `.artel/run/`; logged with its
  narrowed-scope trade-off in `docs/design.md`'s decision log.
- Design-analysis skill: `skills/figma-analysis` — the last Phase-3 stage skill, ported and
  genericized from the source project, dispatching the already-ported `figma-analyst` agent.
  Config-gated by `design.figma` (`docs/config.md`): disabled reports `skipped`; enabled but no
  Figma MCP connected degrades silently and the pipeline continues, per
  `docs/autonomous-run.md` §13 — resolving the source skill's stop-and-ask `ENV_ERROR` handling
  in favor of the runtime-optional contract. Its `design-analysis.md` template moves to
  `skills/figma-analysis/assets/templates/design-analysis.template.md` (genericizing the
  Flutter-specific "go_router routes, Scope widgets, and BLoCs" §3 note into "routing and
  state-management surfaces"), matching `agents/figma-analyst.md`'s existing citation and
  completing the 30-skill Phase-3 roster. Forward-reference sweep: dropped stale "(Phase 3)"
  labels off the now-landed `inner-loop` and `run-app` references (`agents/implementer.md`,
  `agents/validator.md`) and normalized every remaining Phase-4/5 forward reference
  (`agents/reviewer.md`, `agents/tasklist-writer.md`, `agents/task-planner.md`,
  `agents/vision-writer.md`, `agents/planner.md`, `docs/config.md`, `docs/autonomous-run.md`) to
  a uniform first-reference/bare-repeat "(Phase N — see .../porting-plan.md)" form. Folded-in
  deferred minors: `generate-idea`'s no-tracker metadata prose now matches its own
  `$METADATA`/`$COMMENTS_RENDERED` omit behavior instead of a stale "not applicable" promise;
  `skills/README.md`'s per-skill enumeration replaced with a phase-complete summary;
  `pr-description`/`pr-create`'s Bitbucket `projectKey`/`repositorySlug` derivation gains a
  stop-and-ask clause for a missing/malformed git remote; `docs/config.md`'s
  `tracker.adapter`/`verify.commands` Consumed-by columns now list `issue-draft`/`deep-review`.
  Closes out Phase 3.
- Entry-point orchestrators: `skills/feature-development` (full pipeline: chatty head, one
  approval pause, autonomous tail, completion gate, PR close-out) and `skills/dev` (lean loop:
  input ladder, one work-list confirmation, implement + review + runtime gate), ported and
  genericized from the source project. Run state, journal and open questions live at
  `.artel/run/<TICKET_ID>/` per `docs/autonomous-run.md`; the shared
  `## Checkpoint commits & pushes` procedure lives in `feature-development` (referenced by
  `dev`, branch-guard fallback now `main`, verify gate via `verify.commands`, Dart-specific pin
  restore dropped); gate 3.5's deterministic plan-check ports as contract but skips until the
  Phase-5 `scripts/plan_check.py` ships; `ast-index` steps become the optional host
  index-refresh hook; the runtime gate's Dart-specific surface test becomes the new
  `runtime.surface` config key. New `skills/setup` — the one-time config interview both entry
  points invoke when `.artel/config.json` is missing (also run manually to create or revise the
  config; named `setup` to avoid colliding with the built-in `/init`). New config keys:
  `runtime.surface` (globs gating when the runtime gate runs) and `setup.commands` (post-branch
  install/codegen, restoring `init-branch`'s dropped source step — the parked Phase-3
  follow-up). Decisions logged in `docs/design.md`.
- Hooks and gates (Phase 5): `scripts/verify.py` (deterministic envelope wrapper over
  `verify.fast`/`verify.commands` — exit 0 clean / 1 findings / 2 environment error, digit-
  stripped finding keys, `{files}` scoping) and `scripts/plan_check.py` (the plan-anchor checker
  Gate 3.5 invokes; `ref:`/`new:` grammar ported, backticked-path rule genericized, symbols via
  `ast-index` when present else `git grep`), resolving design.md open question 1 (`codegen`
  dropped — `setup.commands` covers it). Five hooks ported from the source project and wired via
  `hooks/hooks.json`: `session_baseline` (SessionStart findings baseline),
  `fast_verify_post_edit` (PostToolUse feedback, never blocks), `verify_stop_gate` (Stop; blocks
  only findings NEW vs the session baseline, 2-block cap with latch), `stop_gate` (Stop; blocks
  while an autonomous run is active and incomplete, 5-block cap, 3h wall clock),
  `sensitive_guard` (PreToolUse; mode-floor denials during armed runs). Shipped default
  sensitive-paths policy (`hooks/sensitive-paths.json`: secrets/gate-config at full-gates,
  ci-cd at plan-gate) with wholesale host override at `.artel/sensitive-paths.json`; new
  `verify.surface` config key; hook state under `.artel/run/.hooks/`; verify-layer hooks inert
  until `.artel/config.json` exists; stdlib `unittest` suite under `tests/`.

### Changed

- Documentation refresh to post-porting reality: README status flipped from "early scaffolding"
  to ported-pre-publish with a complete component table and repo layout (`scripts/`, `tests/`);
  `hooks/README.md` rewritten to describe the five shipped hooks, their state paths, and the
  escape hatch; stale "ship later"/"ported in a later phase"/"(Phase 5)" forward references
  resolved across `docs/autonomous-run.md`, `docs/orchestrator-common.md`, `agents/planner.md`,
  `agents/task-planner.md`, `agents/tasklist-writer.md`, `agents/vision-writer.md`, and
  `agents/reviewer.md` — each now cites the shipped artifact (deviation protocol, sensitive-paths
  policy, `plan_check.py`) instead of the porting plan.
