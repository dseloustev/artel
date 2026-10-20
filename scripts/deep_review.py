#!/usr/bin/env python3
"""The deterministic boundary of the deep review: pin the target, validate the artifacts.

`deep-review` dispatches one reviewer to produce two artifacts for a branch -- a machine
report (schema 1) and the ticket's `deep-review.md`. This script is the mechanical half of
that contract; the orchestrator never judges the reviewer's output itself.

    deep_review.py prepare --ticket T --specs-dir DIR [--branch B] [--base R] --output PATH
        Pin the review target: the checked-out branch, HEAD, the base tip and their merge
        base, all as SHAs, in a manifest (`schema: 1`, a fresh `run_id` UUID). The checkout
        must be clean apart from `.artel/` and the specs dir; a pending source change is
        refused, and a `--branch` that is not the checked-out branch is refused with a
        worktree recommendation rather than silently reviewing HEAD. Read-only: no fetch,
        no new refs, no index, no HEAD changes.

    deep_review.py check --target MANIFEST --findings REPORT --report PATH|- --summary LINE
                         [--report-path PATH]
        Validate the reviewer's artifacts against the pinned target: identities (ticket,
        run, base, head), the machine report's schema, every anchor against the pinned
        `merge_base..head` diff, the Markdown header and metadata, the findings headings,
        the `## Proposed fixes` task blocks, and the completion line's counts and path.

Exit codes:
    0  valid and complete
    1  the artifacts are inconsistent, partial, or violate the contract (errors in the
       JSON envelope on stdout)
    2  the tool cannot evaluate its inputs: usage, unreadable, unparsable or empty files, an
       invalid manifest, or a target that no longer resolves in this checkout

An empty or whitespace-only report is unprocessable input: `check` refuses it as `empty_input`
(exit 2), never as a missing header (exit 1) that a bounded artifact retry could correct.

`check` prints one JSON envelope on stdout: `{"ok": true, "data": ...}` on success,
`{"ok": false, "errors": [...]}` on failure, with best-effort `data` once the machine
report parses. Contract: skills/artel-deep-review/SKILL.md, agents/artel-reviewer.md.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))

import doc_header  # noqa: E402
import validate_findings  # noqa: E402

SCHEMA = 1
PRIORITIES = ('Critical', 'Warning', 'Suggestion')
ACTIONABLE = ('Critical', 'Warning')
SCOPES = ('diff', 'repository')
KINDS = ('defect', 'audit')
BASE_CANDIDATES = ('refs/remotes/origin/main', 'refs/remotes/origin/master',
                   'refs/heads/main', 'refs/heads/master', 'refs/heads/trunk')
SENSITIVE_HOST = Path('.artel/sensitive-paths.json')
SENSITIVE_DEFAULT = Path(__file__).resolve().parent.parent / 'hooks' / 'sensitive-paths.json'
FINDING_ID = re.compile(r'DR-\d{3}\Z')
COMMIT_SHA = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?\Z')
HEADER_FIELD = re.compile(r'([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*?))?[ \t]*\Z')
FENCE = re.compile(r' {0,3}(`{3,}|~{3,})')
SECTION = re.compile(r'^##(?!#)\s+(.*?)\s*$')
SUBSECTION = re.compile(r'^###\s+(.*?)\s*$')
FINDING_HEADING = re.compile(r'^DR-(\d{3})\s*\(([^)]*)\)\s*:\s*(\S.*?)\s*$')
TASK_HEADING = re.compile(r'^Tasks \((Critical|Warning)\)\Z')
TASK = re.compile(r'^- \[ \]\s*\*\*Task (\d+) \((Critical|Warning)(, behavior)?\): (.+?)\*\*\s*$')
CHECKED = re.compile(r'^- \[[xX]\]')
CHECKBOX = re.compile(r'^- \[[ xX]\]')
NESTED_CHECKBOX = re.compile(r'^\s+- \[[ xX]\]')
NONE_ITEM = re.compile(r'^- none\s*$')
FINDING_LINE = re.compile(r'Finding:\s*(\S+)')
SUMMARY = re.compile(r'^Deep review: (\d+) Critical, (\d+) Warning, (\d+) Suggestion — (.+?)\s*$')
META_LABELS = ('Review run', 'Reviewed base', 'Reviewed head', 'Review completeness')
PRIORITY_CATEGORY = {'Critical': 'critical', 'Warning': 'warning', 'Suggestion': 'suggestion'}

# An empty report is a failed producer, not a malformed artifact: on the kartoteka path a
# failed `spec_store.py get` prints nothing, and read as a report it would look like a
# contract violation the bounded retry could try to correct. It is refused as unprocessable
# input (exit 2) with the same `empty_input` token the other stdin consumers use.
EMPTY_INPUT_STDIN = ('the report on stdin is empty (empty_input); if it was piped from '
                     'spec_store.py get, that command failed — its exit status and stderr say '
                     'why (run the pipe with set -o pipefail)')
EMPTY_INPUT_FILE = ('the report {} is empty (empty_input); the reviewer produced no report to '
                    'validate')


class InputError(Exception):
    """The tool cannot evaluate its inputs (exit 2)."""


# --- git, read-only ---------------------------------------------------------

def git(root, *args):
    proc = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or 'git {} failed with exit {}'.format(
            ' '.join(args), proc.returncode)
        raise InputError(detail)
    return proc.stdout


def git_ok(root, *args):
    try:
        return git(root, *args)
    except InputError:
        return None


def repo_root():
    try:
        return git('.', 'rev-parse', '--show-toplevel').strip()
    except InputError as exc:
        raise InputError('not inside a git checkout: {}'.format(exc))


def resolve_commit(root, rev):
    """The full SHA `rev` names a commit, or None."""
    out = git_ok(root, 'rev-parse', '--verify', '--quiet', '--end-of-options', rev + '^{commit}')
    if not out:
        return None
    return out.strip() or None


def current_branch(root):
    out = git_ok(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    return out.strip() if out else None


def pending_paths(root):
    """Every staged, unstaged and untracked path, repo-relative (renames split).

    `--ignore-submodules=none` is explicit: a host preference such as
    `diff.ignoreSubmodules=all` or a per-submodule `ignore` setting must not hide a
    dirty dependency (modified, staged or untracked content) from this inventory.
    """
    paths = set()
    for args in (('diff', '--ignore-submodules=none', '--no-renames', '--name-only', '-z'),
                 ('diff', '--cached', '--ignore-submodules=none', '--no-renames',
                  '--name-only', '-z'),
                 ('ls-files', '--others', '--exclude-standard', '-z')):
        for item in git(root, *args).split('\0'):
            if item:
                paths.add(item)
    return sorted(paths)


def changed_paths(root, base, head):
    """Every path the pinned diff touches on either side (renames split).

    Rename detection is off, so a rename is a deletion of the old path and an addition of
    the new one: a file-level observation may name either. A line anchor still has to fall
    in a new-side hunk, which only the new path has. `--ignore-submodules=none` keeps a
    changed gitlink in the inventory under any host submodule-ignore preference.
    """
    fields = git(root, 'diff', '--ignore-submodules=none', '--no-renames', '--name-status',
                 '-z', base, head).split('\0')
    paths = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        if not status:
            index += 1
            continue
        if index + 1 < len(fields):
            paths.add(fields[index + 1])
        index += 2
    return paths


def blob_line_count(root, head, path):
    """The number of lines of head:path when it is a blob, else None."""
    spec = '{}:{}'.format(head, path)
    kind = git_ok(root, 'cat-file', '-t', spec)
    if kind is None or kind.strip() != 'blob':
        return None
    proc = subprocess.run(['git', '-C', str(root), 'cat-file', '-p', spec], capture_output=True)
    if proc.returncode != 0:
        return None
    data = proc.stdout
    count = data.count(b'\n')
    if data and not data.endswith(b'\n'):
        count += 1
    return count


# --- the dangerous-change record --------------------------------------------

def sensitive_categories(root):
    """The effective sensitive-paths policy: the host override or the shipped default."""
    host = Path(root) / SENSITIVE_HOST
    path = host if host.is_file() else SENSITIVE_DEFAULT
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError('cannot read the sensitive-paths policy {}: {}'.format(path, exc))
    categories = data.get('categories') if isinstance(data, dict) else None
    if not isinstance(categories, list):
        raise InputError('the sensitive-paths policy {} has no categories list'.format(path))
    return categories


def dangerous_change(root, merge_base, head):
    """The pinned diff's sensitive surfaces, as a deterministic dangerous-change record."""
    paths = sorted(changed_paths(root, merge_base, head))
    classes = []
    matched = set()
    for category in sensitive_categories(root):
        if not isinstance(category, dict):
            continue
        globs = category.get('globs')
        if not isinstance(globs, list):
            continue
        hits = [path for path in paths
                if any(isinstance(glob, str) and fnmatch.fnmatch(path, glob)
                       for glob in globs)]
        if not hits:
            continue
        name = category.get('name')
        classes.append(name if isinstance(name, str) and name else 'sensitive')
        matched.update(hits)
    if not classes:
        return {'is_dangerous': False, 'summary': 'none', 'classes': [], 'paths': []}
    matched = sorted(matched)
    return {'is_dangerous': True,
            'summary': 'yes — {} ({})'.format(', '.join(classes), ', '.join(matched)),
            'classes': classes, 'paths': matched}


