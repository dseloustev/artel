#!/usr/bin/env python3
"""bratishka: the command-line engine behind the Bratishka duty.

The daily review-day contract (docs/bratishka-duty.md) as one envelope per
invocation: it composes the libraries below into deterministic JSON, so every
skill reads one verdict instead of re-deriving approvals or queue buckets.

`status` is the read-only board: open testing PRs with their date and age, the
queue split into the three approval buckets plus the `blocked` and `bot-working`
overlays, the dashboard's live bot tasks, and next actions that each name the
skill to run. It writes nothing and never merges, closes or comments.

`testing-pr-cancel` is the one write here: it closes the open testing PRs whose
name spells a date before today, leaves a fixed close comment, and never touches
today's, a non-testing or an already closed one.

`testing-pr-plan` and `testing-pr-prompt` are the read-only composition: they
select the source PRs, resolve the base sha, render the testing PR's title and
branch, and compose the #144-shaped body prompt. `testing-pr-prompt --out PATH`
writes the coder prompt and prints its path, the unique run id and the selected
PRs; nothing is posted, closed, merged or pushed.

`testing-pr-verify` closes that loop read-only: it finds the spawned task by the
prompt's `Run id:` marker and today's testing PR by its title or branch (or the
task's `head_ref`), and reports a failed completion gate separately from an
existing PR -- a red coder task is not a failed testing PR.

The fix request is three read steps and one composition: `findings` numbers the
PR's unresolved review threads, `compose-fix` writes the one `/agent` comment
covering the selected findings (one comment, not one per finding -- the bot
serializes its tasks per branch), and `task-find`/`task-watch` correlate and
follow the spawned task. Nothing here posts: the skill posts the composed file
with `gh pr comment`, after its own confirmation.

`merge-plan` is the read half of the morning merge: the bot's open non-draft PRs
minus the testing and excluded ones, their reviews and team membership, split
into `ready`, `needs_update` and `blocked` with `gh`'s reason on a blocked
entry. It merges nothing; the skill updates `BEHIND` branches and squash-merges
one PR at a time.

Exit codes are the contract's: `0` success, `1` a user-actionable failure
(nothing to do, a refusal), `2` a configuration or environment error. Every
invocation prints one envelope, `{ok, verb, elapsed_ms, data|error}`.

Contract: docs/bratishka-duty.md (`## Queue and board`, `## Testing PR`,
`## Requesting a fix`, `## Merging`, `## Safety and degradation`), docs/config.md
(`bratishka`).

Usage: bratishka.py status [--repo <dir>] [--offline] [--json]
       bratishka.py testing-pr-cancel [--repo <dir>] [--delete-branch] [--yes] [--json]
       bratishka.py testing-pr-plan [--repo <dir>] [--include N,...] [--exclude N,...] [--json]
       bratishka.py testing-pr-prompt [--repo <dir>] [--out <path>] [--json]
              [--include N,...] [--exclude N,...]
       bratishka.py testing-pr-verify --run-id <id> [--repo <dir>] [--json]
       bratishka.py findings --pr <n> [--repo <dir>] [--json]
       bratishka.py compose-fix --pr <n> [--repo <dir>] [--select <n,...|none>]
              [--notes <text>] [--out <path>] [--json]
       bratishka.py task-find --pr <n> [--repo <dir>] [--since <iso>] [--json]
       bratishka.py task-watch --task-id <id> [--repo <dir>] [--timeout <s>]
              [--interval <s>] [--json]
       bratishka.py merge-plan [--repo <dir>] [--json]
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import bratishka_config
import bratishka_rules
import bratishka_testing
from bratishka_dashboard import Dashboard
from bratishka_gh import Gh, GhError

# The board's buckets, in rendering order: three approval buckets and two overlays.
BUCKETS = ('qa-approved', 'needs-qa', 'needs-review', 'bot-working', 'blocked')
# The dashboard states that count as live bot work.
LIVE_STATES = ('active', 'queued')
# The dashboard states that end a watched task; anything else is still running.
TERMINAL_STATES = ('completed', 'failed', 'cancelled')
# A source PR link in a testing PR's body, e.g. `https://github.com/o/r/pull/12`.
SOURCE_LINK_RE = re.compile(r'/pull/(\d+)')
# A GitHub suggestion fence inside a review comment: its first code line is the
# expected change. docs/bratishka-duty.md `## Requesting a fix`.
SUGGESTION_RE = re.compile(r'```suggestion[^\n]*\n(.*?)```', re.S)
# The one close comment the cancel path posts, whatever the testing PR bundled.
CANCEL_COMMENT = ('Closed by the duty reviewer: the daily testing PR lives one day '
                  'and is replaced by today\'s.')


class UsageError(Exception):
    """An argv the command cannot parse; reported as `invalid_argument`."""


def envelope(ok, elapsed_ms, verb, data=None, error=None):
    """The one-line JSON envelope every bratishka command prints."""
    out = {'ok': ok, 'verb': verb, 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def fail(kind, message, verb='bratishka', elapsed_ms=0):
    """Print an error envelope and return the exit code `2`."""
    print(envelope(False, elapsed_ms, verb,
                   error={'kind': kind, 'message': message}))
    return 2


def parse_status(args):
    """`status`'s flags: `repo`, `json` and `offline`; unknown flags are refused."""
    options = {'repo': '.', 'json': False, 'offline': False}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--offline':
            options['offline'] = True
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    return options


