import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import knowledge_mirror as km  # noqa: E402

CONFIG = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'specs/.current'}}


class TestArtifactIdentity(unittest.TestCase):
    def test_ticket_wide_path(self):
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/prd.md', CONFIG),
            ('AW-1234', 'prd', 'prd.md'),
        )

    def test_phase_scoped_path_keeps_ticket_key_canonical(self):
        # The phase belongs in `name`, never in ticket_key: related() joins
        # artifacts to Jira documents whose ticket_keys[] carry the bare key.
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/phase-2/prd.md', CONFIG),
            ('AW-1234', 'prd', 'phase-2.prd.md'),
        )

    def test_tasks_md_maps_to_the_tasklist_stage(self):
        # The one stem exception: artel calls the same document tasklist.md
        # ticket-wide and phase-<N>/tasks.md phase-scoped.
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/phase-2/tasks.md', CONFIG),
            ('AW-1234', 'tasklist', 'phase-2.tasks.md'),
        )

    def test_name_never_contains_a_slash(self):
        # GET /api/artifacts/{ticket_key}/{stage}/{name} uses a plain path
        # converter, which will not match across '/'.
        for rel in ('specs/.current/AW-1234/prd.md',
                    'specs/.current/AW-1234/phase-7/plan.md'):
            _, _, name = km.artifact_identity(rel, CONFIG)
            self.assertNotIn('/', name)

    def test_unlisted_filename_is_not_mirrored(self):
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/pr-pending.md', CONFIG))
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/change-report.html', CONFIG))

    def test_evidence_subdirectories_are_not_mirrored(self):
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/runtime/observation.md', CONFIG))
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/review/findings.json', CONFIG))

    def test_path_outside_specs_dir_is_not_mirrored(self):
        self.assertIsNone(km.artifact_identity('lib/main.dart', CONFIG))
        self.assertIsNone(km.artifact_identity('.artel/run/AW-1234/run-state.json', CONFIG))

    def test_file_directly_under_specs_dir_is_not_mirrored(self):
        # The specs root holds .active_ticket, and the reviewer agent's bare
        # standalone mode writes review-claude.md there; neither is ticket-scoped.
        self.assertIsNone(km.artifact_identity('specs/.current/review-claude.md', CONFIG))

    def test_deep_review_is_mirrored_and_the_merged_summary_is_not(self):
        # deep-review writes one deliberation document now; review-summary.md
        # is no longer written by anything, so mirroring it would only ever
        # re-post a stale file a user kept around.
        self.assertEqual(
            km.artifact_identity('specs/.current/AW-1234/deep-review.md', CONFIG),
            ('AW-1234', 'deep-review', 'deep-review.md'),
        )
        self.assertIsNone(
            km.artifact_identity('specs/.current/AW-1234/review-summary.md', CONFIG))

    def test_directory_not_matching_the_ticket_pattern_is_not_mirrored(self):
        self.assertIsNone(km.artifact_identity('specs/.current/scratch/prd.md', CONFIG))

    def test_non_default_specs_dir_is_honored(self):
        config = {'ticket': {'projectKey': 'AW'}, 'specs': {'dir': 'design/tickets'}}
        self.assertEqual(
            km.artifact_identity('design/tickets/AW-9/plan.md', config),
            ('AW-9', 'plan', 'plan.md'),
        )
        self.assertIsNone(km.artifact_identity('specs/.current/AW-9/plan.md', config))


class TestKnowledgeBaseUrl(unittest.TestCase):
    def test_absent_section_is_off(self):
        self.assertEqual(km.knowledge_base_url({}), (None, None))

    def test_adapter_none_is_off(self):
        config = {'knowledge': {'adapter': 'none', 'baseUrl': 'http://127.0.0.1:8734'}}
        self.assertEqual(km.knowledge_base_url(config), (None, None))

    def test_adapter_on_with_empty_base_url_reports(self):
        # Reading rule 3 calls this a configuration error, but a knowledge
        # mirror reports and continues where vcs would stop the run.
        url, error = km.knowledge_base_url({'knowledge': {'adapter': 'kartoteka'}})
        self.assertIsNone(url)
        self.assertIn('baseUrl', error)

    def test_usable_adapter_returns_url_without_trailing_slash(self):
        config = {'knowledge': {'adapter': 'kartoteka',
                                'baseUrl': 'http://127.0.0.1:8734/'}}
        self.assertEqual(km.knowledge_base_url(config), ('http://127.0.0.1:8734', None))


