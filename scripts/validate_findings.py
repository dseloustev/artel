#!/usr/bin/env python3
"""Anchor validation for the reviewer's machine-readable findings.

A finding with a wrong line number is worse than one with no line number: the
forecaster attaches findings to change units by `file:line` and `deep-review.md` cites
them, so a guessed anchor propagates into a document a human acts on. This script is
the mechanical half of the reviewer's evidence contract — it matches every finding in
`review/findings.json` against the new-side hunks of the diff that was actually
reviewed, and reports what cannot anchor together with the file's real hunk ranges.

    validate_findings.py FINDINGS [--diff PATH | --range REV]

FINDINGS is a path, or `-` for stdin. The diff comes from `--diff` (a unified diff
file, or `-`) or from `--range`, any revision or range `git diff` accepts (default
`HEAD`: the working tree against the last commit).

Each entry may carry `scope`:

- `diff` (the default) — a finding of the change under review. It must anchor inside
  a new-side hunk; `line` may be omitted for a file-level observation.
- `repository` — a pre-existing issue in unchanged code. It must not anchor inside a
  changed hunk: a defect on a changed line is a diff finding, even when the behaviour
  predates the change.

Exit codes: 0 every finding anchors, 1 at least one does not, 2 usage or input error
(unreadable or invalid findings JSON, a bad entry, a diff that cannot be produced).

Best-effort by design, like the review it serves: a diff shape this parser does not
recognise leaves its file absent, and its findings are reported rather than trusted.
Contract: agents/reviewer.md, `## Evidence and anchoring`.
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

SCOPES = ('diff', 'repository')
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

Check = namedtuple('Check', 'label ok reason')


class InputError(Exception):
    pass


def parse_diff(text):
    """Map each new-side path to its (start, end) line ranges, 1-based inclusive.

    Hunks are consumed by their declared line counts, so a hunk body is never
    mistaken for a file header (`+++ ...` is a header only between `---` and the
    first `@@`). Files deleted on the new side (`+++ /dev/null`) are absent.
    """
    hunks = {}
    path = None
    old_left = new_left = 0
    for raw in text.splitlines():
        line = raw.rstrip('\r')
        if old_left > 0 or new_left > 0:
            if line.startswith('\\'):
                continue
            if line.startswith('+'):
                new_left -= 1
            elif line.startswith('-'):
                old_left -= 1
            else:
                old_left -= 1
                new_left -= 1
            continue
        if line.startswith('diff --git ') or line.startswith('--- '):
            path = None
            continue
        if line.startswith('+++ '):
            path = diff_path(line[4:])
            if path is not None:
                hunks.setdefault(path, [])
            continue
        match = HUNK.match(line)
        if match:
            _old_start, old_count, new_start, new_count = match.groups()
            old_left = int(old_count) if old_count is not None else 1
            new_left = int(new_count) if new_count is not None else 1
            if path is not None and new_left > 0:
                start = int(new_start)
                hunks[path].append((start, start + new_left - 1))
    return hunks


def diff_path(header):
    """The repo-relative new-side path of a `+++` header, or None."""
    header = header.strip()
    if not header or header == '/dev/null':
        return None
    if header.startswith('"') and header.endswith('"'):
        try:
            header = ast.literal_eval(header)
        except (SyntaxError, ValueError):
            return None
    if header.startswith('b/'):
        header = header[2:]
    while header.startswith('./'):
        header = header[2:]
    return header or None


def check_findings(findings, hunks):
    """One Check per finding; raises InputError on a malformed entry."""
    checks = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise InputError('findings[{}] is not an object'.format(index))
        path = finding.get('file')
        if not isinstance(path, str) or not path:
            raise InputError('findings[{}] has no file'.format(index))
        path = normalise(path)
        scope = finding.get('scope', 'diff')
        if scope not in SCOPES:
            raise InputError("findings[{}] scope must be 'diff' or 'repository'".format(index))
        line = finding.get('line')
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise InputError('findings[{}] line must be a positive integer, or omitted'.format(index))
        label = path if line is None else '{}:{}'.format(path, line)
        label += ' [{}]'.format(scope)
        ranges = hunks.get(path)
        if scope == 'repository':
            if line is not None and ranges and any(start <= line <= end for start, end in ranges):
                checks.append(Check(label, False, 'line {} is inside a changed hunk; '
                                     'it is a diff finding, not repository scope'.format(line)))
            else:
                checks.append(Check(label, True, ''))
            continue
        if ranges is None:
            checks.append(Check(label, False, 'file is not in the diff'))
        elif line is None or any(start <= line <= end for start, end in ranges):
            checks.append(Check(label, True, ''))
        else:
            checks.append(Check(label, False, 'line {} is not in a new-side hunk{}'.format(
                line, ranges_suffix(ranges))))
    return checks


def normalise(path):
    while path.startswith('./'):
        path = path[2:]
    return path


def ranges_suffix(ranges):
    if not ranges:
        return ' (no new-side hunks)'
    return ' (hunks: {})'.format(', '.join('{}-{}'.format(start, end) for start, end in ranges))


def read_text(path):
    if path == '-':
        return sys.stdin.read()
    return Path(path).read_text(encoding='utf-8')


def read_findings(path):
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise InputError('findings are not valid JSON: {}'.format(exc))
    if not isinstance(data, list):
        raise InputError('findings must be a JSON array')
    return data


def read_diff(args):
    if args.diff is not None:
        return read_text(args.diff)
    revision = args.range if args.range is not None else 'HEAD'
    try:
        proc = subprocess.run(
            ['git', '-c', 'core.quotePath=false', 'diff', '--no-color', '--no-ext-diff',
             revision],
            capture_output=True, text=True)
    except OSError as exc:
        raise InputError('cannot run git: {}'.format(exc))
    if proc.returncode != 0:
        detail = proc.stderr.strip() or 'exit {}'.format(proc.returncode)
        raise InputError('git diff {} failed: {}'.format(revision, detail))
    return proc.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='validate_findings.py',
        description="Check review/findings.json anchors against the reviewed diff's "
                    'new-side hunks.')
    parser.add_argument('findings', help='review/findings.json, or - for stdin')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--diff', help='unified diff file, or - for stdin')
    source.add_argument('--range', help='a git diff revision or range (default: HEAD)')
    args = parser.parse_args(argv)
    try:
        if args.findings == '-' and args.diff == '-':
            raise InputError('only one of FINDINGS and --diff can be stdin')
        findings = read_findings(args.findings)
        hunks = parse_diff(read_diff(args))
        checks = check_findings(findings, hunks)
    except (InputError, OSError) as exc:
        print('validate_findings.py: {}'.format(exc), file=sys.stderr)
        return 2
    failed = 0
    for check in checks:
        if check.ok:
            print('ok    {}'.format(check.label))
        else:
            failed += 1
            print('FAIL  {} — {}'.format(check.label, check.reason))
    print('validate_findings.py: {} finding(s), {} failed'.format(len(checks), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