def parse_merge_plan(args):
    """`merge-plan`'s flags: `repo` and `json`; unknown flags are refused."""
    options = {'repo': '.', 'json': False}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    return options


def parse_cancel(args):
    """`testing-pr-cancel`'s flags: `repo`, `json`, `delete_branch` and `yes`."""
    options = {'repo': '.', 'json': False, 'delete_branch': False, 'yes': False}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--delete-branch':
            options['delete_branch'] = True
        elif arg == '--yes':
            options['yes'] = True
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    return options


def parse_number(value, flag):
    """One PR or seconds value; a non-number is refused."""
    try:
        return int(value)
    except ValueError:
        raise UsageError('{} expects a number: {!r}'.format(flag, value))


def parse_numbers(value, flag):
    """A comma-separated PR-number list, e.g. `1,3`; a non-number is refused."""
    numbers = []
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        numbers.append(parse_number(part, flag))
    return numbers


def parse_selection(args, allow_out=False):
    """The shared `testing-pr-plan`/`testing-pr-prompt` flags.

    Both accept `repo`, `json`, `include` and `exclude`; the prompt adds `out`.
    """
    options = {'repo': '.', 'json': False, 'include': [], 'exclude': []}
    if allow_out:
        options['out'] = ''
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg in ('--include', '--exclude'):
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for {} <n,...>'.format(arg))
            key = 'include' if arg == '--include' else 'exclude'
            options[key].extend(parse_numbers(args[i], arg))
        elif allow_out and arg == '--out':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --out <path>')
            options['out'] = args[i]
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    return options


def parse_plan(args):
    """`testing-pr-plan`'s flags: `repo`, `json`, `include` and `exclude`."""
    return parse_selection(args)


def parse_prompt(args):
    """`testing-pr-prompt`'s flags: `repo`, `json`, `out`, `include` and `exclude`."""
    return parse_selection(args, allow_out=True)


def parse_verify(args):
    """`testing-pr-verify`'s flags: `repo`, `json` and the required `run_id`."""
    options = {'repo': '.', 'json': False, 'run_id': ''}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--run-id':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --run-id <id>')
            options['run_id'] = args[i]
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    if not options['run_id']:
        raise UsageError('missing --run-id <id>')
    return options


def parse_findings(args):
    """`findings`'s flags: `repo`, `json` and the required `pr`."""
    options = {'repo': '.', 'json': False, 'pr': None}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--pr':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --pr <n>')
            options['pr'] = parse_number(args[i], '--pr')
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    if options['pr'] is None:
        raise UsageError('missing --pr <n>')
    return options


def parse_compose_fix(args):
    """`compose-fix`'s flags: `repo`, `json`, `pr`, `select`, `notes` and `out`.

    `--select` takes the numbers `findings` printed; `none` composes from the
    notes alone, and an omitted `--select` selects every finding.
    """
    options = {'repo': '.', 'json': False, 'pr': None, 'select': None,
               'notes': '', 'out': ''}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--pr':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --pr <n>')
            options['pr'] = parse_number(args[i], '--pr')
        elif arg == '--select':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --select <n,...|none>')
            if args[i].strip().lower() == 'none':
                options['select'] = []
            else:
                options['select'] = parse_numbers(args[i], '--select')
        elif arg == '--notes':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --notes <text>')
            options['notes'] = args[i]
        elif arg == '--out':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --out <path>')
            options['out'] = args[i]
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    if options['pr'] is None:
        raise UsageError('missing --pr <n>')
    return options


