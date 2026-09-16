# hooks/

Quality gates: `hooks.json` registers eight Python hooks via `${CLAUDE_PLUGIN_ROOT}` paths.
Requires `python3` on the host. Verify commands come from host config
(`.artel/config.json` — [config.md](../docs/config.md)), never hardcoded.

Five layers:

- **Run layer** — enforces the autonomous-run contract
  ([autonomous-run.md](../docs/autonomous-run.md)):
  - `stop_gate.py` (`Stop`) — blocks a session from ending while a run is armed
    (`run_active: true`, `completed: false`, `pause_reason: null` in
    `.artel/run/<TICKET_ID>/run-state.json`). Fail-safes: disarms past
    `WALL_CLOCK_HOURS = 3` (stale run) and after `MAX_CONSECUTIVE_BLOCKS = 5`
    (counter: `.artel/run/<TICKET_ID>/.stop-gate-blocks`); fails open on infra errors.
  - `sensitive_guard.py` (`PreToolUse` on `Edit|Write|MultiEdit`) — during an armed run,
    denies edits to paths matched by the sensitive-paths policy when the category's floor
    outranks the effective mode, or before `TASKLIST_READY` is in `gates_confirmed`.
    Policy: [`sensitive-paths.json`](sensitive-paths.json) (shipped defaults: `secrets` and
    `gate-config` at `full-gates`, `ci-cd` at `plan-gate`), replaced **wholesale** by a host
    `.artel/sensitive-paths.json` when present. Inert outside armed runs.
