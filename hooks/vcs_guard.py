"""PreToolUse(Bash|mcp__.*): deny writes aimed at a VCS or tracker platform other than the one
.artel/config.json declares. Always armed -- unlike sensitive_guard.py, which arms only during
an autonomous run.

Contract: docs/config.md (the `guard` section) and
docs/superpowers/specs/2026-09-16-vcs-platform-migration-design.md section 2.
"""
import re
import shlex

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