# --- paths ------------------------------------------------------------------

def normalise_rel(path):
    """`path` as a safe repo-relative path, or None when it is not one."""
    if not isinstance(path, str):
        return None
    if not path or path.startswith('/') or path.startswith('~') or path.startswith('\\'):
        return None
    if any(char in path for char in ('\x00', '\n', '\r')):
        return None
    while path.startswith('./'):
        path = path[2:]
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return None
    return path


def allowed_artifact(path, specs_dir):
    return (path == '.artel' or path.startswith('.artel/')
            or path == specs_dir or path.startswith(specs_dir + '/'))


def safe_specs_dir(root, raw, label):
    """`raw` as a normalised repo-relative specs dir inside the checkout."""
    if not isinstance(raw, str) or not raw.strip():
        raise InputError('{} must be a non-empty path'.format(label))
    text = raw.strip()
    if text in ('.', './'):
        raise InputError("{} must not be '.': it would allow every pending change".format(label))
    if os.path.isabs(text) or text.startswith('~'):
        raise InputError('{} must be repo-relative, not absolute: {!r}'.format(label, raw))
    normalised = os.path.normpath(text).replace(os.sep, '/')
    parts = normalised.split('/')
    if normalised in ('.', '') or any(part in ('..', '') for part in parts):
        raise InputError('{} must stay inside the checkout: {!r}'.format(label, raw))
    if parts[0] == '.git':
        raise InputError('{} must not point into .git: {!r}'.format(label, raw))
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(os.path.join(root, normalised))
    if target_real == root_real:
        raise InputError('{} must not be the checkout root: {!r}'.format(label, raw))
    if not target_real.startswith(root_real + os.sep):
        raise InputError('{} resolves outside the checkout: {!r}'.format(label, raw))
    return normalised


def inside_root(root, path):
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(path)
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def display_path(root, path):
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(path)
    if target_real == root_real:
        return '.'
    if target_real.startswith(root_real + os.sep):
        return os.path.relpath(target_real, root_real).replace(os.sep, '/')
    return path


def normalise_report_path(root, path):
    """A report path as written in the report or the summary: repo-relative, or absolute."""
    path = path.strip()
    if not path:
        return None
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(root, path))


def same_path(one, other):
    if one is None or other is None:
        return False
    return os.path.realpath(one) == os.path.realpath(other)


# --- prepare ----------------------------------------------------------------

