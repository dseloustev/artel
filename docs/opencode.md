# OpenCode host

Artel runs on OpenCode as a generated build: the canonical Claude Code plugin is
transformed by `scripts/build_opencode.py` and wired into OpenCode by a small TypeScript
bridge. This doc is the operator guide for that host; everything else in `docs/` applies
to both hosts.

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
| `~/.config/opencode/skills/artel-<name>/SKILL.md` | every artel skill, prefixed |
| `~/.config/opencode/commands/artel-<name>.md` | `/artel-<name>` command wrappers |
| `~/.config/opencode/agents/artel-<name>.md` | the agent crew, prefixed |
| `~/.config/opencode/plugins/artel.ts` | the bridge plugin |
| `~/.config/opencode/artel/` | full plugin copy (hooks, scripts, docs) |
| `~/.config/opencode/.artel-install-manifest` | every path the last install placed |

Restart OpenCode after installing. Paths are baked at install time — re-run the installer
after moving or significantly updating the checkout. Respects `XDG_CONFIG_HOME`.

A refresh and `--remove` delete exactly what the manifest records — skills retired upstream
are pruned, and a skill of your own that happens to be named `artel-…` is left alone. An
install predating the manifest has none to read, so `--remove` falls back to sweeping every
`artel-*` artifact in those directories and says so before it does.

## How it works

**Generator.** OpenCode has no plugin namespace, so every artifact is `artel-`-prefixed.
Each generated skill/agent body keeps the canonical (Claude Code dialect) text with a
prepended `<OPENCODE-HOST-NOTES>` glossary mapping the dialect to OpenCode tools
(`Skill:` → the `skill` tool, `subagent_type` → the `task` tool, `SendMessage` → a fresh
`task` dispatch, `AskUserQuestion` → the `question` tool, `$0`/`$ARGUMENTS` → the invoked
arguments). `${CLAUDE_PLUGIN_ROOT}` is baked to the install root; `/artel:<name>`
references become `artel-<name>`.

**Bridge plugin.** `opencode/plugin/artel.ts` adapts OpenCode events onto the unchanged
Python hooks (`hooks/README.md` documents their stdin/stdout contracts):

