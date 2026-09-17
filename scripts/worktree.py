#!/usr/bin/env python3
"""worktree: move a ticket's work into its own git worktree, and hand it back.

    worktree.py move-in   --ticket T --name N --branch B --base BASE [--create-from REF] [--track]
    worktree.py hand-back --ticket T [--name N] [--check]

move-in runs from the main checkout. hand-back works on the main checkout wherever it is
started, but refuses to run from inside a linked worktree -- that directory is about to be
deleted -- except with --check, which changes nothing. Each run prints one JSON object and
exits 0 only when its `status` is `ok`:

    ok           done
    refused      a precondition failed; nothing was changed
    conflict     a stash did not apply; the stash is kept (`stash`), `files` lists conflicts
    rolled-back  a step failed after changes began; they were undone
    error        unexpected failure; `reason` names the failing command

A stash is dropped only after it applied cleanly. Never `--force`, never a branch deletion.
Contract: docs/worktrees.md
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

WORKTREES_DIR = Path('.claude') / 'worktrees'
ARTEL_DIR = Path('.artel')
RUN_DIR = ARTEL_DIR / 'run'
HOOKS_STATE = RUN_DIR / '.hooks'
CONTEXT = ARTEL_DIR / 'context'
MANIFEST = ARTEL_DIR / 'worktree.json'
MARKER_NAME = 'worktree.json'  # hand-back's recovery marker, under .artel/run/<TICKET>/
DEFAULT_INCLUDE = ('/.claude/', '/.mcp.json')
HOOK_STATE_PREFIXES = ('baseline-', 'stopblocks-')
MOVE_TAG = 'artel move-to-worktree {}'
BACK_TAG = 'artel return-from-worktree {}'


class Stop(Exception):
    """Ends a subcommand with a non-ok status; keyword fields join the JSON report."""

    def __init__(self, status, reason, **fields):
        super().__init__(reason)
        self.report = dict(fields, status=status, reason=reason)


# --- git plumbing -------------------------------------------------------------------------

def git(*args, cwd=None, check=True):
    proc = subprocess.run(['git'] + [str(a) for a in args], cwd=cwd,
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise Stop('error', 'git {} failed: {}'.format(
            ' '.join(str(a) for a in args), (proc.stderr or proc.stdout).strip()))
    return proc


def out(*args, cwd=None):
    return git(*args, cwd=cwd).stdout.strip()


def same_path(a, b):
    return Path(a).resolve() == Path(b).resolve()


def in_linked_worktree(cwd=None):
    git_dir = out('rev-parse', '--path-format=absolute', '--git-dir', cwd=cwd)
    common = out('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=cwd)
    return not same_path(git_dir, common)


def worktrees():
    """[{'path', 'branch'}] from `git worktree list --porcelain`, main checkout first.
    `branch` is None for a detached HEAD."""
    entries = []
    for block in git('worktree', 'list', '--porcelain').stdout.split('\n\n'):
        entry = {'path': None, 'branch': None}
        for line in block.splitlines():
            if line.startswith('worktree '):
                entry['path'] = line[len('worktree '):]
            elif line.startswith('branch refs/heads/'):
                entry['branch'] = line[len('branch refs/heads/'):]
        if entry['path']:
            entries.append(entry)
    return entries


def changes(cwd=None):
    """Uncommitted paths (untracked included, ignored excluded)."""
    return [l for l in out('status', '--porcelain', '--untracked-files=all', cwd=cwd).splitlines()
            if l.strip()]


def current_branch(cwd=None):
    return out('branch', '--show-current', cwd=cwd) or None


def stash_push(tag, cwd=None):
    """Stash everything uncommitted, untracked included. The stash commit id, or None.

    The stash stack is shared by every worktree and every concurrent session, so the entry
    is found by its own unique message, never read back as stash@{0}."""
    if not changes(cwd):
        return None
    message = '{} {}'.format(tag, uuid.uuid4().hex[:12])
    git('stash', 'push', '--include-untracked', '-m', message, cwd=cwd)
    for line in out('stash', 'list', '--format=%H %gs', cwd=cwd).splitlines():
        sha, _, subject = line.partition(' ')
        if subject.endswith(': ' + message):
            return sha
    raise Stop('error', 'the stash just pushed is not listed: {}'.format(message))


def stash_listed(sha):
    return sha in out('stash', 'list', '--format=%H').splitlines()


def stash_drop(sha):
    for index, listed in enumerate(out('stash', 'list', '--format=%H').splitlines()):
        if listed == sha:
            git('stash', 'drop', 'stash@{{{}}}'.format(index))
            return


def stash_apply(sha, cwd):
    """Apply a stash in `cwd` and drop it. On failure the stash is kept -> Stop('conflict')."""
    proc = git('stash', 'apply', sha, cwd=cwd, check=False)
    if proc.returncode != 0:
        files = out('diff', '--name-only', '--diff-filter=U', cwd=cwd).splitlines()
        raise Stop('conflict', 'the stash did not apply in {}: {}'.format(
            cwd, (proc.stderr or proc.stdout).strip()), stash=sha, files=files, path=str(cwd))
    stash_drop(sha)


# --- files --------------------------------------------------------------------------------

def copy_newer(src, dst):
    """Copy src over dst unless dst is at least as new. True when copied."""
    if os.path.lexists(dst) and dst.lstat().st_mtime >= src.lstat().st_mtime:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
    return True


def merge_tree(src, dst):
    """Copy every file under src into dst, newer file wins."""
    for dirpath, _dirnames, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        for name in filenames:
            copy_newer(Path(dirpath) / name, dst / rel / name)


def ignored(paths, cwd):
    """The subset of `paths` the checkout's ignore rules match (tracked paths never do)."""
    if not paths:
        return []
    proc = subprocess.run(['git', 'check-ignore', '--stdin', '-z'], cwd=cwd,
                          input='\0'.join(paths) + '\0', capture_output=True, text=True)
    return sorted(p for p in proc.stdout.split('\0') if p)