def resolve_default_base(root):
    target = git_ok(root, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD')
    if target and target.strip():
        sha = resolve_commit(root, target.strip())
        if sha is None:
            raise InputError('refs/remotes/origin/HEAD points at {}, which does not resolve '
                             'to a commit'.format(target.strip()))
        return sha
    found = []
    for ref in BASE_CANDIDATES:
        sha = resolve_commit(root, ref)
        if sha is not None:
            found.append((ref, sha))
    if not found:
        raise InputError('cannot resolve a default base: none of {} exist; pass --base'.format(
            ', '.join(BASE_CANDIDATES)))
    distinct = sorted({sha for _ref, sha in found})
    if len(distinct) > 1:
        raise InputError('ambiguous default base: {}; pass --base'.format(
            ', '.join('{}={}'.format(ref, sha[:12]) for ref, sha in found)))
    return found[0][1]


def cmd_prepare(args):
    root = repo_root()
    ticket = args.ticket.strip()
    if not ticket or any(char in ticket for char in ('/', '\\', '\x00', '\n', '\r')):
        raise InputError('--ticket must be a non-empty ticket key without path separators, '
                         'got {!r}'.format(args.ticket))
    specs = safe_specs_dir(root, args.specs_dir, '--specs-dir')
    branch = current_branch(root)
    if branch is None:
        raise InputError('HEAD is detached; check out the target branch -- a worktree checked '
                         'out on it is the safe way -- and re-run')
    if args.branch is not None:
        requested = args.branch.strip()
        if requested.startswith('refs/heads/'):
            requested = requested[len('refs/heads/'):]
        if not requested or any(char in requested for char in ('\x00', '\n', '\r')):
            raise InputError('--branch must be a non-empty branch name')
        if resolve_commit(root, 'refs/heads/' + requested) is None:
            raise InputError('--branch {!r} is not a local branch in this checkout'.format(
                args.branch))
        if requested != branch:
            raise InputError('the checkout is on {!r}, not the requested {!r}; review it from '
                             'a worktree checked out on {!r} instead of silently reviewing '
                             'HEAD'.format(branch, requested, requested))
    head = resolve_commit(root, 'HEAD')
    if head is None:
        raise InputError('cannot resolve HEAD to a commit')
    if args.base is not None:
        base_tip = resolve_commit(root, args.base)
        if base_tip is None:
            raise InputError('--base {!r} does not resolve to a commit'.format(args.base))
    else:
        base_tip = resolve_default_base(root)
    merge_base = git_ok(root, 'merge-base', head, base_tip)
    if merge_base is None:
        raise InputError('cannot compute the merge base of HEAD and the base; they may be '
                         'unrelated histories')
    merge_base = merge_base.strip()
    offenders = [path for path in pending_paths(root) if not allowed_artifact(path, specs)]
    if offenders:
        shown = ', '.join(offenders[:5]) + (' ...' if len(offenders) > 5 else '')
        raise InputError('the source checkout has pending changes outside {}/ and .artel/: {}; '
                         'commit, stash or move them to a worktree before preparing'.format(
                             specs, shown))
    out_path = Path(args.output)
    out_abs = os.path.normpath(os.path.join(os.getcwd(), args.output))
    if inside_root(root, out_abs) and not allowed_artifact(display_path(root, out_abs), specs):
        raise InputError('--output {!r} is inside the checkout but outside {}/ and .artel/; '
                         'keep the manifest with the run evidence'.format(args.output, specs))
    if out_path.exists() and out_path.is_dir():
        raise InputError('--output {!r} is a directory'.format(args.output))
    manifest = {
        'schema': SCHEMA,
        'ticket': ticket,
        'run_id': str(uuid.uuid4()),
        'base': base_tip,
        'head': head,
        'merge_base': merge_base,
        'branch': branch,
        'specs_dir': specs,
        'dangerous': dangerous_change(root, merge_base, head),
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n',
                            encoding='utf-8')
    except OSError as exc:
        raise InputError('cannot write the manifest {}: {}'.format(args.output, exc))
    print('wrote {}: run {}, {} {} (base {}, merge-base {})'.format(
        args.output, manifest['run_id'], branch, head[:12], base_tip[:12], merge_base[:12]))
    return 0


# --- check: reading and the target ------------------------------------------

def read_json(path, label):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError('cannot read the {} {}: {}'.format(label, path, exc))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError('the {} {} is not valid JSON: {}'.format(label, path, exc))


def read_report(path):
    if path == '-':
        text = sys.stdin.read()
        if not text.strip():
            raise InputError(EMPTY_INPUT_STDIN)
        return text
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError('cannot read the report {}: {}'.format(path, exc))
    if not text.strip():
        raise InputError(EMPTY_INPUT_FILE.format(path))
    return text


def read_manifest(path):
    data = read_json(path, 'target manifest')
    if not isinstance(data, dict):
        raise InputError('the target manifest must be a JSON object')
    schema = data.get('schema')
    if schema != SCHEMA or isinstance(schema, bool):
        raise InputError('the target manifest schema must be 1, got {!r}'.format(schema))
    for key in ('ticket', 'run_id', 'base', 'head', 'merge_base', 'branch', 'specs_dir'):
        value = data.get(key)
        if not isinstance(value, str) or not value or value != value.strip():
            raise InputError('the target manifest {} must be a non-empty string without '
                             'surrounding whitespace'.format(key))
    try:
        parsed = uuid.UUID(data['run_id'])
    except (ValueError, AttributeError):
        raise InputError('the target manifest run_id is not a UUID: {!r}'.format(data['run_id']))
    if str(parsed) != data['run_id']:
        raise InputError('the target manifest run_id is not a canonical UUID: {!r}'.format(
            data['run_id']))
    for key in ('base', 'head', 'merge_base'):
        if not COMMIT_SHA.match(data[key]):
            raise InputError('the target manifest {} is not a full commit SHA: {!r}'.format(
                key, data[key]))
    if any(char in data['ticket'] for char in ('/', '\\', '\x00', '\n', '\r')):
        raise InputError('the target manifest ticket is not a usable ticket key: {!r}'.format(
            data['ticket']))
    if any(char in data['branch'] for char in ('\x00', '\n', '\r')):
        raise InputError('the target manifest branch is not a usable branch name: {!r}'.format(
            data['branch']))
    dangerous = data.get('dangerous')
    if not isinstance(dangerous, dict):
        raise InputError('the target manifest dangerous must be an object; re-run prepare')
    if not isinstance(dangerous.get('is_dangerous'), bool):
        raise InputError('the target manifest dangerous.is_dangerous must be a boolean')
    for key in ('classes', 'paths'):
        value = dangerous.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise InputError('the target manifest dangerous.{} must be a list of strings'.format(
                key))
    summary = dangerous.get('summary')
    if not isinstance(summary, str) or not summary.strip():
        raise InputError('the target manifest dangerous.summary must be a non-empty string')
    return data


def resolve_target(root, manifest):
    for key in ('base', 'head', 'merge_base'):
        if resolve_commit(root, manifest[key]) is None:
            raise InputError('target {} {} does not resolve to a commit in this checkout; '
                             're-run prepare'.format(key, manifest[key][:12]))


def expected_report_path(root, manifest, args):
    if args.report_path is not None:
        expected = normalise_report_path(root, args.report_path)
        if expected is None:
            raise InputError('--report-path must not be empty')
        return expected
    if args.report != '-':
        return os.path.normpath(os.path.abspath(args.report))
    return normalise_report_path(root, os.path.join(
        manifest['specs_dir'], manifest['ticket'], 'deep-review.md'))


# --- check: the target checkout still matches -------------------------------

def checkout_errors(root, manifest, specs):
    errors = []
    branch = current_branch(root)
    if branch != manifest['branch']:
        errors.append('the checkout is on {} but the target branch is {}'.format(
            branch or 'detached HEAD', manifest['branch']))
    head = resolve_commit(root, 'HEAD')
    if head != manifest['head']:
        errors.append('HEAD is {} but the target head is {}; the checkout moved since '
                      'prepare'.format(head or 'unresolvable', manifest['head']))
    offenders = [path for path in pending_paths(root) if not allowed_artifact(path, specs)]
    if offenders:
        shown = ', '.join(offenders[:5]) + (' ...' if len(offenders) > 5 else '')
        errors.append('the source checkout has pending changes outside {}/ and .artel/: '
                      '{}'.format(specs, shown))
    return errors


def merge_base_errors(root, manifest):
    recomputed = git_ok(root, 'merge-base', manifest['head'], manifest['base'])
    if recomputed is None:
        return ['cannot recompute the merge base of the target base and head']
    recomputed = recomputed.strip()
    if recomputed != manifest['merge_base']:
        return ['the target merge_base {} does not match merge-base(base, head) = {}'.format(
            manifest['merge_base'], recomputed)]
    return []


# --- check: the machine report ----------------------------------------------

def verification_errors(record, index):
    if not isinstance(record, dict):
        return ['machine report verification[{}] must be an object'.format(index)]
    kind = record.get('kind')
    if kind == 'command':
        allowed = {'kind', 'command', 'result'}
        errors = []
        for key in ('command', 'result'):
            value = record.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append('machine report verification[{}].{} must be a non-empty '
                              'string'.format(index, key))
    elif kind == 'evidence':
        allowed = {'kind', 'evidence'}
        errors = []
        value = record.get('evidence')
        if not isinstance(value, str) or not value.strip():
            errors.append('machine report verification[{}].evidence must be a non-empty '
                          'string'.format(index))
    else:
        return ['machine report verification[{}].kind must be command or evidence, '
                'got {!r}'.format(index, kind)]
    unexpected = sorted(set(record) - allowed)
    if unexpected:
        errors.append('machine report verification[{}] has unexpected key(s): {}'.format(
            index, ', '.join(unexpected)))
    return errors


def validate_finding(item, index):
    """(finding, errors): a normalised finding, or None and why not."""
    if not isinstance(item, dict):
        return None, ['machine report findings[{}] must be an object'.format(index)]
    errors = []
    fid = item.get('id')
    if not isinstance(fid, str) or not FINDING_ID.match(fid):
        errors.append('machine report findings[{}].id must be DR-NNN, got {!r}'.format(index, fid))
    for key, allowed in (('kind', KINDS), ('scope', SCOPES), ('priority', PRIORITIES)):
        value = item.get(key)
        if value not in allowed:
            errors.append('machine report findings[{}].{} must be one of {}, got {!r}'.format(
                index, key, '/'.join(allowed), value))
    path = item.get('file')
    normalised = normalise_rel(path)
    if normalised is None:
        errors.append('machine report findings[{}].file must be a safe repo-relative path, '
                      'got {!r}'.format(index, path))
    line = item.get('line')
    if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
        errors.append('machine report findings[{}].line must be a positive integer or null, '
                      'got {!r}'.format(index, line))
    for key in ('evidence', 'recommendation'):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append('machine report findings[{}].{} must be a non-empty string'.format(
                index, key))
    convention_ref = None
    if 'convention_ref' in item:
        convention_ref = normalise_rel(item['convention_ref'])
        if convention_ref is None:
            errors.append('machine report findings[{}].convention_ref must be a safe '
                          'repo-relative path, got {!r}'.format(index, item['convention_ref']))
    if errors:
        return None, errors
    finding = {'id': fid, 'kind': item['kind'], 'scope': item['scope'],
               'priority': item['priority'], 'file': normalised,
               'evidence': item['evidence'], 'recommendation': item['recommendation']}
    if line is not None:
        finding['line'] = line
    if convention_ref is not None:
        finding['convention_ref'] = convention_ref
    return finding, []


def validate_machine_report(report, manifest):
    result = {'parsed': False, 'errors': [], 'findings': [], 'completeness': None,
              'gaps': [], 'verification': 0,
              'counts': {priority: 0 for priority in PRIORITIES}}
    if not isinstance(report, dict):
        result['errors'].append('the machine report must be a JSON object')
        return result
    errors = result['errors']
    schema = report.get('schema')
    if schema != SCHEMA or isinstance(schema, bool):
        errors.append('machine report schema must be 1, got {!r}'.format(schema))
    for key in ('ticket', 'run_id', 'base', 'head'):
        value = report.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append('machine report {} must be a non-empty string'.format(key))
        elif value != manifest[key]:
            errors.append('machine report {} {!r} does not match the target {!r}'.format(
                key, value, manifest[key]))
    completeness = report.get('completeness')
    if completeness not in ('complete', 'partial'):
        errors.append('machine report completeness must be complete or partial, got {!r}'.format(
            completeness))
    else:
        result['completeness'] = completeness
    gaps = report.get('gaps')
    if not isinstance(gaps, list):
        errors.append('machine report gaps must be a list')
        gaps = []
    else:
        for index, gap in enumerate(gaps):
            if not isinstance(gap, str) or not gap.strip():
                errors.append('machine report gaps[{}] must be a non-empty string'.format(index))
        if completeness == 'partial' and not gaps:
            errors.append('a partial machine report must list its gaps')
        if completeness == 'complete' and gaps:
            errors.append('a complete machine report must not list gaps')
    result['gaps'] = [gap for gap in gaps if isinstance(gap, str)]
    verification = report.get('verification')
    if not isinstance(verification, list):
        errors.append('machine report verification must be a list of records')
    elif not verification:
        errors.append('machine report verification must record at least one command or piece '
                      'of evidence')
    else:
        for index, record in enumerate(verification):
            errors.extend(verification_errors(record, index))
        result['verification'] = len(verification)
    findings = report.get('findings')
    if not isinstance(findings, list):
        errors.append('machine report findings must be a list')
        return result
    result['parsed'] = True
    seen = set()
    for index, item in enumerate(findings):
        finding, item_errors = validate_finding(item, index)
        errors.extend(item_errors)
        if finding is None:
            continue
        if finding['id'] in seen:
            errors.append('machine report findings[{}].id {!r} is a duplicate'.format(
                index, finding['id']))
            continue
        seen.add(finding['id'])
        result['findings'].append(finding)
    for finding in result['findings']:
        if finding['kind'] == 'defect' and finding['scope'] == 'diff':
            result['counts'][finding['priority']] += 1
    return result


# --- check: anchors against the pinned diff ---------------------------------

def anchor_errors(root, manifest, findings):
    if not findings:
        return []
    diff_text = git(root, 'diff', '--no-color', '--no-ext-diff', '--ignore-submodules=none',
                    manifest['merge_base'], manifest['head'])
    hunks = validate_findings.parse_diff(diff_text)
    changed = changed_paths(root, manifest['merge_base'], manifest['head'])
    errors = []
    for finding in findings:
        fid, path, scope = finding['id'], finding['file'], finding['scope']
        line = finding.get('line')
        if scope == 'diff' and line is None:
            if path not in changed:
                errors.append('{}: {} [diff] is not in the reviewed diff '
                              '(merge_base..head)'.format(fid, path))
            continue
        if scope == 'repository' and line is None:
            if blob_line_count(root, manifest['head'], path) is None:
                errors.append('{}: {} [repository] does not exist at the reviewed head'.format(
                    fid, path))
            continue
        try:
            check = validate_findings.check_findings([finding], hunks)[0]
        except validate_findings.InputError as exc:
            errors.append('{}: {}'.format(fid, exc))
            continue
        if not check.ok:
            errors.append('{}: {} — {}'.format(fid, check.label, check.reason))
            continue
        if scope == 'repository':
            line_count = blob_line_count(root, manifest['head'], path)
            if line_count is None:
                errors.append('{}: {} [repository] does not exist at the reviewed head'.format(
                    fid, path))
            elif line > line_count:
                errors.append('{}: {}:{} [repository] is past the file end ({} lines at the '
                              'reviewed head)'.format(fid, path, line, line_count))
    return errors


def convention_errors(root, manifest, findings):
    """A finding's convention_ref names a file that exists at the reviewed head."""
    errors = []
    for finding in findings:
        ref = finding.get('convention_ref')
        if ref is None:
            continue
        if blob_line_count(root, manifest['head'], ref) is None:
            errors.append('{}: convention_ref {} does not exist at the reviewed head'.format(
                finding['id'], ref))
    return errors


# --- check: the Markdown report ---------------------------------------------

def unfenced_lines(text):
    """[(line number, line)] outside fenced code blocks; fences are ignored wholesale."""
    lines = []
    fence = None
    for number, raw in enumerate(text.split('\n'), start=1):
        match = FENCE.match(raw)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
                continue
            lines.append((number, raw))
            continue
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1]:
            fence = None
    return lines


