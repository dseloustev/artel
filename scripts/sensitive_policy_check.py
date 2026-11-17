#!/usr/bin/env python3
"""Arm-time precondition for an autonomous run: warn when the host sensitive-paths policy
floors one of the run's own bookkeeping files above the effective mode.

Advisory only, and always exit 0: the guard exempts those paths for the run's own writes
(hooks/README.md), so a match costs a warning line, never a refusal -- a non-zero exit would
reintroduce the deadlock through a different door. Silence means the policy never mentions the
run's own state.

Contract: docs/autonomous-run.md §10.

Usage: sensitive_policy_check.py --ticket <TICKET_ID> --mode <effective_mode>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import hook_common as h  # noqa: E402

WARNING = ('warning: {glob} floors {path} at {floor} (effective {mode}); '
           'the run state stays writable by design')


def check(ticket, mode, categories):
    """The warning lines for `ticket` at `mode`; empty when the policy floors nothing."""
    lines = []
    run_dir = h.ticket_run_dir(ticket).as_posix() + '/'
    for name in h.RUN_OWNED_FILES:
        rel = run_dir + name
        for _category, glob, floor in h.sensitive_matches(rel, categories):
            if h.MODE_RANK.get(mode, 0) < h.MODE_RANK.get(floor, 2):
                lines.append(WARNING.format(glob=glob, path=rel, floor=floor, mode=mode))
    return lines


def main(argv):
    parser = argparse.ArgumentParser(prog='sensitive_policy_check.py',
                                     description=__doc__.splitlines()[0])
    parser.add_argument('--ticket', required=True,
                        help='the run ticket; a phase suffix or bare number is canonicalized')
    parser.add_argument('--mode', required=True,
                        help='the resolved effective mode (yolo | plan-gate | full-gates)')
    args = parser.parse_args(argv)
    try:
        ticket = h.canonical_ticket(args.ticket, h.load_config()) or args.ticket.strip()
        for line in check(ticket, args.mode, h.load_sensitive_categories()):
            print(line)
    except Exception as exc:  # a check that cannot read the policy warns about nothing
        print('sensitive policy check (ignored): {}'.format(exc), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
