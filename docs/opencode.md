# OpenCode host

Artel runs on **OpenCode 2.x** as a generated build: the canonical Claude Code sources are
transformed by `scripts/build_opencode.py` and wired in by the TypeScript bridge
(`opencode/plugin/artel.ts`). This doc is the operator guide for that host; everything else in
`docs/` applies to both hosts.

## Install

From an artel checkout:

```bash
scripts/install-opencode.sh           # install / refresh
scripts/install-opencode.sh --remove  # uninstall
```

This copies the plugin to `~/.config/opencode/artel/`, generates the OpenCode artifacts,
and places them into OpenCode's discovery directories:

| Location | Contents |
|---|---|
| `~/.config/opencode/skills/artel-<name>/` | every artel skill, prefixed: its `SKILL.md`, and beside it the skill's other Markdown files (the head and tail files of `artel-feature-development`, templates, references) |
| `~/.config/opencode/commands/artel-<name>.md` | `/artel-<name>` command wrappers |
| `~/.config/opencode/agents/artel-<name>.md` | the agent crew, prefixed |
| `~/.config/opencode/plugins/artel.ts` | the bridge plugin |
| `~/.config/opencode/artel/` | full plugin copy (hooks, scripts, docs) |
| `~/.config/opencode/.artel-install-manifest` | every path the last install placed |

Restart OpenCode after installing. Paths are baked at install time — re-run the installer
after moving or significantly updating the checkout. Respects `XDG_CONFIG_HOME`.

Requires **OpenCode 2.x** — the bridge plugin requires OpenCode 2.x (v1 does not run v2 plugin
implementations); the installer warns on an older CLI.

A refresh and `--remove` delete exactly what the manifest records — skills retired upstream
are pruned, and a skill of your own that happens to be named `artel-…` is left alone. The
manifest records each generated skill's directory, so every file inside it goes with it. An
install predating the manifest has none to read, so `--remove` falls back to sweeping every
`artel-*` artifact in those directories and says so before it does.

## How it works

**Generator.** OpenCode has no plugin namespace, so every artifact is `artel-`-prefixed.
Each generated skill/agent body keeps the canonical (Claude Code dialect) text with a
prepended `<OPENCODE-HOST-NOTES>` glossary translating it to the v2 host:

- `Skill:` → the `skill` tool (skills are `artel-<name>`).
- agent dispatch → the `subagent` tool with the `artel-<name>` agent.
- `SendMessage` → a fresh `subagent` dispatch; there is no resume-by-id, the agent re-reads
  its context files.
- `$0`/`$1`/`$ARGUMENTS` → the arguments the skill was invoked with.
- `AskUserQuestion` → the `question` tool.
- plan mode → present the plan and wait for approval (OpenCode has no plan-mode tool).
- `/init` → the `artel-agents-md-generator` skill.
- `CLAUDE.md` → `AGENTS.md` (OpenCode reads no `CLAUDE.md`).
- the v2 tool names: `Read`/`Edit`/`Write`/`Grep`/`Bash` → `read`/`edit`/`write`/`grep`/`shell`.

`disable-model-invocation: true` becomes `metadata.opencode/autoinvoke: false`, so a
manual-only skill stays registered and loadable by id but leaves the model's skill list.
`${CLAUDE_PLUGIN_ROOT}` is baked to the install root; `/artel:<name>` references become
`artel-<name>`. A skill's other Markdown files — `feature-development`'s `heads/*.md` and
`tail.md`, templates, references — are generated next to its `SKILL.md`, baked the same way
but with no frontmatter and no glossary: their reader already has the glossary through
`SKILL.md`. A pointer into a skill's directory (`${CLAUDE_PLUGIN_ROOT}/skills/<name>/<file>`)
resolves to the generated copy under
`~/.config/opencode/artel/opencode/dist/skills/artel-<name>/`, never to the Claude-dialect
source.

**Bridge plugin.** `opencode/plugin/artel.ts` adapts OpenCode 2.x's plugin API onto the
unchanged Python hooks (`hooks/README.md` documents their stdin/stdout contracts):