def sections_of(lines):
    """[(name, start index, end index)] for every `## ` heading."""
    headings = []
    for index, (_number, text) in enumerate(lines):
        match = SECTION.match(text)
        if match:
            headings.append((index, match.group(1)))
    found = []
    for position, (index, name) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        found.append((name, index, end))
    return found


SECTION_TITLES = (
    ('critical', ('Critical Issues', 'Critical Issues (must fix)')),
    ('warning', ('Warnings', 'Warnings (should fix)')),
    ('suggestion', ('Suggestions', 'Suggestions (consider improving)')),
    ('repository', ('Pre-existing issues', 'Pre-existing issues (out of diff)')),
    ('audit', ('Accepted audits',)),
    ('proposed', ('Proposed fixes',)),
)


def section_category(name):
    """The category of a canonical `## ` heading, or None: exact titles only."""
    for category, titles in SECTION_TITLES:
        if name in titles:
            return category
    return None


def parse_task_block(priority, content):
    tasks = []
    errors = []
    none_item = False
    for line in content:
        stripped = line.strip()
        if not stripped:
            continue
        match = TASK.match(line)
        if match:
            tasks.append({'number': int(match.group(1)), 'priority': match.group(2),
                          'behavior': bool(match.group(3)), 'title': match.group(4),
                          'body': []})
            continue
        if NONE_ITEM.match(line):
            none_item = True
            continue
        if NESTED_CHECKBOX.match(line):
            errors.append('### Tasks ({}) has a nested checkbox: {!r}'.format(priority, stripped))
            continue
        if CHECKED.match(line):
            errors.append('### Tasks ({}) has a checked task: {!r}'.format(priority, stripped))
            continue
        if CHECKBOX.match(line):
            errors.append('### Tasks ({}) has a malformed task line: {!r}'.format(
                priority, stripped))
            continue
        if line.startswith('-'):
            errors.append('### Tasks ({}) has an unexpected list item: {!r}'.format(
                priority, stripped))
            continue
        if line[:1].isspace():
            if tasks:
                tasks[-1]['body'].append(line)
            else:
                errors.append('### Tasks ({}) has body text before its first task: {!r}'.format(
                    priority, stripped))
            continue
        errors.append('### Tasks ({}) has unexpected content: {!r}'.format(priority, stripped))
    for task in tasks:
        body = '\n'.join(task['body'])
        if not body.strip():
            errors.append('### Tasks ({}) task {} has no body'.format(priority, task['number']))
        found = FINDING_LINE.findall(body)
        if len(found) != 1:
            errors.append('### Tasks ({}) task {} must carry exactly one Finding: DR-NNN '
                          '(found {})'.format(priority, task['number'], len(found)))
        else:
            task['finding'] = found[0]
            if not FINDING_ID.match(task['finding']):
                errors.append('### Tasks ({}) task {} has a malformed finding id {!r}'.format(
                    priority, task['number'], task['finding']))
        if 'Acceptance criteria' not in body:
            errors.append('### Tasks ({}) task {} has no Acceptance criteria'.format(
                priority, task['number']))
    return tasks, none_item, errors


