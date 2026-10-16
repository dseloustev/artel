# OpenCode plugin

Artel is an **OpenCode plugin**: `skills/`, `agents/`, the Python hooks and the bridge plugin
in `opencode/plugin/artel.ts` are what OpenCode reads; only the command wrappers are generated
from them. There is no second host. This doc is the plugin's install and architecture guide;
the workflow itself is [workflow-guide.md](workflow-guide.md).

## Install

From an artel checkout:

```bash
scripts/install-opencode.sh           # install / refresh
scripts/install-opencode.sh --remove  # uninstall
```

This copies the plugin to `~/.config/opencode/artel/`, bakes it, and places its artifacts into
OpenCode's discovery directories:

| Location | Contents |
|---|---|
| `~/.config/opencode/skills/artel-<name>/` | every artel skill: its `SKILL.md`, and beside it the skill's other Markdown files (the head and tail files of `artel-feature-development`, templates, references) |
| `~/.config/opencode/commands/artel-<name>.md` | `/artel-<name>` command wrappers |
| `~/.config/opencode/agents/artel-<name>.md` | the agent crew |
| `~/.config/opencode/plugins/artel.ts` | the bridge plugin |
| `~/.config/opencode/artel/` | full plugin copy (hooks, scripts, docs, skills, agents) |
| `~/.config/opencode/.artel-install-manifest` | every path the last install placed |

Restart OpenCode after installing. Paths are baked at install time — re-run the installer
after moving or significantly updating the checkout. Respects `XDG_CONFIG_HOME`.

Requires **OpenCode 2.x** — the bridge plugin requires OpenCode 2.x (v1 does not run v2 plugin
implementations); the installer warns on an older CLI.

A refresh and `--remove` delete exactly what the manifest records — skills retired upstream
are pruned, and a skill of your own that happens to be named `artel-…` is left alone. The
manifest records each installed skill's directory, so every file inside it goes with it. An
install predating the manifest has none to read, so `--remove` falls back to sweeping every
`artel-*` artifact in those directories and says so before it does.

## Architecture

**The source tree is the artifact.** OpenCode discovers skills, agents and commands from flat
config directories with no plugin namespace, so the installed name is the canonical name:
`skills/artel-<name>/SKILL.md`, `agents/artel-<name>.md`. Nothing is translated on the way in —
the Markdown in this repository is the Markdown OpenCode reads, and the installer places the
skills and agents from the baked copies of `skills/` and `agents/` themselves.

Two things happen at install time (`scripts/build_opencode.py`, run by the installer):

1. **Baking.** Every `*.md` under the install copy's `skills/`, `agents/` and `docs/` has the
   braced install-root token `${ARTEL_ROOT}` replaced with the install root, in place in the
   installed copy and never in the checkout — a `--root` that is the source checkout is refused
   before the first write. Baking matches the braced form only; this doc
   names the token `$ARTEL_ROOT`, without braces, so the bake cannot rewrite its own
   explanation. Python files are never rewritten: a hook docstring's token is a source-level
   pointer to a contract file, and nothing expands it. A pointer into a skill's directory
   resolves to that skill's installed copy under
   `~/.config/opencode/artel/skills/artel-<name>/`.
2. **Command wrappers.** Each skill gets a `/artel-<name>` wrapper under the build's
   `commands/` — the build's only output, so `dist` holds command wrappers and nothing else.
   Its body loads the skill and passes the arguments; its `description` carries
   the skill's `argument-hint` (for example `· args: [ticket-id]`), so the TUI command list
   shows what arguments the skill expects before you invoke it.

A manual-only skill declares `metadata.opencode/autoinvoke: false`, so it stays registered and
loadable by id but leaves the model's skill list.

**Bridge plugin.** `opencode/plugin/artel.ts` adapts OpenCode 2.x's plugin API onto the Python
hooks, whose stdin/stdout contracts are artel's own internal hook ABI
([hooks/README.md](../hooks/README.md)):

