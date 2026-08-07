"""SessionStart: capture the findings baseline so the verify stop gate blocks only NEW findings."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert, zero footprint
    data = h.read_hook_input()
    session = data.get('session_id') or 'unknown'
    config = h.load_config()
    h.STATE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = h.STATE_DIR / 'baseline-{}.json'.format(session)
    if baseline_path.exists():
        return 0
    changed = h.changed_files(config)
    keys = set()
    if changed:
        code, envelope = h.run_fast_verify(changed, timeout=100)
        if code not in (0, 1):
            return 0  # env error -> leave baseline ABSENT; the stop gate's first-sight branch captures it lazily
        keys = h.finding_keys(envelope)
    baseline_path.write_text(json.dumps({'keys': sorted(keys)}), encoding='utf-8')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('baseline hook error (skipping): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
