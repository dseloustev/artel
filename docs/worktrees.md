# Worktrees — one task, one worktree, one session

*Status: v0.1 · 2026-09-17*

Several sessions can work on one host repo at once when each ticket lives in its own git
worktree: its own branch, its own uncommitted files, generated code and `.artel/run/` state.
Artel moves a ticket's work into a worktree and hands it back when the task is done. This doc is
the contract the two skills and `init-branch` follow; the mechanics live in
`${ARTEL_ROOT}/scripts/worktree.py`.

| Operation | Entry points |
|---|---|
| Move a ticket's work into a worktree | `/artel-init-branch` (asks), `/artel-move-to-worktree` (asks) |
| Hand the branch back to the main checkout | `/artel-return-from-worktree` (asks) |

## 1. The model

- **Location:** `.opencode/worktrees/<name>`, where `<name>` is `<TICKET_ID>`, or
  `<TICKET_ID>-<PHASE_NUM>` when the run carries a phase. One directory per ticket under the
  host's own `.opencode/` tree, so parallel sessions do not collide and the host's config paths
  (`opencode.json`, `.opencode/`) never move with a worktree. When the host does not ignore
  that directory, the script adds `/.opencode/worktrees/` to `.git/info/exclude` — local to
  the clone, never committed.
- **The session moves.** After the move, the session continues inside the worktree: there is
  no tool that switches into it, so the skill prints `cd <path> && opencode` and stops. Other
  work goes to a new session in the main checkout.
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

A file the task branch tracks is never overwritten by a copy. `.opencode/worktrees/` is never
copied. A stash is dropped only after it applied cleanly.

## 3. `.worktreeinclude`

Gitignore syntax, at the repo root. A path is copied when the main checkout ignores it **and** a
pattern matches it. Without the file, the patterns are `/.opencode/`, `/opencode.json` and
`/opencode.jsonc`. The file selects host files only — artel's own `.artel/` is handled per §2
regardless.

## 4. Move-in procedure

Skills follow these steps; `init-branch` supplies the branch arguments from its own Step 2.

1. **Arguments.** `--ticket <TICKET_ID> --name <name> --base <BASE_BRANCH> --branch <branch>`,
   plus how the branch comes to exist:

   | The branch | Extra arguments |
   |---|---|
   | exists locally | none |
   | exists only on `origin` | `--create-from origin/<branch> --track` |
   | is new | `--create-from origin/<BASE_BRANCH>` (the local `<BASE_BRANCH>` when `origin/<BASE_BRANCH>` is missing) |

2. **Ask first.** Nothing moves without the user's answer: `init-branch` asks its worktree
   question, `move-to-worktree` asks once (header `Worktree`). Entering a worktree that already
   exists for the ticket needs no question — nothing moves.

3. **Run** from the main checkout:

   ```bash
   python3 ${ARTEL_ROOT}/scripts/worktree.py move-in --ticket <TICKET_ID> --name <name> --base <BASE_BRANCH> --branch <branch> [--create-from <ref> [--track]]
   ```

4. **Act on `status`** (§6). Only `ok` continues.
5. **Enter** the worktree: there is no tool that switches the running session into it — print
   `cd <path> && opencode` and stop. The rest of the work happens in that new session.
6. Everything after this point runs inside the worktree.

## 5. Hand-back procedure

1. **Locate.** Inside a linked worktree (`git rev-parse --git-dir` differs from
   `git rev-parse --git-common-dir`), the ticket comes from the argument or the worktree's
   `.artel/worktree.json`, and `<name>` is the worktree directory's name. In the main checkout,
   the ticket comes from the argument. The main checkout's path is the first `worktree` line of
   `git worktree list --porcelain`.
2. **Check** — changes nothing:

   ```bash
   python3 ${ARTEL_ROOT}/scripts/worktree.py hand-back --ticket <TICKET_ID> --name <name> --check
   ```

3. **Ask.** Confirm once (header `Hand back`), naming the branch the main checkout switches to,
   how many uncommitted files move, and that the worktree is removed. No answer, no hand-back.

4. **Leave** the worktree when the session is inside it: there is no tool that leaves it, so
   stop without changes and tell the user to run `/artel-return-from-worktree <TICKET_ID>` from
   a session in the main checkout.
5. **Run** the same command without `--check` — the session is back in the main checkout.
6. **Act on `status`** (§6).

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
worktree; `.opencode/worktrees/<name>` existing but not a worktree of that branch. Hand-back with
uncommitted changes in the main checkout — they may belong to another session — or with the
worktree on a detached HEAD.

## 7. Hooks

Artel's hooks start in the directory the session runs in — the main checkout, after a move
into a worktree. `hook_common.read_hook_input()` moves each hook into the linked worktree named
by the payload's `cwd` when it belongs to the same repository, so the gates check the worktree
the session actually edits ([hooks/README.md](../hooks/README.md)). A worktree without
`.artel/config.json` leaves the hooks inert.

## 8. Upgrading from the pre-OpenCode layout

