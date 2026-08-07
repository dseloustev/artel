"""Shared plumbing for the artel hooks. Stdlib only; every helper fails open.

Cwd is always the host repo root (hooks.json prefixes every command with
cd "$CLAUDE_PROJECT_DIR"). The plugin root is derived from this file's own location,
so the verify subprocess needs no environment variable.
"""
import fnmatch
import json
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
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


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
    file_path = (data.get('tool_input') or {}).get('file_path') or ''
    cwd = data.get('cwd') or ''
    if cwd and file_path.startswith(cwd.rstrip('/') + '/'):
        return file_path[len(cwd.rstrip('/')) + 1:]
    return file_path


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
    ticket_cfg = config.get('ticket') or {}
    project_key = ticket_cfg.get('projectKey') or 'PROJ'
    pattern = ticket_cfg.get('pattern') or DEFAULT_TICKET_PATTERN
    try:
        compiled = re.compile(pattern.replace('{projectKey}', re.escape(project_key)),
                              re.IGNORECASE)
    except re.error:
        return None
    match = compiled.match(first)
    if not match:
        return None
    return '{}-{}'.format(project_key.upper(), match.group(1))


def ticket_run_dir(ticket):
    return RUN_DIR / ticket
