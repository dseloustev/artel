"""Stop: block completion while the fast gate has findings NEW relative to the session baseline.
Bounded: after 2 consecutive blocks the stop passes with a loud warning."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

MAX_CONSECUTIVE_BLOCKS = 2


def _counter(session):
    h.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return h.STATE_DIR / 'stopblocks-{}.json'.format(session)


def main():
    data = h.read_hook_input()  # first: it moves into the session's worktree
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    # No stop_hook_active early-return: the consecutive-blocks counter below is the loop
    # bound (2 blocks max, then a loud pass-through) — an early return here would allow
    # the continuation's stop attempt unconditionally and dead-code the counter.
    session = data.get('session_id') or 'unknown'
    config = h.load_config()
    changed = h.changed_files(config)
    if not changed:
        _counter(session).write_text('{"consecutive": 0}', encoding='utf-8')
        return 0
    code, envelope = h.run_fast_verify(changed)
    if code == 0:
        _counter(session).write_text('{"consecutive": 0}', encoding='utf-8')
        return 0
    if code != 1:
        print('stop gate: verify environment error — allowing stop; fix the toolchain '
              '({})'.format((envelope.get('error') or {}).get('kind', 'unknown')),
              file=sys.stderr)
        return 0
    baseline_path = h.STATE_DIR / 'baseline-{}.json'.format(session)
    if baseline_path.exists():
        baseline = set(json.loads(baseline_path.read_text(encoding='utf-8')).get('keys') or [])
    else:
        h.STATE_DIR.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({'keys': sorted(h.finding_keys(envelope))}),
                                 encoding='utf-8')
        return 0  # first sight this session: treat current findings as pre-existing (fail open)
    new = sorted(h.finding_keys(envelope) - baseline)
    if not new:
        _counter(session).write_text('{"consecutive": 0}', encoding='utf-8')
        return 0
    counter_path = _counter(session)
    consecutive = 0
    if counter_path.exists():
        try:
            consecutive = json.loads(counter_path.read_text(encoding='utf-8')).get('consecutive') or 0
        except Exception:
            consecutive = 0
    if consecutive >= MAX_CONSECUTIVE_BLOCKS:
        # Latch at cap instead of resetting to 0: resetting here would re-arm the 3-cycle
        # block/pass pattern, which can phase-lock against the coexisting orchestrator Stop
        # gate's 6-cycle pattern so their allows never coincide (indefinite blocking). Stay
        # latched — every subsequent red-new stop passes with this systemMessage — until a
        # green/no-new/clean pass resets the counter via one of the branches above.
        print(json.dumps({'systemMessage':
            'stop gate: still red after 2 blocks — letting the stop through. '
            'UNRESOLVED: ' + '; '.join(new[:5])}))
        return 0
    counter_path.write_text(json.dumps({'consecutive': consecutive + 1}), encoding='utf-8')
    print(json.dumps({'decision': 'block', 'reason':
        'The verify gate is red with findings introduced this session — fix them before '
        'finishing (python3 "{}" --fast --files {}):\n'.format(h.VERIFY_SCRIPT, ','.join(changed))
        + '\n'.join(new[:10])}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('stop gate error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
