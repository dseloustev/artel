"""PostToolUse(Edit|Write|MultiEdit): mirror artel's spec trail into a kartoteka
artifact store. Best-effort by contract — never blocks, never retries."""
import json
import re
import sys
import urllib.request
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
    'review-summary.md', 'qa.md', 'adr.md', 'summary.md',
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


def knowledge_base_url(config):
    """(base_url, error). Adapter off -> (None, None). On but unusable ->
    (None, message): the mirror reports and continues, where vcs would stop
    the run — a pull request cannot be written to disk, but these files are
    already on disk."""
    knowledge = config.get('knowledge') or {}
    if knowledge.get('adapter') != 'kartoteka':
        return None, None
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
    request = urllib.request.Request(
        base_url + '/api/artifacts',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status


def main():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: hooks stay inert
    config = h.load_config()
    base_url, error = knowledge_base_url(config)
    if error:
        log('misconfigured -- ' + error)
        return 0
    if base_url is None:
        return 0
    rel = h.relpath_from_tool_input(h.read_hook_input())
    if not rel:
        return 0
    identity = artifact_identity(rel, config)
    if identity is None:
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
        status = post_artifact(base_url, payload)
        log('ok {} {} {} {}'.format(ticket_key, stage, name, status))
    except Exception as exc:  # fail open: the files on disk are the fallback
        log('fail {} {} -- {}'.format(ticket_key, name, exc))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # fail open
        print('knowledge_mirror hook error (allowing): {}'.format(exc), file=sys.stderr)
        sys.exit(0)
