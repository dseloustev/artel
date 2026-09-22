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


def local_trail(ticket, config):
    """This ticket's spec documents on disk, repo-relative: first <specs.dir>/<T>/
    (phase folders included), then save-context's .artel/context copy. Only
    mirrored filenames count -- evidence and .active_ticket are not a trail."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    found = []
    for root in (Path(specs_dir) / ticket, CONTEXT_TICKETS / ticket / 'spec-trail'):
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob('*.md')):
            logical = str(Path(specs_dir) / ticket / candidate.relative_to(root))
            if kh.artifact_identity(logical, config) is not None:
                found.append(str(candidate))
    return found
