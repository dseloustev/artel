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

Restart OpenCode after installing. Paths are baked at install time — re-run the installer
after moving or significantly updating the checkout. Respects `XDG_CONFIG_HOME`.

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
| `tool.execute.before` (edit/write/apply_patch) | `sensitive_guard.py` | deny → the tool call errors |
| `tool.execute.after` (edit/write/apply_patch) | `fast_verify_post_edit.py`, `knowledge_mirror.py` | findings → the tool result carries them |
| `session.created` | `session_baseline.py`, `using_artel.py` | router + host status injected as context |
| `session.idle` | `stop_gate.py`, `verify_stop_gate.py` | block → a re-prompt continues the run |

Everything is inert unless the project has `.artel/config.json`.

## Differences from Claude Code

| Aspect | Claude Code | OpenCode |
|---|---|---|
| Stop gate | blocks the Stop event outright | the completed turn is visible; the idle event re-prompts the model to continue (the hooks' own block caps — 5 and 2 consecutive — remain the loop bound) |
| Two-phase skills (`researcher`, `planner`, …) | `SendMessage` resumes the agent by id | a fresh `task` dispatch; the agent re-reads its context files |
| Agent model tiers | `opus`/`sonnet` frontmatter | dropped — subagents inherit the caller's model (override per agent in your `opencode.json`) |
| `inner-loop` model-invocation guard | `disable-model-invocation: true` | no equivalent; it is loadable like any skill |
| Knowledge-mirror context | injected after edits | side effect only (the mirror still runs) |
| Invocation | `/artel:<name>` | `/artel-<name>` (command) or the `artel-<name>` skill |

## Headless runs

`docs/autonomous-run.md` §12's headless guidance is Claude Code-specific. On OpenCode,
`opencode run "…"` drives a session from the CLI, and OpenCode's `permission` config
(the `opencode.json` equivalent of a curated allowlist) gates unattended tools — never a
bypass flag. The same run-state, journal and stop-gate machinery applies unchanged.

## Troubleshooting

- **A skill does not appear** — check `~/.config/opencode/skills/artel-<name>/SKILL.md`
  exists, its frontmatter has `name` matching the directory, and restart OpenCode.
- **No router injection / stop gate never fires** — the event payload shapes are not
  documented upstream; the bridge extracts the session id from `info.id` /
  `sessionID` / `session_id`. If an OpenCode update changes the shape, extend
  `sessionId()` in `opencode/plugin/artel.ts`.
- **Plugin errors on startup** — run `bun build ~/.config/opencode/plugins/artel.ts
  --outdir /tmp/x` to surface syntax problems; check `ARTEL_ROOT` (default
  `~/.config/opencode/artel`) points at a full plugin copy.