def task_mapping_errors(findings, tasks):
    errors = []
    actionable = {item['id']: item['priority'] for item in findings
                  if item['kind'] == 'defect' and item['scope'] == 'diff'
                  and item['priority'] in ACTIONABLE}
    referenced = {}
    for priority, task in tasks:
        fid = task.get('finding')
        if task['priority'] != priority:
            errors.append('### Tasks ({}) task {} says ({}) in its title, but its block is '
                          '({})'.format(priority, task['number'], task['priority'], priority))
        if fid is None:
            continue
        if fid not in actionable:
            errors.append('### Tasks ({}) task {} references {}, which is not an actionable '
                          'diff defect'.format(priority, task['number'], fid))
            continue
        if actionable[fid] != priority:
            errors.append('### Tasks ({}) task {} references {} ({}), a different '
                          'priority'.format(priority, task['number'], fid, actionable[fid]))
            continue
        if task['priority'] != actionable[fid]:
            errors.append('### Tasks ({}) task {} says ({}) in its title, but {} is '
                          '({})'.format(priority, task['number'], task['priority'], fid,
                                        actionable[fid]))
            continue
        if fid in referenced:
            errors.append('{} is referenced by two tasks ({} and {})'.format(
                fid, referenced[fid], priority))
            continue
        referenced[fid] = priority
    for fid, priority in actionable.items():
        if fid not in referenced:
            errors.append('actionable finding {} ({}) has no task in ### Tasks ({})'.format(
                fid, priority, priority))
    return errors


