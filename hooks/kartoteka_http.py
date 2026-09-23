"""kartoteka over HTTP: the one client artel's hooks and scripts share.

Hooks are subprocesses and cannot call the session's MCP tools, and artel's
scripts must not route spec documents through a model's context
(docs/spec-storage.md §4), so both speak kartoteka's HTTP facade. Everything
that decides where a request goes and what it carries lives here once: the
target (baseUrl + project), the bearer token, the plaintext guard, the
proxy-free opener, and the spec-trail addressing rules -- one for documents
(artifact_identity), one for images (image_identity).

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

# The project-key prefix of kartoteka's ticket-key grammar (models.TICKET_KEY,
# ^[A-Z][A-Z0-9]+-\d+), copied for the same reason: a guard, never the
# authority. A key outside it (`X`, `MY_PROJ`) can never be stored, and the
# probe cannot tell -- PATCH does not check the key -- so without this a
# ticket would resolve to kartoteka and then have every put refused.
STORABLE_PROJECT_KEY = re.compile(r'^[A-Z][A-Z0-9]+\Z')


def storable_project_key(project_key):
    """Whether kartoteka can key tickets `<project_key>-<N>` (compared upper-cased,
    as artel writes the key)."""
    return bool(STORABLE_PROJECT_KEY.match(project_key.upper()))


# The image files a ticket's spec trail keeps in kartoteka's attachment store
# (docs/spec-storage.md §4.6), by extension, compared lower-cased, with the type
# each must sniff as. A guard, never the authority, like MAX_BYTES: kartoteka
# sniffs the bytes and refuses a type the extension does not name.
IMAGE_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp',
}

# kartoteka's attachment path grammar, copied for the same reason: 1 to 4
# segments of [A-Za-z0-9._-], neither '.' nor '..', at most 255 characters in
# all. A name outside it (a space, a fifth level) could never be stored, so
# the sweep leaves that file local and says why instead of sending it.
MAX_IMAGE_PATH = 255
MAX_IMAGE_SEGMENTS = 4
IMAGE_SEGMENT = re.compile(r'[A-Za-z0-9._-]+\Z')


def is_image_name(name):
    """Whether a file name (or path) ends in an image extension, any case."""
    return os.path.splitext(name)[1].lower() in IMAGE_TYPES


def image_path_ok(path):
    """Whether `path` is inside kartoteka's attachment path grammar, with an
    image extension on its last segment."""
    if not isinstance(path, str) or not path or len(path) > MAX_IMAGE_PATH:
        return False
    segments = path.split('/')
    if len(segments) > MAX_IMAGE_SEGMENTS:
        return False
    for segment in segments:
        if segment in ('.', '..') or not IMAGE_SEGMENT.match(segment):
            return False
    return is_image_name(segments[-1])


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
    base, project, error, _ = knowledge_target_detail(config)
    return base, project, error


def knowledge_target_detail(config):
    """knowledge_target's (base_url, project, error) plus the config key the
    error is about: 'knowledge.baseUrl' when it is empty, 'knowledge.project'
    when it is empty or outside PROJECT_RE, and None for any other error (an
    adapter typo). A caller that words its own record for an unset key --
    spec_store.py decide -- branches on the key, never on the message text."""
    knowledge = config.get('knowledge') or {}
    adapter = knowledge.get('adapter', 'none')
    if adapter == 'none':
        return None, None, None, None
    if adapter != 'kartoteka':
        return None, None, 'knowledge.adapter must be "none" or "kartoteka", got {!r}'.format(
            adapter), None
    base = (knowledge.get('baseUrl') or '').strip().rstrip('/')
    if not base:
        return (None, None, 'knowledge.adapter is "kartoteka" but knowledge.baseUrl is empty',
                'knowledge.baseUrl')
    project = (knowledge.get('project') or '').strip()
    if not project:
        return (None, None, 'knowledge.adapter is "kartoteka" but knowledge.project is empty',
                'knowledge.project')
    if not PROJECT_RE.match(project):
        return None, None, ('knowledge.project must be lowercase kebab-case '
                            '(^[a-z0-9][a-z0-9-]*$), got {!r}'.format(project)), 'knowledge.project'
    return base, project, None, None


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


def _trail_address(rel, config):
    """(ticket_key, parts below the ticket directory) for a path under
    <specs.dir>/<ticket dir>/, else None.

    The one place a trail path's ticket directory is matched and canonicalised
    -- `AW-12-2/` and `12/` both address AW-12 -- so a document and an image in
    the same directory can never be filed under two tickets.
    """
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    try:
        parts = Path(rel).relative_to(specs_dir).parts
    except ValueError:
        return None
    if len(parts) < 2:
        return None
    compiled, project_key = h.ticket_matcher(config)
    if compiled is None:
        return None
    match = compiled.match(parts[0])
    if not match:
        return None
    return '{}-{}'.format(project_key.upper(), match.group(1)), parts[1:]


def artifact_identity(rel, config):
    """(ticket_key, stage, name) for a mirrored spec-trail path, else None.

    ticket_key stays canonical — the phase goes into `name`, because related()
    joins artifacts against Jira documents whose ticket_keys[] carry the bare
    key. `name` stays slash-free because the /api read route uses a plain path
    converter.
    """
    found = _trail_address(rel, config)
    if found is None:
        return None
    ticket_key, rest = found
    if len(rest) == 1:
        phase, filename = None, rest[0]
    elif len(rest) == 2 and PHASE_DIR.match(rest[0]):
        phase, filename = rest
    else:
        return None
    if filename not in MIRRORED:
        return None
    stem = filename[:-len('.md')]
    stage = STAGE_OVERRIDES.get(stem, stem)
    name = filename if phase is None else '{}.{}'.format(phase, filename)
    return ticket_key, stage, name


def image_identity(rel, config):
    """(ticket_key, path) for an image under a ticket's spec trail, else None.

    The ticket directory is read exactly as artifact_identity reads it
    (_trail_address). Everything below it is the attachment path, verbatim:
    images are path-keyed (docs/spec-storage.md §4.6), so
    `phase-2/runtime/x.png` stays a path and never becomes a `phase-2.` name,
    and a document's `![…](design/x.png)` link is the stored path as written.
    """
    found = _trail_address(rel, config)
    if found is None:
        return None
    ticket_key, rest = found
    path = '/'.join(rest)
    return (ticket_key, path) if image_path_ok(path) else None


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
        finally:
            exc.close()  # unclosed -> ResourceWarning; callers never see the object to close it
        return exc.code, _json_or_none(raw)
    except Exception as exc:
        raise Unreachable(redacted(str(exc), token)) from None


def _header_dict(message):
    """{lower-cased name: value} for a response's headers: HTTP names are
    case-insensitive, and a plain dict is what callers compare against."""
    return {name.lower(): value for name, value in message.items()} if message else {}


def call_bytes(base_url, method, path, token=None, query=None, data=None, content_type=None,
               headers=None, timeout=60):
    """(status, raw body bytes, {lower-cased header: value}) for one request.

    call()'s twin for kartoteka's attachment routes, where the body is an
    image: `data` goes up as raw bytes under its own Content-Type, and the
    answer comes back as bytes plus the headers that carry its metadata
    (ETag, X-Kartoteka-Version). Nothing is decoded or parsed here -- an
    image must never be turned into text on its way through.

    The trust boundary is call()'s: the proxy-free opener, TLS verified by the
    system store, and a token that never reaches a message. Every HTTP status
    is an answer, 304 included (urllib raises it as an HTTPError, like any
    status it does not follow); only a request that got no answer raises
    Unreachable. The timeout is longer than call()'s: a body can be megabytes.
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    url = base_url + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    sent = dict(headers or {})
    if data is not None and content_type:
        sent['Content-Type'] = content_type
    if token:
        sent['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, data=data, headers=sent, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(), _header_dict(response.headers)
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b''
        finally:
            exc.close()  # unclosed -> ResourceWarning; callers never see the object to close it
        return exc.code, raw, _header_dict(exc.headers)
    except Exception as exc:
        raise Unreachable(redacted(str(exc), token)) from None
