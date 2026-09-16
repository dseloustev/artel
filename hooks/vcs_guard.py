"""PreToolUse(Bash|mcp__.*): deny writes aimed at a VCS or tracker platform other than the one
.artel/config.json declares. Always armed -- unlike sensitive_guard.py, which arms only during
an autonomous run.

Contract: docs/config.md (the `guard` section) and
docs/superpowers/specs/2026-09-16-vcs-platform-migration-design.md section 2.
"""
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

READ_VERBS = frozenset('get list search view diff show read whoami status checks'.split())
WRITE_VERBS = frozenset(
    'create update edit delete remove merge decline approve comment add post set review '
    'close reopen ready assign transition'.split())
MUTATING_METHODS = frozenset(('POST', 'PATCH', 'PUT', 'DELETE'))

# Adapter -> the platform it speaks to. config.md sections `vcs` and `tracker`.
VCS_PLATFORM = {'github-cli': 'github', 'bitbucket-mcp': 'bitbucket'}
TRACKER_PLATFORM = {'github-issues': 'github', 'jira-mcp': 'jira'}

# `gh` nouns whose calls belong to the tracker domain, not the VCS one: config.md says `gh` is
# required for issues even when vcs.adapter is not github-cli.
TRACKER_NOUNS = frozenset(('issue',))

# `gh` global flags that take a SEPARATE value token. The value must be skipped along with the
# flag, or `gh --repo owner/repo pr view` reads `owner/repo` as the noun and `pr` as the verb.
GH_VALUE_FLAGS = frozenset(('-R', '--repo'))

_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
_SPLIT = re.compile(r'\|\||&&|;|\||\n')


def _segments(command):
    """Argv lists of the command words in a Bash string, split on ; && || | and newlines,
    with leading VAR=value assignments stripped so `GH_CONFIG_DIR=... gh pr create` is seen."""
    out = []
    for raw in _SPLIT.split(command or ''):
        try:
            argv = shlex.split(raw)
        except ValueError:
            argv = raw.split()  # unbalanced quotes: degrade, never crash
        while argv and _ASSIGNMENT.match(argv[0]):
            argv = argv[1:]
        if argv:
            out.append(argv)
    return out


def _mcp_platform(tool):
    """'bitbucket' | 'github' | 'jira' | None (comment, not annotation: 3.9 compatibility)."""
    low = (tool or '').lower()
    for platform in ('bitbucket', 'github', 'jira'):
        if platform in low:
            return platform
    return None


def _mcp_verb(tool, platform):
    """The first recognized verb among the name's segments after the platform segment.

    Positional and scanning, never substring: `bitbucket_get_pr_comments` is a READ whose name
    contains the write token 'comment', and scanning (rather than taking the next segment) keeps
    `mcp__bitbucket__pr_create` right, where the next segment is the noun `pr`."""
    parts = [p for p in (tool or '').lower().split('_') if p]
    for i, part in enumerate(parts):
        if platform in part:
            for candidate in parts[i + 1:]:
                if candidate in READ_VERBS or candidate in WRITE_VERBS:
                    return candidate
            return None
    return None


def _gh_noun_verb(argv):
    """(noun, verb) from a `gh` argv, ignoring flags and the values of value-taking global
    flags. `gh pr view 12 --json title` -> ('pr', 'view'); `gh --repo o/r pr view` -> ('pr',
    'view'). A noun with no following word -> (noun, None)."""
    args = []
    skip = False
    for token in argv[1:]:
        if skip:
            skip = False
            continue
        if token.startswith('-'):
            skip = token in GH_VALUE_FLAGS
            continue
        args.append(token)
    noun = args[0] if args else ''
    verb = args[1] if len(args) > 1 else None
    return noun, verb


def _gh_api_class(argv):
    """'read' | 'write' | 'unknown' for `gh api ...`, by HTTP method. `gh api` defaults to GET;
    a method that comes from a shell variable cannot be resolved, so it is denied."""
    for i, arg in enumerate(argv):
        method = None
        if arg in ('-X', '--method'):
            method = argv[i + 1] if i + 1 < len(argv) else ''
        elif arg.startswith('--method='):
            method = arg.split('=', 1)[1]
        if method is None:
            continue
        if not method or '$' in method or '`' in method:
            return 'unknown'
        return 'write' if method.upper() in MUTATING_METHODS else 'read'
    return 'read'


def _classify(verb):
    """'read' | 'write' | 'unknown'. Unknown fails closed at the call site."""
    if verb in WRITE_VERBS:
        return 'write'
    if verb in READ_VERBS:
        return 'read'
    return 'unknown'


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason,
    }}))


def _calls(tool, tool_input):
    """[(platform, domain, klass, label)] for one tool invocation; [] when it targets no
    known platform. Cheap enough to run before the config is read."""
    if tool == 'Bash':
        command = tool_input.get('command') or ''
        if 'gh' not in command:
            return []
        found = []
        for argv in _segments(command):
            if argv[0] != 'gh':
                continue
            noun, verb = _gh_noun_verb(argv)
            domain = 'tracker' if noun in TRACKER_NOUNS else 'vcs'
            klass = _gh_api_class(argv) if noun == 'api' else _classify(verb)
            found.append(('github', domain, klass, ' '.join(argv[:3])))
        return found
    if tool.startswith('mcp__'):
        platform = _mcp_platform(tool)
        if platform is None:
            return []
        low = tool.lower()
        domain = 'tracker' if platform == 'jira' or 'issue' in low else 'vcs'
        return [(platform, domain, _classify(_mcp_verb(tool, platform)), tool)]
    return []


def main():
    data = h.read_hook_input()
    calls = _calls(data.get('tool_name') or '', data.get('tool_input') or {})
    if not calls:
        return 0

    config = h.load_config()
    if not config:
        return 0  # no artel config: never the reason a plain `gh` call cannot run

    extra = (config.get('guard') or {}).get('extraReadTools')
    extra = [e for e in extra if isinstance(e, str) and e] if isinstance(extra, list) else []
    native = {
        'vcs': VCS_PLATFORM.get((config.get('vcs') or {}).get('adapter') or 'github-cli'),
        'tracker': TRACKER_PLATFORM.get((config.get('tracker') or {}).get('adapter') or 'none'),
    }
    adapter_name = {
        'vcs': (config.get('vcs') or {}).get('adapter') or 'github-cli',
        'tracker': (config.get('tracker') or {}).get('adapter') or 'none',
    }

    for platform, domain, klass, label in calls:
        if native[domain] is None:
            continue  # adapter "none": no declared home, nothing to protect
        if platform == native[domain]:
            continue
        if any(e.lower() in label.lower() for e in extra):
            continue
        if klass == 'read':
            continue
        deny(
            'Blocked: {} {} to {}, but {}.adapter is "{}".\n'
            "This project's home is {} -- writes to the other platform are denied.\n"
            'If this call is actually a read, add its name to guard.extraReadTools in '
            '.artel/config.json.'.format(
                label,
                'writes' if klass == 'write' else 'has an unrecognized verb and may write',
                platform, domain, adapter_name[domain], native[domain]))
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