class TestSizeGuard(unittest.TestCase):
    def test_limit_matches_kartoteka_default(self):
        self.assertEqual(km.MAX_BYTES, 1048576)

    def test_timeout_is_two_seconds(self):
        self.assertEqual(km.TIMEOUT_SECONDS, 2)


HOOK = Path(__file__).resolve().parent.parent / 'hooks' / 'knowledge_mirror.py'

HOOK_INPUT = {
    'tool_name': 'Write',
    'tool_input': {'file_path': 'specs/.current/AW-1234/prd.md'},
}


class _Capture(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        _Capture.received.append({
            'path': self.path,
            'body': json.loads(self.rfile.read(length).decode('utf-8')),
        })
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"artifact_id": 1, "version": 1}')

    def log_message(self, *args):
        pass  # the test's own output is the only output that helps


class _RejectingCapture(BaseHTTPRequestHandler):
    """Answers every POST with a configurable non-2xx status and body, the way
    kartoteka's own 400 carries an explanatory JSON body rather than an empty
    one. Tests set `status`/`body` before running the hook against it."""
    status = 400
    body = b'{"detail": "rejected"}'

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(length)
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass  # the test's own output is the only output that helps


def _run_hook(repo, hook_input, env=None):
    """Run the hook as the harness runs it: cwd = host repo root, JSON on stdin.
    `env=None` inherits this process's environment unchanged; pass an explicit
    mapping to test the hook's behavior under a modified one (e.g. a proxy)."""
    return subprocess.run(
        [sys.executable, str(HOOK)],
        cwd=repo, input=json.dumps(hook_input), text=True, capture_output=True,
        env=env,
    )


def _host_repo(tmp, knowledge):
    repo = Path(tmp)
    (repo / '.artel').mkdir()
    (repo / '.artel' / 'config.json').write_text(json.dumps({
        'version': 1,
        'ticket': {'projectKey': 'AW'},
        'specs': {'dir': 'specs/.current'},
        'knowledge': knowledge,
    }), encoding='utf-8')
    ticket = repo / 'specs' / '.current' / 'AW-1234'
    ticket.mkdir(parents=True)
    (ticket / 'prd.md').write_text('# PRD\n\nThe body.\n', encoding='utf-8')
    return repo


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        _Capture.received = []
        self.server = HTTPServer(('127.0.0.1', 0), _Capture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:{}'.format(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_posts_the_exact_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': self.base})
            result = _run_hook(repo, HOOK_INPUT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(_Capture.received), 1)
        self.assertEqual(_Capture.received[0]['path'], '/api/artifacts')
        self.assertEqual(_Capture.received[0]['body'], {
            'ticket_key': 'AW-1234',
            'stage': 'prd',
            'name': 'prd.md',
            'content': '# PRD\n\nThe body.\n',
        })

    def test_adapter_off_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'none', 'baseUrl': self.base})
            result = _run_hook(repo, HOOK_INPUT)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Not just "nothing received": a hook that crashed immediately would also
        # send nothing and exit 0 under the fail-open wrapper. stderr empty is what
        # tells a cleanly-gated no-op apart from a crash the wrapper swallowed.
        self.assertEqual(result.stderr, '')
        self.assertEqual(_Capture.received, [])

    def test_reaches_loopback_despite_http_proxy_env(self):
        # Regression: urlopen's default opener honours http_proxy/https_proxy, so
        # without post_artifact's explicit empty ProxyHandler this POST would be
        # routed at a proxy host instead of the loopback server -- silently handing
        # the mirrored document's content off-box. Fails without the fix.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': self.base})
            env = {**os.environ, 'http_proxy': 'http://127.0.0.1:9'}
            result = _run_hook(repo, HOOK_INPUT, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(_Capture.received), 1)
        self.assertEqual(_Capture.received[0]['path'], '/api/artifacts')

    def test_oversize_file_sends_nothing_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': self.base})
            big = 'x' * (km.MAX_BYTES + 1)
            (repo / 'specs' / '.current' / 'AW-1234' / 'prd.md').write_text(
                big, encoding='utf-8')
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            self.assertTrue(log.is_file())
            self.assertIn('over', log.read_text(encoding='utf-8'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_Capture.received, [])

    def test_unrecognized_adapter_value_sends_nothing_but_logs(self):
        # config.md's reading rule 3: an adapter name outside its allowed set
        # is a configuration error. Silently treating "kartoteca" the same as
        # "none" would make a typo mirror nothing, forever, with no request
        # and nothing in the log to notice by -- this is what makes it land
        # in the log line instead.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteca', 'baseUrl': self.base})
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            line = log.read_text(encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_Capture.received, [])
        self.assertIn('kartoteca', line)

    def test_adapter_none_writes_nothing_at_all(self):
        # Distinct from test_adapter_off_sends_nothing: this checks the log
        # file too, so "none" reads the same as an absent section below --
        # neither one leaves so much as a log line behind, unlike the
        # unrecognized-adapter case just above.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'none', 'baseUrl': self.base})
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            self.assertFalse(log.exists())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_Capture.received, [])

    def test_absent_knowledge_section_writes_nothing_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / '.artel').mkdir()
            (repo / '.artel' / 'config.json').write_text(json.dumps({
                'version': 1,
                'ticket': {'projectKey': 'AW'},
                'specs': {'dir': 'specs/.current'},
                # no 'knowledge' key at all
            }), encoding='utf-8')
            ticket = repo / 'specs' / '.current' / 'AW-1234'
            ticket.mkdir(parents=True)
            (ticket / 'prd.md').write_text('# PRD\n\nThe body.\n', encoding='utf-8')
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            self.assertFalse(log.exists())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(_Capture.received, [])