def parse_task_find(args):
    """`task-find`'s flags: `repo`, `json`, the required `pr` and `since`."""
    options = {'repo': '.', 'json': False, 'pr': None, 'since': ''}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--pr':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --pr <n>')
            options['pr'] = parse_number(args[i], '--pr')
        elif arg == '--since':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --since <iso>')
            options['since'] = args[i]
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    if options['pr'] is None:
        raise UsageError('missing --pr <n>')
    return options


def parse_task_watch(args):
    """`task-watch`'s flags: `repo`, `json`, `task_id`, `timeout` and `interval`."""
    options = {'repo': '.', 'json': False, 'task_id': '', 'timeout': 900,
               'interval': 30}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--repo':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --repo <dir>')
            options['repo'] = args[i]
        elif arg == '--json':
            options['json'] = True
        elif arg == '--task-id':
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for --task-id <id>')
            options['task_id'] = args[i]
        elif arg in ('--timeout', '--interval'):
            i += 1
            if i >= len(args) or args[i] == '':
                raise UsageError('missing value for {} <s>'.format(arg))
            key = 'timeout' if arg == '--timeout' else 'interval'
            options[key] = parse_number(args[i], arg)
        else:
            raise UsageError('unknown flag: {}'.format(arg))
        i += 1
    if not options['task_id']:
        raise UsageError('missing --task-id <id>')
    if options['timeout'] < 0:
        raise UsageError('--timeout must be zero or more seconds')
    if options['interval'] < 0:
        raise UsageError('--interval must be zero or more seconds')
    return options


def team_members(gh, org, slug):
    """The team's member logins, or None when it is unset or unreadable.

    An empty `teams.*` slug is None without a `gh` call; a failed read (a
    missing team, no `read:org`) is the rules layer's `unknown`, never a stop.
    """
    if not slug:
        return None
    try:
        return gh.team_members(org, slug)
    except GhError:
        return None


def source_numbers(body, own_number):
    """The PR numbers linked in a testing PR's body, oldest link first, deduped."""
    numbers = []
    for match in SOURCE_LINK_RE.finditer(body or ''):
        number = int(match.group(1))
        if number == own_number or number in numbers:
            continue
        numbers.append(number)
    return numbers


def testing_pr(pr, config, open_numbers, today):
    """One testing PR's board record: date, age and its merged-or-closed sources."""
    found = bratishka_rules.pr_date(pr, config)
    age_days = max(0, (today - found).days) if found is not None else None
    sources = source_numbers(pr.get('body'), pr.get('number'))
    return {
        'number': pr.get('number'),
        'title': pr.get('title') or '',
        'url': pr.get('url') or '',
        'head': pr.get('head') or '',
        'date': found.isoformat() if found is not None else None,
        'age_days': age_days,
        'source_merged': sum(1 for number in sources if number not in open_numbers),
    }


def next_actions(testing_prs, queue, today):
    """Short actions that each name the skill to run, built from the board."""
    actions = []
    stamp = today.isoformat()
    for item in testing_prs:
        if item['date'] is not None and item['date'] < stamp:
            actions.append('Close the stale testing PR #{} — '
                           '/artel-bratishka-cancel-testing-pr'.format(item['number']))
    if queue['qa-approved']:
        actions.append('Merge {} QA-approved PR(s) — '
                       '/artel-bratishka-merge-approved'.format(len(queue['qa-approved'])))
    has_today = any(item['date'] == stamp for item in testing_prs)
    if queue['needs-qa'] and not has_today:
        actions.append("Bundle {} reviewed PR(s) into today's testing PR — "
                       '/artel-bratishka-testing-pr'.format(len(queue['needs-qa'])))
    if queue['needs-review']:
        actions.append('Review {} PR(s) awaiting code review — '
                       '/artel-collaborative-review'.format(len(queue['needs-review'])))
    if queue['blocked']:
        actions.append('{} approved PR(s) are blocked by GitHub — '
                       '/artel-bratishka-merge-approved reports the reason'.format(
                           len(queue['blocked'])))
    return actions


