"""PreToolUse(Edit|Write|MultiEdit): with kartoteka as the spec store, a spec
document is never written to disk without the user's permission.

Inert without .artel/config.json, with knowledge.adapter other than "kartoteka",
for a project key outside kartoteka's ticket-key grammar (one character, or
containing `_` or `-`), and for any path that is not a mirrored spec document. Otherwise a write is allowed only while the
ticket's storage decision is a fresh files decision, or for a path the user
approved saving locally during an outage (`pending`) -- spec_decision.py.

Why a hook: "no local copies" otherwise rests on every sentence of 60-odd agent
and skill files staying correct, and a missed one fails silently into exactly
the second copy store mode exists to remove. Here it fails loudly, with the fix
in the message. Bash writes (cat >, cp, rsync) are not seen -- the same limit
vcs_guard.py documents. Contract: docs/spec-storage.md §6.
"""
import json
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


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }}))


def main():
    data = h.read_hook_input()  # first: it moves into the session's worktree
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
    identity = kh.artifact_identity(rel, config)
    if identity is None:
        return 0
    ticket_key, _, name = identity
    if sd.admits_local_write(rel, ticket_key):
        return 0
    deny(REASON.format(name=name))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('spec store guard error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
