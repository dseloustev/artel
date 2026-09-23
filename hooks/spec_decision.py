"""The per-ticket storage decision: .artel/run/<TICKET_ID>/spec-store.json.

Which store this ticket's spec trail lives in right now -- kartoteka, or files
on disk with the reason -- plus the newest version of each stored document at
the last successful listing (the migration base) and any document saved
locally with the user's permission while kartoteka was down (`pending`).

Written by scripts/spec_store.py only; read by it, by every skill (through
`spec_store.py decision`) and by hooks/spec_store_guard.py. A decision is fresh
for WALL_CLOCK_HOURS after `decided_at`, the same window run-state.json's
`started_at` gets, and orchestrators rewrite both together.
Contract: docs/spec-storage.md §2.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402
import kartoteka_http as kh  # noqa: E402

FILENAME = 'spec-store.json'
WALL_CLOCK_HOURS = 3  # autonomous-run.md §2; the constant stop_gate.py and sensitive_guard.py use
CONTEXT_TICKETS = Path('.artel/context/tickets')

# Re-export canonical_ticket from hook_common: shared with kartoteka_http, resolve_active_ticket
canonical_ticket = h.canonical_ticket


def path_for(ticket):
    return h.ticket_run_dir(ticket) / FILENAME


def load(ticket):
    try:
        data = json.loads(path_for(ticket).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def is_fresh(decision, now=None):
    """Whether `decided_at` is within WALL_CLOCK_HOURS. Anything that is not a
    timezone-aware ISO stamp is stale, never an exception: the guard and the
    session hook read this file, and a raise there loses the whole answer."""
    stamp = (decision or {}).get('decided_at')
    if not isinstance(stamp, str):
        return False
    try:
        decided = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if decided.tzinfo is None:
            return False  # naive: no way to know which clock wrote it
        now = now or datetime.now(timezone.utc)
        return now - decided <= timedelta(hours=WALL_CLOCK_HOURS)
    except (ValueError, TypeError):
        return False


def new_decision(store, reason, decided_by, versions=None, pending=None):
    return {
        'store': store,
        'reason': reason,
        'decided_by': decided_by,
        'decided_at': now_iso(),
        'versions': dict(versions or {}),
        'pending': list(pending or []),
    }


def write(ticket, decision):
    """Atomic: a reader -- the guard, mid-write -- sees the old file or the new one."""
    target = path_for(ticket)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(FILENAME + '.tmp')
    tmp.write_text(json.dumps(decision, indent=2) + '\n', encoding='utf-8')
    os.replace(str(tmp), str(target))


def admits_local_write(rel, ticket):
    """Whether a spec document may be written to disk right now.

    Yes for a path saved locally with permission (`pending`), and for anything
    while a fresh files decision stands. A stale files decision admits nothing:
    the next skill re-resolves, and kartoteka may be back. Paths compare
    normalised, so `./specs/…` and `specs/…` are one path."""
    decision = load(ticket)
    if decision is None:
        return False
    rel = os.path.normpath(rel)
    for p in decision.get('pending') or []:
        if isinstance(p, dict) and isinstance(p.get('path'), str) \
                and os.path.normpath(p['path']) == rel:
            return True
    return decision.get('store') == 'files' and is_fresh(decision)


def local_trail(ticket, config, unmovable=False):
    """This ticket's spec trail on disk, repo-relative.

    First its documents: <specs.dir>/<T>/ (phase folders included), then
    save-context's .artel/context copy. Only mirrored filenames count, so
    evidence and .active_ticket are not a trail. Then its images
    (_trail_images), the ones kartoteka can address. unmovable=True also adds
    the images it cannot. Migration lists those to report them, but they are no
    trail a run is asked to migrate, since nothing could ever move them."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    found = []
    for root in (Path(specs_dir) / ticket, CONTEXT_TICKETS / ticket / 'spec-trail'):
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob('*.md')):
            logical = str(Path(specs_dir) / ticket / candidate.relative_to(root))
            if kh.artifact_identity(logical, config) is not None:
                found.append(str(candidate))
    movable, unaddressable = _trail_images(ticket, specs_dir, config)
    return found + movable + (unaddressable if unmovable else [])


def _trail_images(ticket, specs_dir, config):
    """(movable, unaddressable) image files that belong to migration (spec-images
    §8): those git tracks under <specs.dir>/<T>/, then every one in the context
    copy, each list sorted. An untracked working-tree image is the sweep's
    (spec_store.py image sync). Symbolic links are listed: migration reports
    them skipped and never reads one, as it does a symlinked document."""
    movable, unaddressable = [], []
    tree = Path(specs_dir) / ticket
    for root in (tree, CONTEXT_TICKETS / ticket / 'spec-trail'):
        if not root.is_dir():
            continue
        tracked = _tracked_under(root) if root == tree else None
        for candidate in sorted(root.rglob('*')):
            if not kh.is_image_name(candidate.name):
                continue
            if not (candidate.is_symlink() or candidate.is_file()):
                continue
            if tracked is not None and os.path.normpath(str(candidate)) not in tracked:
                continue
            logical = str(tree / candidate.relative_to(root))
            if kh.image_identity(logical, config) is None:
                unaddressable.append(str(candidate))
            else:
                movable.append(str(candidate))
    return movable, unaddressable


def _tracked_under(root):
    """The paths git's index holds under root, normalised and spelled as this
    process's working directory spells them. It draws the same line
    `spec_store.py image sync` draws with `git ls-files`. A file that is staged
    but never committed counts as tracked. Without git, or outside a work tree,
    it returns none, so no working-tree image is taken from the sweep."""
    try:
        proc = subprocess.run(['git', '--literal-pathspecs', 'ls-files', '-z', '--', str(root)],
                              capture_output=True)
    except OSError:
        return set()
    if proc.returncode != 0:
        return set()
    return {os.path.normpath(os.fsdecode(p)) for p in proc.stdout.split(b'\0') if p}


def image_files(root):
    """Every image file under `root`, at any depth, sorted: regular files only.

    A symbolic link is never listed, and a link met while walking below
    `root` is never entered (os.walk does not follow a link once it is
    inside the walk). `root` itself can still be a link -- os.walk walks
    into whatever it is first pointed at -- so a linked ticket directory is
    not this function's problem to catch: the sweep's own
    `_outside_the_trail` runs before any path from here is read. Used by the
    sweep (`spec_store.py image sync`) and by migration.
    """
    found = []
    for directory, _, names in os.walk(str(root)):
        for name in names:
            path = Path(directory) / name
            if kh.is_image_name(name) and path.is_file() and not path.is_symlink():
                found.append(path)
    return sorted(found)