def build_status(config, gh, dashboard, offline=False):
    """The status board's data: testing PRs, queue, live tasks and next actions.

    The queue is narrowed here, as the rules layer expects: open, non-draft bot
    PRs, minus testing PRs and `excludeTitlePatterns` matches. A PR keeps its one
    approval bucket and may also appear in the `blocked` or `bot-working` overlay.
    `dashboard` may be None (unconfigured, or injected as off); it is never called
    when `offline` is true.
    """
    config = config if isinstance(config, dict) else {}
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    base = config.get('baseRef') or gh.default_branch(repo)
    bot_login = config.get('botLogin') or ''
    org = repo.split('/')[0]
    today = date.today()

    open_prs = gh.open_prs(repo, bot_login)
    open_numbers = {pr.get('number') for pr in open_prs}
    testing_prs = [testing_pr(pr, config, open_numbers, today)
                   for pr in open_prs
                   if bratishka_rules.is_testing_pr(pr, config)]

    queue_prs = [pr for pr in open_prs
                 if not pr.get('draft')
                 and not bratishka_rules.is_testing_pr(pr, config)
                 and not bratishka_rules.is_excluded(pr, config)]
    reviews = {pr.get('number'): gh.reviews(repo, pr.get('number'))
               for pr in queue_prs}
    teams = config.get('teams') if isinstance(config.get('teams'), dict) else {}
    members = {'codeReview': team_members(gh, org, teams.get('codeReview')),
               'qa': team_members(gh, org, teams.get('qa'))}
    classified = bratishka_rules.queue(queue_prs, reviews, members, config)
    queue = {name: [] for name in BUCKETS}
    for item in classified:
        queue[item['bucket']].append(item)
        if bratishka_rules.BLOCKED in item.get('flags', []):
            queue['blocked'].append(item)

    live_tasks = []
    errors = []
    if dashboard is not None and not offline:
        seen = set()
        for state in LIVE_STATES:
            for task in dashboard.tasks(repo=repo, status=state):
                if not isinstance(task, dict):
                    continue
                key = task.get('task_id')
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                record = dict(task)
                record.setdefault('status', state)
                live_tasks.append(record)
        errors = [str(message) for message in (getattr(dashboard, 'errors', None) or [])]
    for item in classified:
        needle = 'PR {}#{}'.format(repo, item.get('number'))
        if any(needle in (task.get('description') or '') for task in live_tasks):
            item['bot_working'] = True
            queue['bot-working'].append(item)

    if offline:
        dashboard_state = {'online': False, 'reason': 'offline', 'errors': []}
    elif dashboard is None:
        dashboard_state = {'online': False, 'reason': 'not configured', 'errors': []}
    else:
        dashboard_state = {'online': not errors,
                           'reason': '' if not errors else 'unreachable',
                           'errors': errors}

    return {
        'repo': repo,
        'base': base,
        'testing_prs': testing_prs,
        'queue': queue,
        'live_tasks': live_tasks,
        'dashboard': dashboard_state,
        'next_actions': next_actions(testing_prs, queue, today),
    }


def cancel_plan(config, gh, today=None):
    """The stale testing PRs to close, oldest first, and how many were kept aside.

    Every open PR is read, not just the bot's: a testing PR from before the
    naming rule may carry another author. A PR is a candidate when the broad
    detection accepts it and its name-or-`created_at` date is before `today`.
    Today's, later-dated and undated testing PRs are never candidates; they are
    counted in `skip_today` instead. A merged or closed PR is never one either.
    """
    config = config if isinstance(config, dict) else {}
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    today = today or date.today()
    candidates = []
    skip_today = 0
    for pr in gh.open_prs(repo):
        if str(pr.get('state') or 'OPEN').upper() != 'OPEN':
            continue
        if not bratishka_rules.is_testing_pr(pr, config):
            continue
        found = bratishka_rules.pr_date(pr, config)
        if found is None or found >= today:
            skip_today += 1
            continue
        candidates.append({
            'number': pr.get('number'),
            'title': pr.get('title') or '',
            'url': pr.get('url') or '',
            'head': pr.get('head') or '',
            'date': found.isoformat(),
            'age_days': (today - found).days,
        })
    candidates.sort(key=lambda item: (item['date'], item['number'] or 0))
    return {'repo': repo, 'candidates': candidates, 'skip_today': skip_today}


def cancel_apply(config, gh, candidates, delete_branch=False):
    """Close every candidate with the fixed comment; collect the failures.

    One failing `gh pr close` never stops the rest: the result names what was
    closed and what failed, so a partial run is reported instead of hidden.
    """
    config = config if isinstance(config, dict) else {}
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    closed = []
    failed = []
    for item in candidates:
        try:
            gh.close_pr(repo, item.get('number'), comment=CANCEL_COMMENT,
                        delete_branch=delete_branch)
        except GhError as exc:
            failed.append({'number': item.get('number'), 'kind': exc.kind,
                           'message': exc.message})
            continue
        closed.append(item)
    return {'repo': repo, 'closed': closed, 'failed': failed}


