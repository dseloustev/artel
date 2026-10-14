#!/usr/bin/env python3
"""Validated PR snapshot inventory and the collaborative review's Markdown state.

The collaborative review binds its analysis to one recorded snapshot of a pull
request. `inventory` turns the inputs the orchestrator already captured -- the
`gh pr view --json url,baseRefOid,headRefOid,changedFiles` metadata, the
array-of-arrays `gh api --paginate --slurp` pages of the PR's changed files, and
the merge-base commit -- into one artifact:

    inventory --metadata <json-file> --pages <json-file> --merge-base <sha>
              --output <json-file>

The artifact carries `pr_url`, `base_sha`, `head_sha`, `merge_base_sha`,
`changed_files`, `inventory_digest`, the normalized `files` (each with `path`,
`status`, `previous_path`, `additions`, `deletions`, `blob_sha` and `diff_url`),
and the original `pages` verbatim, for patches and other API fields that
normalization does not repeat. `inventory_digest` is the SHA-256 of the canonical
JSON (`sort_keys=True`, compact separators, UTF-8) of the files sorted by path,
so GitHub's paging order alone cannot change it.

That snapshot becomes a Markdown checklist:

    render --snapshot <file> --preparation <file> --output <file>
    status --checklist <file>
    advance --checklist <file> --snapshot <fresh-file> --entry <N> --response <text>

`render` partitions the inventory into the worker's ordered review sequence and
its exclusions -- every inventory path must be claimed exactly once -- and writes
the ledger. The header comment records the snapshot identity, and only the
`## Files` section holds approval rows; row IDs are the positions 1..M of the
ordered entries. `status` reports checked/total/current, the current file being
the first unchecked row. `advance` records exactly one approval: the message
`reviewed` (surrounding whitespace and case ignored), for the current entry, while
the checklist still matches the fresh snapshot's identity. Anything else changes
nothing -- the old numeric `1` included; a stale snapshot, a wrong or
already-checked entry, and a malformed ledger are refused outright.

Validation is complete before anything is written: the metadata's declared
`changedFiles` must equal the number of returned rows, paths must be unique, and
metadata, revisions, PR URL, rows and counts must be well formed; preparation
must claim every inventory path exactly once; and the checklist must parse before
a row is checked. The script never runs a subprocess and never touches the
network; its only writes are the artifacts it is asked for -- the `--output`
snapshot or checklist, and the advanced `--checklist`.

stdout is one JSON envelope: `ok`, `verb`, `elapsed_ms`, and either `data` -- a
compact summary (output path, PR/revision identity, counts, digest, or checklist
status) so file contents never have to enter the caller's context -- or
`error: {kind, message}`. Exit 0 when answered; 2 for an invalid argument,
invalid input data, or an I/O failure.

Beyond the CLI, the module exposes the narrow pure helpers the skill's recipes
call through `runpy`: `capture_sides` (which side path a capture reads per
file), `contents_endpoint` (one percent-encoded, shell-quoted contents request at
a pinned ref), `validate_context_request` (a worker's bounded supporting-context
request) and `record_report`/`record_report_file` (a report's clickable link,
assessment and unresolved findings persisted in the ledger's discussion
section).
"""
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

