# OpenCode runtime registration vs the generated snapshot

> **Status: evaluated 2026-10-20, not adopted.** This document records the evidence behind the
> decision-log entry of the same date in [design.md](../design.md), which closes the 2026-10-01
> follow-up that parked the transform path.

## The question

OpenCode 2.x exposes `ctx.skill.transform`, `ctx.agent.transform` and `ctx.command.transform`.
Could they register artel's 42 skills, 14 agents and 42 command wrappers at runtime, retiring
the generated install snapshot, its staleness and the install manifest
([design.md](../design.md), decision 2026-10-01: "parked as a follow-up")?

## What the API actually does (2.0.32)

- **Skills** — `SkillEditor` has `list`, `get`, `add`, `update`, `remove`. `add` takes a
  `Skill.Info` whose `content` is the body string and whose `path` is an absolute path that
  need not exist. The built-in `opencode.skill` plugin adds two skills at synthetic
  `/builtin/*.md` paths; the live `/api/skill` lists them, and `/builtin` does not exist.
- **Commands** — `CommandEditor` has **only `add`**, taking `{name, description?, execute}`;
  `execute` receives `{sessionID, prompt, delivery}` and prompts the session itself. The
  built-in `opencode.command` plugin adds `init` and `review` with no files on disk, and the
  live `/api/command` lists them.
- **Agents** — `AgentEditor` has **no `add`**; `update(id, fn)` creates a default agent when the
  id is missing, so it is an upsert. The built-in `opencode.plan` plugin adds the `plan` agent
  this way.
- **Timing** — transforms are registered per location at plugin setup and re-applied on
  `reload`; the callback is synchronous. Content is captured when the transform runs, not per
  prompt.
- **Stability** — the transforms are not marked experimental in `@opencode/plugin@2.0.32` (the
  package version matching the CLI); the built-ins and the config-discovery plugins use them.
  The caveat is the 2.x line's age, not an experimental label: the preview package
  (`@opencode-ai/plugin@1.18.21`) still ships older v2 types without `skill.add` and
  `agent.update` upsert, so the reference must be pinned to 2.0.32.

## What it would replace

- 42 flat skill registrations and 42 command wrappers under `~/.config/opencode/`.
- 14 flat agent registrations.
- The 101-entry `.artel-install-manifest`, after one migration release: the installed paths
  shrink to the plugin copy and the two `plugins/*.ts` files, which the uninstaller can
  hardcode. The old manifest is still needed once, to prune the flat layout on upgrade — the
  config-discovery plugins run *after* artel and would otherwise overwrite the runtime
  registrations with the stale files.

## What it cannot replace

The `${ARTEL_ROOT}` bake. A transform bakes only the content it registers, and artel references
the token outside those two content classes:

| Tree | Files | Refs | Handled by a transform? |
|---|---:|---:|---|
| `skills/*/SKILL.md` (registered content) | 41 | 414 | yes |
| `agents/*.md` (registered system prompt) | 14 | 173 | yes |
| skill companion files (`heads/*.md`, `tail.md`, `parallel.md`) | 5 | 73 | no |
| `docs/` (read by agents through the `read` tool) | 12 | 38 | no |

So the snapshot would shrink to "runtime tree + 17 baked files (111 refs)", not disappear. The
runtime tree (`hooks/`, `scripts/`, docs, companions) is still copied and still read from disk.

Reaching full retirement needs one more mechanism: a `ctx.tool.transform` wrapper on `read`
that substitutes the token for paths under the artel root, a plugin-generated baked cache, or
a canonical rewrite of those 17 files to be root-agnostic.

## Failure modes and costs

- **A throwing transform disables the whole plugin** — hooks included. The TS side would need a
  frontmatter parser (including `metadata.opencode/autoinvoke: false`, or 7 manual-only skills
  become auto-invokable) with per-file error containment, where today a malformed file fails
  the build instead.
- **Ordering** — the config-discovery plugins run after artel and would overwrite same-id
  registrations from a stale flat install; the installer's prune step becomes load-bearing.
- **Commands are add-only** — a stale flat command can only be removed by deleting its file.
- **Test surface** — about 30 build tests and the plugin-surface assertions move from
  build-time Python to runtime TypeScript, which needs a Node harness for what is currently
  deterministic file output.
- **Version pin** — the API is tied to the host version; artel already pins the bridge to the
  proven 2.0.32 surface.

## Verdict

**Partial retirement, not adopted.** The transforms are real and could delete the flat
registrations, the wrappers and the manifest, but the bake — the staleness the follow-up
actually wanted to remove — remains for 17 files. Trading a deterministic, testable Python
generator for runtime TS registration to remove 98 copies while keeping the bake is a poor
trade at this size, and a throwing transform would take the hooks down with it.

**Revisit if** the 17 files become root-agnostic (a docs refactor that needs no transforms), or
if artel is distributed as an npm package, where the runtime tree *is* the package and the
install manifest has nothing left to track.
