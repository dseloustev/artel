# Configuration — `.artel/config.json`

*Status: draft v0.1 · 2026-08-01*

Every project-specific fact artel needs — ticket grammar, tracker, VCS host, verify commands,
output languages, spec-trail location, runtime commands — lives in one JSON file in the **host
repo**. Skills, agents and hooks read it instead of hardcoding literals; see
[design.md](design.md#genericization-strategy).

## Purpose and location

- **Path** — `.artel/config.json` at the host repo root.
- **Format** — strict JSON (no comments, no trailing commas), UTF-8.
- **Owner** — the host project: commit it, it is shared team configuration.

The `.artel/` directory is artel's whole footprint in the host repo:

- `.artel/config.json` — this file, committed.
- `.artel/sensitive-paths.json` — optional host override of the sensitive-paths policy the
  `sensitive_guard` hook enforces (categories of globs with mode floors,
  [autonomous-run.md](autonomous-run.md) §10). When present it replaces the plugin's shipped
  default policy (`hooks/sensitive-paths.json`) wholesale. Committed, like the config; the
  `setup` skill offers to scaffold it from the shipped defaults.
- `.artel/templates/issue-draft.md` — optional host override of the description template the
  `issue-draft` skill renders (`skills/issue-draft/assets/templates/description.template.md`
  in the plugin). When present it replaces the shipped template wholesale; presence is the
  switch, as with the sensitive-paths policy, and there is no config key. It must keep the
  template's convention: each `## ` section is followed by an HTML comment whose first word is
  `required` or `optional`, then one `$NAME` placeholder; a trailing placeholder outside any
  section (the shipped `$SOURCE`) carries its comment after it. Committed, like the config.
- `.artel/run/` — host-writable run state, journals and the per-task worker reports (contract
  defined separately, alongside the autonomous-run rules). Not committed; add it to the host
  `.gitignore`.
- `.artel/context/` — the `save-context`/`restore-context` store: a durable, host-repo-local
  mirror of root docs (`CLAUDE.md`, `CHANGELOG.md`) and the spec trail (`<specs.dir>/<TICKET_ID>/`,
  `<specs.dir>/.active_ticket`), used to declutter or archive the working tree and bring it back
  later. Not committed; add it to the host `.gitignore`, same as `.artel/run/`.
- `.artel/worktree.json` — only inside a ticket worktree under `.claude/worktrees/`: the manifest
  `scripts/worktree.py move-in` writes (ticket, branch, main checkout, what was copied). Hand-back
  leaves its copy at `.artel/run/<TICKET_ID>/worktree.json` with a `handBack` marker — `pending`
  until it finishes, then `done`. Not committed.

In a ticket worktree ([worktrees.md](worktrees.md)) the committed or ignored files above are
copies of the main checkout's, `.artel/context/` is a symlink to the main checkout's store, and
`.artel/run/<TICKET_ID>/` is the ticket's own run state, moved there. Which *other* ignored files
a worktree receives is set by `.worktreeinclude` at the repo root (gitignore syntax; without it,
`/.claude/` and `/.mcp.json`) — Claude Code's file, not an artel key.

Nothing host-writable is ever written into the plugin install or cache directory. The plugin
ships read-only skills, agents and hooks; everything a run produces lands in the host repo.

## Reading rules

1. **Resolution is per key, not per section.** A missing key falls back to its default even when
   its section object is present. `{"verify": {"fast": "npm run lint"}}` still yields
   `verify.commands: []`.
2. **Unknown keys are ignored.** Forward compatibility: a config written for a newer artel must
   not break an older one.
3. **Invalid values stop the run.** An adapter name outside its allowed set, a non-list
   `verify.commands`, or a `specs.dir` that escapes the repo is a configuration error: the skill
   reports it and stops instead of silently substituting a default.
4. **Read once, at run start.** A skill resolves config when it starts and uses that snapshot for
   the whole run.

## The default config

This is the complete set of defaults. A config file that omits everything behaves exactly like
this one. `ticket.projectKey` is the single value with no meaningful default — `"PROJ"` is a
placeholder the init interview replaces.

```json
{
  "version": 1,
  "ticket": {
    "projectKey": "PROJ",
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
    "commands": [],
    "fast": ""
  },
  "review": {
    "perTask": false
  },
  "guard": {
    "extraReadTools": []
  },
  "setup": {
    "commands": []
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
    "releases": "specs/releases",
    "onUnavailable": "abort"
  },
  "knowledge": {
    "adapter": "none",
    "baseUrl": "",
    "project": "",
    "tokenEnv": ""
  },
  "runtime": {}
}
```

The defaults are deliberately inert: no tracker calls, no quality gate, no runtime gate, no
design stage, no per-task review. Out of the box artel runs the pipeline and records every unconfigured gate as
`skipped` rather than `green`. Configuring a key is what turns its gate on.

`version` (integer, default `1`) identifies the config format, not the plugin version. v0.1
accepts only `1`; it exists so later formats can migrate rather than guess.

## Key reference

### Top-level

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `version` | integer | `1` | Identifies the config format, not the plugin version. v0.1 accepts only `1`. | Config loader, init interview (format migrations) |

### `ticket` — identifier grammar

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `ticket.projectKey` | string | `"PROJ"` (placeholder) | Letters and digits, no separators. Uppercase is canonical. | Ticket canonicalization, branch names, PR titles, commit subjects, Jira project key for `tracker.adapter: "jira-mcp"` |
| `ticket.pattern` | string (regex) | `"^(?:{projectKey}-)?(\\d+)(?:-p?(\\d+))?$"` | Must expose the ticket number as **capture group 1** and the optional phase as **capture group 2**. The literal token `{projectKey}` is replaced with `ticket.projectKey` before the regex is compiled. Matched case-insensitively. | Every phase-aware skill and agent; hooks that resolve the active ticket; `issue-draft`, to recognise ticket keys the source cites |
| `ticket.phaseSuffix` | boolean | `true` | `true` \| `false` | Phase-scoped runs and artifact paths |

Positional capture groups (not named groups) are the contract, so the same pattern compiles
unchanged in Python, JavaScript and PCRE. Engines that support names may add them.

With the default pattern and `projectKey: "PROJ"`:

| Input | Ticket ID | Number | Phase |
|---|---|---|---|
| `PROJ-123` | `PROJ-123` | `123` | none |
| `PROJ-123-2` | `PROJ-123` | `123` | `2` |
| `PROJ-123-p2` | `PROJ-123` | `123` | `2` |
| `123` | `PROJ-123` | `123` | none |
| `123-2` | `PROJ-123` | `123` | `2` |

The canonical ticket ID is always `<projectKey>-<group 1>`, whatever form the caller typed. The
phase is capture group 2 or `null`.

**Phase suffix.** Tickets may carry a phase suffix meaning "phase N of this ticket": `PROJ-123-2`
is phase 2 of `PROJ-123`. Phase-scoped artifacts then live under
`<specs.dir>/<TICKET_ID>/phase-<N>/`, ticket-wide ones at `<specs.dir>/<TICKET_ID>/`. Set
`ticket.phaseSuffix: false` for projects that never split a ticket into phases: identifiers
carrying a suffix are then rejected as malformed instead of being read as a phase, and every run
is ticket-wide. Projects needing a different suffix spelling express it in `ticket.pattern`'s
second capture group; `phaseSuffix` only switches the feature on and off.

### `tracker` — where tickets come from

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `tracker.adapter` | string | `"none"` | `"none"` \| `"github-issues"` \| `"jira-mcp"` | Ticket fetch at pipeline start, status comments, PR↔ticket linking, `issue-draft`'s markup-dialect selection |
| `tracker.mcpToolPrefix` | string | `""` | Required when `adapter` is `"jira-mcp"`. Full MCP tool prefix including trailing separator, e.g. `"mcp__tracker__"`. | The `jira-mcp` adapter's tool addressing |

- **`none`** — artel never talks to a tracker. The ticket description is a local file the operator
  writes (or the `generate-idea` skill produces) at `<specs.dir>/<TICKET_ID>/idea.md`. Steps that
  would comment back or transition a ticket report `skipped (no tracker)`.
- **`github-issues`** — the ticket number is the issue number in the host repo; issues are read
  and commented through the `gh` CLI, which must be installed and authenticated. This is
  independent of `vcs.adapter` — `gh` is required for issues even when the VCS adapter is not
  `github-cli`.
- **`jira-mcp`** — tools are addressed as `<tracker.mcpToolPrefix>jira_get_issue`,
  `<prefix>jira_add_comment` and so on, so no server name is hardcoded anywhere in the plugin.
  `ticket.projectKey` doubles as the Jira project key. If the prefix is empty or the tools are not
  connected, the skill reports the failure and falls back to `<specs.dir>/<TICKET_ID>/idea.md` when
  that file exists; otherwise it stops and asks.

### `vcs` — where pull requests go

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `vcs.adapter` | string | `"github-cli"` | `"github-cli"` \| `"bitbucket-mcp"` | PR creation and update, PR description publishing, PR-comment handling |
| `vcs.mcpToolPrefix` | string | `""` | Required when `adapter` is `"bitbucket-mcp"`, e.g. `"mcp__vcs__"`. | The `bitbucket-mcp` adapter's tool addressing |

- **`github-cli`** — the hosting platform's API is reached through the `gh` CLI, which must be
  installed and authenticated.
- **`bitbucket-mcp`** — tools are addressed as `<vcs.mcpToolPrefix>bitbucket_create_pull_request`
  and so on, so no server name is hardcoded anywhere in the plugin.

Branching, committing and pushing always use plain `git`, whatever the adapter. The adapter only
covers the hosting platform's API.

**When the adapter is unusable.** There is no local-file fallback for VCS work — a pull request
cannot be written to disk — so both failure modes stop rather than degrade:

- **Empty `vcs.mcpToolPrefix` with `adapter: "bitbucket-mcp"`** is a configuration error in the
  sense of reading rule 3: the skill reports the missing prefix and stops. Because the value is
  knowable at run start, entry-point skills check it there and refuse to start the pipeline,
  rather than failing hours later at the PR stage.
- **Adapter tools unreachable at call time** (MCP server not connected, `gh` missing or
  unauthenticated) — the skill reports what it tried to call and stops, then asks the user to fix
  the connection. Nothing already done is discarded: the branch, its commits and the spec trail
  stay in place, so the run resumes at the same stage once the adapter works, or the user opens
  the PR by hand.

### `verify` — the quality gate

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `verify.commands` | array of strings | `[]` | Ordered shell commands forming the full gate. Run from the host repo root; the gate stops at the first non-zero exit. | Full-gate checks: inner loop, implementation checkpoints, validate stage, `deep-review`'s step-0 quality gate, `merge-conflicts`' post-resolution check |
| `verify.fast` | string | `""` | One quick command for per-edit feedback (lint/analyze of the touched scope, not the whole test suite). | Fast per-edit and verify stop-gate hooks, `inner-loop`'s fast-check step, `add-automation`/`remove-automation`'s post-change check |
| `verify.surface` | array of strings | absent | Repo-relative glob patterns (`fnmatch` semantics — `*` crosses `/` — like `runtime.surface`); a `!`-prefixed pattern excludes (generated files). A path counts when it matches ≥ 1 positive and 0 negative patterns; a list with only excludes implies `*` as the positive set. Absent → every changed file counts. | The per-edit and stop-gate hooks' changed-file filter |

Commands must be non-interactive, exit non-zero on failure, and be safe to re-run. An empty
`verify.commands` degrades the gate to `skipped` — it is never reported as `green`. An empty
`verify.fast` makes the per-edit hook a no-op.

Any command may contain the literal token `{files}`: callers that pass an explicit file scope
(the hooks; `scripts/verify.py --files`; `add-automation`/`remove-automation`, whose scope is the
scaffold's own changed paths) replace it with the space-joined, shell-quoted paths, so
`"eslint {files}"` checks only what changed. A command without the token always runs unscoped. A
caller that has a scope and skips the substitution passes the literal token to the shell — a
failure that looks like findings but is an invocation bug. Exit codes `126`/`127`, a spawn
failure, or a timeout classify as an environment error (exit 2 — fix the toolchain); any other
non-zero exit is findings (exit 1).
`verify.surface` filters which changed files the hooks act on, e.g.
`["lib/**/*.dart", "!*.g.dart", "!*.freezed.dart"]` for the source project's behavior.

### `review` — the review gates

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `review.perTask` | boolean | `false` | `true` adds a review of each iteration task's diff right after its implementer returns ([autonomous-run.md](autonomous-run.md) §16): the `reviewer` agent in task mode, Blocking / Important findings written under `## Code Review Fixes`, one fix round, no per-task re-review. Adds one reviewer seat per task; the phase review still runs. | `dev` step 4, `feature-development` gate 5 |
| `review.forecast.threshold` | integer | `70` | 1–99. `deep-review`'s cut between `likely to pass` and `at risk` ([review-forecast.md](review-forecast.md) §5): a change whose forecast pass percentage is below it gets a proposed fix. Any other value is a configuration error under reading rule 3. | `deep-review` step 2c, `review-forecaster` |
| `review.forecast.reviewers` | array of strings | `[]` | Reviewer display names as kartoteka renders them. Empty: every precedent thread weighs `1`. Non-empty: a thread whose root comment is by a listed name weighs `1`, any other `0.5` (review-forecast.md §4). Kept in config because the roster changes. | `review-forecaster` |

The phase review (`run-reviewer` after every task in the phase is done) is not configurable
here — it always runs. `review.perTask` only decides whether each task is also gated on its
own before the next one is dispatched. Any value other than a JSON boolean is a configuration
error under reading rule 3. The two `review.forecast.*` keys belong to `deep-review` alone —
the pipeline's gates never read them — and they only matter when `knowledge.adapter` is
`kartoteka`: with the forecast off, the threshold has nothing to cut and the list nothing to
weigh.

### `guard` — the platform guard's escape hatch

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `guard.extraReadTools` | array of strings | `[]` | Tool-name substrings the `vcs_guard` hook treats as reads even when their verb is unrecognized. Matched case-insensitively. Covers both the VCS and the tracker domain. A non-list value is ignored — the hook coerces it to `[]`; unlike a skill, a hook cannot stop a run. | `hooks/vcs_guard.py` |

The `vcs_guard` hook denies any call that **writes** to a platform other than the one
`vcs.adapter` (for pull requests) or `tracker.adapter` (for issues) declares. It classifies a
call by extracting its verb positionally — the first recognized verb among an MCP tool name's
`_`-separated segments after the platform segment, or the subcommand following the noun for
`gh`. A verb it does not recognize is **denied**, so a tool name nobody anticipated cannot
become the hole in the guarantee. `guard.extraReadTools` is the escape: list a tool there and
the guard treats it as a read when its verb is unrecognized. It never rescues a recognized
write.

Each entry is matched as a **bare case-insensitive substring** of the call's label (an MCP tool
name, or `gh <noun> <verb>`) — there is no anchoring and no wildcard syntax, so entries should
be **full tool names**. A fragment as short as `"_"` matches every unknown call and retires the
fail-closed rule for all of them; it still cannot un-guard a call whose verb is a recognized
write.

**Where the guarantee stops.** The hook inspects `gh` subcommands over pull requests, repos,
releases, aliases and `api` (plus `gh issue` for the tracker domain), and tools whose **name**
carries a platform token (`bitbucket`, `github`, `jira`). It does not inspect `git push`, other
`gh` subcommands (`gist`, `secret`, `variable`, `label`, `workflow`, `project`), a `gh` alias
created before the guard existed, or direct HTTP (`curl` against a platform API). Those reach
the platform unguarded.

`tracker.adapter: "none"` — and an absent `tracker` section — leaves the tracker domain
unenforced: no declared home means nothing to protect. `vcs.adapter` has no `none` value, so
the VCS domain is always enforced. An adapter value that is **declared but unrecognized** (say
`"github"`, a typo for `"github-cli"`) does not unguard its domain: it matches no platform, so
every platform write in that domain is denied until the value is fixed. A guard that fails
closed on an unknown verb must not fail open on a malformed value of the key it enforces.

### `setup` — post-branch setup

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `setup.commands` | array of strings | `[]` | Ordered shell commands run once after a ticket branch is created (dependency install, code generation). Same execution rules as `verify.commands`: run from the host repo root, non-interactive, stop at the first non-zero exit. | `init-branch`'s post-branch setup step; `move-to-worktree` (in the new worktree) and `return-from-worktree` (in the main checkout) |

An empty list silently skips the step — a project whose toolchain needs nothing after a branch
switch simply leaves it unset.

### `language` — output languages

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `language.docs` | string | `"en"` | IETF BCP 47 code, e.g. `"en"`, `"es"`, `"de"` | Spec-trail artifacts under `specs.dir` |
| `language.pr` | string | `"en"` | Same | PR title and body, tracker comments, drafted issues |

Neither key affects commit messages or code identifiers — those follow the host repo's own
conventions.

### `design` — optional design stage

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `design.figma` | boolean | `false` | `true` \| `false` | The Figma analysis stage and its agent |

`true` enables the design-analysis stage. Even then it is runtime-optional: with no Figma MCP
connected the stage skips silently and the pipeline continues.

### `specs` — the spec trail

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `specs.dir` | string | `"specs/.current"` | Repo-relative path, no leading `/`, no `..` segments | Every artifact read and write |
| `specs.releases` | string | `"specs/releases"` | Repo-relative path, no leading `/`, no `..` segments. Sits alongside `specs.dir`, not inside it — release scope spans multiple tickets. | Release-scope QA/validation runs (`R-<RELEASE_ID>` identifiers) — `qa` and `validator` agents |
| `specs.onUnavailable` | string | `"abort"` | `"abort"` \| `"local"`. The headless answer when kartoteka is this project's spec store and cannot be reached: stop, or work locally and let `/artel:migrate-specs` move the documents in later. Interactive runs always ask instead. | Headless runs of every skill that resolves the spec store ([spec-storage.md](spec-storage.md) §5.4) |

The trail is `<specs.dir>/<TICKET_ID>/` for ticket-wide artifacts, `<specs.dir>/<TICKET_ID>/phase-<N>/`
for phase-scoped ones, and `<specs.dir>/.active_ticket` for the in-flight identifier.
Release-scope artifacts (`R-<RELEASE_ID>` identifiers) live under `<specs.releases>/` instead:
`<specs.releases>/<RELEASE_ID>.md` (the release definition) and `<specs.releases>/<RELEASE_ID>/qa.md`
(the combined QA report). With knowledge.adapter "kartoteka" the spec trail is not kept here at
all: kartoteka's artifact store holds it, and `<specs.dir>` keeps only .active_ticket and gate
evidence ([spec-storage.md](spec-storage.md)).

### `knowledge` — the institutional-memory mirror

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `knowledge.adapter` | string | `"none"` | `"none"` \| `"kartoteka"` | The `knowledge_mirror` hook; the read half below (`analyst`, `researcher`, `deep-review`, `issue-draft`); the task queue |
| `knowledge.baseUrl` | string | `""` | Required when `adapter` is `"kartoteka"`. Origin only, no trailing path — e.g. `http://127.0.0.1:8734`, or a hosted daemon's `https://` origin. | The `knowledge_mirror` hook's request addressing; `scripts/spec_store.py` |
| `knowledge.project` | string | `""` | Required when `adapter` is `"kartoteka"`. The kartoteka project this repository's trail, queue and consultations belong to: lowercase kebab-case, `^[a-z0-9][a-z0-9-]*$`, e.g. `adguard-wallet`. Must be registered in the daemon's database — `kartoteka project add <name>`, once, on the daemon machine. No default; see below. | Every kartoteka call: the `knowledge_mirror` hook's request body, `related`, the scoped reads, `task_create` / `task_ready`; the `issue-draft` consultation |
| `knowledge.tokenEnv` | string | `""` | Optional. The **name** of the environment variable holding a kartoteka bearer token — never the token itself; `[A-Za-z_][A-Za-z0-9_]*`, conventionally `KARTOTEKA_TOKEN`. Needed when the daemon has `[auth] enabled = true` (kartoteka 0.32.0; every hosted daemon). Empty, or naming a variable that is unset, sends the request unauthenticated. See below. | The `knowledge_mirror` hook's `Authorization` header; the `using-artel` host status (set or not, never the value); `scripts/spec_store.py` — which needs a CLI-minted token even where the MCP session signs in with GitHub (kartoteka 0.42.0) |

- **`none`** — nothing is mirrored. The spec trail stays on disk, exactly as it always has.
- **`kartoteka`** — kartoteka's artifact store is this project's spec store
  ([spec-storage.md](spec-storage.md)): spec documents are written there and nowhere else,
  and `<specs.dir>` keeps only `.active_ticket` and gate evidence text. When kartoteka cannot be
  reached, a run asks before saving anything locally (headless: `specs.onUnavailable`), and
  `/artel:migrate-specs` moves local trails in. The `PostToolUse` mirror hook still posts
  files written on the files path — `--local`, or a run the user allowed to work locally —
  best-effort as before: it never blocks and never retries.

Which artifacts are mirrored, and which are deliberately not, is fixed in the hook rather than
configured: the deliberation documents (`prd.md`, `plan.md`, `adr.md`, `review.md`, …) go, and
gate evidence, machine-readable findings, derived reports and transient adapter state do not.
Everything under `.artel/` is outside `<specs.dir>` and never leaves the machine.

**`knowledge.project` names the namespace.** Since kartoteka 0.31.0 one daemon can serve several
projects out of one database, and it refuses any write that does not say which project it
belongs to — a guessed project would append to another project's deliberation trail, and nothing
later undoes that. So there is deliberately no default: kartoteka removed its own for that reason
and artel does not reinvent one. The value travels on every call artel makes — the mirror hook's
request body, `related(<project>, …)`, `task_create` and `task_ready`, and as a scope on the
reads (`search_knowledge`, `index_status`, `task_list`, `artifact_list`), so that a daemon
serving several projects answers for this one. The name must be **registered** in the database
the daemon serves: `kartoteka project add <name>`, once, on the machine running it
(`kartoteka ingest` registers its own config's project already, so this matters for a project
kartoteka indexes nothing for — an artifact trail and a task list, no sources). An unregistered
name is refused, not created — a typo opens no namespace — with a message naming that command;
the hook logs the refusal as a `reject`, and the agents record it (`docs/task-queue.md` §1,
`docs/knowledge-consultation.md` §2).

An adapter of `"kartoteka"` with an empty `baseUrl`, or with a `project` that is empty or
outside the grammar, is a configuration error under reading rule 3, but the hook **reports it to
`.artel/run/.hooks/knowledge-mirror.log` and continues** rather than stopping the run. The
agents do the same on their side: they consult nothing and record
`kartoteka is configured for this project but knowledge.project is not set`
(`docs/knowledge-consultation.md` §1), and the queue takes its fallback path with the same
record (`docs/task-queue.md` §1).

**`knowledge.tokenEnv` names the credential and never holds it.** Since kartoteka 0.32.0 a
daemon may leave loopback, and when it does — or whenever its operator sets
`[auth] enabled = true` — every request on its port needs `Authorization: Bearer ktk_…`, reads
included; a missing, revoked or expired token answers `401`. The token is one machine's
credential and this file is committed team configuration, so the config carries only the name
of the environment variable that holds it. The operator mints one per host that runs artel, on
the daemon machine — `kartoteka token add <principal> --note "artel hook"`, printed once — and
each host exports it: `export KARTOTEKA_TOKEN=ktk_…`. The hook then sends the header, and every
artifact it writes records that principal beside the (still null) `author_agent`. What a `401`
becomes in the mirror log depends on what the hook had: with a token, `reject … HTTP 401`, naming
the variable — the token was refused, so `kartoteka token list` on the daemon host; with none,
`misconfigured`, naming `knowledge.tokenEnv` when it is empty or the unset variable when it is
not. A named variable that is unset is **not** an error by itself: the request goes out without
a header, and a daemon with `[auth]` off accepts it — so one committed config serves a laptop
talking to a loopback daemon and a host talking to a hosted one. The token never reaches the
log or the session context on any path, and two more `misconfigured` lines guard that promise:
a value with whitespace or control characters (a file exported with its second line) is reported
by the variable's name before any header exists, and a token is never sent over plaintext
`http://` to a host that is not loopback — kartoteka refuses a non-loopback bind without TLS, so
such a `baseUrl` is a proxy's upstream port reached directly; use the `https://` origin. Both
send nothing.

The MCP session takes the same variable through the client's own expansion:
`claude mcp add --transport http kartoteka <baseUrl>/mcp --header "Authorization: Bearer ktk_…"`
stores the literal in Claude Code's config, while an `.mcp.json` entry with
`"headers": {"Authorization": "Bearer ${KARTOTEKA_TOKEN}"}` reads the export at session start,
so one variable feeds both the hook and the session. `tokenEnv` itself is read by the hook and
the `using-artel` host status only; nothing in the read half or the task queue consults it —
those go over MCP, whose token is wired where the server is (`docs/opencode.md` for OpenCode's
`{env:…}` form).

#### The read half

`knowledge.adapter` gates three things, not one. Beyond the mirror above, it declares that this
project's agents may **consult** kartoteka before working: the `analyst` before its interview,
the `researcher` during its scan, and `issue-draft` from the conversation before it drafts — the
full contract is `docs/knowledge-consultation.md` — and `deep-review`'s forecast of how a branch
will fare in review, which has its own contract and its own, larger lookup budget:
`docs/review-forecast.md`. The third is the task queue — `#### The task queue` below.

Reading goes over kartoteka's **MCP tools** (`search_knowledge`, `related`, `index_status`),
not over `baseUrl`. There is no MCP URL in this config: the host wires the kartoteka MCP server
into its own session — with its bearer token, `--header` or `${KARTOTEKA_TOKEN}`, when the
daemon has `[auth]` on — and `knowledge.adapter` says whether this project wants it used. So the
config declares intent and the session supplies capability, and the two can disagree:

| `--local` | `knowledge.adapter` | kartoteka MCP tools | Behavior |
|---|---|---|---|
| **yes** | either | either | No consultation. Recorded as `local-only run requested` |
| no | `none` / absent | absent | No consultation, silently |
| no | `none` / absent | present | No consultation, silently — an undeclared capability is not used |
| no | `kartoteka` | present | **Consult and cite** |
| no | `kartoteka` | **absent** | No consultation, and the agent records that it was configured but unavailable |

The fourth row is the working configuration; the fifth is the one worth knowing about, because
it is how a correct `.artel/config.json` still produces no citations — the MCP server is not
wired into the session, or is wired without the token a daemon with `[auth]` on requires, so
Claude Code never connects. The agent says so in its own output rather than leaving you to
guess.

The third row is why the adapter still matters when the tools are present: kartoteka's daemon
may serve several projects out of one database, and `knowledge.project` is what names this one
on every call. A project that has not declared the adapter has declared no project either, so
an index wired up for some other checkout is never consulted for this one.

**Nothing in the read half writes**, and agents never read this ticket's own `prd.md` or
`plan.md` back from kartoteka — those are read from disk, because kartoteka's copy is a
best-effort mirror that can lag.

#### The task queue

The same key gates a third thing, and this one does write. With the adapter on and the
tools present, the tasklist is mirrored into kartoteka's task store as it is written, and
`implementer` claims its next iteration task from there instead of scanning `tasklist.md`.
The full contract is `docs/task-queue.md`; its §1 resolves the gate exactly as the read
table above does, with the two `none` rows collapsed into one.

Four more **MCP tools** carry it, wired into the session the same way and named nowhere in
this config:

| Tool | Used for |
|---|---|
| `task_create` | mirroring one tasklist row into the queue (idempotent on title) |
| `task_ready` | claiming the next ready task for this actor |
| `task_update` | reporting a task `done`, `blocked` or released back to `ready`; moving a fix-section row `in_progress` |
| `task_list` | reading the ticket's rows, for promotion and for an empty-queue report |

Absent tools degrade the same way the read half's do: the run continues from `tasklist.md`
and records that it did. Only iteration work is ever offered: gate-remediation sections
and `## Final Verification` are recorded as rows but always worked from the file
(`docs/task-queue.md` §6).

#### The conversational front doors

The same key admits two skills a person invokes from the conversation rather than the pipeline:
[`knowledge`](skills-reference.md#knowledge) (read: `search_knowledge`, `related`,
`index_status`, a non-active ticket's `artifact_list` / `artifact_get`) and
[`tasks`](skills-reference.md#tasks) (write: `task_list`, `task_create`, `task_update`). Both
resolve the table above without its `--local` row — a person invoking them has asked for
kartoteka — and both **refuse** rather than degrade: adapter `none` stops with a pointer to
`/artel:setup`, absent tools stop with the "configured but not available" message. There is no
override flag, for the third row's reason. `issue-draft` is a third skill a person invokes from
the conversation and consults the same key, but it is the exception to both of those rules: it
takes `--local` and resolves the table above in full, and where these two refuse, it degrades —
no Related section, every gap asked — because a person asked it for a draft, not for the index.
The `using_artel` `SessionStart` hook reads the key too, only to tell the injected router
whether those two routes are live.

### `runtime` — optional runtime and automation commands

All `runtime` keys are optional and adapter-shaped: a project supplies whichever commands it has,
and unset ones degrade to `skipped`.

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `runtime.run` | string | absent | Command that builds and launches the app | `run-app` — the `RUNTIME_OK` gate |
| `runtime.drive` | string | absent | Command that drives the running app (UI automation) | `drive-app` |
| `runtime.scaffold.add` | string | absent | Command that adds the transient automation scaffold | `add-automation` |
| `runtime.scaffold.remove` | string | absent | Command that removes it again | `remove-automation` |
| `runtime.surface` | array of strings | absent | Repo-relative glob patterns (`fnmatch` semantics, like the sensitive-paths policy) naming the files whose changes make the runtime gate worth running, e.g. `["lib/**/*.dart", "packages/*/lib/**/*.dart"]`. | The entry-point orchestrators' `RUNTIME_OK` gate decision (`feature-development`, `dev`) |

An absent or empty command means the corresponding skill reports `not configured` and the
`RUNTIME_OK` gate is recorded as `skipped`. Missing runtime configuration never blocks a run.

`runtime.surface` is a filter, not a command: when set, the entry-point orchestrators run the
`RUNTIME_OK` gate only when the run's diff (changed files vs the default branch plus the working
tree) matches at least one glob, recording `RUNTIME_OK: skipped (no runtime surface)` otherwise.
When absent with `runtime.run` configured, the gate always runs. It has no effect on manual
`/artel:run-app` invocations.

## A filled example

A hypothetical TypeScript project tracked in Jira, shipped through GitHub, with a runtime gate:

```json
{
  "version": 1,
  "ticket": {
    "projectKey": "PROJ",
    "pattern": "^(?:{projectKey}-)?(\\d+)(?:-p?(\\d+))?$",
    "phaseSuffix": true
  },
  "tracker": {
    "adapter": "jira-mcp",
    "mcpToolPrefix": "mcp__tracker__"
  },
  "vcs": {
    "adapter": "github-cli",
    "mcpToolPrefix": ""
  },
  "verify": {
    "commands": [
      "npm run lint",
      "npm run typecheck",
      "npm test -- --run"
    ],
    "fast": "npm run lint -- --cache",
    "surface": ["src/**", "!src/generated/**"]
  },
  "review": {
    "perTask": true
  },
  "guard": {
    "extraReadTools": []
  },
  "setup": {
    "commands": ["npm ci"]
  },
  "language": {
    "docs": "en",
    "pr": "es"
  },
  "design": {
    "figma": true
  },
  "specs": {
    "dir": "specs/.current",
    "releases": "specs/releases"
  },
  "knowledge": {
    "adapter": "kartoteka",
    "baseUrl": "http://127.0.0.1:8734",
    "project": "acme-web",
    "tokenEnv": "KARTOTEKA_TOKEN"
  },
  "runtime": {
    "run": "npm run dev -- --port 5173",
    "drive": "npm run e2e:drive",
    "surface": ["src/**"],
    "scaffold": {
      "add": "npm run automation:add",
      "remove": "npm run automation:remove"
    }
  }
}
```

The command strings are illustrations, not requirements. A Makefile-driven project would write
`"commands": ["make verify"]` instead; any toolchain works as long as the commands are
non-interactive and exit non-zero on failure.

## When the config is missing

There is no bundled fallback file — absence is a defined state, not an error.

- **Entry-point skills** (`feature-development`, `dev`) find no `.artel/config.json` and invoke
  the `setup` skill (`/artel:setup`), which interviews for ticket grammar, tracker, VCS, verify
  commands and languages (plus optional extras), writes `.artel/config.json`, and returns
  control so the requested run continues. The interview happens once per repo; afterwards the
  file is edited by hand, or revised via `/artel:setup`.
- **À-la-carte stage skills** never interview. They run against the defaults above and note in
  their report that no config was found, so a single stage invocation is never blocked by a
  missing file.

## Precedence

1. **Explicit user instruction in the session** — highest. "Skip verification this run", "open the
   PR against `release/3.2`", "write the PR description in English" override the config for that
   session only. Overrides are never written back to `.artel/config.json`; a durable change is an
   explicit edit the user asks for.
2. **`.artel/config.json`** — the project's standing answer.
3. **Built-in defaults** — the default config above, applied per key.
