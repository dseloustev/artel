# Worktrees — one task, one worktree, one session

*Status: v0.1 · 2026-09-17*

Several sessions can work on one host repo at once when each ticket lives in its own git
worktree: its own branch, its own uncommitted files, generated code and `.artel/run/` state.
Artel moves a ticket's work into a worktree and hands it back when the task is done. This doc is
the contract the two skills and `init-branch` follow; the mechanics live in
`${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py`.

| Operation | Entry points |
|---|---|
| Move a ticket's work into a worktree | `/artel:init-branch` (asks), `/artel:move-to-worktree` |
| Hand the branch back to the main checkout | `/artel:return-from-worktree` |

## 1. The model

- **Location:** `.claude/worktrees/<name>`, where `<name>` is `<TICKET_ID>`, or
  `<TICKET_ID>-<PHASE_NUM>` when the run carries a phase. Claude Code enters paths there without
  a permission prompt, and a session already inside a worktree can switch only to paths there.
  When the host does not ignore that directory, the script adds `/.claude/worktrees/` to
  `.git/info/exclude` — local to the clone, never committed.
- **The session moves.** After the move, the current session continues inside the worktree
  (`EnterWorktree`). Other work goes to a new session in the main checkout.
- **Hand-back** returns the branch — with its uncommitted work — to the main checkout and
  removes the worktree. It never merges, never deletes the branch, never uses `--force`.
- **Main checkout** is the first entry of `git worktree list`. `move-in` runs from there.
  `hand-back` switches there by itself, and refuses to start inside a linked worktree — that
  directory is about to be deleted — except with `--check`, which changes nothing.

## 2. What moves

| What | At move-in | At hand-back |
|---|---|---|
| Uncommitted work, untracked files included | stashed, applied in the worktree | stashed, applied in the main checkout |
| Commits | nothing to do — the branch lives in the shared repository | same |
| `.artel/*` except `run/` and `context/` | copied, when ignored | not copied back |
| `.artel/context/` | symlinked to the main checkout's store, when it exists | nothing (a real store the worktree grew is merged back, newer wins) |
| `.artel/run/<TICKET_ID>/` | moved | merged back, newer wins |
| `.artel/run/.hooks/baseline-*.json`, `stopblocks-*.json` | copied, newer wins | copied back, newer wins |
| Other ignored files | per `.worktreeinclude` (§3) | not copied back; differences reported as `envChanged` |
| Dependencies, generated code, build output | never copied — `setup.commands` rebuilds them | same, in the main checkout |

A file the task branch tracks is never overwritten by a copy. `.claude/worktrees/` is never
copied. A stash is dropped only after it applied cleanly.

## 3. `.worktreeinclude`

The same file Claude Code reads for `claude -w`: gitignore syntax, at the repo root. A path is
copied when the main checkout ignores it **and** a pattern matches it. Without the file, the
patterns are `/.claude/` and `/.mcp.json`. The file selects host files only — artel's own
`.artel/` is handled per §2 regardless.

## 4. Move-in procedure

Skills follow these steps; `init-branch` supplies the branch arguments from its own Step 2.

1. **Arguments.** `--ticket <TICKET_ID> --name <name> --base <BASE_BRANCH> --branch <branch>`,
   plus how the branch comes to exist:

   | The branch | Extra arguments |
   |---|---|
   | exists locally | none |
   | exists only on `origin` | `--create-from origin/<branch> --track` |
   | is new | `--create-from origin/<BASE_BRANCH>` (the local `<BASE_BRANCH>` when `origin/<BASE_BRANCH>` is missing) |

2. **Run** from the main checkout:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py move-in --ticket <TICKET_ID> --name <name> --base <BASE_BRANCH> --branch <branch> [--create-from <ref> [--track]]
   ```

3. **Act on `status`** (§6). Only `ok` continues.
4. **Enter** the worktree: `EnterWorktree` with `path` set to the report's `path`.
   **OpenCode** has no `EnterWorktree`: print `cd <path> && opencode` and stop — the rest of the
   work happens in that new session.
5. Everything after this point runs inside the worktree.

## 5. Hand-back procedure

1. **Locate.** Inside a linked worktree (`git rev-parse --git-dir` differs from
   `git rev-parse --git-common-dir`), the ticket comes from the argument or the worktree's
   `.artel/worktree.json`, and `<name>` is the worktree directory's name. In the main checkout,
   the ticket comes from the argument. The main checkout's path is the first `worktree` line of
   `git worktree list --porcelain`.
2. **Check** — changes nothing:

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/worktree.py hand-back --ticket <TICKET_ID> --name <name> --check
   ```

3. **Leave** the worktree when the session is inside it: `ExitWorktree` with
   `action: "keep"`. If it reports that no worktree session is active (the session was started
   inside the worktree), stop without changes and tell the user to run
   `/artel:return-from-worktree <TICKET_ID>` from a session in the main checkout. **OpenCode:**
   the same message.
4. **Run** the same command without `--check` — the session is back in the main checkout.
5. **Act on `status`** (§6).

## 6. Statuses and recovery

Every run prints one JSON object; exit code 0 means `status: "ok"`.

| `status` | Meaning | What the skill does |
|---|---|---|
| `ok` | done | continue |
| `refused` | a precondition failed — nothing changed | report `reason`, stop |
| `conflict` | a stash did not apply; it is kept | report `stash` (its commit ID), `files` and `path`; tell the user to resolve the conflicts there, then `git stash drop` that entry; stop |
| `rolled-back` | a step failed after changes began; they were undone | report `reason`, stop |
| `error` | unexpected failure, or a rollback that could not finish | report `reason` verbatim, plus `stash` (the kept uncommitted work — `git stash apply <stash>` restores it), `path` and `mainNowOn` when present; stop |

An interrupted hand-back leaves `.artel/run/<TICKET_ID>/worktree.json` with
`"handBack": "pending"` in the main checkout; running hand-back again finishes it.

Refusals worth knowing: move-in from inside a worktree; a branch checked out in another
worktree; `.claude/worktrees/<name>` existing but not a worktree of that branch. Hand-back with
uncommitted changes in the main checkout — they may belong to another session — or with the
worktree on a detached HEAD.

## 7. Hooks

Artel's hooks start in `$CLAUDE_PROJECT_DIR`, which stays on the main checkout after
`EnterWorktree`. `hook_common.read_hook_input()` moves each hook into the linked worktree named
by the payload's `cwd` when it belongs to the same repository, so the gates check the worktree
the session actually edits ([hooks/README.md](../hooks/README.md)). A worktree without
`.artel/config.json` leaves the hooks inert.
