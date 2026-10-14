# Collaborative review fixtures

Evaluation-only data for the `collaborative-review` skill. Nothing here is shipped, imported by
plugin code, or reached by `scripts/build_opencode.py` — these files only back the offline
behavioral scenarios in `skills/collaborative-review/evals/evals.json` and the sandbox replay
instructions in each `scenario.md`.

## Layout

```
tests/fixtures/collaborative_review/
├── sandbox/gh                    offline `gh` shim (copy into the sandbox, never run live)
├── mixed-pr/                     fresh capture: paginated fork PR, stacked base, mixed files
│   ├── scenario.md               run card: setup, prompt, variants, expectations
│   ├── github/                   canned GitHub responses + immutable before/after contents
│   └── variants/pages-truncated.json
├── conversation/                 resume with a saved current file and an unresolved finding
│   ├── scenario.md
│   ├── github/
│   ├── checklists/current.md     saved approval ledger (2 of 4 checked, current file 3)
│   ├── preparation.json          saved worker ordering
│   └── reports/001.md … 003.md   saved per-file analyses (003 holds the finding)
└── stale-resume/                 matching / changed / completed resume and failed persistence
    ├── scenario.md
    ├── github/                   data matching the saved checklists
    ├── github-next/              changed head: one file added, one file modified
    ├── checklists/matching.md    saved ledger (2 of 4 checked, current file 3)
    ├── checklists/completed.md   all-checked prior ledger (stale after the head moved)
    ├── preparation.json
    └── reports/001.md … 004.md
```

`github/` always holds the canned responses exactly as the pinned commands would return them:
`pr-view.json` for `gh pr view --json url,title,body,baseRefOid,headRefOid,changedFiles,
headRepository,headRepositoryOwner`, `pages.json` for `gh api --method GET --paginate --slurp
'repos/<owner>/<repo>/pulls/<n>/files' -F per_page=100`, `compare.json` for the compare API
whose `merge_base_commit.sha` is the real merge base, `pr-diff.patch` for `gh pr diff`, and
`contents/{base,head}/<path>` for full file contents at the merge base and the head. Blob SHAs
in `pages.json` are real git blob hashes of those contents. `headRepository` follows `gh`'s
GraphQL shape — a node-id string, `name`, `nameWithOwner`, `owner.login` and `isFork` — so the
pinned capture resolves the fork head repository (`headRepository.nameWithOwner`) exactly as it
would against live `gh` output; the REST-only names (`full_name`, `fork`, `default_branch`) are
never used.

## Sandbox recipe (no live GitHub calls)

The shim answers only from the sandbox's `github/` directory; it never touches the network and
never writes. The fixture tree itself stays read-only — all writes land in the sandbox.

```bash
FIXTURES="<checkout>/tests/fixtures/collaborative_review"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/collab-review-XXXXXX")"
mkdir -p "$SANDBOX/bin" "$SANDBOX/repo"
cp "$FIXTURES/sandbox/gh" "$SANDBOX/bin/gh"
chmod +x "$SANDBOX/bin/gh"
cp -r "$FIXTURES/<scenario>/github" "$SANDBOX/github"
export PATH="$SANDBOX/bin:$PATH"          # `gh` now resolves to the shim
```

Then dispatch the agent with its working directory set to `$SANDBOX/repo` and the scenario's
first prompt. The scenario card lists any state to place under
`$SANDBOX/repo/.artel/run/collaborative-review/acme/session-kit/pr-<n>/` before the run.

If the dispatch cannot inherit `PATH`, tell the agent about the sandbox instead of relying on
bare `gh`: `Offline sandbox: there is no network. Run GitHub commands through the local shim at
"$SANDBOX/bin/gh" (for example `"$SANDBOX/bin/gh" pr view ...`); it serves the captured PR data.
Never call the live API.` Keep that line out of the graded prompt when `PATH` does work, and
record which form a run used.

Supported shim commands: `gh auth status`, `gh --version`, `gh pr view` (with or without
`--json`), `gh pr diff`, the paginated/slurped files endpoint, the compare endpoint, and the
contents endpoint with `?ref=` (raw `Accept: application/vnd.github.raw` or the base64 JSON
shape). Anything else exits 1 with a GitHub-shaped `Not Found`. `--jq` is not emulated: raw
JSON is printed.

## Capturing evidence

For every run save: the full transcript, the final `review-checklist.md` bytes, the run
directory tree (`reports/`, `preparation.json`, any history directory), and a check that the
fixture tree is byte-identical afterwards. The transcripts and omissions of the no-skill
baselines are the point of Task 3 Step 1 — do not replace them with rationalizations.