def header_errors(text, manifest):
    """The document header block: type, ticket, produced_by, no duplicates."""
    block, _body = doc_header.split(text)
    if block is None:
        return ['report has no document header block (--- ... ---)']
    errors = []
    keys = []
    for line in block.split('\n'):
        match = HEADER_FIELD.match(line)
        if match:
            keys.append(match.group(1))
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        errors.append('report header repeats field(s): {}'.format(', '.join(repeated)))
    fields = doc_header.fields(text) or {}
    for key, want in (('type', 'deep-review'), ('ticket', manifest['ticket']),
                      ('produced_by', 'artel:reviewer')):
        got = fields.get(key)
        if got is None:
            errors.append('report header is missing {!r}; expected {}: {}'.format(key, key, want))
        elif got != want:
            errors.append('report header {} is {!r}, expected {!r}'.format(key, got, want))
    return errors


def metadata_errors(lines, manifest, machine):
    """The four body metadata lines: present once each and matching the target."""
    errors = []
    meta = {}
    for label in META_LABELS:
        pattern = re.compile(r'^\*\*' + re.escape(label) + r':\*\*\s*(.*?)\s*$')
        values = []
        for _number, line in lines:
            match = pattern.match(line)
            if match:
                values.append(match.group(1))
        if not values:
            errors.append('report is missing the **{}:** line'.format(label))
        elif len(values) > 1:
            errors.append('report has {} **{}:** lines'.format(len(values), label))
        else:
            meta[label] = values[0]
    if 'Review run' in meta and meta['Review run'] != manifest['run_id']:
        errors.append('report **Review run:** {} does not match the target run {}'.format(
            meta['Review run'], manifest['run_id']))
    for label, key in (('Reviewed base', 'base'), ('Reviewed head', 'head')):
        if label in meta and meta[label] != manifest[key]:
            errors.append('report **{}:** {} does not match the target {} {}'.format(
                label, meta[label], key, manifest[key]))
    if 'Review completeness' in meta:
        value = meta['Review completeness']
        if value not in ('complete', 'partial'):
            errors.append('report **Review completeness:** must be complete or partial, '
                          'got {!r}'.format(value))
        elif machine['completeness'] is not None and value != machine['completeness']:
            errors.append('report **Review completeness:** is {}, the machine report says '
                          '{}'.format(value, machine['completeness']))
    return errors


