"""PostToolUse(Edit|Write|MultiEdit): mirror artel's spec trail into a kartoteka
artifact store. Best-effort by contract — never blocks, never retries.
Addressing, credentials and transport live in kartoteka_http.py, shared with
scripts/spec_store.py."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402
from kartoteka_http import (  # noqa: E402,F401 -- re-exported: tests and readers use km.<name>
    ERROR_BODY_LIMIT, MAX_BYTES, MIRRORED, PHASE_DIR, PROJECT_RE, STAGE_OVERRIDES,
    artifact_identity, bearer_token, knowledge_target, plaintext_off_loopback, post_artifact,
    redacted, rejection_body,
)

TIMEOUT_SECONDS = 2

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


def main():
    data = h.read_hook_input()  # first: it moves into the session's worktree
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    config = h.load_config()
    base_url, project, error = knowledge_target(config)
    if base_url is None and error is None:
        return 0  # adapter off: the cheapest path, checked before a path match
    rel = h.relpath_from_tool_input(data)
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
    token, token_env, token_error = bearer_token(config)
    if token_error:
        log('misconfigured -- ' + token_error)
        return 0
    if token and plaintext_off_loopback(base_url):
        log('misconfigured -- knowledge.baseUrl {} is plaintext http:// off loopback and a bearer '
            'token would cross the network in the clear; use the daemon\'s https:// origin, or a '
            'loopback proxy -- nothing was sent'.format(base_url))
        return 0
    ticket_key, stage, name = identity
    try:
        content = Path(rel).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return 0  # deleted, binary, or unreadable between the write and here
    if len(content.encode('utf-8')) > MAX_BYTES:
        log('skip {} {} -- over {} bytes'.format(ticket_key, name, MAX_BYTES))
        return 0
    payload = {'project': project, 'ticket_key': ticket_key, 'stage': stage,
               'name': name, 'content': content}
    try:
        import urllib.error  # deferred with urllib.request, same reasoning
        status = post_artifact(base_url, payload, token, timeout=TIMEOUT_SECONDS)
        log('ok {} {} {} {}'.format(ticket_key, stage, name, status))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            # The daemon has [auth] on. The token itself is never logged, on this
            # path or any other: the log is a plain file in the host repo's
            # .artel/run/, and a secret that lands there outlives the session.
            body = rejection_body(exc)
            if token:
                # A credential was sent and refused: revoked, expired, or minted
                # for another daemon. Permanent until the operator acts, so a
                # `reject` like the other contract refusals, naming the variable
                # rather than the value.
                log('reject {} {} {} -- HTTP 401 {} -- the token in {} was refused '
                    '(revoked, expired, or not this daemon\'s); check `kartoteka token '
                    'list` on the daemon host'.format(ticket_key, stage, name, body,
                                                     token_env))
            elif token_env:
                log('misconfigured -- the daemon requires a bearer token (HTTP 401 {}) '
                    'but {} (knowledge.tokenEnv) is not set in the hook\'s '
                    'environment'.format(body, token_env))
            else:
                log('misconfigured -- the daemon requires a bearer token (HTTP 401 {}) '
                    'but knowledge.tokenEnv is empty; name the variable that holds a '
                    'token from `kartoteka token add`'.format(body))
            return 0
        # A permanent contract rejection -- not a transient outage, so it will
        # not self-heal on the next edit and the log has to say so distinctly.
        # Three real ways to land here: kartoteka's ticket-key grammar requires
        # a project key of two-or-more characters while config.md allows a
        # one-character ticket.projectKey; an operator who lowers
        # workspace.max_artifact_bytes below this hook's 1 MiB guard gets a
        # 400 the guard cannot predict; and a knowledge.project nobody
        # registered in the daemon's database is a 400 whose body names
        # `kartoteka project add <name>` -- the daemon deliberately cannot
        # register one on demand, so the fix is that command, run once on the
        # machine serving the daemon.
        body = rejection_body(exc)
        log('reject {} {} {} -- HTTP {} {}'.format(ticket_key, stage, name, exc.code, body))
    except Exception as exc:  # fail open: the files on disk are the fallback
        log('fail {} {} -- {}'.format(ticket_key, name, redacted(str(exc), token)))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # fail open
        print('knowledge_mirror hook error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