def run_cancel(config, gh, options, elapsed):
    """Plan, ask or apply the testing-PR cancel; returns the exit code."""
    verb = 'bratishka-testing-pr-cancel'
    delete_branch = bool(options['delete_branch']
                         or (config.get('testingPr') or {}).get('deleteBranchOnCancel'))
    try:
        plan = cancel_plan(config, gh)
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    candidates = plan['candidates']
    if not candidates:
        print(envelope(False, elapsed(), verb, error={
            'kind': 'nothing_to_do',
            'message': 'no testing PR older than today',
            'repo': plan['repo'],
            'skip_today': plan['skip_today']}))
        return 1
    if not options['yes']:
        print(envelope(False, elapsed(), verb, error={
            'kind': 'confirmation_required',
            'message': 'close {} testing PR(s) older than today'.format(len(candidates)),
            'confirmation_required': True,
            'repo': plan['repo'],
            'delete_branch': delete_branch,
            'candidates': candidates,
            'skip_today': plan['skip_today']}))
        return 1
    try:
        result = cancel_apply(config, gh, candidates, delete_branch)
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    if result['failed']:
        print(envelope(False, elapsed(), verb, error={
            'kind': 'partial_failure',
            'message': '{} of {} testing PR(s) could not be closed'.format(
                len(result['failed']), len(candidates)),
            'closed': result['closed'],
            'failed': result['failed'],
            'skip_today': plan['skip_today']}))
        return 1
    print(envelope(True, elapsed(), verb, data={
        'repo': result['repo'],
        'closed': result['closed'],
        'skip_today': plan['skip_today']}))
    return 0


def run_plan(config, gh, options, elapsed):
    """Compose and print the testing-PR plan; returns the exit code."""
    verb = 'bratishka-testing-pr-plan'
    try:
        data = bratishka_testing.plan(config, gh, include=options['include'],
                                      exclude=options['exclude'])
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def run_prompt(config, gh, options, elapsed):
    """Write the coder prompt, or return it, and print its path and run id."""
    verb = 'bratishka-testing-pr-prompt'
    try:
        data = bratishka_testing.plan(config, gh, include=options['include'],
                                      exclude=options['exclude'])
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    if not options['out']:
        print(envelope(True, elapsed(), verb, data={
            'prompt_path': None, 'run_id': data['run_id'], 'tasks': data['tasks'],
            'prompt': data['prompt']}))
        return 0
    target = Path(options['out'])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data['prompt'] + '\n', encoding='utf-8')
    except OSError as exc:
        return fail('failed', 'could not write the prompt: {}'.format(exc),
                    verb=verb, elapsed_ms=elapsed())
    print(envelope(True, elapsed(), verb, data={
        'prompt_path': str(target), 'run_id': data['run_id'], 'tasks': data['tasks']}))
    return 0


def testing_name(config, key, day):
    """A testing-PR title or branch rendered from the config, default included."""
    testing = config.get('testingPr') if isinstance(config, dict) else None
    template = testing.get(key) if isinstance(testing, dict) else None
    return bratishka_config.render(
        template or bratishka_config.DEFAULTS['testingPr'][key], day)


def verify_testing_pr(config, gh, dashboard, run_id, day=None):
    """The spawned task and today's testing PR, with a red gate kept separate.

    The task is found by the prompt's `Run id:` marker; the PR by today's
    rendered title or branch name, falling back to the task's `head_ref` when
    the dashboard reports one. `gate_failed` is true only when the task's
    completion gate failed and a PR exists: the PR is the artifact, the gate is
    a separate finding. A missing task and a missing PR are ordinary answers,
    never errors. `day` defaults to the local date and pins the names in a test.
    """
    config = config if isinstance(config, dict) else {}
    day = day or date.today()
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    task = dashboard.find_task(run_id) if dashboard is not None else None
    title = testing_name(config, 'titleTemplate', day)
    branch = testing_name(config, 'branchTemplate', day)
    open_prs = gh.open_prs(repo)
    pr = None
    for item in open_prs:
        if item.get('title') == title or item.get('head') == branch:
            pr = item
            break
    if pr is None and isinstance(task, dict) and task.get('head_ref'):
        for item in open_prs:
            if item.get('head') == task.get('head_ref'):
                pr = item
                break
    detail = task.get('result_detail') if isinstance(task, dict) else None
    error = detail.get('error') if isinstance(detail, dict) else None
    return {
        'run_id': run_id,
        'task': task,
        'pr': pr,
        'gate_failed': pr is not None and error == 'final_tools_gate_not_satisfied',
    }