def task_block_errors(lines, start, end, machine):
    """(errors, block ranges, tasks) for the two `### Tasks` blocks under Proposed fixes."""
    errors = []
    subsections = []
    for index in range(start + 1, end):
        match = SUBSECTION.match(lines[index][1])
        if match:
            subsections.append((index, match.group(1)))
    blocks = {}
    for position, (index, heading) in enumerate(subsections):
        stop = subsections[position + 1][0] if position + 1 < len(subsections) else end
        if not heading.startswith('Tasks'):
            errors.append('unexpected heading in ## Proposed fixes: ### {}'.format(heading))
            continue
        match = TASK_HEADING.match(heading)
        if not match:
            errors.append('malformed task block heading: ### {}'.format(heading))
            continue
        priority = match.group(1)
        if priority in blocks:
            errors.append('report has more than one ### Tasks ({}) block'.format(priority))
            continue
        blocks[priority] = (index, stop)
    for priority in ACTIONABLE:
        if priority not in blocks:
            errors.append('report is missing the ### Tasks ({}) block'.format(priority))
    tasks = []
    for priority, (first, stop) in blocks.items():
        content = [lines[index][1] for index in range(first + 1, stop)]
        block_tasks, none_item, block_errors = parse_task_block(priority, content)
        errors.extend(block_errors)
        expected = sum(1 for item in machine['findings']
                       if item['kind'] == 'defect' and item['scope'] == 'diff'
                       and item['priority'] == priority)
        if expected == 0:
            if block_tasks:
                errors.append('### Tasks ({}) must read "- none" but holds {} '
                              'task(s)'.format(priority, len(block_tasks)))
            elif not none_item:
                errors.append('### Tasks ({}) is empty; an empty block reads '
                              '"- none"'.format(priority))
        else:
            if none_item:
                errors.append('### Tasks ({}) reads "- none" but {} task(s) are '
                              'required'.format(priority, expected))
            if len(block_tasks) != expected:
                errors.append('### Tasks ({}) holds {} task(s), expected {}'.format(
                    priority, len(block_tasks), expected))
            numbers = [task['number'] for task in block_tasks]
            if numbers != list(range(1, len(block_tasks) + 1)):
                errors.append('### Tasks ({}) task numbers must run 1..{}, got {}'.format(
                    priority, len(block_tasks), numbers))
        tasks.extend((priority, task) for task in block_tasks)
    return errors, list(blocks.values()), tasks


def stray_checkbox_errors(lines, block_ranges):
    """Any unfenced checkbox outside the two `### Tasks` blocks is an extra task."""
    errors = []
    for index, (_number, line) in enumerate(lines):
        if any(first < index < stop for first, stop in block_ranges):
            continue
        if CHECKBOX.match(line) or NESTED_CHECKBOX.match(line):
            errors.append('task checkbox outside a ### Tasks block: {!r}'.format(line.strip()))
    return errors


def check_markdown(text, manifest, machine):
    """(errors, task counts per block or None)."""
    lines = unfenced_lines(text)
    errors = []
    errors.extend(header_errors(text, manifest))
    errors.extend(metadata_errors(lines, manifest, machine))

    found = sections_of(lines)
    by_category = {}
    for name, start, end in found:
        by_category.setdefault(section_category(name), []).append((name, start, end))
    for category, entries in by_category.items():
        if category is not None and len(entries) > 1:
            errors.append('report has {} "{}" sections'.format(len(entries), entries[0][0]))

    block_ranges = []
    tasks = []
    task_counts = None
    proposed = by_category.get('proposed') or []
    if not proposed:
        errors.append('report is missing the ## Proposed fixes section')
    elif len(proposed) == 1:
        _name, start, end = proposed[0]
        if found and found[-1][1] != start:
            errors.append('## Proposed fixes must close the report')
        block_errors, block_ranges, tasks = task_block_errors(lines, start, end, machine)
        errors.extend(block_errors)
        task_counts = {priority: 0 for priority in ACTIONABLE}
        for priority, _task in tasks:
            task_counts[priority] += 1

    errors.extend(stray_checkbox_errors(lines, block_ranges))
    errors.extend(task_mapping_errors(machine['findings'], tasks))

    errors.extend(finding_heading_errors(lines, by_category, machine))
    return errors, task_counts


def finding_heading_errors(lines, by_category, machine):
    """Every machine finding's `### DR-NNN (...)` heading, once, in its section."""
    errors = []
    heading_map = {}
    for category, entries in by_category.items():
        for name, start, end in entries:
            if category == 'proposed':
                continue
            for index in range(start + 1, end):
                match = SUBSECTION.match(lines[index][1])
                if not match:
                    continue
                heading = match.group(1)
                if heading.startswith('Tasks'):
                    errors.append('### {} appears outside ## Proposed fixes'.format(heading))
                    continue
                finding_match = FINDING_HEADING.match(heading)
                if not finding_match:
                    if 'DR-' in heading:
                        errors.append('malformed finding heading: ### {}'.format(heading))
                    continue
                fid = 'DR-' + finding_match.group(1)
                if category is None:
                    errors.append('finding heading {} sits in an unrecognized section: '
                                  '## {}'.format(fid, name))
                    continue
                if fid in heading_map:
                    errors.append('finding heading {} appears more than once'.format(fid))
                    continue
                heading_map[fid] = (category, finding_match.group(2), finding_match.group(3))
    machine_ids = {item['id'] for item in machine['findings']}
    for item in machine['findings']:
        fid = item['id']
        if item['kind'] == 'audit':
            want_category, want_paren = 'audit', 'audit'
        elif item['scope'] == 'repository':
            want_category, want_paren = 'repository', item['priority']
        else:
            want_category, want_paren = PRIORITY_CATEGORY[item['priority']], item['priority']
        entry = heading_map.get(fid)
        if entry is None:
            errors.append('finding {} ({}, {}) has no heading in the report'.format(
                fid, item['kind'], item['scope']))
            continue
        category, paren, _title = entry
        if category != want_category:
            errors.append('finding heading {} is in the {} section, expected the {} '
                          'section'.format(fid, category, want_category))
        if paren != want_paren:
            errors.append('finding heading {} says ({}), expected ({})'.format(
                fid, paren, want_paren))
    for fid in heading_map:
        if fid not in machine_ids:
            errors.append('report heading {} has no machine finding'.format(fid))
    return errors


