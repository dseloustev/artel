"""PreToolUse(Bash|mcp__.*): deny writes aimed at a VCS or tracker platform other than the one
.artel/config.json declares. Always armed -- unlike sensitive_guard.py, which arms only during
an autonomous run.

Contract: docs/config.md (the `guard` section) and
docs/superpowers/specs/2026-09-16-vcs-platform-migration-design.md section 2.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

# Read verbs on the `gh` surface, where every one of them is a real subcommand: `gh pr
# status|checks|diff`, `gh repo clone`, `gh pr checkout`, `gh release download`. `fork` and
# `sync` are deliberately NOT here -- both write to the remote.
GH_READ_VERBS = frozenset(
    'get list search view diff show read whoami status checks clone checkout download'.split())
# Read verbs in MCP tool NAMES -- the same set minus three. `status`, `checks` and `diff`
# appear in those names as nouns (`bitbucket_build_status_post`, a real Bitbucket Server
# endpoint; `bitbucket_checks_create`; `bitbucket_diff_comment_add`), and first-match-wins
# would take the noun for the verb and classify those writes as reads.
MCP_READ_VERBS = GH_READ_VERBS - frozenset(('status', 'checks', 'diff'))
WRITE_VERBS = frozenset(
    'create update edit delete remove merge decline approve comment add post set review '
    'close reopen ready assign transition'.split())
MUTATING_METHODS = frozenset(('POST', 'PATCH', 'PUT', 'DELETE'))

# `gh api` flags that add request parameters. From `gh api --help`: "The default HTTP request
# method is GET normally and POST if any parameters were added" -- so one of these with no
# explicit `--method` is a write, not the default GET.
GH_API_FIELD_FLAGS = frozenset(('-f', '--raw-field', '-F', '--field', '--input'))

# Adapter -> the platform it speaks to. config.md sections `vcs` and `tracker`.
VCS_PLATFORM = {'github-cli': 'github', 'bitbucket-mcp': 'bitbucket'}
TRACKER_PLATFORM = {'github-issues': 'github', 'jira-mcp': 'jira'}

# Stands in for an adapter that IS declared but names no recognized value (`"github"`, a
# plausible typo for `"github-cli"`). It equals no platform, so foreign writes in that domain
# are denied: a fail-closed guard must not fail OPEN on a malformed value of the very key it
# enforces. Only an absent tracker section and `tracker.adapter: "none"` unguard a domain.
UNRECOGNIZED_PLATFORM = '<unrecognized>'

# `gh` nouns whose calls belong to the tracker domain, not the VCS one: config.md says `gh` is
# required for issues even when vcs.adapter is not github-cli.
TRACKER_NOUNS = frozenset(('issue',))

# `gh` nouns that belong to the VCS domain. A noun in NEITHER set is not a platform-home
# operation at all (`gh auth`, `gh search`, `gh run`, `gh workflow`, `gh extension`, `gh gist`,
# `gh browse`, `gh config`) and emits no call -- denying those over-blocks reads the design
# promises will keep working. `alias` is here for `gh alias set`: an alias's CREATION can be
# denied even though its later use (`gh nuke ...`) is outside this perimeter -- hooks/README.md
# names that gap rather than pretending it is covered.
VCS_NOUNS = frozenset(('pr', 'repo', 'release', 'api', 'alias'))

# Platforms that can serve only one domain. `github` is deliberately absent: it hosts both pull
# requests and issues, so a github MCP call is judged against BOTH domains (see main()).
PLATFORM_DOMAIN = {'bitbucket': 'vcs', 'jira': 'tracker'}

# Command wrappers skipped when deciding whether a segment invokes `gh`.
COMMAND_WRAPPERS = frozenset((
    'sudo', 'command', 'env', 'nice', 'nohup', 'timeout', 'xargs', 'time', 'stdbuf', 'script'))

# Shells whose `-c` argument is a whole command line of its own (parsed recursively).
SHELL_COMMANDS = frozenset(('bash', 'sh', 'zsh', 'dash', 'ksh'))

# `gh` global flags that take a SEPARATE value token. The value must be skipped along with the
# flag, or `gh --repo owner/repo pr view` reads `owner/repo` as the noun and `pr` as the verb.
GH_VALUE_FLAGS = frozenset(('-R', '--repo'))

_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
# Segment separators. The two-character operators come FIRST in the alternation so `&&` and
# `||` win over `&` and `|`. Parentheses and backticks are separators too: command
# substitution and subshells run their contents as commands in their own right
# (`echo $(gh pr create)`, `` `gh pr create` ``). Splitting there only ever ADDS segments --
# `gh pr create --title "$(cat f)"` still yields the segment that starts with `gh`.
_SPLIT = re.compile(r'\|\||&&|;|\||&|\n|[()`]')
# A bare duration/count operand belonging to a wrapper: `timeout 60 gh ...`, `nice -n 10 gh`.
_WRAPPER_OPERAND = re.compile(r'^\d+(\.\d+)?[smhd]?$')


def _segments(command):
    """Argv lists of the command words in a Bash string, split on ; && || | & newlines and
    command substitution, with leading VAR=value assignments stripped so
    `GH_CONFIG_DIR=... gh pr create` is seen. `bash -c "..."` is parsed recursively."""
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
            out.extend(_shell_c_segments(_strip_wrappers(argv)))
    return out


def _shell_c_segments(argv):
    """The segments hidden inside `bash -c "gh pr create"`, which is one opaque token to the
    splitter above. Terminates: that token is always strictly shorter than its own segment."""
    if not argv or os.path.basename(argv[0]) not in SHELL_COMMANDS:
        return []
    for i in range(1, len(argv) - 1):
        if argv[i] == '-c':
            return _segments(argv[i + 1])
    return []


def _strip_wrappers(argv):
    """Drop leading command wrappers AND the wrapper's own operands, so `sudo gh pr create`,
    `env FOO=1 gh ...`, `timeout 60 gh ...`, `sudo -u ci gh ...`, `nice -n 10 gh ...` and
    `xargs -I{} gh ...` are all still recognized as `gh` calls.

    The wrapped command word itself is never swallowed: a `gh` token ends the scan whatever
    precedes it, and the last token of a segment is always left in place."""
    while argv and os.path.basename(argv[0]) in COMMAND_WRAPPERS:
        argv = argv[1:]
        flag_pending = False  # the previous token was a flag that may take a separate value
        while len(argv) > 1 and os.path.basename(argv[0]) != 'gh':
            token = argv[0]
            if _ASSIGNMENT.match(token):
                flag_pending = False
            elif token.startswith('-'):
                flag_pending = '=' not in token
            elif _WRAPPER_OPERAND.match(token) or flag_pending:
                flag_pending = False
            else:
                break  # an operand that is not the wrapper's: the command being wrapped
            argv = argv[1:]
    return argv


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
                if candidate in MCP_READ_VERBS or candidate in WRITE_VERBS:
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


def _gh_api_method(argv):
    """The HTTP method `gh api ...` will actually use, uppercased, or '' when it cannot be
    resolved (it comes from a shell variable). An explicit method always wins; without one,
    `gh` sends GET normally and POST as soon as any request parameter is added."""
    for i, arg in enumerate(argv):
        method = None
        if arg in ('-X', '--method'):
            method = argv[i + 1] if i + 1 < len(argv) else ''
        elif arg.startswith('--method='):
            method = arg.split('=', 1)[1]
        elif arg.startswith('-X') and len(arg) > 2:
            # The attached shorthand `-XPOST` (and `-X=POST`), which `gh` accepts.
            method = arg[2:]
            if method.startswith('='):
                method = method[1:]
        if method is None:
            continue
        if not method or '$' in method or '`' in method:
            return ''
        return method.upper()
    for arg in argv[1:]:
        if arg in GH_API_FIELD_FLAGS or arg.split('=', 1)[0] in GH_API_FIELD_FLAGS:
            return 'POST'  # "-f" / "-F" / "--input" with no explicit method: gh posts
    return 'GET'


def _gh_api_class(argv):
    """'read' | 'write' | 'unknown' for `gh api ...`, by HTTP method. A method that comes from
    a shell variable cannot be resolved, so it is denied."""
    method = _gh_api_method(argv)
    if not method:
        return 'unknown'
    return 'write' if method in MUTATING_METHODS else 'read'


def _classify(verb, read_verbs=GH_READ_VERBS):
    """'read' | 'write' | 'unknown'. Unknown fails closed at the call site."""
    if verb in WRITE_VERBS:
        return 'write'
    if verb in read_verbs:
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
            argv = _strip_wrappers(argv)
            if not argv or os.path.basename(argv[0]) != 'gh':
                continue
            noun, verb = _gh_noun_verb(argv)
            if noun in TRACKER_NOUNS:
                domain = 'tracker'
            elif noun in VCS_NOUNS:
                domain = 'vcs'
            else:
                continue  # not a platform-home operation
            if noun == 'api':
                # The resolved method, not the verb, is what `gh api` is judged on -- and it
                # is what the deny label has to name, whichever spelling the flag used.
                method = _gh_api_method(argv)
                klass = _gh_api_class(argv)
                label = ('gh api ' + method).strip()  # '' when it cannot be resolved
            else:
                klass = _classify(verb)
                label = ('gh ' + noun + ' ' + (verb or '')).strip()
            found.append(('github', domain, klass, label))
        return found
    # Any non-Bash tool is classified by the platform its NAME carries -- no `mcp__` prefix
    # test. That prefix is a Claude Code convention (where hooks.json's matcher already scopes
    # what arrives); OpenCode names MCP tools without it, and requiring it left the Bitbucket
    # MCP surface unguarded on that host.
    platform = _mcp_platform(tool)
    if platform is None:
        return []
    # 'both' when the platform serves either domain -- main() judges it against both.
    domain = PLATFORM_DOMAIN.get(platform, 'both')
    return [(platform, domain, _classify(_mcp_verb(tool, platform), MCP_READ_VERBS), tool)]


def main():
    data = h.read_hook_input()
    tool_input = data.get('tool_input')
    if not isinstance(tool_input, dict):
        tool_input = {}
    calls = _calls(data.get('tool_name') or '', tool_input)
    if not calls:
        return 0

    config = h.load_config()
    if not config:
        return 0  # no artel config: never the reason a plain `gh` call cannot run

    extra = (config.get('guard') or {}).get('extraReadTools')
    extra = [e for e in extra if isinstance(e, str) and e] if isinstance(extra, list) else []
    adapter_name = {
        'vcs': (config.get('vcs') or {}).get('adapter') or 'github-cli',
        'tracker': (config.get('tracker') or {}).get('adapter') or 'none',
    }
    native = {
        # An unrecognized value keeps the domain ENFORCED (sentinel), never unguards it.
        'vcs': VCS_PLATFORM.get(adapter_name['vcs'], UNRECOGNIZED_PLATFORM),
        'tracker': (None if adapter_name['tracker'] == 'none'
                    else TRACKER_PLATFORM.get(adapter_name['tracker'], UNRECOGNIZED_PLATFORM)),
    }

    for platform, domain, klass, label in calls:
        domains = ('vcs', 'tracker') if domain == 'both' else (domain,)
        if any(platform == native[d] for d in domains):
            continue  # native to a domain that could claim this call
        if all(native[d] is None for d in domains):
            continue  # no declared home in any claiming domain -- nothing to protect
        if klass == 'read':
            continue
        if klass == 'unknown' and any(e.lower() in label.lower() for e in extra):
            continue  # guard.extraReadTools rescues UNRECOGNIZED verbs only, never a write
        blocked = next(d for d in domains if native[d] is not None)
        if native[blocked] == UNRECOGNIZED_PLATFORM:
            home = ('"{}" is not a recognized {}.adapter value, so this project has no known '
                    'home and every platform write is denied. Fix it in '
                    '.artel/config.json.'.format(adapter_name[blocked], blocked))
        else:
            home = ("This project's home is {} -- writes to the other platform are "
                    'denied.'.format(native[blocked]))
        deny(
            'Blocked: {} {} to {}, but {}.adapter is "{}".\n{}\n'
            'If this call is actually a read, add its name to guard.extraReadTools in '
            '.artel/config.json.'.format(
                label,
                'writes' if klass == 'write' else 'has an unrecognized verb and may write',
                platform, blocked, adapter_name[blocked], home))
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