def artel_files(checkout):
    """Files under .artel/ except run/, context/ and the manifest, relative to checkout."""
    base = Path(checkout) / ARTEL_DIR
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(checkout)
        if rel_dir == ARTEL_DIR:
            dirnames[:] = [d for d in dirnames if d not in ('run', 'context')]
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if rel != MANIFEST.as_posix():
                found.append(rel)
    return found


def host_files(checkout, include_file):
    """Files .worktreeinclude selects (default: /.claude/ and /.mcp.json), relative to
    checkout, outside .artel/ and .claude/worktrees/."""
    if include_file.is_file():
        rules = ['--exclude-from={}'.format(include_file)]
    else:
        rules = ['--exclude={}'.format(p) for p in DEFAULT_INCLUDE]
    listed = git('ls-files', '-z', '--others', '--ignored', *rules, cwd=checkout).stdout
    skip = (ARTEL_DIR.as_posix() + '/', WORKTREES_DIR.as_posix() + '/')
    return [p for p in listed.split('\0')
            if p and not p.endswith('/') and not p.startswith(skip)]


def environment(checkout, include_file):
    """Every ignored file a worktree gets a copy of, relative to checkout."""
    return ignored(artel_files(checkout) + host_files(checkout, include_file), checkout)


def copy_hook_state(src_root, dst_root):
    src = Path(src_root) / HOOKS_STATE
    if not src.is_dir():
        return
    for path in sorted(src.iterdir()):
        if path.is_file() and path.suffix == '.json' and path.name.startswith(HOOK_STATE_PREFIXES):
            copy_newer(path, Path(dst_root) / HOOKS_STATE / path.name)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


# --- move-in ------------------------------------------------------------------------------

def ensure_excluded(root, name):
    """Keep .claude/worktrees/ out of `git status` without touching tracked files."""
    probe = (WORKTREES_DIR / name).as_posix()
    if git('check-ignore', '-q', probe, cwd=root, check=False).returncode == 0:
        return
    exclude = root / out('rev-parse', '--git-path', 'info/exclude', cwd=root)
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open('a', encoding='utf-8') as fh:
        fh.write('\n/{}/\n'.format(WORKTREES_DIR.as_posix()))


