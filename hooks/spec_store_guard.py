"""PreToolUse(Edit|Write|MultiEdit): with kartoteka as the spec store, a spec
document is never written to disk without the user's permission.
PreToolUse(Read): with a fresh kartoteka decision, a Read of an image the sweep
already moved into kartoteka is denied with the command that fetches it -- a
hint, not enforcement.

Inert without .artel/config.json, with knowledge.adapter other than "kartoteka",
and for a project key outside kartoteka's ticket-key grammar (one character, or
containing `_` or `-`). A write is guarded only for a mirrored spec document,
and allowed only while the ticket's storage decision is a fresh files decision,
or for a path the user approved saving locally during an outage (`pending`) --
spec_decision.py. Writing an image is always allowed: producers save images at
their logical paths and the orchestrators sweep them in. A Read is looked at
only for an image under the trail with no file at its path.

Why a hook: "no local copies" otherwise rests on every sentence of 60-odd agent
and skill files staying correct, and a missed one fails silently into exactly
the second copy store mode exists to remove. Here it fails loudly, with the fix
in the message. Bash writes (cat >, cp, rsync) are not seen -- the same limit
vcs_guard.py documents. The Read hint exists because an agent reading a swept
image's old path by habit would see "file does not exist" and may conclude the
image is missing. Contract: docs/spec-storage.md §6.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402
import kartoteka_http as kh  # noqa: E402
import spec_decision as sd  # noqa: E402

REASON = ("kartoteka is this project's spec store: {name} is written with artifact_put / "
          'artifact_patch (docs/spec-storage.md §4), not as a file. If kartoteka is '
          'unreachable, stop and report STORE_UNAVAILABLE -- a spec is never saved locally '
          "without the user's permission.")
STALE_REASON = ('the storage decision for {ticket} is stale (older than {hours} hours): '
                're-resolve it (docs/spec-storage.md §2) before writing {name}')
READ_HINT = ('images are stored in kartoteka: run spec_store.py image fetch {path} and Read '
             'the path it prints')


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }}))


def read_hint(rel, config):
    """Deny a Read of an image the sweep already moved into kartoteka, naming the fetch."""
    identity = kh.image_identity(rel, config)
    if identity is None or os.path.exists(rel):
        return  # not an image under the trail, or still on disk: it reads as usual
    decision = sd.load(identity[0])
    if decision is not None and decision.get('store') == 'kartoteka' and sd.is_fresh(decision):
        deny(READ_HINT.format(path=rel))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    reading = str(data.get('tool_name') or '').lower() == 'read'  # OpenCode sends `read`
    file_path = str((data.get('tool_input') or {}).get('file_path') or '')
    if reading and not kh.is_image_name(os.path.basename(file_path)):
        return 0  # this hook sees every Read: anything but an image stops here, cheaply
    h.enter_session_root(data)  # as h.read_hook_input() does: into the session's worktree first
    if not h.CONFIG_PATH.exists():
        return 0
    config = h.load_config()
    if ((config.get('knowledge') or {}).get('adapter') or 'none') != 'kartoteka':
        return 0
    if not kh.storable_project_key((config.get('ticket') or {}).get('projectKey') or 'PROJ'):
        return 0  # kartoteka cannot key this project's tickets: always the files path
    rel = h.relpath_from_tool_input(data)
    if not rel:
        return 0
    rel = os.path.normpath(rel)  # a `..` segment must not walk past the address check
    if reading:
        read_hint(rel, config)
        return 0
    identity = kh.artifact_identity(rel, config)
    if identity is None:
        return 0
    ticket_key, _, name = identity
    if sd.admits_local_write(rel, ticket_key):
        return 0
    decision = sd.load(ticket_key)
    if decision is not None and decision.get('store') == 'files':
        # A files decision that has gone stale: this write was allowed until
        # then, so "use the tools" would be the wrong fix. Re-resolving is right.
        deny(STALE_REASON.format(ticket=ticket_key, hours=sd.WALL_CLOCK_HOURS, name=name))
    else:
        deny(REASON.format(name=name))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('spec store guard error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