def run_verify(config, gh, dashboard, options, elapsed):
    """Find the spawned task and today's testing PR; returns the exit code."""
    verb = 'bratishka-testing-pr-verify'
    try:
        data = verify_testing_pr(config, gh, dashboard, options['run_id'])
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def merge_plan_data(config, gh):
    """The fully approved PRs split into `ready`, `needs_update` and `blocked`.

    The queue is narrowed here, as the rules layer expects: the bot's open,
    non-draft PRs minus the testing PRs and `excludeTitlePatterns` matches. Only
    a PR with both a real code review and a real QA approval reaches
    `bratishka_rules.merge_candidates`; an unreadable team is `unknown`, never
    approval. Nothing is updated or merged -- the skill performs the writes.
    """
    config = config if isinstance(config, dict) else {}
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    bot_login = config.get('botLogin') or ''
    org = repo.split('/')[0]
    candidates = [pr for pr in gh.open_prs(repo, bot_login)
                  if not pr.get('draft')
                  and not bratishka_rules.is_testing_pr(pr, config)
                  and not bratishka_rules.is_excluded(pr, config)]
    reviews = {pr.get('number'): gh.reviews(repo, pr.get('number'))
               for pr in candidates}
    teams = config.get('teams') if isinstance(config.get('teams'), dict) else {}
    members = {'codeReview': team_members(gh, org, teams.get('codeReview')),
               'qa': team_members(gh, org, teams.get('qa'))}
    return bratishka_rules.merge_candidates(candidates, reviews, members, config)


def run_merge_plan(config, gh, options, elapsed):
    """Print the three merge groups; returns the exit code."""
    verb = 'bratishka-merge-plan'
    try:
        data = merge_plan_data(config, gh)
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    config = config if isinstance(config, dict) else {}
    data['repo'] = config.get('repo') or gh.repo_slug()
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def findings_data(config, gh, number):
    """The PR's unresolved review threads, numbered from 1.

    A resolved thread is never a finding: it was fixed or dismissed, and asking
    the bot to act on it again would undo that decision. `index` is the number the
    skill shows and `compose-fix --select` takes.
    """
    config = config if isinstance(config, dict) else {}
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    threads = []
    for thread in gh.review_threads(repo, number):
        if not isinstance(thread, dict) or thread.get('resolved'):
            continue
        record = dict(thread)
        record['index'] = len(threads) + 1
        threads.append(record)
    return {'repo': repo, 'number': number, 'threads': threads}


def suggestion_text(body):
    """The first line of a GitHub `suggestion` fence in `body`, or ''."""
    match = SUGGESTION_RE.search(body or '')
    if not match:
        return ''
    for line in match.group(1).splitlines():
        if line.strip():
            return line.strip()
    return ''


def finding_text(thread):
    """A review comment flattened to one line, its suggestion fence removed."""
    body = SUGGESTION_RE.sub('', thread.get('body') or '')
    return ' '.join(body.split()) or '(the bot comment has no text)'


def expected_text(thread):
    """The first line of the bot's suggested change, or ''."""
    suggestion = thread.get('suggestion')
    if isinstance(suggestion, str) and suggestion.strip():
        return ' '.join(suggestion.split())
    return suggestion_text(thread.get('body'))


def compose_fix_body(threads, selected, notes, config):
    """One `/agent` comment for the selected findings, plus extra notes.

    One comment, not one per finding: the bot serializes `/agent` tasks per
    branch, so N comments queue N runs against the same branch
    (docs/bratishka-duty.md `## Requesting a fix`). `selected` holds 0-based
    positions into `threads`; None means every finding.
    """
    config = config if isinstance(config, dict) else {}
    command = config.get('fixCommand') or '/agent'
    positions = range(len(threads)) if selected is None else list(selected)
    notes = ' '.join((notes or '').split())
    lines = [
        command + ' Fix the review findings on this pull request.',
        '',
        'Scope: change only what the findings below name; leave unrelated code alone.',
        '',
        "Before pushing, run the repository's verification commands "
        '(`verify.commands`) and report the result.',
    ]
    numbered = []
    for position in positions:
        thread = threads[position]
        path = thread.get('path') or ''
        line = thread.get('line')
        location = '`{}:{}`'.format(path, line) if line is not None else '`{}`'.format(path)
        item = '{}. {} — {}'.format(len(numbered) + 1, location, finding_text(thread))
        expected = expected_text(thread)
        if expected:
            item += ' Expected: {}'.format(expected)
        numbered.append(item)
    if numbered:
        lines.append('')
        lines.extend(numbered)
    if notes:
        lines.append('')
        lines.append('Additional notes: {}'.format(notes))
    return '\n'.join(lines)


