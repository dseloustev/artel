#!/usr/bin/env python3
"""Working-tree snapshots and review packages for the per-task review.

artel's implementer does not commit — commits happen at the phase checkpoints — so a task's
diff cannot be `BASE..HEAD`. Instead the orchestrator snapshots the working tree before each
implementer dispatch and diffs against that snapshot after the completion:

    snapshot              print a tree id of the working tree as it is now: tracked and
                          untracked files, .gitignore respected, written through a temporary
                          index so the real index (and the checkpoint's explicit staging)
                          is never touched. Take one before dispatching an implementer.
    diff BASE --out PATH  snapshot again and write the package BASE..now to PATH: the files
                          changed (stat) and the full diff with ten lines of context. Prints
                          exactly one line, "wrote PATH: N file(s), B bytes", so the package
                          itself never has to enter the caller's context — the reviewer
                          reads the file.

Run from anywhere inside the host repo. Contract: docs/autonomous-run.md §16.

Exit codes: 0 ok (an empty diff is still ok — the caller reads the file count), 2 invalid
argument or environment error (not a git repository, unknown BASE, unwritable PATH).
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CONTEXT_LINES = 10


class GitError(Exception):
    pass


def git(root, *args, env=None):
    proc = subprocess.run(['git', '-C', str(root), *args],
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or 'git {} failed'.format(' '.join(args))
        raise GitError(detail)
    return proc.stdout


def repo_root(start='.'):
    """The host repo's top level; GitError outside a work tree."""
    return git(start, 'rev-parse', '--show-toplevel').strip()


def snapshot_tree(root):
    """A tree object of the working tree (tracked + untracked, ignore rules honoured).

    Built in a throwaway index: `read-tree --empty` then `add -A` then `write-tree`, with
    GIT_INDEX_FILE pointed at a temp file, so the repo's own index is untouched and no
    commit, ref or stash is created. The tree is dangling; gc reclaims it eventually.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env['GIT_INDEX_FILE'] = os.path.join(tmp, 'index')
        git(root, 'read-tree', '--empty', env=env)
        git(root, 'add', '-A', env=env)
        return git(root, 'write-tree', env=env).strip()


def object_type(root, ref):
    try:
        return git(root, 'cat-file', '-t', ref).strip()
    except GitError:
        return None


def build_package(root, base, head):
    """(package text, changed-file list) for base..head; both may be trees or commits."""
    files = [line for line in
             git(root, 'diff-tree', '-r', '--name-only', base, head).splitlines() if line]
    stat = git(root, 'diff-tree', '-r', '--stat', base, head)
    patch = git(root, 'diff-tree', '-r', '-p', '-U{}'.format(CONTEXT_LINES), base, head)
    lines = [
        '# Review package: {}..{}'.format(base[:7], head[:7]),
        '',
        '## Files changed',
        '',
        stat.rstrip() or '(no changes)',
        '',
        '## Diff',
        '',
        patch.rstrip() or '(no changes)',
        '',
    ]
    return '\n'.join(lines), files


def cmd_snapshot(_args):
    root = repo_root()
    print(snapshot_tree(root))
    return 0


def cmd_diff(args):
    root = repo_root()
    kind = object_type(root, args.base)
    if kind not in ('tree', 'commit'):
        raise GitError('BASE is not a tree or commit: {}'.format(args.base))
    head = snapshot_tree(root)
    text, files = build_package(root, args.base, head)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding='utf-8')
    print('wrote {}: {} file(s), {} bytes'.format(out, len(files), len(text.encode('utf-8'))))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='review_package.py',
        description='Working-tree snapshots and review packages for the per-task review.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('snapshot', help='print a tree id of the working tree as it is now')
    diff = sub.add_parser('diff', help='write the package BASE..now to --out')
    diff.add_argument('base', help='tree id from a previous snapshot (a commit also works)')
    diff.add_argument('--out', required=True, help='package file to write')
    args = parser.parse_args(argv)
    try:
        return {'snapshot': cmd_snapshot, 'diff': cmd_diff}[args.command](args)
    except (GitError, OSError) as exc:
        print('review_package.py: {}'.format(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
