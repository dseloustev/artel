# Phase 5 — Hooks and Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship artel's quality-gate layer — `scripts/verify.py`, `scripts/plan_check.py`, the five ported Python hooks wired via `hooks/hooks.json`, the default sensitive-paths policy, the `verify.surface` config key, unit tests, and the closing doc updates.

**Architecture:** Two independent layers with a subprocess boundary (spec decision 5): the scripts are standalone CLIs reading `.artel/config.json` and printing a one-line JSON envelope (exit 0 clean / 1 findings / 2 environment error); the hooks share `hooks/hook_common.py` and call `verify.py` as a subprocess, exactly as the source hooks called the Dart CLI. Every hook fails open.

**Tech Stack:** Python 3 stdlib only (no pip dependencies), `unittest` for tests, JSON config/policy files, Claude Code plugin hooks (`hooks/hooks.json`, `${CLAUDE_PLUGIN_ROOT}` paths).

**Spec:** `docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md`. Source material: `../adguard-wallet/.claude/hooks/*.py` and `../adguard-wallet/.claude/tools/agent/` (read them when porting — the logic, caps, and comments port verbatim unless a task says otherwise).

## Global Constraints

- Python: stdlib only, compatible with Python 3.9 — no PEP 604 (`X | None`) annotations, no `match` statements.
- Every hook fails **open**: top-level `try/except → exit 0` with a stderr note; missing config, missing state files, unparseable JSON, and verify exit-2 all allow.
- Scripts print **exactly one JSON line to stdout**; exit codes 0 (clean/skipped), 1 (findings), 2 (environment error). An exit-2 never means "fix the app code".
- Host-writable state only under `.artel/` in the host repo — never inside the plugin directory. Hook state: `.artel/run/.hooks/`; per-ticket stop counter: `.artel/run/<TICKET>/.stop-gate-blocks`.
- Hooks are **inert until `.artel/config.json` exists** — the verify-layer hooks return 0 immediately when the host has no config, so an installed-but-unconfigured plugin leaves zero footprint.
- Commits: conventional commits, subject line only, no trailers. All docs in English, repo-relative paths only.
- Run all tests from the repo root: `python3 -m unittest discover -s tests -v`.

---

### Task 1: `scripts/verify.py` — the deterministic verify wrapper

**Files:**
- Create: `scripts/verify.py`
- Create: `tests/test_verify.py`

