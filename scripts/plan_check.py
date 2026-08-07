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
