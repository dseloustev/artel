#!/usr/bin/env python3
"""plan-check: resolve every anchor a plan cites (ref:/new: tokens + backticked repo-relative
paths) against the filesystem and a symbol index; flag hallucinated references before
implementation starts.

Symbols resolve via `ast-index symbol <name> --format json` when that binary is on PATH and its
output is usable (with one `ast-index update` stale-index retry for a real miss), else via
`git grep -l -w <name>` — used as the per-symbol fallback whenever ast-index is unusable (no
index built, non-JSON output) too, so the anti-hallucination check works in repos without
ast-index and does not misreport real symbols as hallucinations when the index is merely
missing.

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


def _classify_ast_index_output(returncode, stdout):
    """Classify one `ast-index symbol <name> --format json` invocation.

    'hit'      — valid JSON, a non-empty list: the symbol is indexed and found.
    'miss'     — valid JSON, an empty list: a real, trustworthy indexed miss.
    'unusable' — anything else (non-zero exit, non-JSON stdout, non-list JSON) — notably
                 the no-index case, where ast-index prints
                 "Index not found. Run 'ast-index rebuild' first." to stdout and exits 0.
                 Callers must not treat 'unusable' as a miss; it carries no information
                 about whether the symbol exists.
    """
    if returncode != 0:
        return 'unusable'
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return 'unusable'
    if not isinstance(parsed, list):
        return 'unusable'
    return 'hit' if len(parsed) > 0 else 'miss'


def _symbol_status_ast_index(name):
    base = name.split('.')[0]  # member refs like Foo.bar resolve on Foo
    try:
        proc = _run(['ast-index', 'symbol', base, '--format', 'json'], timeout=60)
    except Exception:
        return 'unusable'
    return _classify_ast_index_output(proc.returncode, proc.stdout)


def _symbol_resolves_git_grep(name):
    base = name.split('.')[0]
    try:
        proc = _run(['git', 'grep', '-l', '-w', base], timeout=60)
    except Exception:
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def resolve_pass(pending, have_ast_index):
    """Returns (unresolved, ast_index_misses). `ast_index_misses` lists the symbol values
    that came back as a real ast-index 'miss' (eligible for the single `ast-index update`
    retry) — 'unusable' ast-index results are routed to git grep immediately in this same
    pass and are never added, so a repo with no index does not thrash the retry."""
    unresolved = []
    ast_index_misses = []
    for kind, value in pending:
        if '/' in value:
            if not Path(value).exists():  # file or directory
                unresolved.append({'ref': value, 'reason': 'file not found'})
            continue
        if have_ast_index:
            status = _symbol_status_ast_index(value)
            if status == 'hit':
                continue
            if status == 'miss':
                unresolved.append({'ref': value, 'reason': 'symbol not found'})
                ast_index_misses.append(value)
                continue
            # 'unusable' — the index is missing/stale/unreadable; fall back to git grep for
            # this symbol right away instead of reporting a hallucination.
            if not _symbol_resolves_git_grep(value):
                unresolved.append({'ref': value, 'reason': 'symbol not found'})
        else:
            if not _symbol_resolves_git_grep(value):
                unresolved.append({'ref': value, 'reason': 'symbol not found'})
    return unresolved, ast_index_misses


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

    unresolved, ast_index_misses = resolve_pass(to_resolve, have_ast_index)
    if ast_index_misses:
        # Index may be stale — refresh once and re-check only the real ast-index misses.
        # Anchors that fell back to git grep because ast-index was unusable are excluded
        # (see resolve_pass): retrying those against the same unusable index would just
        # thrash without new information.
        try:
            _run(['ast-index', 'update'], timeout=600)
        except Exception:
            pass
        retry_set = set(ast_index_misses)
        pending = [(k, v) for k, v in to_resolve if v in retry_set]
        retried_unresolved, _ = resolve_pass(pending, have_ast_index)
        unresolved = [u for u in unresolved if u['ref'] not in retry_set] + retried_unresolved

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
