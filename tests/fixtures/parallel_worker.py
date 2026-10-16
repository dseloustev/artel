#!/usr/bin/env python3
"""Fake worker process for tests/test_parallel_execution.py (stdlib only).

This is the deterministic stand-in for an OpenCode worker *process*: the real session
mechanics are covered by the native proof; here the process exists only so whole-process
overlap, barriers and interruptions are observable. The worker never touches the
coordinator's manifest, lock, queue or Git state -- it writes its own change inside its
worker root, its structured result under the artifact root, and a durable finish record
that the test-side fake session service reads.

Protocol (every path is absolute; all steps are bounded, no unbounded wait):

    --root ROOT            the worker root (a real linked worktree)
    --artifact ARTIFACT    the artifact root inside the worker root (git-ignored)
    --result RESULT        a result.json template copied to ARTIFACT/result.json
    --changed REL          a repo-relative path inside ROOT the worker changes
    --content TEXT         the content written to that path
    --entered ENTERED      written first, so the driver can observe the process is up
    --release RELEASE      the driver creates this file to release the barrier
    --finished FINISHED    the durable fake-session finish record ({"status": "succeeded"})
    --result-first         write result.json before waiting (interrupt-over-result case)
    --deadline SECONDS     bounded barrier wait; exit 3 when it expires

Exit codes: 0 after the full protocol; 3 when the barrier deadline expires. A killed
process writes nothing more; the driver records the interrupt itself.
"""
import argparse
import json
import os
import sys
import time


def write_text(path, text):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


def write_result(args):
    with open(args.result, encoding='utf-8') as handle:
        payload = json.load(handle)
    write_text(os.path.join(args.artifact, 'result.json'),
               json.dumps(payload, indent=2, sort_keys=True) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='fake worker process (test fixture)')
    parser.add_argument('--root', required=True)
    parser.add_argument('--artifact', required=True)
    parser.add_argument('--result', required=True)
    parser.add_argument('--changed', required=True)
    parser.add_argument('--content', default='worker change\n')
    parser.add_argument('--entered', required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--finished', required=True)
    parser.add_argument('--result-first', action='store_true')
    parser.add_argument('--deadline', type=float, default=120.0)
    args = parser.parse_args(argv)

    os.makedirs(args.artifact, exist_ok=True)
    write_text(args.entered, json.dumps({'pid': os.getpid()}) + '\n')
    target = os.path.join(args.root, args.changed)
    if args.result_first:
        # The worker finished its work (change + result) and is interrupted while still
        # waiting: the contradictory succeeded result must never win.
        write_text(target, args.content)
        write_result(args)

    deadline = time.time() + args.deadline
    while not os.path.exists(args.release):
        if time.time() >= deadline:
            sys.stderr.write('fake worker: barrier deadline expired\n')
            return 3
        time.sleep(0.02)

    if not args.result_first:
        write_text(target, args.content)
        write_result(args)
    write_text(args.finished, json.dumps({'schema': 1, 'status': 'succeeded'}) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
