"""PostToolUse(Edit|Write|MultiEdit): mirror artel's spec trail into a kartoteka
artifact store. Best-effort by contract — never blocks, never retries."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

# The deliberation documents — the ones that record why a decision was made.
# Gate evidence, machine-readable findings, derived HTML and transient adapter
# state are deliberately absent; see the design's §5 for each exclusion.
MIRRORED = {
    'idea.md', 'vision.md', 'prd.md', 'research.md', 'plan.md',
    'tasklist.md', 'tasks.md', 'implementation-notes.md', 'review.md',
    'deep-review.md', 'qa.md', 'adr.md', 'summary.md',
    'design-analysis.md', 'pr-description.md', 'post_feedback.md',
}

# stem -> stage. Absent means "the stem is the stage". The single entry exists
# because artel calls one document tasklist.md ticket-wide and
# phase-<N>/tasks.md phase-scoped, and filing those under different stages
# would defeat the grouping the stem rule exists for. A table rather than a
# heuristic: guessing that two stems mean one stage is how a mapping starts
# absorbing artel's naming history.
STAGE_OVERRIDES = {'tasks': 'tasklist'}

PHASE_DIR = re.compile(r'phase-\d+\Z')

# A guard, never the authority: kartoteka enforces the real max_artifact_bytes,
# so the two disagreeing costs at worst a skipped-and-logged artifact the
# server would have accepted. This is why a second copy of the constant is
# acceptable here and is not the meta-key-names situation, where two copies of
# one value must agree or the index lies. If it ever has to track a raised
# server limit it becomes a `knowledge` config key, not a smarter guess.
MAX_BYTES = 1048576
TIMEOUT_SECONDS = 2

# A large error page (an HTML 500 page from some intermediary, say) must not
# bloat the log; a few hundred characters is enough of kartoteka's own JSON
# body ("both guards describe what the caller got wrong...") to act on.
ERROR_BODY_LIMIT = 300


def knowledge_base_url(config):
    """(base_url, error). Adapter off ("none", or the section absent) ->
    (None, None), silently. Anything else unusable -> (None, message): the
    mirror reports and continues, where vcs would stop the run — a pull
    request cannot be written to disk, but these files are already on disk.

    An adapter value that is neither "none" nor "kartoteka" (a typo like
    "kartoteca") is one of those unusable cases, not a third silent state:
    config.md's reading rule 3 calls a name outside its allowed set a
    configuration error, and treating it the same as "none" would make a
    misspelled adapter mirror nothing, forever, with no request, no log line
    and no stderr to notice by.
    """
    knowledge = config.get('knowledge') or {}
    adapter = knowledge.get('adapter', 'none')
    if adapter == 'none':
        return None, None
    if adapter != 'kartoteka':
        return None, 'knowledge.adapter must be "none" or "kartoteka", got {!r}'.format(adapter)
    base = (knowledge.get('baseUrl') or '').strip().rstrip('/')
    if not base:
        return None, 'knowledge.adapter is "kartoteka" but knowledge.baseUrl is empty'
    return base, None


def _ticket_matcher(config):
    ticket_cfg = config.get('ticket') or {}
    project_key = ticket_cfg.get('projectKey') or 'PROJ'
    pattern = ticket_cfg.get('pattern') or h.DEFAULT_TICKET_PATTERN
    try:
        compiled = re.compile(pattern.replace('{projectKey}', re.escape(project_key)),
                              re.IGNORECASE)
    except re.error:
        return None, project_key
    return compiled, project_key


def artifact_identity(rel, config):
    """(ticket_key, stage, name) for a mirrored spec-trail path, else None.

    ticket_key stays canonical — the phase goes into `name`, because related()
    joins artifacts against Jira documents whose ticket_keys[] carry the bare
    key. `name` stays slash-free because the /api read route uses a plain path
    converter.
    """
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    try:
        parts = Path(rel).relative_to(specs_dir).parts
    except ValueError:
        return None
    if len(parts) == 2:
        ticket_dir, phase, filename = parts[0], None, parts[1]
    elif len(parts) == 3 and PHASE_DIR.match(parts[1]):
        ticket_dir, phase, filename = parts[0], parts[1], parts[2]
    else:
        return None
    if filename not in MIRRORED:
        return None
    compiled, project_key = _ticket_matcher(config)
    if compiled is None:
        return None
    match = compiled.match(ticket_dir)
    if not match:
        return None
    ticket_key = '{}-{}'.format(project_key.upper(), match.group(1))
    stem = filename[:-len('.md')]
    stage = STAGE_OVERRIDES.get(stem, stem)
    name = filename if phase is None else '{}.{}'.format(phase, filename)
    return ticket_key, stage, name


LOG_PATH = h.STATE_DIR / 'knowledge-mirror.log'


def log(line):
    """One line per attempt. A silent mirror that has been failing for a week
    is worse than no mirror: it looks like a complete trail."""
    try:
        h.STATE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with LOG_PATH.open('a', encoding='utf-8') as handle:
            handle.write('{} {}\n'.format(stamp, line))
    except OSError:
        pass  # the log is a convenience; failing to write it changes nothing


def post_artifact(base_url, payload):
    # Deferred: this module is imported on every Edit/Write in every host repo, and
    # the import alone is ~28ms of the ~63ms a no-op invocation costs. Pay it only on
    # the path that actually has something to send.
    import urllib.request
    request = urllib.request.Request(
        base_url + '/api/artifacts',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    # An explicit empty ProxyHandler, not urlopen's default opener: the default one
    # installs ProxyHandler(getproxies()), so it honours http_proxy/https_proxy from
    # the environment and would route this "loopback" POST -- the full text of every
    # mirrored document -- through a configured proxy host. The trust boundary this
    # hook promises is "nothing leaves the machine"; this is what keeps that true on a
    # host with a corporate proxy exported, regardless of no_proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status


def _read_rejection_body(exc):
    """Best-effort, truncated body of an HTTPError. kartoteka answers 400 with
    an explanatory JSON body deliberately -- its own source comment reads
    "both guards describe what the caller got wrong, and an agent reading a
    bare 400 learns nothing." Discarding it here would throw that reasoning
    away and make a permanent contract rejection indistinguishable in the log
    from a transient outage."""
    try:
        body = exc.read().decode('utf-8', errors='replace').strip()
    except Exception:
        return ''
    if len(body) > ERROR_BODY_LIMIT:
        body = body[:ERROR_BODY_LIMIT] + '...(truncated)'
    return body


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    config = h.load_config()
    base_url, error = knowledge_base_url(config)
    if base_url is None and error is None:
        return 0  # adapter off: the cheapest path, checked before stdin or a path match
    rel = h.relpath_from_tool_input(h.read_hook_input())
    if not rel:
        return 0
    identity = artifact_identity(rel, config)
    if identity is None:
        return 0
    if error:
        # Only now, not at the top: gating on the error before matching the path made
        # a misconfigured-but-on host log one line per edit of anything, forever. This
        # way it logs once per edit of something that was actually going to be
        # mirrored -- still noisy, but proportionate to the problem.
        log('misconfigured -- ' + error)
        return 0
    ticket_key, stage, name = identity
    try:
        content = Path(rel).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return 0  # deleted, binary, or unreadable between the write and here
    if len(content.encode('utf-8')) > MAX_BYTES:
        log('skip {} {} -- over {} bytes'.format(ticket_key, name, MAX_BYTES))
        return 0
    payload = {'ticket_key': ticket_key, 'stage': stage,
               'name': name, 'content': content}
    try:
        import urllib.error  # deferred with urllib.request, same reasoning
        status = post_artifact(base_url, payload)
        log('ok {} {} {} {}'.format(ticket_key, stage, name, status))
    except urllib.error.HTTPError as exc:
        # A permanent contract rejection -- not a transient outage, so it will
        # not self-heal on the next edit and the log has to say so distinctly.
        # Two real ways to land here: kartoteka's ticket-key grammar requires
        # a project key of two-or-more characters while config.md allows a
        # one-character ticket.projectKey, and an operator who lowers
        # workspace.max_artifact_bytes below this hook's 1 MiB guard gets a
        # 400 the guard cannot predict.
        body = _read_rejection_body(exc)
        log('reject {} {} {} -- HTTP {} {}'.format(ticket_key, stage, name, exc.code, body))
    except Exception as exc:  # fail open: the files on disk are the fallback
        log('fail {} {} -- {}'.format(ticket_key, name, exc))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # fail open
        print('knowledge_mirror hook error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