def newest_task(tasks):
    """The task with the greatest `created_at`; ISO 8601 UTC stamps sort lexically."""
    if not tasks:
        return None
    return max(tasks, key=lambda task: str(task.get('created_at') or ''))


def task_find_data(config, gh, dashboard, number, since=None):
    """The coder task already working this PR, and how it was matched.

    The PR marker in the description is the primary key; when the dashboard does
    not carry it, a task whose `head_ref` is the PR's head and whose `created_at`
    is at or after `since` is the fallback. `match` is `description`, `head_ref`
    or `none`; `dashboard_online` says whether the dashboard could be read.
    """
    config = config if isinstance(config, dict) else {}
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    errors = [str(message) for message in (getattr(dashboard, 'errors', None) or [])]
    data = {
        'repo': repo,
        'number': number,
        'task': None,
        'match': 'none',
        'dashboard_online': dashboard is not None and not errors,
        'dashboard_errors': errors,
    }
    if dashboard is None:
        return data
    initiator = gh.current_login() or None
    task = dashboard.find_task_for_pr(repo, number, initiator=initiator, since=since)
    if task is not None:
        data['task'] = task
        data['match'] = 'description'
    else:
        candidates = dashboard.tasks(repo=repo, initiator=initiator)
        head = ''
        if candidates:
            head = (gh.pr(repo, number) or {}).get('head') or ''
        matches = []
        for item in candidates:
            if not isinstance(item, dict) or item.get('head_ref') != head:
                continue
            if since is not None and (item.get('created_at') or '') < since:
                continue
            matches.append(item)
        found = newest_task(matches) if head else None
        if found is not None:
            data['task'] = found
            data['match'] = 'head_ref'
    errors = [str(message) for message in (getattr(dashboard, 'errors', None) or [])]
    data['dashboard_online'] = not errors
    data['dashboard_errors'] = errors
    return data


def task_watch_data(dashboard, task_id, timeout=900, interval=30,
                    sleep=None, clock=None):
    """Poll one task until a terminal status or `timeout` seconds.

    `terminal` is true only for `completed`, `failed` or `cancelled`; a timeout
    is `timed_out` with the last task seen, never a failure. `sleep` and `clock`
    are injection points for a test.
    """
    if dashboard is None:
        return {'task': None, 'terminal': False, 'timed_out': False}
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + max(0, timeout)
    task = None
    while True:
        found = dashboard.task(task_id)
        if isinstance(found, dict):
            task = found
        if ((task or {}).get('status') or '') in TERMINAL_STATES:
            return {'task': task, 'terminal': True, 'timed_out': False}
        remaining = deadline - clock()
        if remaining <= 0:
            return {'task': task, 'terminal': False, 'timed_out': True}
        sleep(min(max(0, interval), remaining))