def check_summary(line, root, machine, task_counts, expected):
    match = SUMMARY.match(line.strip()) if isinstance(line, str) else None
    if not match:
        return ['summary line does not match "Deep review: <c> Critical, <w> Warning, '
                '<s> Suggestion — <path>"']
    errors = []
    critical, warning, suggestion = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if machine['parsed']:
        for label, got in (('Critical', critical), ('Warning', warning),
                           ('Suggestion', suggestion)):
            want = machine['counts'][label]
            if got != want:
                errors.append('summary says {} {} but the validated diff defects are {}'.format(
                    got, label, want))
    if task_counts is not None:
        if critical != task_counts['Critical'] or warning != task_counts['Warning']:
            errors.append('summary counts ({} Critical, {} Warning) do not match the copied '
                          'task blocks ({} Critical, {} Warning)'.format(
                              critical, warning, task_counts['Critical'],
                              task_counts['Warning']))
    named = normalise_report_path(root, match.group(4))
    if not same_path(named, expected):
        errors.append('summary names {!r} but the expected report path is {!r}'.format(
            match.group(4), display_path(root, expected)))
    return errors


def build_data(manifest, machine, task_counts, expected, root):
    findings = machine['findings']
    return {
        'schema': SCHEMA,
        'ticket': manifest['ticket'],
        'run_id': manifest['run_id'],
        'branch': manifest['branch'],
        'base': manifest['base'],
        'head': manifest['head'],
        'merge_base': manifest['merge_base'],
        'completeness': machine['completeness'],
        'gaps': machine['gaps'],
        'counts': machine['counts'],
        'tasks': task_counts if task_counts is not None else {'Critical': None, 'Warning': None},
        'findings': {
            'by_kind': {
                'defect': sum(1 for item in findings if item['kind'] == 'defect'),
                'audit': sum(1 for item in findings if item['kind'] == 'audit'),
            },
            'by_scope': {
                'diff': sum(1 for item in findings if item['scope'] == 'diff'),
                'repository': sum(1 for item in findings if item['scope'] == 'repository'),
            },
        },
        'verification': machine['verification'],
        'dangerous': manifest['dangerous'],
        'report': display_path(root, expected),
    }


def emit(envelope, code):
    print(json.dumps(envelope, ensure_ascii=False))
    return code


def cmd_check(args):
    try:
        root = repo_root()
        manifest = read_manifest(args.target)
        specs = safe_specs_dir(root, manifest['specs_dir'], 'the target manifest specs_dir')
        resolve_target(root, manifest)
        machine_raw = read_json(args.findings, 'machine report')
        report_text = read_report(args.report)
        expected = expected_report_path(root, manifest, args)
        if not inside_root(root, expected):
            raise InputError('the expected report path {} is outside the checkout'.format(
                display_path(root, expected)))
    except InputError as exc:
        print('deep_review.py: {}'.format(exc), file=sys.stderr)
        return emit({'ok': False, 'errors': [str(exc)]}, 2)
    errors = []
    try:
        errors.extend(checkout_errors(root, manifest, specs))
        errors.extend(merge_base_errors(root, manifest))
        machine = validate_machine_report(machine_raw, manifest)
        errors.extend(machine['errors'])
        errors.extend(anchor_errors(root, manifest, machine['findings']))
        errors.extend(convention_errors(root, manifest, machine['findings']))
        markdown_errors, task_counts = check_markdown(report_text, manifest, machine)
        errors.extend(markdown_errors)
        errors.extend(check_summary(args.summary, root, machine, task_counts, expected))
        if machine['completeness'] == 'partial':
            errors.append('the review is partial: {}'.format(
                '; '.join(machine['gaps']) if machine['gaps'] else 'no gaps recorded'))
    except InputError as exc:
        print('deep_review.py: {}'.format(exc), file=sys.stderr)
        return emit({'ok': False, 'errors': [str(exc)]}, 2)
    envelope = {'ok': not errors}
    if machine['parsed']:
        envelope['data'] = build_data(manifest, machine, task_counts, expected, root)
    if errors:
        envelope['errors'] = errors
        return emit(envelope, 1)
    return emit(envelope, 0)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='deep_review.py',
        description='Pin a deep review target and validate the reviewer artifacts '
                    'against it.')
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare', help='pin the review target to a manifest')
    prepare.add_argument('--ticket', required=True, help='the ticket key under review')
    prepare.add_argument('--specs-dir', required=True,
                         help='the configured specs dir, repo-relative')
    prepare.add_argument('--branch', help='the branch under review (must be checked out)')
    prepare.add_argument('--base', help='the base revision (default: origin/HEAD fallback)')
    prepare.add_argument('--output', required=True, help='manifest file to write')
    check = sub.add_parser('check', help='validate the reviewer artifacts against a manifest')
    check.add_argument('--target', required=True, help='the manifest from prepare')
    check.add_argument('--findings', required=True, help='the machine report (schema 1)')
    check.add_argument('--report', required=True, help='the Markdown report, or - for stdin')
    check.add_argument('--summary', required=True, help='the reviewer completion line')
    check.add_argument('--report-path',
                       help='the expected report path when --report is stdin')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            return cmd_prepare(args)
        return cmd_check(args)
    except InputError as exc:
        print('deep_review.py: {}'.format(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
