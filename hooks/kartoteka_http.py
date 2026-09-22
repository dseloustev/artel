"""kartoteka over HTTP: the one client artel's hooks and scripts share.

Hooks are subprocesses and cannot call the session's MCP tools, and artel's
scripts must not route spec documents through a model's context
(docs/spec-storage.md §4), so both speak kartoteka's HTTP facade. Everything
that decides where a request goes and what it carries lives here once: the
target (baseUrl + project), the bearer token, the plaintext guard, the
proxy-free opener, and the spec-trail addressing rule.

Stdlib only. Imported by hooks/knowledge_mirror.py, hooks/spec_decision.py,
hooks/spec_store_guard.py and scripts/spec_store.py.
"""
import json
import os
import re
import sys
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

# kartoteka's project-id grammar (models.validate_project), copied for the same
# reason MAX_BYTES is: a guard, never the authority. The server refuses a name
# outside it, so the two disagreeing costs at worst one misconfigured line for
# a name the server would have taken -- and it turns "HTTP 422 on every edit"
# into a log line that names the key to fix.
PROJECT_RE = re.compile(r'^[a-z0-9][a-z0-9-]*\Z')

# A large error page (an HTML 500 page from some intermediary, say) must not
# bloat the log; a few hundred characters is enough of kartoteka's own JSON
# body ("both guards describe what the caller got wrong...") to act on.
ERROR_BODY_LIMIT = 300


def knowledge_target(config):
    """(base_url, project, error). Adapter off ("none", or the section absent)
    -> (None, None, None), silently. Anything else unusable -> (None, None,
    message): the mirror reports and continues, where vcs would stop the run —
    a pull request cannot be written to disk, but these files are already on
    disk.

    `project` is required alongside `baseUrl`: since kartoteka's project
    namespacing (E4) a write that names no project is refused, and one that
    names an unregistered project is refused too — see the HTTPError branch
    in main(). There is deliberately no default: kartoteka removed its own
    because a guessed project appends to another project's trail.

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
        return None, None, None
    if adapter != 'kartoteka':
        return None, None, 'knowledge.adapter must be "none" or "kartoteka", got {!r}'.format(
            adapter)
    base = (knowledge.get('baseUrl') or '').strip().rstrip('/')
    if not base:
        return None, None, 'knowledge.adapter is "kartoteka" but knowledge.baseUrl is empty'
    project = (knowledge.get('project') or '').strip()
    if not project:
        return None, None, 'knowledge.adapter is "kartoteka" but knowledge.project is empty'
    if not PROJECT_RE.match(project):
        return None, None, ('knowledge.project must be lowercase kebab-case '
                            '(^[a-z0-9][a-z0-9-]*$), got {!r}'.format(project))
    return base, project, None


# What may travel in an HTTP header value: printable ASCII, no whitespace.
# http.client refuses anything else -- with a ValueError that echoes the whole
# header, which is the one path on which the token could have reached the log.
# So the value is checked here, by name, before any header exists.
_HEADER_SAFE = re.compile(r'^[\x21-\x7e]+\Z')


def bearer_token(config, environ=None):
    """(token or None, variable name or '', error or None). `knowledge.tokenEnv`
    names an environment variable; the config never holds the value, because
    the file is committed team configuration and a token is one machine's
    credential.

    A named variable that is unset yields no token *and no error*: the hook
    sends the request unauthenticated and lets the daemon decide. One
    committed config then serves a laptop talking to a loopback daemon with
    `[auth]` off and a host talking to a hosted one that requires the token;
    a 401 is classified in main() with the variable's name in hand, so the log
    line can say which variable to export. Since kartoteka 0.32.0.

    A value that cannot travel in a header -- a file exported with a second
    line, say -- is the one error: reported by the variable's name, never its
    content, and nothing is sent.
    """
    environ = os.environ if environ is None else environ
    knowledge = config.get('knowledge') or {}
    token_env = (knowledge.get('tokenEnv') or '').strip()
    if not token_env:
        return None, '', None
    token = (environ.get(token_env) or '').strip()
    if not token:
        return None, token_env, None
    if not _HEADER_SAFE.match(token):
        return None, token_env, (
            '{} (knowledge.tokenEnv) holds a value with whitespace or control characters; '
            'export the token as one line'.format(token_env))
    return token, token_env, None


def redacted(message, token):
    """`message` with the token replaced. Defence in depth for any line that
    quotes an exception: a library that echoes a header in its error text must
    not turn the mirror log into a credential store."""
    return message.replace(token, '<redacted>') if token else message


def plaintext_off_loopback(base_url):
    """True when `base_url` is plain http to a host that is not loopback --
    where a bearer token would cross the network in the clear. kartoteka
    itself refuses a non-loopback bind without TLS, so such an origin is a
    proxy's upstream port reached directly, or a typo; never a working
    deployment."""
    from urllib.parse import urlsplit  # deferred like urllib.request: send path only
    parts = urlsplit(base_url)
    if parts.scheme != 'http':
        return False
    host = (parts.hostname or '').lower()
    return not (host == 'localhost' or host == '::1' or host.startswith('127.'))


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


def post_artifact(base_url, payload, token=None, timeout=2):
    # Deferred: this module is imported on every Edit/Write in every host repo, and
    # the import alone is ~28ms of the ~63ms a no-op invocation costs. Pay it only on
    # the path that actually has something to send.
    import urllib.request
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(
        base_url + '/api/artifacts',
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    # An explicit empty ProxyHandler, not urlopen's default opener: the default one
    # installs ProxyHandler(getproxies()), so it honours http_proxy/https_proxy from
    # the environment and would route this POST -- the full text of every mirrored
    # document, and the bearer token with it -- through a configured proxy host. The
    # trust boundary this hook promises is "the document goes to baseUrl and nowhere
    # else": on a loopback baseUrl that means nothing leaves the machine, and against
    # a hosted daemon (an https origin, legitimate since kartoteka 0.32.0) it means
    # the daemon and no intermediary. Either way it holds on a host with a corporate
    # proxy exported, regardless of no_proxy. TLS is urllib's default: the system
    # trust store verifies the certificate, and a self-signed one lands in the log
    # as a `fail` naming CERTIFICATE_VERIFY_FAILED rather than being sent to blindly.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return response.status


def rejection_body(exc):
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


class Unreachable(Exception):
    """No HTTP answer at all: refused, DNS, TLS, timeout. Its message is already
    token-redacted, so a caller may print it."""


def quote(segment):
    from urllib.parse import quote as _quote  # deferred, like urllib.request below
    return _quote(segment, safe='')


def _json_or_none(raw):
    try:
        return json.loads(raw.decode('utf-8')) if raw else None
    except ValueError:
        return None


def call(base_url, method, path, token=None, query=None, body=None, timeout=15):
    """(status, parsed JSON or None) for one request to kartoteka.

    An HTTP error status is an answer, not an exception: callers classify 404,
    409 and 401 themselves, because each means something different to them.
    Only a request that got no answer raises Unreachable. The trust boundary is
    post_artifact's: the proxy-free opener, TLS verified by the system store,
    and a token that never reaches a message.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    url = base_url + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, _json_or_none(response.read())
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b''
        return exc.code, _json_or_none(raw)
    except Exception as exc:
        raise Unreachable(redacted(str(exc), token)) from None