def tracked(checkout):
    return set(p for p in git('ls-files', '-z', cwd=checkout).stdout.split('\0') if p)


def transfer_in(root, target, ticket):
    # A path the main checkout ignores may be tracked on the task branch: never overwrite it.
    in_branch = tracked(target)
    copied = [rel for rel in environment(root, root / '.worktreeinclude') if rel not in in_branch]
    for rel in copied:
        copy_newer(root / rel, target / rel)
    store, linked = root / CONTEXT, False
    if store.is_dir():
        link = target / CONTEXT
        if not link.exists() and not link.is_symlink():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(store.resolve(), target_is_directory=True)
        linked = True
    run_src, moved = root / RUN_DIR / ticket, False
    if run_src.is_dir():
        merge_tree(run_src, target / RUN_DIR / ticket)
        shutil.rmtree(run_src)
        moved = True
    copy_hook_state(root, target)
    return {'copied': copied, 'contextLinked': linked, 'runMoved': moved}


def move_in(args):
    if in_linked_worktree():
        raise Stop('refused', 'run move-in from the main checkout, not from inside a worktree')
    root = Path(out('rev-parse', '--show-toplevel'))
    target = root / WORKTREES_DIR / args.name
    for entry in worktrees()[1:]:
        if entry['branch'] != args.branch:
            continue
        if same_path(entry['path'], target):
            return {'status': 'ok', 'path': str(target), 'branch': args.branch,
                    'alreadyExisted': True, 'mainDirty': bool(changes())}
        raise Stop('refused', 'branch {} is checked out in another worktree: {}'.format(
            args.branch, entry['path']))
    if target.exists():
        raise Stop('refused', '{} exists but is not a worktree of {}'.format(target, args.branch))

    original = current_branch()
    stash = stash_push(MOVE_TAG.format(args.ticket))
    main_now_on = original
    if original == args.branch:
        if git('checkout', args.base, check=False).returncode == 0:
            main_now_on = args.base
        else:
            git('checkout', '--detach')
            main_now_on = None
    ensure_excluded(root, args.name)

    add = ['worktree', 'add']
    if args.create_from:
        add += (['--track'] if args.track else []) + ['-b', args.branch, target, args.create_from]
    else:
        add += [target, args.branch]
    proc = git(*add, check=False)
    if proc.returncode != 0:
        if original and main_now_on != original:
            git('checkout', original, check=False)
        if stash and git('stash', 'apply', stash, check=False).returncode == 0:
            stash_drop(stash)
        raise Stop('rolled-back', 'git worktree add failed: {}'.format(proc.stderr.strip()),
                   mainNowOn=original)

    moved = transfer_in(root, target, args.ticket)
    write_json(target / MANIFEST, dict(moved, ticket=args.ticket, name=args.name,
                                       branch=args.branch, main=str(root)))
    report = dict(moved, status='ok', path=str(target), branch=args.branch,
                  created=bool(args.create_from), alreadyExisted=False,
                  mainNowOn=main_now_on, stashApplied=False)
    if stash:
        stash_apply(stash, target)
        report['stashApplied'] = True
    return report


# --- entry point --------------------------------------------------------------------------

def parse(argv):
    parser = argparse.ArgumentParser(prog='worktree.py', description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    move = sub.add_parser('move-in')
    move.add_argument('--ticket', required=True)
    move.add_argument('--name', required=True)
    move.add_argument('--branch', required=True)
    move.add_argument('--base', required=True)
    move.add_argument('--create-from')
    move.add_argument('--track', action='store_true')
    return parser.parse_args(argv)


def main(argv):
    args = parse(argv)
    try:
        report = move_in(args)
    except Stop as stop:
        report = stop.report
    except Exception as exc:  # noqa: BLE001 -- the JSON report is the contract
        report = {'status': 'error', 'reason': '{}: {}'.format(type(exc).__name__, exc)}
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get('status') == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