def run_findings(config, gh, options, elapsed):
    """List the PR's unresolved review threads; returns the exit code."""
    verb = 'bratishka-findings'
    try:
        data = findings_data(config, gh, options['pr'])
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def run_compose_fix(config, gh, options, elapsed):
    """Compose the one fix comment, optionally writing it; returns the exit code."""
    verb = 'bratishka-compose-fix'
    try:
        found = findings_data(config, gh, options['pr'])
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    threads = found['threads']
    if options['select'] is None:
        positions = list(range(len(threads)))
    else:
        positions = []
        for number in options['select']:
            if number < 1 or number > len(threads):
                return fail('invalid_argument',
                            '--select {} is out of range; {} finding(s)'.format(
                                number, len(threads)),
                            verb=verb, elapsed_ms=elapsed())
            if number - 1 not in positions:
                positions.append(number - 1)
    if not positions and not options['notes'].strip():
        print(envelope(False, elapsed(), verb, error={
            'kind': 'nothing_to_do',
            'message': 'no finding is selected and no notes were given'}))
        return 1
    body = compose_fix_body(threads, positions, options['notes'], config)
    data = {
        'repo': found['repo'],
        'number': found['number'],
        'selected': [threads[position]['index'] for position in positions],
        'body': body,
        'body_path': None,
    }
    if options['out']:
        target = Path(options['out'])
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body + '\n', encoding='utf-8')
        except OSError as exc:
            return fail('failed', 'could not write the fix comment: {}'.format(exc),
                        verb=verb, elapsed_ms=elapsed())
        data['body_path'] = str(target)
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def run_task_find(config, gh, dashboard, options, elapsed):
    """Correlate the PR with its coder task; returns the exit code."""
    verb = 'bratishka-task-find'
    try:
        data = task_find_data(config, gh, dashboard, options['pr'],
                              since=options['since'] or None)
    except GhError as exc:
        return fail(exc.kind, exc.message, verb=verb, elapsed_ms=elapsed())
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def run_task_watch(dashboard, options, elapsed):
    """Follow one dashboard task to a terminal status; returns the exit code."""
    verb = 'bratishka-task-watch'
    if dashboard is None:
        return fail('missing_config',
                    'bratishka.dashboardUrl is not set; task-watch needs the dashboard',
                    verb=verb, elapsed_ms=elapsed())
    data = task_watch_data(dashboard, options['task_id'],
                           timeout=options['timeout'], interval=options['interval'])
    data['task_id'] = options['task_id']
    print(envelope(True, elapsed(), verb, data=data))
    return 0


def main(argv=None, gh=None, dashboard=None):
    """Dispatch one bratishka command and print its envelope; returns the exit code."""
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return fail('invalid_argument', 'missing command')
    command = args[0]
    if command == 'status':
        parser = parse_status
    elif command == 'testing-pr-cancel':
        parser = parse_cancel
    elif command == 'testing-pr-plan':
        parser = parse_plan
    elif command == 'testing-pr-prompt':
        parser = parse_prompt
    elif command == 'testing-pr-verify':
        parser = parse_verify
    elif command == 'findings':
        parser = parse_findings
    elif command == 'compose-fix':
        parser = parse_compose_fix
    elif command == 'task-find':
        parser = parse_task_find
    elif command == 'task-watch':
        parser = parse_task_watch
    elif command == 'merge-plan':
        parser = parse_merge_plan
    else:
        return fail('invalid_argument', 'unknown command: {}'.format(command))

    try:
        options = parser(args[1:])
    except UsageError as exc:
        return fail('invalid_argument', str(exc))

    try:
        config = bratishka_config.load(options['repo'])
    except bratishka_config.ConfigError as exc:
        return fail(exc.kind, exc.message)

    client = gh if gh is not None else Gh(cwd=options['repo'])
    if command == 'testing-pr-cancel':
        return run_cancel(config, client, options, elapsed)
    if command == 'testing-pr-plan':
        return run_plan(config, client, options, elapsed)
    if command == 'testing-pr-prompt':
        return run_prompt(config, client, options, elapsed)
    if command == 'testing-pr-verify':
        dash = dashboard
        if dash is None and config['dashboardUrl']:
            dash = Dashboard(config['dashboardUrl'])
        return run_verify(config, client, dash, options, elapsed)
    if command == 'findings':
        return run_findings(config, client, options, elapsed)
    if command == 'compose-fix':
        return run_compose_fix(config, client, options, elapsed)
    if command == 'merge-plan':
        return run_merge_plan(config, client, options, elapsed)
    if command in ('task-find', 'task-watch'):
        dash = dashboard
        if dash is None and config['dashboardUrl']:
            dash = Dashboard(config['dashboardUrl'])
        if command == 'task-find':
            return run_task_find(config, client, dash, options, elapsed)
        return run_task_watch(dash, options, elapsed)

    dash = dashboard
    if dash is None and not options['offline'] and config['dashboardUrl']:
        dash = Dashboard(config['dashboardUrl'])

    try:
        data = build_status(config, client, dash, offline=options['offline'])
    except GhError as exc:
        return fail(exc.kind, exc.message, verb='bratishka-status',
                    elapsed_ms=elapsed())
    print(envelope(True, elapsed(), 'bratishka-status', data=data))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(envelope(False, 0, 'bratishka',
                       error={'kind': 'internal_error', 'message': str(exc)}))
        sys.exit(2)