VERB = 'inventory'
VERBS = ('inventory', 'render', 'status', 'advance')
CONFIRMATION_WORD = 'reviewed'
PR_URL_RE = re.compile(r'^https://github\.com/[^/\s]+/[^/\s]+/pull/([1-9][0-9]*)$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
SNAPSHOT_HEADER_RE = re.compile(r'^<!--\s*collaborative-review-snapshot:\s*(\{.*\})\s*-->$')
FILE_ROW_RE = re.compile(r'^- \[([ xX])\] ([0-9]+)\. ')
ORDERED_COUNT_RE = re.compile(r'^Ordered files: ([0-9]+) \| Excluded: ([0-9]+)$')
FENCE_RE = re.compile(r'^\s*(?:```+|~~~+)')
IDENTITY_KEYS = ('pr_url', 'base_sha', 'head_sha', 'merge_base_sha', 'inventory_digest')
CONTEXT_SIDES = ('base', 'head', 'both')
CONTEXT_REQUEST_FIELDS = ('path', 'sides', 'reason')
CONTEXT_REQUEST_LIMIT = 8
ASSESSMENTS = ('No issues found', 'Needs edits', 'Cannot verify')
USAGE = ('usage: collaborative_review.py inventory --metadata <json-file> '
         '--pages <json-file> --merge-base <sha> --output <json-file>\n'
         '       collaborative_review.py render --snapshot <file> '
         '--preparation <file> --output <file>\n'
         '       collaborative_review.py status --checklist <file>\n'
         '       collaborative_review.py advance --checklist <file> '
         '--snapshot <fresh-file> --entry <n> --response <text>')


class SnapshotError(ValueError):
    """Input that cannot become a review snapshot (metadata, pages, revisions)."""


class ChecklistError(ValueError):
    """Checklist state that cannot be rendered, parsed, or advanced safely."""


class UsageError(ValueError):
    """The command line itself is wrong."""


class ContextRequestError(ValueError):
    """A worker's supporting-context request that cannot be safely acquired."""


def pr_url_base(pr_url):
    """The canonical `.../pull/<n>` prefix of a GitHub PR URL, or SnapshotError."""
    if not isinstance(pr_url, str):
        raise SnapshotError('PR URL must be a string')
    candidate = pr_url.rstrip('/')
    if not PR_URL_RE.match(candidate):
        raise SnapshotError('not a GitHub pull request URL: {!r}'.format(pr_url))
    return candidate


def diff_url(pr_url: str, path: str) -> str:
    """The direct GitHub diff link of `path` in `pr_url`.

    GitHub's per-file anchor is SHA-256 over the exact UTF-8 path, so unusual and
    non-ASCII filenames need no special casing. The link carries no line fragment.
    """
    base = pr_url_base(pr_url)
    if not isinstance(path, str) or not path:
        raise SnapshotError('file path must be a non-empty string')
    digest = hashlib.sha256(path.encode('utf-8')).hexdigest()
    return '{}/files#diff-{}'.format(base, digest)


def capture_sides(file_entry: dict) -> dict:
    """The base and head side paths a capture reads for one snapshot file entry.

    The head side is `path`; the base side is `path` too, except that a renamed
    file's merge-base content lives at `previous_path` and an added file has no
    base side. `None` marks a side that does not exist. A renamed entry without a
    recorded `previous_path` cannot be captured and is refused.
    """
    if not isinstance(file_entry, dict):
        raise SnapshotError('file entry must be an object')
    path = file_entry.get('path')
    if not isinstance(path, str) or not path:
        raise SnapshotError('file entry has no path')
    status = file_entry.get('status')
    if not isinstance(status, str) or not status:
        raise SnapshotError('file entry {} has no status'.format(path))
    previous = file_entry.get('previous_path')
    if status == 'added':
        base = None
    elif status == 'renamed':
        if not isinstance(previous, str) or not previous:
            raise SnapshotError(
                'renamed file {} has no previous_path for its base side'.format(path))
        base = previous
    else:
        base = path
    return {'base': base, 'head': None if status == 'removed' else path}


def contents_endpoint(repo: str, path: str, ref: str) -> str:
    """The shell-quoted contents endpoint for one exact `path` at pinned `ref`.

    The path is percent-encoded segment by segment (`/` stays the separator), so
    `#`, `?`, `%`, spaces and non-ASCII names cannot become a fragment, a query
    or a decoded escape, and the raw path is never rewritten. The 40-hex `ref` is
    appended as its own `?ref=` query after the encoded path. The returned value
    is one complete shell word: place it in a command exactly as returned.
    """
    if not isinstance(repo, str) or not repo:
        raise SnapshotError('repository must be a non-empty owner/repo string')
    if not isinstance(path, str) or not path:
        raise SnapshotError('file path must be a non-empty string')
    pinned = _sha(ref, 'contents ref')
    encoded = urllib.parse.quote(path, safe='/')
    return shlex.quote('repos/{}/contents/{}?ref={}'.format(repo, encoded, pinned))


def _context_path(path, index):
    """Refuse a context-request path that is not one exact repo-relative file."""
    if not isinstance(path, str) or not path:
        raise ContextRequestError('request entry {} has no path'.format(index))
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise ContextRequestError(
            'request entry {} path contains a control character'.format(index))
    if path.startswith('/') or '\\' in path:
        raise ContextRequestError(
            'request entry {} path must be repo-relative without backslashes'.format(index))
    if any(segment in ('', '.', '..') for segment in path.split('/')):
        raise ContextRequestError(
            'request entry {} path has an empty, `.` or `..` segment'.format(index))


def validate_context_request(entries) -> list:
    """The validated entries of one worker supporting-context request.

    `entries` is a list of at most eight unique repo-relative exact paths, each
    an object with exactly `path`, `sides` (`base`, `head` or `both`) and a
    one-line `reason`. A path is literal: `?`, `[`, `]` and `*` are ordinary
    filename characters and are never expanded as a pattern. The whole request is
    refused for traversal, absolute paths, backslashes, empty segments, control
    characters, duplicates, an over-limit size, bad sides or reasons, and any
    extra pattern/wildcard-request field.
    """
    if not isinstance(entries, list):
        raise ContextRequestError('a context request must be a list of entries')
    if not entries:
        raise ContextRequestError('a context request must name at least one path')
    if len(entries) > CONTEXT_REQUEST_LIMIT:
        raise ContextRequestError('a context request carries at most {} paths; '
                                  'found {}'.format(CONTEXT_REQUEST_LIMIT, len(entries)))
    validated = []
    seen = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ContextRequestError('request entry {} is not an object'.format(index))
        if set(entry) != set(CONTEXT_REQUEST_FIELDS):
            raise ContextRequestError(
                'request entry {} must carry exactly {}'.format(
                    index, ', '.join(CONTEXT_REQUEST_FIELDS)))
        path = entry['path']
        _context_path(path, index)
        sides = entry['sides']
        if sides not in CONTEXT_SIDES:
            raise ContextRequestError('request entry {} sides must be one of '
                                      '{}'.format(index, ', '.join(CONTEXT_SIDES)))
        reason = entry['reason']
        if not isinstance(reason, str) or not reason.strip():
            raise ContextRequestError('request entry {} has no reason'.format(index))
        if '\n' in reason or '\r' in reason:
            raise ContextRequestError(
                'request entry {} reason must stay on one line'.format(index))
        if path in seen:
            raise ContextRequestError('duplicate context path: {}'.format(path))
        seen.add(path)
        validated.append({'path': path, 'sides': sides, 'reason': reason})
    return validated


def _sha(value, label):
    if not isinstance(value, str) or not SHA_RE.match(value):
        raise SnapshotError('{} must be a 40-character lowercase hex revision'.format(label))
    return value


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotError('{} must be a non-negative integer'.format(label))
    return value


def _normalized_file(row, index, pr_url):
    if not isinstance(row, dict):
        raise SnapshotError('file row {} is not an object'.format(index))
    filename = row.get('filename')
    if not isinstance(filename, str) or not filename:
        raise SnapshotError('file row {} has no filename'.format(index))
    status = row.get('status')
    if not isinstance(status, str) or not status:
        raise SnapshotError('file row {} ({}) has no status'.format(index, filename))
    additions = _count(row.get('additions'), 'file row {} additions'.format(index))
    deletions = _count(row.get('deletions'), 'file row {} deletions'.format(index))
    blob_sha = row.get('sha')
    if not isinstance(blob_sha, str) or not blob_sha:
        raise SnapshotError('file row {} ({}) has no blob sha'.format(index, filename))
    previous = row.get('previous_filename')
    if previous is not None and (not isinstance(previous, str) or not previous):
        raise SnapshotError(
            'file row {} ({}) has an invalid previous filename'.format(index, filename))
    return {'path': filename, 'status': status, 'previous_path': previous,
            'additions': additions, 'deletions': deletions, 'blob_sha': blob_sha,
            'diff_url': diff_url(pr_url, filename)}


def _collect_rows(pages, pr_url):
    if not isinstance(pages, list):
        raise SnapshotError('pages must be a list of page arrays')
    files = []
    seen = set()
    for page_number, page in enumerate(pages, 1):
        if not isinstance(page, list):
            raise SnapshotError('pages[{}] is not an array'.format(page_number))
        for row in page:
            entry = _normalized_file(row, len(files), pr_url)
            if entry['path'] in seen:
                raise SnapshotError('duplicate path in inventory: {}'.format(entry['path']))
            seen.add(entry['path'])
            files.append(entry)
    return files


def _canonical(files):
    """The digest input: compact sorted-keys JSON of the files, UTF-8 encoded."""
    return json.dumps(files, sort_keys=True, separators=(',', ':')).encode('utf-8')


def normalize_snapshot(metadata: dict, pages: list, merge_base_sha: str) -> dict:
    """The validated snapshot of one PR inventory, or SnapshotError.

    `metadata` is `gh pr view --json url,baseRefOid,headRefOid,changedFiles`
    output; `pages` is the `gh api --paginate --slurp` array of file pages. The
    returned dict carries the normalized files sorted by path, the digest over
    them, and the original pages -- see the module docstring for the field list.
    """
    if not isinstance(metadata, dict):
        raise SnapshotError('metadata must be a JSON object')
    pr_url = pr_url_base(metadata.get('url'))
    base_sha = _sha(metadata.get('baseRefOid'), 'metadata baseRefOid')
    head_sha = _sha(metadata.get('headRefOid'), 'metadata headRefOid')
    merge_base = _sha(merge_base_sha, 'merge base')
    declared = _count(metadata.get('changedFiles'), 'metadata changedFiles')
    files = _collect_rows(pages, pr_url)
    if len(files) != declared:
        raise SnapshotError('inventory has {} file(s) but the PR declares '
                            'changedFiles={}'.format(len(files), declared))
    files.sort(key=lambda entry: entry['path'])
    return {'pr_url': pr_url, 'base_sha': base_sha, 'head_sha': head_sha,
            'merge_base_sha': merge_base, 'changed_files': len(files),
            'inventory_digest': hashlib.sha256(_canonical(files)).hexdigest(),
            'files': files, 'pages': pages}


def _read_json(path):
    """Parsed JSON from `path`; OSError for unreadable files, SnapshotError otherwise."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except UnicodeDecodeError as exc:
        raise SnapshotError('{} is not UTF-8 text: {}'.format(path, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SnapshotError('{} is not valid JSON: {}'.format(path, exc))


def _atomic_write(path, text):
    """Write `text` through a temporary sibling, then replace atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp',
                                   dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_snapshot(path, snapshot):
    """The snapshot artifact: pretty canonical JSON, atomically replaced."""
    _atomic_write(path, json.dumps(snapshot, indent=2, sort_keys=True) + '\n')


def _read_text(path):
    """UTF-8 text from `path`; OSError for unreadable files, ChecklistError otherwise."""
    try:
        return Path(path).read_text(encoding='utf-8')
    except UnicodeDecodeError as exc:
        raise ChecklistError('{} is not UTF-8 text: {}'.format(path, exc))


def _identity_from(values, label, error):
    """The five identity fields of `values`, validated, or `error`.

    `label` names the source (for example 'snapshot' or 'checklist header') and
    `error` is the exception class raised for malformed input.
    """
    if not isinstance(values, dict):
        raise error('{} must be a JSON object'.format(label))
    missing = [key for key in IDENTITY_KEYS if key not in values]
    if missing:
        raise error('{} lacks {}'.format(label, ', '.join(missing)))
    try:
        identity = {
            'pr_url': pr_url_base(values['pr_url']),
            'base_sha': _sha(values['base_sha'], '{} base_sha'.format(label)),
            'head_sha': _sha(values['head_sha'], '{} head_sha'.format(label)),
            'merge_base_sha': _sha(values['merge_base_sha'],
                                   '{} merge_base_sha'.format(label)),
        }
    except SnapshotError as exc:
        if error is SnapshotError:
            raise
        raise error(str(exc)) from exc
    digest = values['inventory_digest']
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        raise error('{} inventory_digest must be a 64-character lowercase hex '
                    'digest'.format(label))
    identity['inventory_digest'] = digest
    return identity


def _snapshot_identity(snapshot):
    """The validated identity of a Task 1 snapshot artifact."""
    return _identity_from(snapshot, 'snapshot', SnapshotError)


def _checklist_identity(raw):
    """The validated identity recorded in a checklist's header comment."""
    return _identity_from(raw, 'checklist header', ChecklistError)


def _snapshot_files(snapshot):
    """The snapshot's files by path, or SnapshotError when malformed."""
    if not isinstance(snapshot, dict):
        raise SnapshotError('snapshot must be a JSON object')
    files = snapshot.get('files')
    if not isinstance(files, list):
        raise SnapshotError('snapshot files must be a list')
    by_path = {}
    for index, entry in enumerate(files, 1):
        if not isinstance(entry, dict):
            raise SnapshotError('snapshot file {} is not an object'.format(index))
        path = entry.get('path')
        if not isinstance(path, str) or not path:
            raise SnapshotError('snapshot file {} has no path'.format(index))
        if path in by_path:
            raise SnapshotError('duplicate path in snapshot: {}'.format(path))
        status = entry.get('status')
        if not isinstance(status, str) or not status:
            raise SnapshotError('snapshot file {} ({}) has no status'.format(index, path))
        link = entry.get('diff_url')
        if not isinstance(link, str) or not link:
            raise SnapshotError('snapshot file {} ({}) has no diff_url'.format(index, path))
        by_path[path] = entry
    return by_path


def _display_path(path):
    """A code span around `path`, safe for one Markdown line.

    The fence grows past the longest backtick run so unusual names stay readable,
    and a line break is shown as escaped text instead of splitting the row. The
    path itself is never rewritten: hashing and identity live in the snapshot.
    """
    text = path.replace('\r', '\\r').replace('\n', '\\n')
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * (longest + 1)
    return '{}{}{}'.format(fence, text, fence)


def _one_line(value, label):
    """Refuse worker text that would break the row it is rendered into."""
    if '\n' in value or '\r' in value:
        raise ChecklistError('{} must stay on one line'.format(label))


def _preparation_entry(entry, section, index, required):
    """One validated preparation entry; `required` names its prose fields."""
    if not isinstance(entry, dict):
        raise ChecklistError('{}[{}] is not an object'.format(section, index))
    path = entry.get('path')
    if not isinstance(path, str) or not path:
        raise ChecklistError('{}[{}] has no path'.format(section, index))
    item = {'path': path}
    for key in required:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ChecklistError('{}[{}] ({}) has no {}'.format(section, index, path, key))
        _one_line(value, '{}[{}] {}'.format(section, index, key))
        item[key] = value
    note = entry.get('ordering_note')
    if note is not None and not isinstance(note, str):
        raise ChecklistError('{}[{}] ({}) has an invalid ordering_note'.format(
            section, index, path))
    if note and note.strip():
        _one_line(note, '{}[{}] ordering_note'.format(section, index))
        item['ordering_note'] = note
    return item


def _claim_path(path, label, files, seen):
    """Claim `path` for `label`, refusing duplicates and unknown paths."""
    if path in seen:
        raise ChecklistError('duplicate preparation path {}: {} and {}'.format(
            path, seen[path], label))
    if path not in files:
        raise ChecklistError('{} names a path outside the snapshot: {}'.format(label, path))
    seen[path] = label


def _partition_preparation(preparation, files):
    """The validated (ordered, excluded) preparation entries, or ChecklistError.

    Every snapshot path must be claimed exactly once across the two lists.
    """
    if not isinstance(preparation, dict):
        raise ChecklistError('preparation must be a JSON object')
    if not isinstance(preparation.get('ordered'), list) or \
            not isinstance(preparation.get('excluded'), list):
        raise ChecklistError('preparation must carry ordered and excluded arrays')
    seen = {}
    ordered = []
    for index, entry in enumerate(preparation['ordered'], 1):
        item = _preparation_entry(entry, 'ordered', index, ('role', 'focus'))
        _claim_path(item['path'], 'ordered[{}]'.format(index), files, seen)
        ordered.append(item)
    excluded = []
    for index, entry in enumerate(preparation['excluded'], 1):
        item = _preparation_entry(entry, 'excluded', index, ('reason',))
        _claim_path(item['path'], 'excluded[{}]'.format(index), files, seen)
        excluded.append(item)
    missing = [path for path in files if path not in seen]
    if missing:
        raise ChecklistError('preparation omits {} inventory path(s): {}'.format(
            len(missing), ', '.join(missing)))
    return ordered, excluded


def _header_comment(identity):
    """The identity as one machine-readable JSON HTML comment payload."""
    text = json.dumps(identity, sort_keys=True, separators=(',', ':'))
    # a `-->` inside the JSON would end the HTML comment early
    return text.replace('-->', '--\\u003e')


def _file_row(position, file_entry, preparation_entry):
    """One `## Files` approval row: checkbox, position, path, facts, direct link."""
    note = preparation_entry.get('ordering_note')
    note = ' ({})'.format(note) if note else ''
    return ('- [ ] {}. {} — {}; {}; {}{}. [PR diff]({})'.format(
        position, _display_path(file_entry['path']), file_entry['status'].capitalize(),
        preparation_entry['role'], preparation_entry['focus'], note,
        file_entry['diff_url']))


def render_checklist(snapshot: dict, preparation: dict) -> str:
    """The Markdown checklist for one snapshot and a validated preparation.

    `preparation` partitions the snapshot's files into `ordered` entries (with
    `role`, `focus`, and an optional `ordering_note`) and `excluded` entries (with
    `reason`); every inventory path must occur exactly once. Row IDs are the
    positions 1..M of `ordered` -- any worker-supplied IDs are ignored.
    """
    identity = _snapshot_identity(snapshot)
    files = _snapshot_files(snapshot)
    ordered, excluded = _partition_preparation(preparation, files)
    lines = [
        '# Collaborative review checklist',
        '',
        '<!-- collaborative-review-snapshot: {} -->'.format(_header_comment(identity)),
        '',
        'PR: {}'.format(identity['pr_url']),
        'Base: `{}` | Head: `{}` | Merge base: `{}`'.format(
            identity['base_sha'], identity['head_sha'], identity['merge_base_sha']),
        'Inventory digest: `{}`'.format(identity['inventory_digest']),
        'Ordered files: {} | Excluded: {}'.format(len(ordered), len(excluded)),
        '',
        '## Files',
        '',
    ]
    if ordered:
        for position, entry in enumerate(ordered, 1):
            lines.append(_file_row(position, files[entry['path']], entry))
    else:
        lines.append('_None._')
    lines += ['', '## Excluded', '']
    if excluded:
        for entry in excluded:
            lines.append('- {} — {}'.format(_display_path(entry['path']), entry['reason']))
    else:
        lines.append('_None._')
    lines += ['', '## Discussion and findings', '', '_None yet._']
    return '\n'.join(lines) + '\n'


def _parse_checklist(text):
    """The (identity, rows) of a checklist, or ChecklistError when malformed.

    `rows` are `{id, checked, line}` dicts in appearance order. Only rows inside
    the single `## Files` section count, and fenced example rows never do. The
    rendered `Ordered files: N` count of the preamble is part of the ledger's
    validity: exactly one count line must be present and the parsed rows must
    number N, so a shortened or malformed checklist cannot pass as progress.
    """
    if not isinstance(text, str):
        raise ChecklistError('checklist text must be a string')
    lines = text.split('\n')
    identity = None
    ordered_declared = None
    files_sections = 0
    rows = []
    section = None
    fenced = False
    for number, line in enumerate(lines):
        stripped = line.strip()
        if fenced:
            if FENCE_RE.match(stripped):
                fenced = False
            continue
        match = SNAPSHOT_HEADER_RE.match(line)
        if match:
            if identity is not None:
                raise ChecklistError('checklist has more than one snapshot header')
            try:
                raw = json.loads(match.group(1))
            except ValueError as exc:
                raise ChecklistError(
                    'checklist snapshot header is not valid JSON: {}'.format(exc))
            identity = _checklist_identity(raw)
            continue
        if FENCE_RE.match(stripped):
            fenced = True
            continue
        if stripped.startswith('## '):
            section = stripped[3:].strip()
            if section == 'Files':
                files_sections += 1
            continue
        if section is None:
            match = ORDERED_COUNT_RE.match(stripped)
            if match:
                if ordered_declared is not None:
                    raise ChecklistError('checklist has more than one ordered-file count')
                ordered_declared = int(match.group(1))
            continue
        if section == 'Files':
            match = FILE_ROW_RE.match(line)
            if match:
                rows.append({'id': int(match.group(2)),
                             'checked': match.group(1) in 'xX',
                             'line': number})
    if fenced:
        raise ChecklistError('checklist has an unclosed code fence')
    if identity is None:
        raise ChecklistError('checklist has no snapshot header comment')
    if files_sections != 1:
        raise ChecklistError('checklist must have exactly one ## Files section')
    if ordered_declared is None:
        raise ChecklistError('checklist has no ordered-file count')
    if ordered_declared != len(rows):
        raise ChecklistError('checklist declares Ordered files: {} but has {} approval '
                             'row(s)'.format(ordered_declared, len(rows)))
    for position, row in enumerate(rows, 1):
        if row['id'] != position:
            raise ChecklistError('file row IDs must run 1..{} in order; found {}'.format(
                len(rows), row['id']))
    return identity, rows


def _checklist_summary(identity, rows):
    """The compact state `status` and `advance` report."""
    return {
        'checked': sum(1 for row in rows if row['checked']),
        'total': len(rows),
        'current': next((row['id'] for row in rows if not row['checked']), None),
        'snapshot': identity,
    }


def checklist_status(text: str) -> dict:
    """`checked`, `total`, `current` (first unchecked ID, or None), and `snapshot`.

    The first unchecked row is the current file; an empty or fully excluded
    inventory has no current file.
    """
    identity, rows = _parse_checklist(text)
    return _checklist_summary(identity, rows)


def advance_checklist(text: str, snapshot: dict, entry: int, response: str) -> tuple:
    """The (new_text, status) of one approval attempt against the current file.

    Only a response whose `response.strip().casefold()` equals `reviewed`
    approves, and only for the current entry while the checklist still matches
    `snapshot`'s identity. Every other response -- the old numeric `1` included --
    returns the text unchanged with `advanced: false`; a stale snapshot, a wrong
    or already-checked entry, and a malformed checklist raise ChecklistError and
    never yield altered text.
    """
    identity, rows = _parse_checklist(text)
    status = _checklist_summary(identity, rows)
    if not isinstance(response, str):
        raise ChecklistError('response must be a string')
    if response.strip().casefold() != CONFIRMATION_WORD:
        return text, dict(status, advanced=False)
    fresh = _snapshot_identity(snapshot)
    stale = [key for key in IDENTITY_KEYS if identity[key] != fresh[key]]
    if stale:
        raise ChecklistError('checklist snapshot is stale; changed: {}'.format(
            ', '.join(stale)))
    if isinstance(entry, bool) or not isinstance(entry, int) or entry < 1:
        raise ChecklistError('entry must be a positive integer')
    if status['current'] is None:
        raise ChecklistError('checklist is complete; no current file to advance')
    if entry != status['current']:
        raise ChecklistError('entry {} is not the current file ({}); no approval '
                             'recorded'.format(entry, status['current']))
    lines = text.split('\n')
    number = rows[entry - 1]['line']
    lines[number] = '- [x]' + lines[number][5:]
    new_text = '\n'.join(lines)
    new_identity, new_rows = _parse_checklist(new_text)
    return new_text, dict(_checklist_summary(new_identity, new_rows), advanced=True)


def _report_link(link):
    """The clickable relative Markdown link of a report: raw label, encoded target."""
    target = urllib.parse.quote(link, safe='/')
    return '[`{}`]({})'.format(link, target)


def _report_note_lines(note):
    """The Markdown lines of one validated report note, or ChecklistError."""
    if not isinstance(note, dict):
        raise ChecklistError('report note must be an object')
    link = note.get('link')
    if not isinstance(link, str) or not link.strip():
        raise ChecklistError('report note link must be a non-empty string')
    _one_line(link, 'report note link')
    assessment = note.get('assessment')
    if assessment not in ASSESSMENTS:
        raise ChecklistError('report note assessment must be one of {}'.format(
            ', '.join(ASSESSMENTS)))
    findings = note.get('findings', [])
    if not isinstance(findings, list):
        raise ChecklistError('report note findings must be a list')
    lines = ['- Report {} — {}.'.format(_report_link(link), assessment)]
    for finding in findings:
        if not isinstance(finding, str) or not finding.strip():
            raise ChecklistError('report note findings must be non-empty strings')
        _one_line(finding, 'report note finding')
        lines.append('  - Unresolved: {}'.format(finding))
    return lines


def _discussion_bounds(lines):
    """The [start, end) body bounds of the `## Discussion and findings` section."""
    start = None
    for index, line in enumerate(lines):
        if line.strip() == '## Discussion and findings':
            start = index + 1
            break
    if start is None:
        raise ChecklistError('checklist has no ## Discussion and findings section')
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith('## '):
            end = index
            break
    return start, end


def record_report(text: str, note: dict) -> tuple:
    """The (new_text, summary) of recording one worker report in the ledger.

    `note` carries `link` (the repo-relative report path), `assessment` (one of
    the three review assessments) and `findings` (the report's unresolved
    one-line findings). The note renders as an actual clickable Markdown link --
    the raw link as the label, its percent-encoded relative target -- plus one
    bullet per finding, under the non-approval `## Discussion and findings`
    section. Recording never touches `## Files` or a checkbox; re-recording the
    same report replaces its own block, so a resume stays idempotent, and other
    notes are preserved. Only new analysis changes a note; the person's
    `reviewed` confirmation does not.
    """
    if not isinstance(text, str):
        raise ChecklistError('checklist text must be a string')
    block = _report_note_lines(note)
    _parse_checklist(text)
    link = note['link']
    lines = text.split('\n')
    start, end = _discussion_bounds(lines)
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    if [line for line in body if line.strip()] == ['_None yet._']:
        body = [line for line in body if line.strip() != '_None yet._']
    anchor = '- Report [`{}`]('.format(link)
    for offset, line in enumerate(body):
        if line.startswith(anchor):
            block_end = offset + 1
            while block_end < len(body) and body[block_end].startswith('  '):
                block_end += 1
            body = body[:offset] + block + body[block_end:]
            break
    else:
        body = body + block
    new_text = '\n'.join(lines[:start] + body + [''] + lines[end:])
    summary = {'link': link, 'assessment': note['assessment'],
               'findings': len(note.get('findings', [])),
               'changed': new_text != text}
    return new_text, summary


def record_report_file(path, note) -> dict:
    """Read, check the report link, `record_report` and atomically replace the ledger.

    The note's `link` is the report path relative to the checklist
    (`reports/004.md`). It is resolved against the ledger's directory and
    canonicalized to that checked ledger-relative path, so the recorded
    hyperlink always reaches the report beside the ledger. An absolute link, a
    link that escapes the ledger directory or one that names no existing file is
    refused and nothing is written. An unchanged record rewrites nothing, so
    re-presenting a saved report on resume stays idempotent.
    """
    if not isinstance(note, dict):
        raise ChecklistError('report note must be an object')
    text = _read_text(path)
    checked = dict(note)
    checked['link'] = _checked_report_link(Path(path).parent, note.get('link'))
    new_text, summary = record_report(text, checked)
    if summary['changed']:
        _atomic_write(path, new_text)
    return summary


def _checked_report_link(ledger_dir, link):
    """The checked ledger-relative report link, or ChecklistError.

    The link is resolved against the ledger's directory; a repo-root-relative
    link is accepted only when it resolves from the current working directory to
    an existing file inside the ledger directory, and the returned value is
    always the canonical ledger-relative path. An absolute link, a link that
    escapes the ledger directory or one that names no existing file is refused,
    so a recorded hyperlink can never point at a nonexistent or wrongly-rooted
    destination. No pattern is blacklisted: any existing nested report path
    inside the ledger directory is kept.
    """
    if not isinstance(link, str) or not link.strip():
        raise ChecklistError('report note link must be a non-empty string')
    if link.startswith('/') or '\\' in link:
        raise ChecklistError('report link must be relative to the ledger: {!r}'.format(link))
    base = Path(ledger_dir).resolve()
    for candidate in (Path(ledger_dir) / link, Path.cwd() / link):
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(base):
            return resolved.relative_to(base).as_posix()
    raise ChecklistError('report link {!r} does not name an existing file under the ledger '
                         'directory'.format(link))


def _parse_flags(argv, flags):
    """The named `flags` parsed from `argv` pairs; UsageError when malformed."""
    options = dict.fromkeys(flags)
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg not in options:
            raise UsageError('unknown argument: {}'.format(arg))
        if index + 1 >= len(argv):
            raise UsageError('missing value for {}'.format(arg))
        options[arg] = argv[index + 1]
        index += 2
    missing = [flag for flag, value in options.items() if value is None]
    if missing:
        raise UsageError('missing required {}'.format(' '.join(missing)))
    return options


def _parse_inventory(argv):
    return _parse_flags(argv, ('--metadata', '--pages', '--merge-base', '--output'))


def _entry(value):
    """A positive row ID from the `--entry` flag; UsageError otherwise."""
    if not re.match(r'^[1-9][0-9]*$', value):
        raise UsageError('--entry must be a positive integer: {!r}'.format(value))
    return int(value)


def _run_inventory(argv):
    options = _parse_inventory(argv)
    metadata = _read_json(options['--metadata'])
    pages = _read_json(options['--pages'])
    snapshot = normalize_snapshot(metadata, pages, options['--merge-base'])
    _write_snapshot(options['--output'], snapshot)
    return {'output': options['--output'], 'pr_url': snapshot['pr_url'],
            'base_sha': snapshot['base_sha'], 'head_sha': snapshot['head_sha'],
            'merge_base_sha': snapshot['merge_base_sha'],
            'changed_files': snapshot['changed_files'],
            'inventory_digest': snapshot['inventory_digest']}


def _run_render(argv):
    options = _parse_flags(argv, ('--snapshot', '--preparation', '--output'))
    snapshot = _read_json(options['--snapshot'])
    preparation = _read_json(options['--preparation'])
    text = render_checklist(snapshot, preparation)
    _atomic_write(options['--output'], text)
    identity = _snapshot_identity(snapshot)
    return {'output': options['--output'], 'pr_url': identity['pr_url'],
            'base_sha': identity['base_sha'], 'head_sha': identity['head_sha'],
            'merge_base_sha': identity['merge_base_sha'],
            'inventory_digest': identity['inventory_digest'],
            'ordered': len(preparation['ordered']),
            'excluded': len(preparation['excluded'])}


def _run_status(argv):
    options = _parse_flags(argv, ('--checklist',))
    return checklist_status(_read_text(options['--checklist']))


def _run_advance(argv):
    options = _parse_flags(argv, ('--checklist', '--snapshot', '--entry', '--response'))
    entry = _entry(options['--entry'])
    text = _read_text(options['--checklist'])
    snapshot = _read_json(options['--snapshot'])
    new_text, status = advance_checklist(text, snapshot, entry, options['--response'])
    if status['advanced']:
        _atomic_write(options['--checklist'], new_text)
    return status


def envelope(ok, elapsed_ms, data=None, error=None, verb=VERB):
    out = {'ok': ok, 'verb': verb, 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def main(argv: list = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    verb = argv[0] if argv and not argv[0].startswith('-') else None
    try:
        if argv and argv[0] in ('-h', '--help'):
            print(USAGE)
            return 0
        if not argv:
            raise UsageError('no verb given; expected one of {}'.format(', '.join(VERBS)))
        if argv[0] == 'inventory':
            data = _run_inventory(argv[1:])
        elif argv[0] == 'render':
            data = _run_render(argv[1:])
        elif argv[0] == 'status':
            data = _run_status(argv[1:])
        elif argv[0] == 'advance':
            data = _run_advance(argv[1:])
        else:
            raise UsageError('unknown verb: {}'.format(argv[0]))
        print(envelope(True, elapsed(), data=data, verb=argv[0]))
        return 0
    except UsageError as exc:
        print(envelope(False, elapsed(), verb=verb,
                       error={'kind': 'invalid_argument', 'message': str(exc)}))
        return 2
    except (SnapshotError, ChecklistError) as exc:
        print(envelope(False, elapsed(), verb=verb,
                       error={'kind': 'invalid_data', 'message': str(exc)}))
        return 2
    except OSError as exc:
        print(envelope(False, elapsed(), verb=verb,
                       error={'kind': 'io_error', 'message': str(exc)}))
        return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # an uncaught crash is an environment error, reported loudly
        print(json.dumps({'ok': False, 'verb': VERB, 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
