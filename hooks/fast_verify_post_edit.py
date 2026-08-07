"""PostToolUse(Edit|Write|MultiEdit): fast verify on the edited file; feedback, never block."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MAX_LINES = 10


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    data = h.read_hook_input()
    config = h.load_config()
    rel = h.relpath_from_tool_input(data)
    if not rel or not h.is_verifiable(rel, config) or not Path(rel).exists():
        return 0
    code, envelope = h.run_fast_verify([rel], timeout=120)
    if code != 1:
        return 0  # green, skipped, or environment error — per-edit noise helps nobody
    lines = []
    for stage in (envelope.get('data') or {}).get('stages') or []:
        if stage.get('ok'):
            continue
        lines.extend(l for l in (stage.get('tail') or '').splitlines() if l.strip())
    if not lines:
        return 0
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        # the tail's END is closest to the failure summary most linters print last
        'additionalContext': 'fast verify findings on ' + rel + ':\n'
                             + '\n'.join(lines[-MAX_LINES:]),
    }}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # fail open
        print('fast_verify hook error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