- **Platform layer** — always armed, never gated on a run: enforces which VCS and tracker
  platform this project actually uses, in every session, not only autonomous ones.
  - `vcs_guard.py` (`PreToolUse` on `Bash|mcp__.*`) — denies any call that **writes** to a VCS
    or tracker platform other than the one `.artel/config.json` declares. **Always armed**,
    unlike `sensitive_guard.py` (the Run layer's own `PreToolUse` guard): a project that has
    moved to GitHub must not post to Bitbucket in any session, autonomous run or not. Domain
    routing is by platform, not tool name: `gh pr`/`repo`/`release`/`api` and Bitbucket-named MCP
    tools answer to `vcs.adapter`; `gh issue` and Jira-named MCP tools answer to
    `tracker.adapter`; a GitHub-named MCP tool is judged against **both** domains, since GitHub
    hosts both pull requests and issues. config.md requires `gh` for issues even when the VCS
    adapter is not `github-cli`, so one adapter cannot govern both. Verb extraction is
    positional, never substring: for an MCP tool it is the first recognized verb among the
    `_`-separated segments after the platform segment, and for `gh` the subcommand after the
    noun — substring matching would read the write token `comment` inside
    `bitbucket_get_pr_comments` and deny a read that `migrate-prs` depends on. An unrecognized
    verb is **denied** (fail closed), so a tool name nobody anticipated cannot become the hole
    in the guarantee; `guard.extraReadTools` ([config.md](../docs/config.md)) rescues an
    unrecognized verb only, never a recognized write. Fails open where artel is not in charge: a
    missing or unparseable config allows everything, and `tracker.adapter: "none"` leaves the
    tracker domain unenforced (`vcs.adapter` has no `"none"`, so the VCS domain is always
    enforced). Two deliberate gaps: it does not inspect `git push`, so a stale `origin` still
    pushes to the old host (`/artel:set-home` moves it), and it runs no entry-point preflight — a
    mismatch surfaces at the moment of the call.
- **Verify layer** — same-session quality feedback, driven by `verify.fast` /
  `verify.surface` (config.md) through the `scripts/verify.py` envelope
  (exit 0 clean / 1 findings / 2 environment error):
  - `session_baseline.py` (`SessionStart`) — captures the findings baseline so pre-existing
    findings never block.
  - `fast_verify_post_edit.py` (`PostToolUse` on `Edit|Write|MultiEdit`) — runs `verify.fast`
    scoped to the edited file for same-turn feedback; never blocks.
  - `verify_stop_gate.py` (`Stop`) — blocks completion only while findings **new vs the
    session baseline** stay red; after `MAX_CONSECUTIVE_BLOCKS = 2` it latches open with a
    loud warning. Escape hatch: delete `.artel/run/.hooks/baseline-<session_id>.json` to
    re-baseline on the next stop.
- **Knowledge layer** — optional, driven by `knowledge.adapter` / `knowledge.baseUrl` /
  `knowledge.project` / `knowledge.tokenEnv` (config.md):
  - `knowledge_mirror.py` (`PostToolUse` on `Edit|Write|MultiEdit`) — posts each
    deliberation artifact written under `<specs.dir>/<TICKET>/` to a kartoteka artifact
    store as it is written, under `knowledge.project`. Additive and best-effort: files on
    disk stay primary, nothing blocks, nothing retries, every attempt is logged to
    `.artel/run/.hooks/knowledge-mirror.log`. Inert unless `knowledge.adapter` is
    `"kartoteka"`; with the adapter on and `baseUrl` or `project` missing it logs one
    `misconfigured` line per mirrorable edit and sends nothing. When `knowledge.tokenEnv`
    names a variable that is set, the request carries `Authorization: Bearer` from it (a
    daemon with `[auth]` on — kartoteka 0.32.0 — refuses everything else); a `401` is logged
    as `reject` when a token was sent (revoked or expired: `kartoteka token list` on the
    daemon host) and as `misconfigured` when none was, naming the empty key or the unset
    variable. An unset variable sends the request unauthenticated rather than failing, so one
    committed config serves an auth-off loopback daemon and a hosted one. The token itself
    never reaches the log: a value with whitespace or control characters, or a plaintext
    `http://` `baseUrl` off loopback while a token is present, is `misconfigured` by name and
    nothing is sent, and an exception text is redacted before it is logged.
- **Session layer** — turn-one routing, no gate:
  - `using_artel.py` (`SessionStart`, matcher `startup|clear|compact`) — injects the
    `using-artel` router skill (frontmatter stripped) plus the host-status lines as
    `additionalContext`, so the routing rule is in context on turn one and comes back after
    `/clear` and compaction. The lines: `config: present`; `knowledge.adapter` with `baseUrl`
    and `project`; with the adapter `kartoteka`, `knowledge.tokenEnv` and whether the variable
    it names is set in the session's environment (never its value); the raw `.active_ticket`
    pointer; and whether the `ast-index` CLI is on PATH (`shutil.which`, no subprocess). Prints nothing without `.artel/config.json`; any
    error goes to stderr and the hook still exits 0 — a session never fails to start
    because of it. The `ast-index` line reflects the PATH Claude Code was launched with, not
    the Bash tool's shell profile: a launch from an IDE can say `not on PATH` for a CLI that
    `/opt/homebrew/bin` supplies in a terminal. A config that is present but not valid JSON
    is named as such rather than reported as `knowledge.adapter: none`.

Hook state lives in the host repo at `.artel/run/.hooks/` (session baselines, verify-stop
counters) — never inside the plugin directory. The verify-layer, session-layer and platform-layer
hooks return immediately when `.artel/config.json` does not exist, so an installed-but-unconfigured
plugin leaves zero footprint; `hook_common.py` is the shared helper library, not a registered hook.

## The OpenCode bridge

On OpenCode the same scripts run unchanged, driven by
`opencode/plugin/artel.ts` instead of `hooks.json`: it synthesizes the stdin payloads
this README documents and maps the JSON outputs onto OpenCode's mechanisms (deny by
throwing, router context prepended in-memory to the first user message, Stop-block
by an idle re-prompt). One output has
no OpenCode channel: `knowledge_mirror.py`'s `additionalContext` is dropped (the mirror
side effect still runs). See `docs/opencode.md`.
