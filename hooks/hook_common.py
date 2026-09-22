"""Shared plumbing for the artel hooks. Stdlib only; every helper fails open.

Cwd is the root of the checkout the session works in. hooks.json starts every hook in
$CLAUDE_PROJECT_DIR, which stays on the main checkout when a session enters a linked
worktree; read_hook_input() then moves the process into that worktree (see
enter_session_root). Every hook therefore reads its input before touching `.artel/`.
The plugin root is derived from this file's own location, so the verify subprocess
needs no environment variable.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path('.artel/config.json')
RUN_DIR = Path('.artel/run')
STATE_DIR = RUN_DIR / '.hooks'  # dot-prefixed: can never collide with a ticket dir
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = PLUGIN_ROOT / 'scripts' / 'verify.py'
DEFAULT_TICKET_PATTERN = r'^(?:{projectKey}-)?(\d+)(?:-p?(\d+))?$'


def read_hook_input():
    """The hook's stdin payload ({} when unreadable). Also moves the process into the
    session's checkout root, so call it before anything reads `.artel/`."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    if isinstance(data, dict):
        enter_session_root(data)
    return data


def _git_path(cwd, flag):
    """`git rev-parse --path-format=absolute <flag>` run in cwd, resolved; '' on failure."""
    proc = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--path-format=absolute', flag],
                          capture_output=True, text=True, timeout=10)
    out = proc.stdout.strip() if proc.returncode == 0 else ''
    return str(Path(out).resolve()) if out else ''


def enter_session_root(data):
    """chdir into the linked worktree the session is working in, when that worktree belongs
    to the same repository as the current directory. Anything else -- no `cwd`, the main
    checkout, another repository, any error -- leaves the process where it is."""
    try:
        cwd = data.get('cwd') or ''
        if not cwd or not Path(cwd).is_dir() or Path(cwd).resolve() == Path.cwd().resolve():
            return  # the common case costs no subprocess
        common = _git_path(cwd, '--git-common-dir')
        if not common or _git_path(cwd, '--git-dir') == common:
            return  # not a repository, or the main checkout: stay in $CLAUDE_PROJECT_DIR
        if _git_path(Path.cwd(), '--git-common-dir') != common:
            return  # a different repository
        root = _git_path(cwd, '--show-toplevel')
        if root and root != str(Path.cwd().resolve()):
            os.chdir(root)
    except Exception:
        return


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def is_verifiable(path, config):
    """verify.surface filter: fnmatch globs ('*' crosses '/'), '!'-prefixed patterns exclude.
    A list with only excludes implies '*' as the positive set; key absent -> every file."""
    surface = (config.get('verify') or {}).get('surface')
    if not surface or not isinstance(surface, list):
        return True
    positives = [p for p in surface if isinstance(p, str) and p and not p.startswith('!')]
    negatives = [p[1:] for p in surface if isinstance(p, str) and p.startswith('!')]
    if any(fnmatch.fnmatch(path, n) for n in negatives):
        return False
    if not positives:
        return True
    return any(fnmatch.fnmatch(path, p) for p in positives)


def changed_files(config):
    """Changed files vs HEAD plus the working tree, filtered by verify.surface and existence."""
    files = set()
    try:
        out = subprocess.run(['git', 'diff', '--name-only', 'HEAD'],
                             capture_output=True, text=True, timeout=30)
        files.update(l.strip() for l in out.stdout.splitlines() if l.strip())
        out = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'],
                             capture_output=True, text=True, timeout=30)
        for line in out.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]  # unstripped: XY + space prefix is exactly 3 chars
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]  # rename: keep the new path
            files.add(path.strip())
    except Exception:
        return []
    # Unconditional: hooks' own state under .artel/run/ (and .artel/config.json etc.) must
    # never surface as a changed file, even when the host hasn't gitignored .artel/run/.
    return sorted(f for f in files
                  if not f.startswith('.artel/') and is_verifiable(f, config) and Path(f).is_file())


def run_fast_verify(paths, timeout=240):
    """Run scripts/verify.py --fast as a subprocess; returns (exit_code, envelope_dict)."""
    cmd = [sys.executable or 'python3', str(VERIFY_SCRIPT), '--fast',
           '--timeout', str(timeout)]
    if paths:
        cmd += ['--files', ','.join(paths)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    except Exception:
        return 2, {}
    try:
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return proc.returncode, {}


def finding_keys(envelope):
    keys = set()
    for stage in (envelope.get('data') or {}).get('stages') or []:
        keys.update(stage.get('keys') or [])
    return keys


def relpath_from_tool_input(data):
    """The edited file relative to the checkout root the hook runs in; falls back to the
    payload's `cwd`, then to the path as given."""
    file_path = (data.get('tool_input') or {}).get('file_path') or ''
    for base in (os.getcwd(), data.get('cwd') or ''):
        base = base.rstrip('/')
        if base and file_path.startswith(base + '/'):
            return file_path[len(base) + 1:]
    return file_path


def ticket_matcher(config):
    """Compiles the ticket pattern into a regex and extracts the project key.

    Returns (compiled_regex, project_key), or (None, project_key) if the pattern
    is invalid (on re.error)."""
    ticket_cfg = config.get('ticket') or {}
    project_key = ticket_cfg.get('projectKey') or 'PROJ'
    pattern = ticket_cfg.get('pattern') or DEFAULT_TICKET_PATTERN
    try:
        compiled = re.compile(pattern.replace('{projectKey}', re.escape(project_key)),
                              re.IGNORECASE)
    except re.error:
        return None, project_key
    return compiled, project_key


def canonical_ticket(value, config):
    """Canonical <projectKey>-<number> for any accepted spelling of a ticket ID, or None.

    Accepts 'AW-12', 'aw-12-3', '12' and the like. A phase suffix never reaches the
    result because the pattern's first group is the number alone; the key is
    upper-cased here."""
    compiled, project_key = ticket_matcher(config)
    if compiled is None:
        return None
    match = compiled.match((value or '').strip())
    return '{}-{}'.format(project_key.upper(), match.group(1)) if match else None


def resolve_active_ticket(config):
    """Base ticket ID (canonical <projectKey>-<number>, phase suffix stripped) from
    <specs.dir>/.active_ticket, or None. .artel/run/<TICKET>/ is always ticket-top-level."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    pointer = Path(specs_dir) / '.active_ticket'
    if not pointer.is_file():
        return None
    try:
        lines = pointer.read_text(encoding='utf-8').strip().splitlines()
    except (OSError, ValueError):
        return None
    first = lines[0].strip() if lines else ''
    if not first:
        return None
    return canonical_ticket(first, config)


def ticket_run_dir(ticket):
    return RUN_DIR / ticket
