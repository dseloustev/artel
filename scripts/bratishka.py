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

Exit codes are the contract's: `0` success, `1` a user-actionable failure
(nothing to do, a refusal), `2` a configuration or environment error. Every
invocation prints one envelope, `{ok, verb, elapsed_ms, data|error}`.

Contract: docs/bratishka-duty.md (`## Queue and board`, `## Testing PR`,
`## Safety and degradation`), docs/config.md (`bratishka`).

Usage: bratishka.py status [--repo <dir>] [--offline] [--json]
       bratishka.py testing-pr-cancel [--repo <dir>] [--delete-branch] [--yes] [--json]
"""
import json
import re
import sys
import time
from datetime import date

import bratishka_config
import bratishka_rules
from bratishka_dashboard import Dashboard
from bratishka_gh import Gh, GhError

# The board's buckets, in rendering order: three approval buckets and two overlays.
BUCKETS = ('qa-approved', 'needs-qa', 'needs-review', 'bot-working', 'blocked')
# The dashboard states that count as live bot work.
LIVE_STATES = ('active', 'queued')
# A source PR link in a testing PR's body, e.g. `https://github.com/o/r/pull/12`.
SOURCE_LINK_RE = re.compile(r'/pull/(\d+)')
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