**Interfaces:**
- Consumes: `.artel/config.json` keys `verify.fast` (string), `verify.commands` (array of strings) — read tolerantly, missing file → `{}`.
- Produces (for Task 3's `run_fast_verify` and for skills/humans): CLI `python3 scripts/verify.py [--fast] [--files a,b,c] [--timeout N]`; envelope `{"ok": bool, "verb": "verify", "elapsed_ms": int, "data": {"skipped": bool, "stages": [{"command": str, "exit_code": int, "ok": bool, "keys": [str], "tail": str}]}}` on exit 0/1, `{"ok": false, ..., "error": {"kind": str, "message": str, "details": {...}}}` on exit 2. Pure functions `normalize_keys(output, stage_index)`, `substitute_files(command, files)`, `classify_exit(returncode)`, `parse_args(argv)` (used by tests).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import verify  # noqa: E402


class TestNormalizeKeys(unittest.TestCase):
    def test_strips_ansi_and_digits(self):
        out = '\x1b[31msrc/app.ts:10:5 error no-unused-vars\x1b[0m'
        self.assertEqual(verify.normalize_keys(out, 0),
                         ['s0:src/app.ts:: error no-unused-vars'])

    def test_collapses_whitespace_and_drops_empty_lines(self):
        out = 'a   b\n\n   \n\tc  d\n'
        self.assertEqual(verify.normalize_keys(out, 1), ['s1:a b', 's1:c d'])

    def test_dedupes_repeated_lines(self):
        out = 'warn: x1\nwarn: x2\nother\n'
        # digit-stripping folds x1/x2 into one key
        self.assertEqual(verify.normalize_keys(out, 0), ['s0:warn: x', 's0:other'])

    def test_caps_at_max_keys_per_stage(self):
        # digit-free lines so digit-stripping cannot fold them together
        lines = '\n'.join('issue-' + ('x' * (i + 1)) for i in range(250))
        keys = verify.normalize_keys(lines, 0)
        self.assertEqual(len(keys), verify.MAX_KEYS_PER_STAGE)

    def test_prefixes_stage_index(self):
        self.assertEqual(verify.normalize_keys('boom', 3), ['s3:boom'])


class TestSubstituteFiles(unittest.TestCase):
    def test_replaces_placeholder_with_quoted_paths(self):
        cmd = verify.substitute_files('eslint {files}', ['a.ts', 'dir with space/b.ts'])
        self.assertEqual(cmd, "eslint a.ts 'dir with space/b.ts'")

    def test_without_placeholder_returns_command_unchanged(self):
        self.assertEqual(verify.substitute_files('npm run lint', ['a.ts']), 'npm run lint')


class TestClassifyExit(unittest.TestCase):
    def test_zero_is_ok(self):
        self.assertEqual(verify.classify_exit(0), 'ok')

    def test_126_and_127_are_env_errors(self):
        self.assertEqual(verify.classify_exit(126), 'env_error')
        self.assertEqual(verify.classify_exit(127), 'env_error')

    def test_other_nonzero_is_findings(self):
        for code in (1, 2, 3, 100, 255):
            self.assertEqual(verify.classify_exit(code), 'findings')


class TestParseArgs(unittest.TestCase):
    def test_parses_fast_files_timeout(self):
        fast, files, timeout = verify.parse_args(
            ['--fast', '--files', 'a.py,b.py', '--timeout', '90'])
        self.assertTrue(fast)
        self.assertEqual(files, ['a.py', 'b.py'])
        self.assertEqual(timeout, 90)

    def test_defaults(self):
        fast, files, timeout = verify.parse_args([])
        self.assertFalse(fast)
        self.assertIsNone(files)
        self.assertEqual(timeout, verify.DEFAULT_TIMEOUT)

    def test_rejects_blank_files(self):
        for blank in ('', '   ', ',,,'):
            with self.assertRaises(ValueError):
                verify.parse_args(['--files', blank])

    def test_rejects_unknown_flag(self):
        with self.assertRaises(ValueError):
            verify.parse_args(['--paths', 'a.py'])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'verify'`

- [ ] **Step 3: Implement `scripts/verify.py`**

```python
#!/usr/bin/env python3
"""Deterministic verify gate: run the configured verify commands, print one JSON envelope line.

Wraps `verify.fast` (--fast) or `verify.commands` (default) from .artel/config.json in the
envelope contract the hooks consume. Run from the host repo root.

Exit codes: 0 clean or skipped (no commands configured), 1 findings, 2 environment error.
An exit-2 always means a toolchain/invocation problem, never "the code has a bug".
Contract: docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md
"""
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

CONFIG_PATH = Path('.artel/config.json')
DEFAULT_TIMEOUT = 240
TAIL_CHARS = 2000
MAX_KEYS_PER_STAGE = 200
ENV_ERROR_EXIT_CODES = (126, 127)  # not executable / command not found
ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def substitute_files(command, files):
    """Replace {files} with the space-joined shell-quoted paths; no placeholder -> unchanged."""
    if '{files}' not in command:
        return command
    return command.replace('{files}', ' '.join(shlex.quote(f) for f in files))


def normalize_keys(output, stage_index):
    """Finding keys: non-empty output lines of a red stage, ANSI- and digit-stripped,
    whitespace-collapsed, deduped, prefixed s<index>:, capped. Digit-stripping keeps keys
    stable against shifting line numbers and timing noise ("Done in 3.2s")."""
    keys = []
    seen = set()
    for line in output.splitlines():
        line = ANSI_RE.sub('', line)
        line = re.sub(r'\d+', '', line)
        line = re.sub(r'\s+', ' ', line).strip()
        if not line:
            continue
        key = 's{}:{}'.format(stage_index, line)
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
        if len(keys) >= MAX_KEYS_PER_STAGE:
            break
    return keys


def classify_exit(returncode):
    """0 -> ok; 126/127 -> env_error (fix the toolchain); any other non-zero -> findings."""
    if returncode == 0:
        return 'ok'
    if returncode in ENV_ERROR_EXIT_CODES:
        return 'env_error'
    return 'findings'


def parse_args(argv):
    """Return (fast, files_or_None, timeout). Raises ValueError on a bad invocation.
    A present-but-blank --files is rejected rather than parsed to []: blank would be
    indistinguishable from the flag being absent and silently widen a scoped call."""
    fast = False
    files = None
    timeout = DEFAULT_TIMEOUT
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--fast':
            fast = True
        elif arg == '--files':
            i += 1
            raw = argv[i] if i < len(argv) else ''
            files = [p.strip() for p in raw.split(',') if p.strip()]
            if not files:
                raise ValueError('blank --files: a scoped call must name its paths')
        elif arg == '--timeout':
            i += 1
            try:
                timeout = int(argv[i])
            except (IndexError, ValueError):
                raise ValueError('--timeout expects an integer number of seconds')
        else:
            raise ValueError('unknown flag: {}'.format(arg))
        i += 1
    return fast, files, timeout


def envelope(ok, elapsed_ms, data=None, error=None):
    out = {'ok': ok, 'verb': 'verify', 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def run_stage(command, files, timeout, index):
    """Run one configured command. Returns (stage_dict, error_kind_or_None)."""
    cmd = substitute_files(command, files)
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'command': cmd, 'ok': False}, 'timeout'
    except OSError:
        return {'command': cmd, 'ok': False}, 'spawn_failed'
    output = (proc.stdout or '') + (proc.stderr or '')
    verdict = classify_exit(proc.returncode)
    if verdict == 'env_error':
        return {'command': cmd, 'ok': False, 'exit_code': proc.returncode,
                'tail': output[-TAIL_CHARS:]}, 'command_not_found'
    return {
        'command': cmd,
        'exit_code': proc.returncode,
        'ok': verdict == 'ok',
        'keys': normalize_keys(output, index) if verdict != 'ok' else [],
        'tail': output[-TAIL_CHARS:],
    }, None


def main(argv):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    try:
        fast, files, timeout = parse_args(argv)
    except ValueError as exc:
        print(envelope(False, elapsed(),
                       error={'kind': 'invalid_argument', 'message': str(exc)}))
        return 2

    config = load_config()
    verify_cfg = config.get('verify') or {}
    if fast:
        fast_cmd = verify_cfg.get('fast') or ''
        commands = [fast_cmd] if isinstance(fast_cmd, str) and fast_cmd.strip() else []
    else:
        commands = [c for c in (verify_cfg.get('commands') or [])
                    if isinstance(c, str) and c.strip()]

    if not commands:
        # No gate configured: exit 0, but the caller journals "skipped", never "green".
        print(envelope(True, elapsed(), data={'skipped': True, 'stages': []}))
        return 0

    stages = []
    for index, command in enumerate(commands):
        stage, error_kind = run_stage(command, files or [], timeout, index)
        stages.append(stage)
        if error_kind is not None:
            print(envelope(False, elapsed(), error={
                'kind': error_kind,
                'message': 'stage {} ({}) failed: {}'.format(
                    index, stage['command'], error_kind),
                'details': {'stages': stages},
            }))
            return 2
        if not stage['ok']:
            break  # the gate stops at the first red stage (config.md rule)

    if all(s['ok'] for s in stages):
        print(envelope(True, elapsed(), data={'skipped': False, 'stages': stages}))
        return 0
    print(envelope(False, elapsed(), data={'skipped': False, 'stages': stages}))
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # an uncaught crash is an environment error, reported loudly
        print(json.dumps({'ok': False, 'verb': 'verify', 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 5: Smoke-check the CLI from the repo root** (this repo has no `.artel/config.json`, so `--fast` must report skipped)

Run: `python3 scripts/verify.py --fast; echo "exit=$?"`
Expected: `{"ok": true, "verb": "verify", "elapsed_ms": ..., "data": {"skipped": true, "stages": []}}` then `exit=0`

Run: `python3 scripts/verify.py --files ""; echo "exit=$?"`
Expected: an `invalid_argument` error envelope, then `exit=2`

Run a real findings case in the scratchpad (never write `.artel/` into this repo):

```bash
SCRATCH=$(mktemp -d) && cd "$SCRATCH" && mkdir .artel \
  && echo '{"verify": {"fast": "echo lint-error-in-app.ts && exit 1"}}' > .artel/config.json \
  && python3 "$OLDPWD/scripts/verify.py" --fast; echo "exit=$?"; cd "$OLDPWD"
```
Expected: envelope with `"ok": false`, one stage with `"keys": ["s0:lint-error-in-app.ts"]`, then `exit=1`

- [ ] **Step 6: Commit**

```bash
git add scripts/verify.py tests/test_verify.py
git commit -m "feat: add deterministic verify wrapper (scripts/verify.py)"
```

---

### Task 2: `scripts/plan_check.py` — the plan-anchor checker

**Files:**
- Create: `scripts/plan_check.py`
- Create: `tests/test_plan_check.py`

**Interfaces:**
- Consumes: nothing from other tasks (standalone; uses `ast-index` when on PATH, else `git grep`).
- Produces: CLI `python3 scripts/plan_check.py --plan <path> [--strict]` — the exact invocation `skills/feature-development/SKILL.md` Gate 3.5 already ships. Envelope data `{"checked": int, "resolved": int, "new_declared": [str], "unresolved": [{"ref": str, "reason": "file not found"|"symbol not found"}]}`. Pure function `extract_anchors(markdown)` returning ordered `(kind, value)` tuples, kinds `ref`/`new`/`path` (used by tests).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plan_check.py`:

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import plan_check  # noqa: E402


class TestExtractAnchors(unittest.TestCase):
    def test_ref_and_new_tokens_with_trailing_punct_trim(self):
        md = 'Uses ref:UserService, creates new:CacheRebuilder.'
        self.assertEqual(plan_check.extract_anchors(md),
                         [('ref', 'UserService'), ('new', 'CacheRebuilder')])

    def test_dedupes_by_kind_and_value(self):
        md = 'ref:Foo then ref:Foo again\nref:Foo once more'
        self.assertEqual(plan_check.extract_anchors(md), [('ref', 'Foo')])

    def test_backticked_repo_path_is_implicit_ref(self):
        md = 'Touch `src/api/client.ts` here.'
        self.assertEqual(plan_check.extract_anchors(md), [('path', 'src/api/client.ts')])

    def test_backticked_token_without_slash_or_extension_ignored(self):
        md = 'See `README` and `Makefile.am is odd` and `docs/design` too.'
        self.assertEqual(plan_check.extract_anchors(md), [])

    def test_line_scoped_new_anchor_suppresses_backticked_path(self):
        md = 'Create new:CacheRebuilder in `lib/data/cache_rebuilder.dart` here.'
        self.assertEqual(plan_check.extract_anchors(md), [('new', 'CacheRebuilder')])

    def test_line_scoped_new_file_marker_suppresses_backticked_path(self):
        md = 'Add `src/util/helpers.py` (new file).'
        self.assertEqual(plan_check.extract_anchors(md), [])

    def test_exact_value_new_suppresses_document_wide(self):
        md = 'new:src/util/helpers.py\nLater we flesh out `src/util/helpers.py` fully.'
        self.assertEqual(plan_check.extract_anchors(md), [('new', 'src/util/helpers.py')])

    def test_path_on_other_line_without_marker_still_checked(self):
        md = 'Create new:Thing here.\nAlso touch `src/other/file.py` normally.'
        self.assertEqual(plan_check.extract_anchors(md),
                         [('new', 'Thing'), ('path', 'src/other/file.py')])


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'plan_check'`

- [ ] **Step 3: Implement `scripts/plan_check.py`**

```python
#!/usr/bin/env python3
"""plan-check: resolve every anchor a plan cites (ref:/new: tokens + backticked repo-relative
paths) against the filesystem and a symbol index; flag hallucinated references before
implementation starts.

Symbols resolve via `ast-index symbol <name> --format json` when that binary is on PATH
(with one `ast-index update` stale-index retry), else via `git grep -l -w <name>` — the
anti-hallucination check works in repos without ast-index.

Exit codes: 0 clean (or unresolved without --strict), 1 unresolved with --strict, 2 error.
Contract: docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md
"""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REF_NEW_RE = re.compile(r'\b(ref|new):([A-Za-z0-9_$./-]+)')
BACKTICKED_PATH_RE = re.compile(r'`([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+)`')
TRAILING_PUNCT_RE = re.compile(r'[.,;:]+$')


def extract_anchors(markdown):
    """Ordered (kind, value) tuples, kind in {'ref', 'new', 'path'}, deduped by kind:value."""
    seen = set()
    anchors = []
    lines = markdown.split('\n')

    # Pass 1: ref:/new: tokens across the whole document (order preserved top-to-bottom).
    for line in lines:
        for match in REF_NEW_RE.finditer(line):
            kind = match.group(1)
            value = TRAILING_PUNCT_RE.sub('', match.group(2))
            key = '{}:{}'.format(kind, value)
            if value and key not in seen:
                seen.add(key)
                anchors.append((kind, value))

    # Pass 2: backticked repo-relative paths (contain '/' and end in an extension) are
    # implicit refs to EXISTING code — unless the line itself declares a new file (a new:
    # anchor or the '(new file)' marker, line-scoped), or the exact path value was declared
    # via new:<path> anywhere in the document.
    for line in lines:
        line_declares_new = (
            any(m.group(1) == 'new' for m in REF_NEW_RE.finditer(line))
            or '(new file)' in line
        )
        for match in BACKTICKED_PATH_RE.finditer(line):
            value = match.group(1)
            if not value or line_declares_new or ('new:' + value) in seen:
                continue
            if ('path:' + value) not in seen:
                seen.add('path:' + value)
                anchors.append(('path', value))
    return anchors


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _symbol_resolves_ast_index(name):
    base = name.split('.')[0]  # member refs like Foo.bar resolve on Foo
    try:
        proc = _run(['ast-index', 'symbol', base, '--format', 'json'], timeout=60)
    except Exception:
        return False
    if proc.returncode != 0:
        return False
    try:
        parsed = json.loads(proc.stdout)
        return isinstance(parsed, list) and len(parsed) > 0
    except ValueError:
        return False


def _symbol_resolves_git_grep(name):
    base = name.split('.')[0]
    try:
        proc = _run(['git', 'grep', '-l', '-w', base], timeout=60)
    except Exception:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def resolve_pass(pending, have_ast_index):
    unresolved = []
    for kind, value in pending:
        if '/' in value:
            if not Path(value).exists():  # file or directory
                unresolved.append({'ref': value, 'reason': 'file not found'})
        else:
            resolves = (_symbol_resolves_ast_index(value) if have_ast_index
                        else _symbol_resolves_git_grep(value))
            if not resolves:
                unresolved.append({'ref': value, 'reason': 'symbol not found'})
    return unresolved


def envelope(ok, elapsed_ms, data=None, error=None):
    out = {'ok': ok, 'verb': 'plan-check', 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def main(argv):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    plan_path = None
    strict = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--plan':
            i += 1
            plan_path = argv[i] if i < len(argv) else None
        elif arg == '--strict':
            strict = True
        else:
            print(envelope(False, elapsed(), error={
                'kind': 'invalid_argument', 'message': 'unknown flag: {}'.format(arg)}))
            return 2
        i += 1
    if not plan_path:
        print(envelope(False, elapsed(), error={
            'kind': 'invalid_argument', 'message': 'missing required --plan <path>'}))
        return 2
    plan_file = Path(plan_path)
    if not plan_file.is_file():
        print(envelope(False, elapsed(), error={
            'kind': 'plan_not_found', 'message': 'plan file not found: {}'.format(plan_path)}))
        return 2

    anchors = extract_anchors(plan_file.read_text(encoding='utf-8'))
    new_declared = [v for k, v in anchors if k == 'new']
    to_resolve = [(k, v) for k, v in anchors if k != 'new']
    have_ast_index = shutil.which('ast-index') is not None

    unresolved = resolve_pass(to_resolve, have_ast_index)
    if have_ast_index and any(u['reason'] == 'symbol not found' for u in unresolved):
        # Index may be stale — refresh once and re-check only the still-pending anchors.
        try:
            _run(['ast-index', 'update'], timeout=600)
        except Exception:
            pass
        pending = [(k, v) for k, v in to_resolve
                   if any(u['ref'] == v for u in unresolved)]
        unresolved = resolve_pass(pending, have_ast_index)

    data = {
        'checked': len(to_resolve),
        'resolved': len(to_resolve) - len(unresolved),
        'new_declared': new_declared,
        'unresolved': unresolved,
    }
    if unresolved and strict:
        print(envelope(False, elapsed(), data=data))
        return 1
    print(envelope(True, elapsed(), data=data))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(json.dumps({'ok': False, 'verb': 'plan-check', 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 5: Smoke-check against this repo** (no ast-index needed — `git grep` fallback)

```bash
SCRATCH=$(mktemp -d) && printf 'Cites `docs/config.md` and ref:TotallyFakeSymbolXyz.\n' > "$SCRATCH/plan.md" \
  && python3 scripts/plan_check.py --plan "$SCRATCH/plan.md"; echo "exit=$?" \
  && python3 scripts/plan_check.py --plan "$SCRATCH/plan.md" --strict; echo "exit=$?"
```
Expected: first call `"checked": 2, "resolved": 1`, unresolved `TotallyFakeSymbolXyz` with `"symbol not found"`, `exit=0`; second call same data, `exit=1`

Run: `python3 scripts/plan_check.py; echo "exit=$?"`
Expected: `invalid_argument` envelope, `exit=2`

- [ ] **Step 6: Commit**

```bash
git add scripts/plan_check.py tests/test_plan_check.py
git commit -m "feat: add plan-anchor checker (scripts/plan_check.py)"
```

---

### Task 3: `hooks/hook_common.py` + the `verify.surface` config key

**Files:**
- Create: `hooks/hook_common.py`
- Create: `tests/test_hook_common.py`
- Modify: `docs/config.md` (verify section + filled example)

**Interfaces:**
- Consumes: Task 1's `scripts/verify.py` CLI (as a subprocess) and its envelope shape.
- Produces (for Tasks 4–6): module `hook_common` with `CONFIG_PATH` (Path), `STATE_DIR` (Path `.artel/run/.hooks`), `VERIFY_SCRIPT` (Path), `read_hook_input()`, `load_config()`, `is_verifiable(path, config)`, `changed_files(config)`, `run_fast_verify(paths, timeout=240)` → `(exit_code, envelope_dict)`, `finding_keys(envelope)` → `set`, `relpath_from_tool_input(data)`, `resolve_active_ticket(config)` → base ticket ID str or `None`, `ticket_run_dir(ticket)` → Path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hook_common.py`:

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common as h  # noqa: E402


class TestIsVerifiable(unittest.TestCase):
    def test_absent_surface_matches_everything(self):
        self.assertTrue(h.is_verifiable('anything/at/all.txt', {}))
        self.assertTrue(h.is_verifiable('a.py', {'verify': {}}))

    def test_positive_globs(self):
        config = {'verify': {'surface': ['src/**/*.ts']}}
        self.assertTrue(h.is_verifiable('src/deep/app.ts', config))
        self.assertFalse(h.is_verifiable('docs/readme.md', config))

    def test_negative_glob_excludes(self):
        config = {'verify': {'surface': ['lib/**/*.dart', '!*.g.dart']}}
        self.assertTrue(h.is_verifiable('lib/app/main.dart', config))
        self.assertFalse(h.is_verifiable('lib/app/main.g.dart', config))

    def test_only_negatives_implies_star_positive(self):
        config = {'verify': {'surface': ['!*.md']}}
        self.assertTrue(h.is_verifiable('src/app.py', config))
        self.assertFalse(h.is_verifiable('docs/readme.md', config))


class TestFindingKeys(unittest.TestCase):
    def test_unions_stage_keys(self):
        env = {'data': {'stages': [{'keys': ['s0:a', 's0:b']}, {'keys': ['s1:c']}]}}
        self.assertEqual(h.finding_keys(env), {'s0:a', 's0:b', 's1:c'})

    def test_tolerates_missing_shapes(self):
        self.assertEqual(h.finding_keys({}), set())
        self.assertEqual(h.finding_keys({'data': {}}), set())
        self.assertEqual(h.finding_keys({'data': {'stages': [{}]}}), set())


class TestRelpath(unittest.TestCase):
    def test_strips_cwd_prefix(self):
        data = {'tool_input': {'file_path': '/repo/src/a.py'}, 'cwd': '/repo'}
        self.assertEqual(h.relpath_from_tool_input(data), 'src/a.py')

    def test_leaves_other_paths_alone(self):
        data = {'tool_input': {'file_path': 'src/a.py'}, 'cwd': '/repo'}
        self.assertEqual(h.relpath_from_tool_input(data), 'src/a.py')


class TestResolveActiveTicket(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def _write_pointer(self, content, specs_dir='specs/.current'):
        d = Path(specs_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / '.active_ticket').write_text(content, encoding='utf-8')

    def test_missing_pointer_returns_none(self):
        self.assertIsNone(h.resolve_active_ticket({}))

    def test_canonicalizes_all_accepted_forms(self):
        config = {'ticket': {'projectKey': 'PROJ'}}
        for raw in ('PROJ-123', '123', 'proj-123', 'PROJ-123-2', 'PROJ-123-p2', '123-2'):
            self._write_pointer(raw + '\n')
            self.assertEqual(h.resolve_active_ticket(config), 'PROJ-123', raw)

    def test_malformed_pointer_returns_none(self):
        self._write_pointer('not a ticket at all\n')
        self.assertIsNone(h.resolve_active_ticket({'ticket': {'projectKey': 'PROJ'}}))

    def test_respects_specs_dir_and_project_key(self):
        self._write_pointer('AW-77\n', specs_dir='my/specs')
        config = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'my/specs'}}
        self.assertEqual(h.resolve_active_ticket(config), 'AW-77')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'hook_common'`

- [ ] **Step 3: Implement `hooks/hook_common.py`**

```python
"""Shared plumbing for the artel hooks. Stdlib only; every helper fails open.

Cwd is always the host repo root (hooks.json prefixes every command with
cd "$CLAUDE_PROJECT_DIR"). The plugin root is derived from this file's own location,
so the verify subprocess needs no environment variable.
"""
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path('.artel/config.json')
RUN_DIR = Path('.artel/run')
STATE_DIR = RUN_DIR / '.hooks'  # dot-prefixed: can never collide with a ticket dir
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = PLUGIN_ROOT / 'scripts' / 'verify.py'
DEFAULT_TICKET_PATTERN = r'^(?:{projectKey}-)?(\d+)(?:-p?(\d+))?$'


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def is_verifiable(path, config):
    """verify.surface filter: fnmatch globs ('*' crosses '/'), '!'-prefixed patterns exclude.
    A list with only excludes implies '*' as the positive set; key absent -> every file."""
    surface = (config.get('verify') or {}).get('surface')
    if not surface or not isinstance(surface, list):
        return True
    positives = [p for p in surface if isinstance(p, str) and p and not p.startswith('!')]
    negatives = [p[1:] for p in surface if isinstance(p, str) and p.startswith('!')]
    if any(fnmatch.fnmatch(path, n) for n in negatives):
        return False
    if not positives:
        return True
    return any(fnmatch.fnmatch(path, p) for p in positives)


def changed_files(config):
    """Changed files vs HEAD plus the working tree, filtered by verify.surface and existence."""
    files = set()
    try:
        out = subprocess.run(['git', 'diff', '--name-only', 'HEAD'],
                             capture_output=True, text=True, timeout=30)
        files.update(l.strip() for l in out.stdout.splitlines() if l.strip())
        out = subprocess.run(['git', 'status', '--porcelain'],
                             capture_output=True, text=True, timeout=30)
        for line in out.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]  # unstripped: XY + space prefix is exactly 3 chars
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]  # rename: keep the new path
            files.add(path.strip())
    except Exception:
        return []
    return sorted(f for f in files if is_verifiable(f, config) and Path(f).is_file())


def run_fast_verify(paths, timeout=240):
    """Run scripts/verify.py --fast as a subprocess; returns (exit_code, envelope_dict)."""
    cmd = [sys.executable or 'python3', str(VERIFY_SCRIPT), '--fast',
           '--timeout', str(timeout)]
    if paths:
        cmd += ['--files', ','.join(paths)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    try:
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return proc.returncode, {}


def finding_keys(envelope):
    keys = set()
    for stage in (envelope.get('data') or {}).get('stages') or []:
        keys.update(stage.get('keys') or [])
    return keys


def relpath_from_tool_input(data):
    file_path = (data.get('tool_input') or {}).get('file_path') or ''
    cwd = data.get('cwd') or ''
    if cwd and file_path.startswith(cwd.rstrip('/') + '/'):
        return file_path[len(cwd.rstrip('/')) + 1:]
    return file_path


def resolve_active_ticket(config):
    """Base ticket ID (canonical <projectKey>-<number>, phase suffix stripped) from
    <specs.dir>/.active_ticket, or None. .artel/run/<TICKET>/ is always ticket-top-level."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    pointer = Path(specs_dir) / '.active_ticket'
    if not pointer.is_file():
        return None
    try:
        lines = pointer.read_text(encoding='utf-8').strip().splitlines()
    except OSError:
        return None
    first = lines[0].strip() if lines else ''
    if not first:
        return None
    ticket_cfg = config.get('ticket') or {}
    project_key = ticket_cfg.get('projectKey') or 'PROJ'
    pattern = ticket_cfg.get('pattern') or DEFAULT_TICKET_PATTERN
    try:
        compiled = re.compile(pattern.replace('{projectKey}', re.escape(project_key)),
                              re.IGNORECASE)
    except re.error:
        return None
    match = compiled.match(first)
    if not match:
        return None
    return '{}-{}'.format(project_key.upper(), match.group(1))


def ticket_run_dir(ticket):
    return RUN_DIR / ticket
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 5: Document `verify.surface` and `{files}` in `docs/config.md`**

In the `### verify — the quality gate` table, add this row after the `verify.fast` row:

```markdown
| `verify.surface` | array of strings | absent | Repo-relative glob patterns (`fnmatch` semantics — `*` crosses `/` — like `runtime.surface`); a `!`-prefixed pattern excludes (generated files). A path counts when it matches ≥ 1 positive and 0 negative patterns; a list with only excludes implies `*` as the positive set. Absent → every changed file counts. | The per-edit and stop-gate hooks' changed-file filter |
```

Replace the paragraph after the table:

```markdown
Commands must be non-interactive, exit non-zero on failure, and be safe to re-run. An empty
`verify.commands` degrades the gate to `skipped` — it is never reported as `green`. An empty
`verify.fast` makes the per-edit hook a no-op.
```

with:

```markdown
Commands must be non-interactive, exit non-zero on failure, and be safe to re-run. An empty
`verify.commands` degrades the gate to `skipped` — it is never reported as `green`. An empty
`verify.fast` makes the per-edit hook a no-op.

Any command may contain the literal token `{files}`: callers that pass an explicit file scope
(the hooks; `scripts/verify.py --files`) replace it with the space-joined, shell-quoted paths,
so `"eslint {files}"` checks only what changed. A command without the token always runs
unscoped. Exit codes `126`/`127`, a spawn failure, or a timeout classify as an environment
error (exit 2 — fix the toolchain); any other non-zero exit is findings (exit 1).
`verify.surface` filters which changed files the hooks act on, e.g.
`["lib/**/*.dart", "!*.g.dart", "!*.freezed.dart"]` for the source project's behavior.
```

In the "A filled example" JSON, extend the `verify` object:

```json
  "verify": {
    "commands": [
      "npm run lint",
      "npm run typecheck",
      "npm test -- --run"
    ],
    "fast": "npm run lint -- --cache",
    "surface": ["src/**", "!src/generated/**"]
  },
```

- [ ] **Step 6: Commit**

```bash
git add hooks/hook_common.py tests/test_hook_common.py docs/config.md
git commit -m "feat: add hook plumbing and verify.surface config key"
```

---

### Task 4: `session_baseline.py` + `fast_verify_post_edit.py`

**Files:**
- Create: `hooks/session_baseline.py`
- Create: `hooks/fast_verify_post_edit.py`

**Interfaces:**
- Consumes: Task 3's `hook_common` (`CONFIG_PATH`, `STATE_DIR`, `read_hook_input`, `load_config`, `changed_files`, `run_fast_verify`, `finding_keys`, `is_verifiable`, `relpath_from_tool_input`).
- Produces: `.artel/run/.hooks/baseline-<session>.json` (`{"keys": [str]}`) — Task 5's verify stop gate reads it.

- [ ] **Step 1: Implement `hooks/session_baseline.py`**

```python
"""SessionStart: capture the findings baseline so the verify stop gate blocks only NEW findings."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert, zero footprint
    data = h.read_hook_input()
    session = data.get('session_id') or 'unknown'
    config = h.load_config()
    h.STATE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = h.STATE_DIR / 'baseline-{}.json'.format(session)
    if baseline_path.exists():
        return 0
    changed = h.changed_files(config)
    keys = set()
    if changed:
        code, envelope = h.run_fast_verify(changed, timeout=100)
        if code not in (0, 1):
            return 0  # env error -> leave baseline ABSENT; the stop gate's first-sight branch captures it lazily
        keys = h.finding_keys(envelope)
    baseline_path.write_text(json.dumps({'keys': sorted(keys)}), encoding='utf-8')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('baseline hook error (skipping): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
```

- [ ] **Step 2: Implement `hooks/fast_verify_post_edit.py`**

```python
"""PostToolUse(Edit|Write|MultiEdit): fast verify on the edited file; feedback, never block."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MAX_LINES = 10


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    data = h.read_hook_input()
    config = h.load_config()
    rel = h.relpath_from_tool_input(data)
    if not rel or not h.is_verifiable(rel, config) or not Path(rel).exists():
        return 0
    code, envelope = h.run_fast_verify([rel], timeout=120)
    if code != 1:
        return 0  # green, skipped, or environment error — per-edit noise helps nobody
    lines = []
    for stage in (envelope.get('data') or {}).get('stages') or []:
        if stage.get('ok'):
            continue
        lines.extend(l for l in (stage.get('tail') or '').splitlines() if l.strip())
    if not lines:
        return 0
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        # the tail's END is closest to the failure summary most linters print last
        'additionalContext': 'fast verify findings on ' + rel + ':\n'
                             + '\n'.join(lines[-MAX_LINES:]),
    }}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # fail open
        print('fast_verify hook error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
```

- [ ] **Step 3: Smoke-check both hooks from the repo root** (no `.artel/config.json` here → both must be inert and create nothing)

Run: `echo '{}' | python3 hooks/session_baseline.py; echo "exit=$?"; ls .artel 2>&1`
Expected: `exit=0` and `ls: .artel: No such file or directory`

Run: `echo '{"tool_input": {"file_path": "README.md"}}' | python3 hooks/fast_verify_post_edit.py; echo "exit=$?"`
Expected: no stdout, `exit=0`

- [ ] **Step 4: Run the full test suite (no regressions)**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add hooks/session_baseline.py hooks/fast_verify_post_edit.py
git commit -m "feat: port session-baseline and fast per-edit verify hooks"
```

---

### Task 5: `verify_stop_gate.py`

**Files:**
- Create: `hooks/verify_stop_gate.py`

**Interfaces:**
- Consumes: Task 3's `hook_common`; Task 4's `baseline-<session>.json`.
- Produces: `.artel/run/.hooks/stopblocks-<session>.json` (`{"consecutive": int}`); Stop-event JSON (`{"decision": "block", "reason": ...}` / `{"systemMessage": ...}`).

- [ ] **Step 1: Implement `hooks/verify_stop_gate.py`**

```python
"""Stop: block completion while the fast gate has findings NEW relative to the session baseline.
Bounded: after 2 consecutive blocks the stop passes with a loud warning."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MAX_CONSECUTIVE_BLOCKS = 2


def _counter(session):
    h.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return h.STATE_DIR / 'stopblocks-{}.json'.format(session)


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    data = h.read_hook_input()
    # No stop_hook_active early-return: the consecutive-blocks counter below is the loop
    # bound (2 blocks max, then a loud pass-through) — an early return here would allow
    # the continuation's stop attempt unconditionally and dead-code the counter.
    session = data.get('session_id') or 'unknown'
    config = h.load_config()
    changed = h.changed_files(config)
    if not changed:
        _counter(session).write_text('{"consecutive": 0}', encoding='utf-8')
        return 0
    code, envelope = h.run_fast_verify(changed)
    if code == 0:
        _counter(session).write_text('{"consecutive": 0}', encoding='utf-8')
        return 0
    if code != 1:
        print('stop gate: verify environment error — allowing stop; fix the toolchain '
              '({})'.format((envelope.get('error') or {}).get('kind', 'unknown')),
              file=sys.stderr)
        return 0
    baseline_path = h.STATE_DIR / 'baseline-{}.json'.format(session)
    if baseline_path.exists():
        baseline = set(json.loads(baseline_path.read_text(encoding='utf-8')).get('keys') or [])
    else:
        h.STATE_DIR.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({'keys': sorted(h.finding_keys(envelope))}),
                                 encoding='utf-8')
        return 0  # first sight this session: treat current findings as pre-existing (fail open)
    new = sorted(h.finding_keys(envelope) - baseline)
    if not new:
        _counter(session).write_text('{"consecutive": 0}', encoding='utf-8')
        return 0
    counter_path = _counter(session)
    consecutive = 0
    if counter_path.exists():
        try:
            consecutive = json.loads(counter_path.read_text(encoding='utf-8')).get('consecutive') or 0
        except Exception:
            consecutive = 0
    if consecutive >= MAX_CONSECUTIVE_BLOCKS:
        # Latch at cap instead of resetting to 0: resetting here would re-arm the 3-cycle
        # block/pass pattern, which can phase-lock against the coexisting orchestrator Stop
        # gate's 6-cycle pattern so their allows never coincide (indefinite blocking). Stay
        # latched — every subsequent red-new stop passes with this systemMessage — until a
        # green/no-new/clean pass resets the counter via one of the branches above.
        print(json.dumps({'systemMessage':
            'stop gate: still red after 2 blocks — letting the stop through. '
            'UNRESOLVED: ' + '; '.join(new[:5])}))
        return 0
    counter_path.write_text(json.dumps({'consecutive': consecutive + 1}), encoding='utf-8')
    print(json.dumps({'decision': 'block', 'reason':
        'The verify gate is red with findings introduced this session — fix them before '
        'finishing (python3 "{}" --fast --files {}):\n'.format(h.VERIFY_SCRIPT, ','.join(changed))
        + '\n'.join(new[:10])}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('stop gate error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
```

- [ ] **Step 2: Smoke-check inertness and the block path**

Run from the repo root (no config → inert): `echo '{}' | python3 hooks/verify_stop_gate.py; echo "exit=$?"`
Expected: no output, `exit=0`

Block path, in a scratch git repo:

```bash
SCRATCH=$(mktemp -d) && cd "$SCRATCH" && git init -q && mkdir .artel \
  && echo '{"verify": {"fast": "echo new-finding-here && exit 1"}}' > .artel/config.json \
  && echo x > f.txt && git add f.txt && git commit -qm init && echo y >> f.txt \
  && echo '{"session_id": "smoke"}' | python3 "$OLDPWD/hooks/verify_stop_gate.py"; echo "exit=$?"; cd "$OLDPWD"
```
Expected: no block JSON, `exit=0` — first sight writes the baseline (check: `cat $SCRATCH/.artel/run/.hooks/baseline-smoke.json` shows `s0:new-finding-here`). Piping the same input a second time still exits 0 with no output (findings match the captured baseline — not NEW).

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add hooks/verify_stop_gate.py
git commit -m "feat: port verify stop gate"
```

---

### Task 6: `stop_gate.py` + `sensitive_guard.py` + shipped policy

**Files:**
- Create: `hooks/stop_gate.py`
- Create: `hooks/sensitive_guard.py`
- Create: `hooks/sensitive-paths.json`
- Modify: `docs/config.md` ("Purpose and location" list)

**Interfaces:**
- Consumes: Task 3's `hook_common` (`load_config`, `read_hook_input`, `resolve_active_ticket`, `ticket_run_dir`, `relpath_from_tool_input`); `.artel/run/<TICKET>/run-state.json` fields `run_active`, `completed`, `pause_reason`, `started_at`, `effective_mode`, `gates_confirmed` (autonomous-run.md §2).
- Produces: `.artel/run/<TICKET>/.stop-gate-blocks` counter; Stop block JSON; PreToolUse deny JSON (`hookSpecificOutput.permissionDecision: "deny"`).

- [ ] **Step 1: Implement `hooks/stop_gate.py`**

```python
#!/usr/bin/env python3
"""Stop gate for autonomous orchestrator runs.

Blocks the session from ending while an autonomous run is active and incomplete.
Contract: ${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md (sections 2 and 8).

Allow when any of:
  - no active ticket / no run-state.json / run_active false  (interactive session)
  - pause_reason set                                          (waiting for a human)
  - completed true                                            (run finished)
  - started_at older than WALL_CLOCK_HOURS                    (stale run)
  - MAX_CONSECUTIVE_BLOCKS reached                            (fail-safe, loud warning)
  - any infra error                                           (fail open)
Otherwise: block with a reason instructing the model to continue or record an abort.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MAX_CONSECUTIVE_BLOCKS = 5
WALL_CLOCK_HOURS = 3


def allow(counter_file):
    if counter_file is not None:
        try:
            counter_file.unlink(missing_ok=True)
        except OSError:
            pass
    sys.exit(0)


def main():
    h.read_hook_input()  # payload unused; consume defensively
    config = h.load_config()
    ticket = h.resolve_active_ticket(config)
    if ticket is None:
        allow(None)
    ticket_dir = h.ticket_run_dir(ticket)
    state_file = ticket_dir / 'run-state.json'
    counter_file = ticket_dir / '.stop-gate-blocks'

    if not state_file.is_file():
        allow(counter_file)
    state = json.loads(state_file.read_text(encoding='utf-8'))
    if not state.get('run_active', False):
        allow(counter_file)
    if state.get('completed', False) or state.get('pause_reason'):
        allow(counter_file)

    started_raw = str(state.get('started_at', ''))
    try:
        started = datetime.fromisoformat(started_raw.replace('Z', '+00:00'))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - started > timedelta(hours=WALL_CLOCK_HOURS):
            allow(counter_file)  # stale run: budget expired
    except ValueError:
        allow(counter_file)  # unparseable timestamp: fail open

    blocks = 0
    if counter_file.is_file():
        try:
            blocks = int(counter_file.read_text(encoding='utf-8').strip() or '0')
        except ValueError:
            blocks = 0
    if blocks >= MAX_CONSECUTIVE_BLOCKS:
        print(
            'stop_gate: {} consecutive blocks for {} — allowing stop. '
            'The run is NOT complete; resume it or record an abort.'.format(
                MAX_CONSECUTIVE_BLOCKS, ticket),
            file=sys.stderr,
        )
        allow(counter_file)

    counter_file.write_text(str(blocks + 1), encoding='utf-8')
    reason = (
        'Autonomous run for {t} is active and gates are not green — continue the '
        'pipeline, or record an explicit abort in .artel/run/{t}/run-state.json '
        '(set pause_reason="user-abort" or run_active=false). '
        'Block {n}/{cap}.'.format(t=ticket, n=blocks + 1, cap=MAX_CONSECUTIVE_BLOCKS)
    )
    print(json.dumps({'decision': 'block', 'reason': reason}))
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail open on any infra error
        print('stop_gate: hook error, failing open: {}'.format(exc), file=sys.stderr)
        sys.exit(0)
```

- [ ] **Step 2: Create `hooks/sensitive-paths.json`** (spec decision 3 — fnmatch on repo-relative paths, `*` crosses `/`)

```json
{
  "categories": [
    {
      "name": "secrets",
      "floor": "full-gates",
      "globs": [
        ".env*",
        "*/.env*",
        "*.pem",
        "*.key",
        "*.p12",
        "*secret*",
        "*credential*",
        "*id_rsa*"
      ]
    },
    {
      "name": "gate-config",
      "floor": "full-gates",
      "globs": [
        ".claude/*",
        ".artel/config.json",
        ".artel/sensitive-paths.json"
      ]
    },
    {
      "name": "ci-cd",
      "floor": "plan-gate",
      "globs": [
        ".github/workflows/*",
        ".gitlab-ci.yml",
        "Jenkinsfile",
        ".circleci/*",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml"
      ]
    }
  ]
}
```

- [ ] **Step 3: Implement `hooks/sensitive_guard.py`**

```python
"""PreToolUse(Edit|Write|MultiEdit): during an armed autonomous run (run-state.json,
autonomous-run.md sections 2 and 10), deny writes to sensitive paths whose floor the effective
mode does not satisfy. Inactive in normal sessions."""
import fnmatch
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

HOST_RULES_PATH = Path('.artel/sensitive-paths.json')
DEFAULT_RULES_PATH = Path(__file__).resolve().parent / 'sensitive-paths.json'
MODE_RANK = {'yolo': 0, 'plan-gate': 1, 'full-gates': 2}
WALL_CLOCK_HOURS = 3  # single staleness constant — matches autonomous-run.md §2 and stop_gate.py
STALE_AFTER_SECONDS = WALL_CLOCK_HOURS * 3600
FLOOR_PAUSE_GATE = 'TASKLIST_READY'


def _run_state(config):  # -> dict | None (comment, not annotation: 3.9 compatibility)
    ticket = h.resolve_active_ticket(config)
    if ticket is None:
        return None
    state_path = h.ticket_run_dir(ticket) / 'run-state.json'
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if not state.get('run_active') or state.get('completed'):
        return None
    started = state.get('started_at') or ''
    try:
        started_ts = datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()
        if time.time() - started_ts > STALE_AFTER_SECONDS:
            return None  # stale run — never arm guards forever
    except Exception:
        return None
    return state


def _load_rules():
    """Host policy replaces the shipped default wholesale — the effective policy is always
    exactly one file."""
    path = HOST_RULES_PATH if HOST_RULES_PATH.is_file() else DEFAULT_RULES_PATH
    return json.loads(path.read_text(encoding='utf-8'))


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }}))


def main():
    config = h.load_config()
    state = _run_state(config)
    if state is None:
        return 0  # no active autopilot run — guard disarmed
    data = h.read_hook_input()
    rel = h.relpath_from_tool_input(data)
    if not rel:
        return 0
    rules = _load_rules()
    effective = state.get('effective_mode') or 'yolo'
    confirmed = state.get('gates_confirmed') or []
    for category in rules.get('categories') or []:
        for glob in category.get('globs') or []:
            if fnmatch.fnmatch(rel, glob):
                floor = category.get('floor') or 'full-gates'
                if MODE_RANK.get(effective, 0) < MODE_RANK.get(floor, 2):
                    deny("sensitive path ({}): floor '{}' exceeds effective mode '{}' — the "
                         'risk classifier must re-run and the mode be escalated before this '
                         'file can change'.format(category.get('name'), floor, effective))
                    return 0
                if FLOOR_PAUSE_GATE not in confirmed:
                    deny("sensitive path ({}): floor '{}' requires the {} pause to be "
                         'confirmed in run-state.json before writes'.format(
                             category.get('name'), floor, FLOOR_PAUSE_GATE))
                    return 0
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('sensitive guard error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
```

- [ ] **Step 4: Add `.artel/sensitive-paths.json` to `docs/config.md` "Purpose and location"**

Replace:

```markdown
- `.artel/config.json` — this file, committed.
```

with:

```markdown
- `.artel/config.json` — this file, committed.
- `.artel/sensitive-paths.json` — optional host override of the sensitive-paths policy the
  `sensitive_guard` hook enforces (categories of globs with mode floors,
  [autonomous-run.md](autonomous-run.md) §10). When present it replaces the plugin's shipped
  default policy (`hooks/sensitive-paths.json`) wholesale. Committed, like the config; the
  `setup` skill offers to scaffold it from the shipped defaults.
```

- [ ] **Step 5: Smoke-check both hooks from the repo root** (no active ticket / no run state → both allow)

Run: `echo '{}' | python3 hooks/stop_gate.py; echo "exit=$?"`
Expected: no output, `exit=0`

Run: `echo '{"tool_input": {"file_path": ".env.local"}}' | python3 hooks/sensitive_guard.py; echo "exit=$?"`
Expected: no output, `exit=0` (guard disarmed without an armed run)

Deny path, in a scratch repo with an armed run:

```bash
SCRATCH=$(mktemp -d) && cd "$SCRATCH" && mkdir -p .artel/run/PROJ-1 specs/.current \
  && echo '{}' > .artel/config.json && echo 'PROJ-1' > specs/.current/.active_ticket \
  && printf '{"run_active": true, "completed": false, "started_at": "%s", "effective_mode": "plan-gate", "gates_confirmed": ["TASKLIST_READY"]}' \
     "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > .artel/run/PROJ-1/run-state.json \
  && echo '{"tool_input": {"file_path": ".env.local"}}' | python3 "$OLDPWD/hooks/sensitive_guard.py"; cd "$OLDPWD"
```
Expected: a `permissionDecision: "deny"` JSON naming category `secrets` and floor `full-gates` (plan-gate < full-gates). Also check the stop gate blocks there: `cd "$SCRATCH" && echo '{}' | python3 "$OLDPWD/hooks/stop_gate.py"; cd "$OLDPWD"` → `{"decision": "block", ...}` with `Block 1/5`.

- [ ] **Step 6: Run the full test suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add hooks/stop_gate.py hooks/sensitive_guard.py hooks/sensitive-paths.json docs/config.md
git commit -m "feat: port run stop gate and sensitive-path guard"
```

---

### Task 7: `hooks/hooks.json` — event wiring

**Files:**
- Create: `hooks/hooks.json`

**Interfaces:**
- Consumes: the five hook scripts from Tasks 4–6.
- Produces: plugin hook registration Claude Code discovers by convention (no `plugin.json` change needed).

- [ ] **Step 1: Create `hooks/hooks.json`**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/session_baseline.py\"",
            "timeout": 120
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/fast_verify_post_edit.py\"",
            "timeout": 150
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/stop_gate.py\"",
            "timeout": 60
          },
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/verify_stop_gate.py\"",
            "timeout": 300
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "cd \"$CLAUDE_PROJECT_DIR\" && python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/sensitive_guard.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Validate the JSON and the referenced paths**

Run: `python3 -c "import json; d = json.load(open('hooks/hooks.json')); print(sorted(d['hooks'].keys()))"`
Expected: `['PostToolUse', 'PreToolUse', 'SessionStart', 'Stop']`

Run: `ls hooks/session_baseline.py hooks/fast_verify_post_edit.py hooks/stop_gate.py hooks/verify_stop_gate.py hooks/sensitive_guard.py`
Expected: all five paths print (every script hooks.json references exists)

(A live `/hooks`-listing check happens at the Phase-6 install dry run — plugin hook registration cannot be exercised from inside this repo.)

- [ ] **Step 3: Commit**

```bash
git add hooks/hooks.json
git commit -m "feat: wire quality-gate hooks via hooks.json"
```

---

### Task 8: Docs close-out

**Files:**
- Modify: `docs/design.md` (open question 1 + decision log)
- Modify: `docs/porting-plan.md` (Phase 5 checkboxes + two map rows)
- Modify: `docs/autonomous-run.md` (§8, §10)
- Modify: `skills/feature-development/SKILL.md` (Gate 3.5 skip clause)
- Modify: `skills/setup/SKILL.md` (interview + validate + write additions)
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md` (version-bump erratum)

**Interfaces:**
- Consumes: everything shipped in Tasks 1–7 (this task records it).
- Produces: nothing executable — documentation truth.

- [ ] **Step 1: `docs/design.md` — mark open question 1 decided**

Replace:

```markdown
1. **The deterministic CLI.** The source system's `agent` CLI (plan-check, gate verbs) is
   written in Dart — unacceptable as a hard dependency for "any project". Options: rewrite in
   Python (hooks already require `python3`), rewrite as POSIX shell, or drop the CLI and fold
   its checks into hooks. *Leaning: Python rewrite, shipped under `hooks/` or `scripts/`.*
```

with:

```markdown
1. ~~**The deterministic CLI.**~~ Decided 2026-08-07: Python rewrite of `verify` + `plan-check`
   under `scripts/`; `codegen` dropped (`setup.commands` covers install/codegen) — see decision
   log.
```

- [ ] **Step 2: `docs/design.md` — append decision-log entries** (after the 2026-08-01 `/artel:setup` entry, matching its style)

```markdown
- **2026-08-07 — Open question 1: Python rewrite of `verify` + `plan-check`; `codegen` dropped.**
  `scripts/plan_check.py` was effectively pre-decided — feature-development's Gate 3.5 already
  calls it. `scripts/verify.py` wraps the configured `verify.fast`/`verify.commands` in the
  source CLI's one-line JSON envelope (exit 0 clean / 1 findings / 2 environment error, kinds
  `invalid_argument`/`timeout`/`spawn_failed`/`command_not_found`/`internal_error`), giving hooks
  one deterministic contract over arbitrary commands: exit 126/127, a spawn failure, or a timeout
  classify as environment errors, any other non-zero as findings. The source `codegen` verb is
  not ported — its auto-detection rules (freezed/arb/API annotations) are inherently
  Dart-specific and `setup.commands` covers install/codegen generically. `plan_check.py` keeps
  the source anchor grammar, genericizes the backticked-path rule (any repo-relative token with
  a `/` and a file extension, Dart root whitelist dropped), and resolves symbols via `ast-index`
  when on PATH, else `git grep -l -w` — no hard tool dependency. Phase-3 skill bodies keep
  running config commands directly; `verify.py` is the hooks' engine, not a forced migration.
- **2026-08-07 — Finding keys are digit-stripped output lines.** The source stop gate diffed
  structured `file:line:rule` keys; generic commands emit arbitrary text. A key = a non-empty
  output line of a red stage, ANSI-stripped, digit-stripped, whitespace-collapsed, deduped,
  prefixed `s<stage-index>:`, capped at 200 per stage — stable against shifting line numbers and
  timing noise ("Done in 3.2s") at the accepted cost of deduping same-rule-same-file findings.
  Keys are computed once in `verify.py`; hooks read them from the envelope.
- **2026-08-07 — `verify.surface` config key + `{files}` placeholder.** Replaces the source
  hooks' hardcoded `is_code_dart` filter: optional fnmatch globs (`!`-prefix excludes; only-
  excludes implies `*`; absent → every changed file counts), deliberately separate from
  `runtime.surface` (runtime and lintable surfaces are different sets). `verify.fast`/
  `verify.commands` entries may carry `{files}`, replaced with the space-joined shell-quoted
  changed paths; a blank `--files` value is an `invalid_argument`, never a silent widening to
  unscoped.
- **2026-08-07 — Sensitive-paths policy: shipped defaults + wholesale host override.** The
  plugin ships `hooks/sensitive-paths.json` with three generic categories: `secrets`
  (full-gates), `gate-config` (full-gates — an armed run must not rewrite its own gates or the
  host's hook wiring), `ci-cd` (plan-gate). A host `.artel/sensitive-paths.json` replaces the
  default wholesale — no merge semantics, the effective policy is always exactly one readable
  file; the `setup` skill offers to scaffold it from the defaults. Broader nets (migrations,
  lockfiles, infra) were rejected: too many innocent matches across ecosystems.
- **2026-08-07 — Hook state at `.artel/run/.hooks/`; hooks inert until configured.** Session
  baselines and verify-stop counters live in a dot-prefixed dir inside the already-gitignored
  run tree (can never collide with a ticket dir); the per-ticket stop-gate counter stays at
  `.artel/run/<TICKET>/.stop-gate-blocks` for source parity. The verify-layer hooks return 0
  immediately when `.artel/config.json` does not exist, so an installed-but-unconfigured plugin
  leaves zero footprint in the host repo.
```

- [ ] **Step 3: `docs/porting-plan.md` — check off Phase 5 and update two map rows**

Replace the Phase 5 checkbox block:

```markdown
- [ ] Port Python hooks: `session_baseline`, `fast_verify_post_edit`, `stop_gate`,
      `verify_stop_gate`, `sensitive_guard`, `hook_common`.
- [ ] Wire via `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}` paths.
- [ ] Verify commands come from config, not `make`/Dart assumptions.
- [ ] Decide the deterministic-CLI question (design.md open question 1) and implement.
```

with:

```markdown
- [x] Port Python hooks: `session_baseline`, `fast_verify_post_edit`, `stop_gate`,
      `verify_stop_gate`, `sensitive_guard`, `hook_common`.
- [x] Wire via `hooks/hooks.json` with `${CLAUDE_PLUGIN_ROOT}` paths.
- [x] Verify commands come from config, not `make`/Dart assumptions — `scripts/verify.py` wraps
      `verify.fast`/`verify.commands` in the envelope contract; new `verify.surface` key +
      `{files}` placeholder (config.md).
- [x] Decide the deterministic-CLI question (design.md open question 1) and implement — Python
      `verify` + `plan-check` under `scripts/`; `codegen` dropped (see decision log and the map
      below).
```

Replace the map row:

```markdown
| `tools/agent/` (Dart CLI) | `scripts/` or `hooks/` | Python rewrite: `verify`, `plan-check`, `codegen`; `dcm` folds into `verify` stages (Phase 5) |
```

with:

```markdown
| `tools/agent/` (Dart CLI) | `scripts/` | Python rewrite: `verify`, `plan-check` (Phase 5); `codegen` dropped — detection rules were Dart-specific, `setup.commands` covers install/codegen; `dcm` subsumed by config-driven `verify.commands` |
```

Replace the map row:

```markdown
| `rules/sensitive-paths.json` | `hooks/` or `docs/` | port, categories configurable (Phase 5) |
```

with:

```markdown
| `rules/sensitive-paths.json` | `hooks/sensitive-paths.json` | generic default categories (secrets, gate-config, ci-cd); host override at `.artel/sensitive-paths.json` (Phase 5) |
```

- [ ] **Step 4: `docs/autonomous-run.md` — resolve the Phase-5 forward references**

§8, replace:

```markdown
The Stop hook (registered on the `Stop` event, shipped with the plugin's own hooks; Phase 5 — see
[porting-plan.md](porting-plan.md)) blocks a session from ending while
```

with:

```markdown
The Stop hook (registered on the `Stop` event — the plugin's `hooks/stop_gate.py`) blocks a
session from ending while
```

§10 classifier item 2, replace:

```markdown
2. Match them against the sensitive-paths policy (glob-based categories, `fnmatch` semantics;
   ships with generic defaults, project-extensible (Phase 5)). `forced_floor` = the highest floor
   among matched categories, else `null`.
```

with:

```markdown
2. Match them against the sensitive-paths policy (glob-based categories, `fnmatch` semantics;
   shipped defaults in the plugin's `hooks/sensitive-paths.json`, replaced wholesale by a host
   `.artel/sensitive-paths.json` when present — config.md, "Purpose and location").
   `forced_floor` = the highest floor among matched categories, else `null`.
```

§10 `gates_confirmed` paragraph, replace:

```markdown
appends `"TASKLIST_READY"`. The sensitive-path guard (a `PreToolUse` hook shipped with the
plugin) denies writes to floored paths until it is present.
```

with:

```markdown
appends `"TASKLIST_READY"`. The sensitive-path guard (the plugin's `hooks/sensitive_guard.py`,
a `PreToolUse` hook) denies writes to floored paths until it is present.
```

- [ ] **Step 5: `skills/feature-development/SKILL.md` — drop Gate 3.5's now-obsolete skip clause**

In the gate-3.5 table row, replace:

```markdown
where `<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. The script ships in Phase 5 (see `${CLAUDE_PLUGIN_ROOT}/docs/porting-plan.md`); **when it does not exist yet** (check before running), journal `PLAN_GROUNDED: skipped (plan-check ships in Phase 5)` and proceed — an unshipped tool degrades like an unconfigured gate. When it exists: exit 0 → proceed.
```

with:

```markdown
where `<plan-path>` is the phase-aware plan path per ticket-parsing.md §4. Exit 0 → proceed.
```

- [ ] **Step 6: `skills/setup/SKILL.md` — interview, validate, and write additions**

Round 3, replace:

```markdown
- **Round 3 — quality gate and languages:** `verify.commands` (ordered list, one command per
  line; empty = no gate, recorded `skipped`), `verify.fast` (one quick per-edit command, or
  empty), and `language.docs` / `language.pr` (IETF BCP 47 codes, default `en`).
```

with:

```markdown
- **Round 3 — quality gate and languages:** `verify.commands` (ordered list, one command per
  line; empty = no gate, recorded `skipped`), `verify.fast` (one quick per-edit command, or
  empty; commands may carry a `{files}` token the hooks replace with the changed paths), and
  `language.docs` / `language.pr` (IETF BCP 47 codes, default `en`).
```

Round 4, replace:

```markdown
- **Round 4 — optional extras**, one multi-select question ("configure now, or leave inert?")
  offering: `setup.commands` (post-branch install/codegen), `design.figma` (the design-analysis
  stage), the `runtime.*` commands (`run`, `drive`, `scaffold.add`, `scaffold.remove`), and
  `runtime.surface` (globs gating when the runtime gate runs). Ask follow-up value questions
  only for the selected ones; everything skipped keeps its inert default.
```

with:

```markdown
- **Round 4 — optional extras**, one multi-select question ("configure now, or leave inert?")
  offering: `setup.commands` (post-branch install/codegen), `design.figma` (the design-analysis
  stage), the `runtime.*` commands (`run`, `drive`, `scaffold.add`, `scaffold.remove`),
  `runtime.surface` (globs gating when the runtime gate runs), `verify.surface` (globs with
  `!`-excludes filtering which edits the verify hooks check), and a `.artel/sensitive-paths.json`
  scaffold (a copy of the plugin's default sensitive-paths policy, for projects that want to
  extend it). Ask follow-up value questions only for the selected ones; everything skipped keeps
  its inert default.
```

Step 3 (Validate), replace:

```markdown
Before writing: adapter names inside their allowed sets; `verify.commands` / `setup.commands` /
`runtime.surface` are arrays of strings; MCP-adapter prefixes non-empty; language codes plausible
BCP 47. A violation re-asks that round — never write a config that config.md's reading rules
would reject at run start.
```

with:

```markdown
Before writing: adapter names inside their allowed sets; `verify.commands` / `setup.commands` /
`runtime.surface` / `verify.surface` are arrays of strings; MCP-adapter prefixes non-empty;
language codes plausible BCP 47. A violation re-asks that round — never write a config that
config.md's reading rules would reject at run start.
```

Step 4 (Write), replace:

```markdown
Write `.artel/config.json` — one complete, explicit file in the shape of config.md's "A filled
example": `version: 1` first, then every section in that example's order, interviewed values
filled in and untouched keys carrying their documented defaults. Strict JSON, UTF-8, no
comments, no trailing commas. This is the skill's only write to the file and its last mutating
step but one — an interview aborted earlier leaves no partial config behind.
```

with:

```markdown
Write `.artel/config.json` — one complete, explicit file in the shape of config.md's "A filled
example": `version: 1` first, then every section in that example's order, interviewed values
filled in and untouched keys carrying their documented defaults. Strict JSON, UTF-8, no
comments, no trailing commas. When the sensitive-paths scaffold was selected in Round 4, also
copy `${CLAUDE_PLUGIN_ROOT}/hooks/sensitive-paths.json` to `.artel/sensitive-paths.json`
(skip with a note if the host file already exists — never overwrite a policy). These are the
skill's only writes and its last mutating steps but one — an interview aborted earlier leaves
no partial config behind.
```

- [ ] **Step 7: `CHANGELOG.md` — Phase 5 entry** (append under `### Added`, after the entry-point orchestrators bullet)

```markdown
- Hooks and gates (Phase 5): `scripts/verify.py` (deterministic envelope wrapper over
  `verify.fast`/`verify.commands` — exit 0 clean / 1 findings / 2 environment error, digit-
  stripped finding keys, `{files}` scoping) and `scripts/plan_check.py` (the plan-anchor checker
  Gate 3.5 invokes; `ref:`/`new:` grammar ported, backticked-path rule genericized, symbols via
  `ast-index` when present else `git grep`), resolving design.md open question 1 (`codegen`
  dropped — `setup.commands` covers it). Five hooks ported from the source project and wired via
  `hooks/hooks.json`: `session_baseline` (SessionStart findings baseline),
  `fast_verify_post_edit` (PostToolUse feedback, never blocks), `verify_stop_gate` (Stop; blocks
  only findings NEW vs the session baseline, 2-block cap with latch), `stop_gate` (Stop; blocks
  while an autonomous run is active and incomplete, 5-block cap, 3h wall clock),
  `sensitive_guard` (PreToolUse; mode-floor denials during armed runs). Shipped default
  sensitive-paths policy (`hooks/sensitive-paths.json`: secrets/gate-config at full-gates,
  ci-cd at plan-gate) with wholesale host override at `.artel/sensitive-paths.json`; new
  `verify.surface` config key; hook state under `.artel/run/.hooks/`; hooks inert until
  `.artel/config.json` exists; stdlib `unittest` suite under `tests/`.
```

- [ ] **Step 8: Spec erratum — no version bump before the Phase-6 tag**

In `docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md`, replace:

```markdown
- `CHANGELOG.md` + `.claude-plugin/plugin.json` version bump (minor — new capability).
```

with:

```markdown
- `CHANGELOG.md` entry under `[Unreleased]`. *(Erratum vs. the approved draft, which said to
  bump the plugin version now: the repo accumulates all pre-release work under `[Unreleased]`
  at version `0.1.0` and tags `v0.1.0` in Phase 6 — a mid-stream bump would be the first and
  only one of its kind.)*
```

and in the Deliverables list, replace:

```markdown
- Doc updates: config.md, autonomous-run.md §10, design.md (open question 1 + decision log),
  porting-plan.md (checkboxes + map row), skills/setup interview additions, CHANGELOG + version.
```

with:

```markdown
- Doc updates: config.md, autonomous-run.md §10, design.md (open question 1 + decision log),
  porting-plan.md (checkboxes + map row), skills/setup interview additions, CHANGELOG.
```

- [ ] **Step 9: Run the full test suite one last time**

Run: `python3 -m unittest discover -s tests -v`
Expected: all tests PASS

- [ ] **Step 10: Commit**

```bash
git add docs/design.md docs/porting-plan.md docs/autonomous-run.md \
  skills/feature-development/SKILL.md skills/setup/SKILL.md CHANGELOG.md \
  docs/superpowers/specs/2026-08-07-phase5-hooks-gates-design.md
git commit -m "docs: close out phase-5 hooks and gates"
```