| OpenCode | artel hook | Behavior |
|---|---|---|
| `tool.execute.before` (edit/write/apply_patch) | `sensitive_guard.py`, then `spec_store_guard.py` | deny → the tool call errors (either guard, `hooks.json`'s order) |
| `tool.execute.before` (read) | `spec_store_guard.py` | deny → the read errors with the `image fetch` hint (`hooks.json`'s `Read` matcher; spec-storage.md §6) |
| `tool.execute.after` (edit/write/apply_patch) | `knowledge_mirror.py`, then `fast_verify_post_edit.py` | findings → the tool result carries them. Reverse of `hooks.json`'s order on purpose: findings leave the handler by throwing, which would skip a mirror queued behind them |
| `session.created` | `session_baseline.py` | findings baseline captured (child sessions skip it) |
| `experimental.chat.messages.transform` | `using_artel.py` | router + host status prepended to the first user message on every model step (in-memory) |
| `session.idle` | `stop_gate.py`, `verify_stop_gate.py` | block → a re-prompt continues the run; pass-through warnings are mirrored into the app log |

Everything is inert unless the project has `.artel/config.json`.

## Differences from Claude Code

| Aspect | Claude Code | OpenCode |
|---|---|---|
| Stop gate | blocks the Stop event outright | the completed turn is visible; the idle event re-prompts the model to continue (the hooks' own block caps — 5 and 2 consecutive — remain the loop bound, under a bridge-side fail-safe of 10 consecutive blocks that resets on any clean stop) |
| Router injection | SessionStart hook context, before the first prompt | prepended to the first user message on every model step (sessions only come into being with their first prompt, so there is no pre-prompt moment) |
| Two-phase skills (`researcher`, `planner`, …) | `SendMessage` resumes the agent by id | a fresh `task` dispatch; the agent re-reads its context files |
| Agent model tiers | `opus`/`sonnet` frontmatter | dropped — subagents inherit the caller's model (override per agent in your `opencode.json`) |
| `inner-loop` model-invocation guard | `disable-model-invocation: true` | no equivalent; it is loadable like any skill |
| Knowledge-mirror context | injected after edits | side effect only (the mirror still runs) |
| Invocation | `/artel:<name>` | `/artel-<name>` (command) or the `artel-<name>` skill |

## kartoteka behind a token

Since kartoteka 0.32.0 a daemon with `[auth]` on refuses every request without a bearer
token. The mirror hook reads the variable `knowledge.tokenEnv` names (config.md) and runs
unchanged on OpenCode. The MCP entry is OpenCode's own: a remote server in `opencode.json`
takes `headers`, and `{env:KARTOTEKA_TOKEN}` reads the same variable the hook reads —

```json
"kartoteka": {"type": "remote", "url": "http://127.0.0.1:8734/mcp",
              "headers": {"Authorization": "Bearer {env:KARTOTEKA_TOKEN}"}}
```

## Headless runs

`docs/autonomous-run.md` §12's headless guidance is Claude Code-specific. On OpenCode,
`opencode run "…"` drives a session from the CLI, and OpenCode's `permission` config
(the `opencode.json` equivalent of a curated allowlist) gates unattended tools — never a
bypass flag. The same run-state, journal and stop-gate machinery applies unchanged.

One caveat: `opencode run` exits on the session's first idle event, so a stop-gate block
shows as one re-prompt message in the transcript whose continuation is cut off (the
CLI is already tearing down). Use `--continue` follow-up runs, or the TUI, to watch the
gate's full block/cap cycle. The gates' pass-through warnings (cap reached, verify
environment error) are mirrored into OpenCode's app log either way.

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
| Session lifecycle | `Reply with exactly: ok` | reply is `ok`; a `baseline-<session>.json` appears under `.artel/run/.hooks/` |
| Router | `In one line: which skill do you route a ticket feature request to?` | names the router (`artel-feature-development`) |
| Sensitive guard | arm a run (`.active_ticket` = `SMOKE-1` + `.artel/run/SMOKE-1/run-state.json`, `run_active: true`), then `Create the file .env.local containing hello` | write denied naming the guard; `.env.local` absent |
| Fast verify + stop gate | un-arm, dirty `README.md`, `Append 'x' to README.md, then reply done.` | findings surface as the tool error; a `stopblocks-<session>.json` counter appears; at cap 2 the stop passes with a warning |
| Skill surface | `List the artel-* skills you can load, names only` | `artel-feature-development`, `artel-dev`, `artel-using-artel`, `artel-inner-loop` present |

When a probe fails: rerun with `--print-logs --log-level DEBUG`. Plugin load errors appear
at startup ("Plugin export is not a function" = a non-function export). The SQLite
transcripts (`~/.local/share/opencode/opencode.db`) show whether an injection persisted
or was delivered in-memory only.

Verified against OpenCode **1.18.x**. The bridge leans on upstream plugin API details that
are undocumented and can move (`experimental.chat.messages.transform`, event payload
shapes) — re-run this smoke after upgrading OpenCode.

## Troubleshooting

- **A skill does not appear** — check `~/.config/opencode/skills/artel-<name>/SKILL.md`
  exists, its frontmatter has `name` matching the directory, and restart OpenCode.
- **No router injection / stop gate never fires** — the event payload shapes are not
  documented upstream; the bridge extracts the session id from `info.id` /
  `sessionID` / `session_id`. If an OpenCode update changes the shape, extend
  `sessionId()` in `opencode/plugin/artel.ts`.
- **Plugin errors on startup** — run `bun build ~/.config/opencode/plugins/artel.ts
  --target=node --outdir /tmp/x` to surface syntax problems; check `ARTEL_ROOT` (default
  `~/.config/opencode/artel`) points at a full plugin copy. Also check the plugin's
  exports: OpenCode's loader requires every module export to be a function and rejects
  the whole plugin otherwise ("Plugin export is not a function") — run with
  `--print-logs` to see the load error.