class TestHttpRejectionLogging(unittest.TestCase):
    """A 400 is not a transient outage: F2's fix reads the response body kartoteka
    sends deliberately (its own source comment: "both guards describe what the
    caller got wrong, and an agent reading a bare 400 learns nothing") and puts a
    truncated form of it in the log, distinct from a plain `fail` line."""

    def setUp(self):
        _RejectingCapture.status = 400
        _RejectingCapture.body = b'{"detail": "rejected"}'
        self.server = HTTPServer(('127.0.0.1', 0), _RejectingCapture)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:{}'.format(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_400_body_reaches_the_log(self):
        explanation = 'ticket_key must have a project key of two or more characters'
        _RejectingCapture.body = json.dumps({'detail': explanation}).encode('utf-8')
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': self.base})
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            line = log.read_text(encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('reject', line)
        self.assertIn(explanation, line)

    def test_long_error_body_is_truncated(self):
        long_detail = 'x' * 5000
        _RejectingCapture.body = json.dumps({'detail': long_detail}).encode('utf-8')
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': self.base})
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            line = log.read_text(encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('truncated', line)
        # The raw body alone is ~5000 chars; a truncated log line must be a
        # small fraction of that, not merely "shorter than the whole thing."
        self.assertLess(len(line), 1000)


class TestFailOpen(unittest.TestCase):
    def test_unreachable_server_exits_zero_and_logs(self):
        # Port 1 on loopback refuses instantly; nothing must propagate.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': 'http://127.0.0.1:1'})
            result = _run_hook(repo, HOOK_INPUT)
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            self.assertIn('fail', log.read_text(encoding='utf-8'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')  # no blocking hook JSON, ever

    def test_unconfigured_host_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook(tmp, HOOK_INPUT)  # no .artel/config.json at all
        self.assertEqual(result.returncode, 0, result.stderr)
        # See test_adapter_off_sends_nothing: a crash swallowed by the fail-open
        # wrapper also exits 0 with nothing sent, so stderr is what distinguishes
        # "correctly inert" from "crashed silently."
        self.assertEqual(result.stderr, '')


class TestMisconfiguredLogging(unittest.TestCase):
    # Regression pair for the main() reordering: gating on the config error before
    # matching the path used to make a host with adapter "kartoteka" and an empty
    # baseUrl log one line per edit of *anything*, forever. The path is now matched
    # first, so these two behaviors are asserted separately -- one line of misconduct
    # each, matching the "one assertion" discipline applied to the fail-open tests above.

    def test_non_mirrorable_path_writes_no_log_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': ''})
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            non_mirrorable = {
                'tool_name': 'Write',
                'tool_input': {'file_path': 'lib/main.dart'},
            }
            result = _run_hook(repo, non_mirrorable)
            # Must be checked inside the TemporaryDirectory block: once it exits, `repo`
            # (and any log under it) is gone regardless of what the hook wrote, which
            # would make this assertion pass unconditionally.
            self.assertFalse(log.exists())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mirrorable_path_writes_exactly_one_misconfigured_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _host_repo(tmp, {'adapter': 'kartoteka', 'baseUrl': ''})
            log = (repo / '.artel' / 'run' / '.hooks' / 'knowledge-mirror.log')
            result = _run_hook(repo, HOOK_INPUT)  # specs/.current/AW-1234/prd.md
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(log.is_file())
            lines = log.read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn('misconfigured', lines[0])


if __name__ == '__main__':
    unittest.main()
