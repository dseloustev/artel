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
- `.artel/run/` — host-writable run state and journals (contract defined separately, alongside
  the autonomous-run rules). Not committed; add it to the host `.gitignore`.
- `.artel/context/` — the `save-context`/`restore-context` store: a durable, host-repo-local
  mirror of root docs (`CLAUDE.md`, `CHANGELOG.md`) and the spec trail (`<specs.dir>/<TICKET_ID>/`,
  `<specs.dir>/.active_ticket`), used to declutter or archive the working tree and bring it back
  later. Not committed; add it to the host `.gitignore`, same as `.artel/run/`.

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
  "runtime": {}
}
```

The defaults are deliberately inert: no tracker calls, no quality gate, no runtime gate, no
design stage. Out of the box artel runs the pipeline and records every unconfigured gate as
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
| `ticket.pattern` | string (regex) | `"^(?:{projectKey}-)?(\\d+)(?:-p?(\\d+))?$"` | Must expose the ticket number as **capture group 1** and the optional phase as **capture group 2**. The literal token `{projectKey}` is replaced with `ticket.projectKey` before the regex is compiled. Matched case-insensitively. | Every phase-aware skill and agent; hooks that resolve the active ticket |
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
| `verify.commands` | array of strings | `[]` | Ordered shell commands forming the full gate. Run from the host repo root; the gate stops at the first non-zero exit. | Full-gate checks: inner loop, implementation checkpoints, validate stage, stop-gate hook, `deep-review`'s step-0 quality gate, `merge-conflicts`' post-resolution check |
| `verify.fast` | string | `""` | One quick command for per-edit feedback (lint/analyze of the touched scope, not the whole test suite). | Fast per-edit hook, `inner-loop`'s fast-check step, `add-automation`/`remove-automation`'s post-change check |

Commands must be non-interactive, exit non-zero on failure, and be safe to re-run. An empty
`verify.commands` degrades the gate to `skipped` — it is never reported as `green`. An empty
`verify.fast` makes the per-edit hook a no-op.

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

The trail is `<specs.dir>/<TICKET_ID>/` for ticket-wide artifacts, `<specs.dir>/<TICKET_ID>/phase-<N>/`
for phase-scoped ones, and `<specs.dir>/.active_ticket` for the in-flight identifier.
Release-scope artifacts (`R-<RELEASE_ID>` identifiers) live under `<specs.releases>/` instead:
`<specs.releases>/<RELEASE_ID>.md` (the release definition) and `<specs.releases>/<RELEASE_ID>/qa.md`
(the combined QA report).

### `runtime` — optional runtime and automation commands

All `runtime` keys are optional and adapter-shaped: a project supplies whichever commands it has,
and unset ones degrade to `skipped`.

| Key | Type | Default | Allowed values / notes | Consumed by |
|---|---|---|---|---|
| `runtime.run` | string | absent | Command that builds and launches the app | `run-app` — the `RUNTIME_OK` gate |
| `runtime.drive` | string | absent | Command that drives the running app (UI automation) | `drive-app` |
| `runtime.scaffold.add` | string | absent | Command that adds the transient automation scaffold | `add-automation` |
| `runtime.scaffold.remove` | string | absent | Command that removes it again | `remove-automation` |

An absent or empty command means the corresponding skill reports `not configured` and the
`RUNTIME_OK` gate is recorded as `skipped`. Missing runtime configuration never blocks a run.

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
    "fast": "npm run lint -- --cache"
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
  "runtime": {
    "run": "npm run dev -- --port 5173",
    "drive": "npm run e2e:drive",
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

- **Entry-point skills** (`feature-development`, `dev`) find no `.artel/config.json`, run a
  one-time init interview covering ticket grammar, tracker, VCS, verify commands and languages,
  write `.artel/config.json`, and continue into the requested run. The interview happens once per
  repo; afterwards the file is edited by hand (Phase 4 — see
  [porting-plan.md](porting-plan.md)).
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
