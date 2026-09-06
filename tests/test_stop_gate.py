"""The Stop gate for autonomous runs: blocks while a run is active and incomplete,
allows on every documented escape hatch, and fails open on anything unexpected.

Contract: docs/autonomous-run.md sections 2 and 8.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
sys.path.insert(0, str(HOOKS))
import stop_gate  # noqa: E402

TICKET = 'AW-1234'
CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


def iso(delta_hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


class StopGateCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        os.chdir(self.root)
        (self.root / '.artel').mkdir()
        (self.root / '.artel' / 'config.json').write_text(json.dumps(CONFIG), encoding='utf-8')

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    # --- fixtures -------------------------------------------------------
    def set_ticket(self, ticket=TICKET):
        specs = self.root / 'specs' / '.current'
        specs.mkdir(parents=True, exist_ok=True)
        (specs / '.active_ticket').write_text(ticket + '\n', encoding='utf-8')

    def run_dir(self, ticket=TICKET):
        d = self.root / '.artel' / 'run' / ticket
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_state(self, **overrides):
        state = {'run_active': True, 'completed': False, 'started_at': iso()}
        state.update(overrides)
        (self.run_dir() / 'run-state.json').write_text(json.dumps(state), encoding='utf-8')

    @property
    def counter(self):
        return self.run_dir() / '.stop-gate-blocks'

    # --- driver ---------------------------------------------------------
    def invoke(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'stdin', io.StringIO('{}')), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                stop_gate.main()
        self.assertEqual(raised.exception.code, 0, 'the gate must always exit 0')
        return out.getvalue(), err.getvalue()

    def assertAllowed(self):
        stdout, _ = self.invoke()
        self.assertEqual(stdout, '', 'an allow prints nothing on stdout')

    def assertBlocked(self):
        stdout, _ = self.invoke()
        payload = json.loads(stdout)
        self.assertEqual(payload['decision'], 'block')
        return payload['reason']


class TestAllows(StopGateCase):
    def test_no_active_ticket_allows(self):
        self.assertAllowed()

    def test_no_run_state_allows(self):
        self.set_ticket()
        self.run_dir()
        self.assertAllowed()

    def test_run_not_active_allows(self):
        self.set_ticket()
        self.write_state(run_active=False)
        self.assertAllowed()

    def test_completed_run_allows(self):
        self.set_ticket()
        self.write_state(completed=True)
        self.assertAllowed()

    def test_pause_reason_allows(self):
        """A run waiting on a human is not a run that should be held open."""
        self.set_ticket()
        self.write_state(pause_reason='awaiting-approval')
        self.assertAllowed()

    def test_stale_run_allows(self):
        """Past the 3h wall clock the run's budget is spent."""
        self.set_ticket()
        self.write_state(started_at=iso(-stop_gate.WALL_CLOCK_HOURS - 1))
        self.assertAllowed()

    def test_unparseable_timestamp_allows(self):
        self.set_ticket()
        self.write_state(started_at='not-a-timestamp')
        self.assertAllowed()

    def test_naive_timestamp_is_treated_as_utc(self):
        """A timestamp with no offset must not crash the comparison."""
        self.set_ticket()
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self.write_state(started_at=naive)
        self.assertBlocked()  # fresh: still an active run


class TestBlocks(StopGateCase):
    def setUp(self):
        super().setUp()
        self.set_ticket()
        self.write_state()

    def test_active_run_blocks_and_names_the_ticket(self):
        reason = self.assertBlocked()
        self.assertIn(TICKET, reason)
        self.assertIn('Block 1/{}'.format(stop_gate.MAX_CONSECUTIVE_BLOCKS), reason)

    def test_reason_points_at_the_abort_route(self):
        """The model has to be told how to end the run legitimately."""
        reason = self.assertBlocked()
        self.assertIn('.artel/run/{}/run-state.json'.format(TICKET), reason)
        self.assertIn('pause_reason', reason)
        self.assertIn('run_active=false', reason)

    def test_counter_increments_across_blocks(self):
        for expected in range(1, stop_gate.MAX_CONSECUTIVE_BLOCKS + 1):
            reason = self.assertBlocked()
            self.assertIn('Block {}/'.format(expected), reason)
            self.assertEqual(self.counter.read_text(encoding='utf-8'), str(expected))

    def test_allows_once_the_cap_is_reached(self):
        self.counter.write_text(str(stop_gate.MAX_CONSECUTIVE_BLOCKS), encoding='utf-8')
        stdout, stderr = self.invoke()
        self.assertEqual(stdout, '')
        self.assertIn('consecutive blocks', stderr)
        self.assertIn('NOT complete', stderr)

    def test_corrupt_counter_is_treated_as_zero(self):
        self.counter.write_text('not-a-number', encoding='utf-8')
        self.assertIn('Block 1/', self.assertBlocked())


class TestCounterHygiene(StopGateCase):
    def test_allow_clears_a_stale_counter(self):
        """Otherwise a run that pauses and resumes starts partway to its cap."""
        self.set_ticket()
        self.write_state()
        self.assertBlocked()
        self.assertTrue(self.counter.is_file())

        self.write_state(completed=True)
        self.assertAllowed()
        self.assertFalse(self.counter.exists())


if __name__ == '__main__':
    unittest.main()