Worktrees made by the Claude Code build of artel live under the previous host's config
directory — `.claude`, in its `worktrees/` subdirectory — and the current `worktree.py` does
not recognise them: it resolves only `.opencode/worktrees/<name>` and **refuses rather than
deleting a directory it does not know** (§6). Two ways forward, in order of preference:

1. **Finish the hand-back before upgrading.** With the previous version still installed, run
   its hand-back skill from the main checkout. It moves the branch, the uncommitted work and
   `.artel/run/<TICKET_ID>/` back, and removes the worktree — the upgrade then starts clean.
2. **Relocate the worktree, manifest and all.** The worktree's `.artel/worktree.json` — written
   by move-in and read by hand-back — is what makes the move safe: it names the ticket, the
   branch and the main checkout's branch.

   ```bash
   OLD_HOST_DIR=.claude   # the previous host's config directory
   git worktree move "$OLD_HOST_DIR"/worktrees/<name> .opencode/worktrees/<name>
   python3 ${ARTEL_ROOT}/scripts/worktree.py hand-back --ticket <TICKET_ID> --name <name>
   ```

   Before the move, make sure `.opencode/worktrees/` is ignored — add
   `/.opencode/worktrees/` to `.git/info/exclude` or the host's `.gitignore` — or hand-back's
   clean-main-checkout check reads the relocated tree as uncommitted work and refuses. A stale
   exclude line naming the old worktrees directory is inert and may stay.

Never hand a legacy worktree back with plain Git (`git worktree remove`, a manual directory
delete): it moves tracked commits but not the ignored `.artel/run/` state, the context store
or the `.worktreeinclude` files the hand-back exists to carry.

**`.worktreeinclude` implications.** The default patterns moved with the host: the old
defaults (`/.claude`, `/.mcp.json`) select nothing on OpenCode, whose host files are
`/.opencode/`, `/opencode.json` and `/opencode.jsonc` (§3). A host that wrote a
`.worktreeinclude` for the old defaults keeps the old selection until it updates the file;
`.artel/` handling is unchanged either way.

## 9. The ticket lock

One ticket belongs to one run at a time, whichever mode that run is in. Two sessions on the
same ticket would commit the same tasks twice, so the lock is taken **before arming** and held
until the completion gate.

The lock is one record per ticket at `<git-common-dir>/artel/parallel/<TICKET>.json` — the
repository-common Git directory, so every linked worktree of the repository resolves the same
lock and a session working in a ticket worktree cannot bypass it. The file name is historical:
the parallel path introduced it first, and a serial run takes the same lock, because two locks
would not exclude each other. Its contract — identity, token, explicit recovery — is
[opencode-parallel.md](opencode-parallel.md) §13.

A serial run takes it with `owner arm`, which mints the run's `run_id` on a fresh arm and
adopts the one a resume passes:

```bash
python3 ${ARTEL_ROOT}/scripts/parallel.py owner arm --request <a request file>
# {"repo": "<checkout>", "ticket": "PROJ-2052", "phase": 1, "coordinator_id": "ses_…"}
```

The reply is the owner record; `run_id` and `token` are written into `run-state.json` as
`run_id` and `ownership: {mode: "serial", token: …, coordinator_id: …}`
([autonomous-run.md](autonomous-run.md) §2). `ticket` is the canonical base ticket and `phase`
the phase number or `null`, so `PROJ-2052` and `PROJ-2052-1` cannot both hold it. A crash
between the arm and the run-state write leaves the lock with no run state — the refusal names
its run, phase and acquisition time, which is enough to `owner recover` it.

**Refusals stop the run; they never arm around the lock.** `ticket_owned` means another run
holds the ticket; `coordinator_conflict` means this run is armed under another session;
`legacy_owner` and `ambiguous_owner` mean the record cannot be adopted automatically. The run is
reported with what the record proves — run, phase, coordinator, acquisition time — and stops
there. A deliberate handoff
is `owner recover` — the explicit, journaled takeover that rotates the token — and nothing else
takes a lock over, not a timeout and not a dead-looking session.

**Held from arm to completion.** A pause, an abort and a crash all keep it: the run is still
armed, and a pause is not a finish. `owner release` therefore succeeds only for a run whose run
state is `completed: true` with no recorded `pause_reason` (on the parallel path, whose manifest
is the proof, the same rule applies to the manifest). The consequence is deliberate: resuming a
paused run in a **new session** is the documented handoff, one `owner recover`, not a silent
takeover. A crashed run leaves the same record and is recovered the same way.

**Ownership is re-checked, not just taken.** A serial task commit and every checkpoint ask
before they stage: `owner validate` with the run state's `run_id`, `phase` and
`ownership.coordinator_id` plus the live session id. A refusal — `coordinator_conflict`,
`foreign_owner`, `ticket_owned`, `not_owned` — stops the commit and the run: the ticket belongs
to another session now, so the person decides, and the orchestrator never commits over the new
owner or recovers on its own. That is what makes the handoff safe: a session taken over by
`owner recover` stops at its next commit instead of writing on top of the new coordinator. A
parallel run needs no such check — every mutating command of its loop already matches the owner
under the guard.
