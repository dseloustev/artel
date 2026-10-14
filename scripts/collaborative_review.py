#!/usr/bin/env python3
"""Validated PR snapshot inventory for the collaborative review.

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

Validation is complete before anything is written: the metadata's declared
`changedFiles` must equal the number of returned rows, paths must be unique, and
metadata, revisions, PR URL, rows and counts must be well formed. The script
never runs a subprocess and never touches the network; its only write is the
named `--output` artifact, replaced atomically.

stdout is one JSON envelope: `ok`, `verb`, `elapsed_ms`, and either `data` -- a
compact summary (output path, PR/revision identity, file count, digest) so the
patches never have to enter the caller's context -- or `error: {kind, message}`.
Exit 0 when answered; 2 for an invalid argument, invalid input data, or an I/O
failure.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

VERB = 'inventory'
PR_URL_RE = re.compile(r'^https://github\.com/[^/\s]+/[^/\s]+/pull/([1-9][0-9]*)$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
USAGE = ('usage: collaborative_review.py inventory --metadata <json-file> '
         '--pages <json-file> --merge-base <sha> --output <json-file>')


class SnapshotError(ValueError):
    """Input that cannot become a review snapshot (metadata, pages, revisions)."""


class UsageError(ValueError):
    """The command line itself is wrong."""


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


def _write_snapshot(path, snapshot):
    """Write the artifact through a temporary sibling, then replace atomically."""
    text = json.dumps(snapshot, indent=2, sort_keys=True) + '\n'
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


def _parse_inventory(argv):
    flags = {'--metadata': None, '--pages': None, '--merge-base': None, '--output': None}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg not in flags:
            raise UsageError('unknown argument: {}'.format(arg))
        if index + 1 >= len(argv):
            raise UsageError('missing value for {}'.format(arg))
        flags[arg] = argv[index + 1]
        index += 2
    missing = [flag for flag, value in flags.items() if value is None]
    if missing:
        raise UsageError('missing required {}'.format(' '.join(missing)))
    return flags


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
            raise UsageError('no verb given; expected {}'.format(VERB))
        if argv[0] != VERB:
            raise UsageError('unknown verb: {}'.format(argv[0]))
        options = _parse_inventory(argv[1:])
        metadata = _read_json(options['--metadata'])
        pages = _read_json(options['--pages'])
        snapshot = normalize_snapshot(metadata, pages, options['--merge-base'])
        _write_snapshot(options['--output'], snapshot)
        data = {'output': options['--output'], 'pr_url': snapshot['pr_url'],
                'base_sha': snapshot['base_sha'], 'head_sha': snapshot['head_sha'],
                'merge_base_sha': snapshot['merge_base_sha'],
                'changed_files': snapshot['changed_files'],
                'inventory_digest': snapshot['inventory_digest']}
        print(envelope(True, elapsed(), data=data))
        return 0
    except UsageError as exc:
        print(envelope(False, elapsed(), verb=verb,
                       error={'kind': 'invalid_argument', 'message': str(exc)}))
        return 2
    except SnapshotError as exc:
        print(envelope(False, elapsed(), error={'kind': 'invalid_data', 'message': str(exc)}))
        return 2
    except OSError as exc:
        print(envelope(False, elapsed(), error={'kind': 'io_error', 'message': str(exc)}))
        return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # an uncaught crash is an environment error, reported loudly
        print(json.dumps({'ok': False, 'verb': VERB, 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