| OpenCode v2 | artel hook | Behavior |
|---|---|---|
| `tool.hook("execute.before")` (edit/write/patch) | `sensitive_guard.py`, then `spec_store_guard.py` | deny → the tool call errors naming the guard (`hooks.json`'s order) |
| `tool.hook("execute.before")` (read) | `spec_store_guard.py` | deny → the read errors with the `image fetch` hint (spec-storage.md §6) |
| `tool.hook("execute.before")` (shell, platform-named tools) | `vcs_guard.py` | deny → the tool call errors |
| `tool.hook("execute.after")` (edit/write/patch) | `fast_verify_post_edit.py`, then `knowledge_mirror.py` | findings are appended to the tool result (`hooks.json`'s order; v2 has no failure channel here) |
| `session.hook("context")` | `using_artel.py` | router + host status prepended to the first user message in memory |
| `session.created` | `session_baseline.py` | baseline captured; child sessions skip |
| `session.execution.succeeded` | `stop_gate.py`, `verify_stop_gate.py` | block → `ctx.session.prompt` re-prompts the run; pass-through warnings go to the console |
| `session.compaction.ended` | — | router cache invalidated so host status refreshes |
| `session.deleted` | — | caches dropped |

`patch` (GPT-family models) carries no file path; the bridge reads the patch grammar's
`*** Add|Delete|Update File:` / `*** Move to:` headers and runs the guards per path.
Everything is inert unless the project has `.artel/config.json`.

## Differences from Claude Code

| Aspect | Claude Code | OpenCode |
|---|---|---|
| Stop gate | blocks the Stop event outright | the completed turn is visible; `session.execution.succeeded` re-prompts the model to continue (the hooks' own block caps — 5 and 2 consecutive — remain the loop bound, under a bridge-side fail-safe of 10 consecutive blocks that resets on any clean stop) |
| Router injection | SessionStart hook context, before the first prompt | `session.hook("context")` prepends it to the first user message on every model step (sessions only come into being with their first prompt, so there is no pre-prompt moment) |
| Two-phase skills (`researcher`, `planner`, …) | `SendMessage` resumes the agent by id | a fresh `subagent` dispatch; the agent re-reads its context files |
| Agent model tiers | `opus`/`sonnet` frontmatter, plus a per-dispatch `model` for the route helper, the reviewer's scope and the step-up rounds ([agents.md](agents.md) `## Models`) | dropped — subagents inherit the caller's model (override per agent in your `opencode.json`); a per-dispatch model has no effect |
| Orchestrator seat | `seat.enabled: true` runs the post-approval tail one layer down on the `seat` agent (`sonnet`), every pause relayed to the main thread ([autonomous-run.md](autonomous-run.md) §18) | `seat.enabled` is inert — the generated `feature-development` runs the tail inline (no per-dispatch models) |
| `inner-loop` model-invocation guard | `disable-model-invocation: true` | `metadata.opencode/autoinvoke: false`: hidden from the model's skill list, still loadable by id |
| A sketch on a question's option (`analysis`, `generate-vision`) | the option's `preview` field | the `question` tool has no such field: the sketch goes into the question text as a fenced block |
| Knowledge-mirror context | injected after edits | side effect only (the mirror still runs) |
| Invocation | `/artel:<name>` | `/artel-<name>` (command) or the `artel-<name>` skill |

## kartoteka behind a token

Since kartoteka 0.32.0 a daemon with `[auth]` on refuses every request without a bearer
token. The mirror hook reads the variable `knowledge.tokenEnv` names (config.md) and runs
unchanged on OpenCode. The MCP entry is OpenCode's own: a remote server under `mcp.servers`
in `opencode.json` takes `headers`, and `{env:KARTOTEKA_TOKEN}` reads the same variable the
hook reads —

```json
"mcp": {
  "servers": {
    "kartoteka": {
      "type": "remote",
      "url": "http://127.0.0.1:8734/mcp",
      "headers": {"Authorization": "Bearer {env:KARTOTEKA_TOKEN}"}
    }
  }
}
```

## Headless runs

`docs/autonomous-run.md` §12's headless guidance is Claude Code-specific. On OpenCode,
`opencode run "…"` drives a session from the CLI, and OpenCode's `permission` config
(the `opencode.json` equivalent of a curated allowlist) gates unattended tools — never a
bypass flag. The same run-state, journal and stop-gate machinery applies unchanged.

One caveat: `opencode run` ends on the session's terminal execution event and waits for idle;
a stop-gate re-prompt sent after that terminal is not reliably awaited, so a blocked run
shows one re-prompt whose continuation the CLI may cut off. Use `--continue` follow-up runs,
or the TUI, to watch the gate's full block/cap cycle. The gates' pass-through warnings (cap
reached, verify environment error) go to the console either way.

One more: on a freshly started server (for example `opencode run --standalone`, or the first
run against a just-booted service), `session.created` can fire before the bridge's event
subscription is live, so that first session gets no baseline — the verify stop gate then
fails open for it (it records the current findings as pre-existing). The long-lived service
and the TUI load plugins before any session and capture it.

## Verifying an install

The E2E smoke this port was verified with, distilled to a maintainer checklist.
Scratch repo with a minimal config (adapters `none`, a deliberately failing fast verify):

```bash
rm -rf /tmp/artel-oc-smoke && mkdir -p /tmp/artel-oc-smoke && cd /tmp/artel-oc-smoke
git init -q && mkdir -p .artel specs/.current
cat > .artel/config.json <<'JSON'
{"version": 1,
 "ticket": {"projectKey": "SMOKE", "pattern": "^(?:{projectKey}-)?(\\d+)(?:-p?(\\d+))?$", "phaseSuffix": true},
 "tracker": {"adapter": "none", "mcpToolPrefix": ""},
 "vcs": {"adapter": "github-cli", "mcpToolPrefix": ""},
 "verify": {"commands": [], "fast": "sh -c \"echo 'smoke-lint: planted failure' >&2; exit 1\""},
 "knowledge": {"adapter": "none", "baseUrl": "", "project": ""}}
JSON
echo "# smoke" > README.md && git add -A && git commit -qm init
```

Five probes — each an `opencode run "<prompt>"` from that repo:

| Probe | Prompt | Pass |
|---|---|---|
| Plugin load + session lifecycle | `Reply with exactly: ok` | the plugin loads with no schema error; the reply is `ok`; a `baseline-<session>.json` appears under `.artel/run/.hooks/` |
| Router | `In one line: which skill do you route a ticket feature request to?` | names the router (`artel-feature-development`) |
| Sensitive guard | arm a run (`.active_ticket` = `SMOKE-1` + `.artel/run/SMOKE-1/run-state.json`, `run_active: true`), then `Create the file .env.local containing hello` | write denied naming the guard; `.env.local` absent |
| Fast verify + stop gate | un-arm, dirty `README.md`, `Append 'x' to README.md, then reply done.` | findings surface in the tool result; a `stopblocks-<session>.json` counter appears; at cap 2 the stop passes with a warning |
| Skill surface | `List the artel-* skills you can load, names only` | `artel-feature-development` is visible; `artel-inner-loop` is hidden from the model's list but loadable by id |

When a probe fails: rerun with `--print-logs --log-level DEBUG`. Plugin load errors appear
at startup ("Plugin must export a default definition with an id and an effect or setup
function" = a v1 bridge or a malformed export). The SQLite transcripts
(`~/.local/share/opencode/opencode.db`) show whether an injection persisted or was delivered
in-memory only.

Verified against OpenCode **2.0.19** (plugin SDK 2.0.21). Re-run this smoke after upgrading
OpenCode.

## Troubleshooting

- **A skill does not appear** — check `~/.config/opencode/skills/artel-<name>/SKILL.md`
  exists, its frontmatter has `name` matching the directory, and restart OpenCode.
- **Plugin load errors** — `Plugin must export a default definition with an id and an effect
  or setup function` means a v1 bridge or a malformed export: reinstall from this checkout
  (`scripts/install-opencode.sh`) so `opencode/plugin/artel.ts` is the v2 bridge. Check
  `ARTEL_ROOT` (default `~/.config/opencode/artel`) points at a full plugin copy.
- **No router injection / stop gate never fires** — rerun with `--print-logs --log-level
  DEBUG`; the bridge reports failures on the console with an `artel:` prefix.
- **An agent or command is missing** — `opencode debug agents` lists what OpenCode actually
  discovered; a stale install is fixed by re-running the installer.