| OpenCode v2 | artel hook | Behavior |
|---|---|---|
| `tool.hook("execute.before")` (edit/write/patch) | `sensitive_guard.py`, then `spec_store_guard.py` | deny → the tool call errors naming the guard (this order) |
| `tool.hook("execute.before")` (read) | `spec_store_guard.py` | deny → the read errors with the `image fetch` hint (spec-storage.md §6) |
| `tool.hook("execute.before")` (shell, platform-named tools) | `vcs_guard.py` | deny → the tool call errors |
| `tool.hook("execute.after")` (edit/write/patch) | `fast_verify_post_edit.py`, then `knowledge_mirror.py` | findings are appended to the tool result (this order; v2 has no failure channel here) |
| `session.hook("context")` | `using_artel.py` | router + host status prepended to the first user message in memory |
| `session.created` | `session_baseline.py` | baseline captured; child sessions skip |
| `session.execution.succeeded` | `stop_gate.py`, `verify_stop_gate.py` | block → `ctx.session.prompt` re-prompts the run; the hooks' own caps (5 and 2 consecutive) remain the loop bound, under a bridge-side fail-safe of 10 consecutive blocks that resets on any clean stop; pass-through warnings go to the console |
| `session.compaction.ended` | — | router cache invalidated so host status refreshes |
| `session.deleted` | — | caches dropped |

`patch` (GPT-family models) carries no file path; the bridge reads the patch grammar's
`*** Add|Delete|Update File:` / `*** Move to:` headers and runs the guards per path.
Everything is inert unless the project has `.artel/config.json`.
The event stream is server-wide, so the bridge filters session events to sessions whose own
`location.directory` is this checkout — `session.created` carries it, execution events do not
(the session is read once via `ctx.session.get` and cached) — and never baselines or
stop-gates another checkout's sessions.

**Dispatch and models.** A skill reaches an agent through the `subagent` tool — a fresh
dispatch, never a resume-by-id, so a re-dispatched agent re-reads its context files. Each
dispatch carries the model its site resolves from `models.opencode` in the host's
`.artel/config.json` through `scripts/models.py`: per-dispatch models are resolved from
`models.opencode` and passed on the `subagent` call, and unset → the child inherits the
caller's model — except in the `planner` family, where an unset `planner.<kind>` resolves
`planner.default` first. The site table is [agents.md](agents.md) `## Models`.

**Questions.** A sketch attached to a question's option goes into the question text as a
fenced block — a flow, structure or chart sketch is a `mermaid` block, which the OpenChamber
client renders inline.

**MCP tool names.** An MCP tool reaches these hooks as `<server>_<tool>` — one underscore, no
`mcp__` prefix; each half is sanitized to `[A-Za-z0-9_-]`, anything else becoming `_`, so a
server `aiguard` with a tool `bitbucket_create_pr` arrives as `aiguard_bitbucket_create_pr`.
That is what the table's `platform-named tools` means: the `bitbucket`/`github`/`jira` token
`vcs_guard.py` classifies by is read from the combined string, and it is found when either
half carries it. (Checked 2026-10-02 against OpenCode 2.0.21's registry source and a live
2.0.19 session's recorded tool names.)

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

`docs/autonomous-run.md` §12 is the headless run book. `opencode run "…"` drives a session
from the CLI, and OpenCode's `permission` config in `opencode.json` gates unattended tools —
never a bypass flag. The same run-state, journal and stop-gate machinery applies unchanged.

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

The E2E smoke this plugin was verified with, distilled to a maintainer checklist.
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
| Fast verify + stop gate | un-arm; leave `README.md` clean at session start — the run's edit dirties it; `Append 'x' to README.md, then reply done.` | findings surface in the tool result; a `stopblocks-<session>.json` counter appears; at cap 2 the stop passes with a warning |
| Skill surface | `List the artel-* skills you can load, names only` | `artel-feature-development` is visible; `artel-inner-loop` is hidden from the model's list but loadable by id |

When a probe fails: rerun with `--print-logs --log-level DEBUG`. Plugin load errors appear
at startup ("Plugin must export a default definition with an id and an effect or setup
function" = a v1 bridge or a malformed export). The SQLite transcripts
(`~/.local/share/opencode/opencode.db`) show whether an injection persisted or was delivered
in-memory only.

Verified against OpenCode **2.0.29** (2026-10-15). Re-run this smoke after upgrading
OpenCode.

The probes run isolated: install and every `opencode run` with `XDG_CONFIG_HOME` pointed at a
throwaway config home, plus `--standalone` on each run — a bare `scripts/install-opencode.sh`
derives its source root from the script's own location and installs this checkout over the real
`~/.config/opencode/`.

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
