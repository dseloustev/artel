"""bratishka-rules: approvals, buckets, testing-PR detection and merge readiness.

The pure decision layer behind the bratishka duty (docs/bratishka-duty.md): it
turns gh's normalized PR and review dicts (bratishka_gh) into the two approval
booleans, the approval bucket, the board flags, the testing-PR candidates and
the three merge groups. It reads only its arguments -- no `gh`, no dashboard,
no filesystem -- so the CLI and every skill share one verdict.

Approval is one latest review state per login: the review with the greatest
`submitted_at` (ISO 8601 UTC stamps compare lexically, a later entry wins a
tie). `members` is `{'codeReview': set | None, 'qa': set | None}`; `None` means
the team could not be read. An unreadable QA team with no `qaLogins` fallback is
`unknown`, never approved. With no `teams.codeReview` the code review falls back
to any approval that is neither the bot's nor a QA login.

`is_testing_pr` is deliberately broad -- a title matching the configured
template, a title starting with `Testing `, or any branch containing `testing`
-- while creation stays strict on `testingPr`. `merge_candidates` groups only
PRs with both approvals: `CLEAN`/`HAS_HOOKS`/`UNSTABLE` with a `MERGEABLE` PR
are ready, `BEHIND` needs an update, and every other state is blocked with the
reason named.

`classify`'s `flags` are short board tags; this module emits `blocked` for a
fully approved PR that `mergeStateStatus` or `mergeable` still holds back.

Pure library: stdlib only, no CLI, no network. `bratishka_config` is imported
only for the shared testing-PR date templates.

Contract: docs/bratishka-duty.md (`## Approval model`, `## Queue and board`,
`## Testing PR`, `## Merging`), docs/config.md (`bratishka`).
"""
import re
from datetime import datetime

import bratishka_config

# `mergeStateStatus` values that need no update and no other intervention.
READY_STATES = frozenset({'CLEAN', 'HAS_HOOKS', 'UNSTABLE'})

BLOCKED = 'blocked'


def _approved(latest):
    """The logins whose latest review state is `APPROVED`."""
    return {login for login, review in latest.items()
            if str(review.get('state') or '').upper() == 'APPROVED'}


def _logins(config, key):
    """The non-empty string entries of the `config[key]` login list."""
    values = config.get(key) or []
    return {value for value in values if isinstance(value, str) and value}


def _config(value):
    return value if isinstance(value, dict) else {}


def latest_reviews(reviews):
    """`login` -> the review with the latest `submitted_at`.

    ISO 8601 UTC stamps compare lexically. A review with no login cannot vote,
    and when two entries share a timestamp the later one in the list wins,
    because the list is GitHub's chronological order.
    """
    latest = {}
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        login = review.get('login') or ''
        if not login:
            continue
        at = str(review.get('submitted_at') or '')
        current = latest.get(login)
        if current is None or at >= str(current.get('submitted_at') or ''):
            latest[login] = review
    return latest


def _merge_verdict(pr):
    """`(group, reason)` for a PR's merge fields, per docs `## Merging`.

    `group` is `ready`, `needs_update` or `blocked`; `reason` names the field
    that holds the PR back and is empty for the first two groups. Only a
    `MERGEABLE` PR is ready, so an `UNKNOWN` or `CONFLICTING` mergeable state is
    reported instead of merged.
    """
    merge_state = str(pr.get('merge_state') or '')
    mergeable = str(pr.get('mergeable') or '')
    if mergeable != 'MERGEABLE':
        if mergeable == 'CONFLICTING':
            return BLOCKED, 'mergeable CONFLICTING (branch conflicts)'
        return BLOCKED, 'mergeable {}'.format(mergeable or 'UNKNOWN')
    if merge_state in READY_STATES:
        return 'ready', ''
    if merge_state == 'BEHIND':
        return 'needs_update', ''
    return BLOCKED, 'merge state {}'.format(merge_state or 'UNKNOWN')


def classify(pr, reviews, members, config):
    """One PR's approval verdict: the two booleans, its bucket and board flags.

    `bucket` is `qa-approved` (both approvals), `needs-qa` (code review only)
    or `needs-review` (neither, including a `CODEOWNERS` approval from outside
    both sets). `qa_unknown` is True only when the QA team is unreadable and no
    `qaLogins` fallback is configured; `blocked` flags a PR that has both
    approvals but a merge state that still holds it back.
    """
    config = _config(config)
    members = members if isinstance(members, dict) else {}
    approved = _approved(latest_reviews(reviews))
    code_members = members.get('codeReview')
    qa_members = members.get('qa')
    qa_fallback = _logins(config, 'qaLogins')

    if qa_members is not None:
        qa_approved = bool(approved & qa_members)
        qa_unknown = False
    else:
        qa_approved = bool(approved & qa_fallback)
        qa_unknown = not qa_fallback

    if code_members is not None:
        code_reviewed = bool(approved & code_members)
    else:
        excluded = set(qa_fallback)
        if qa_members is not None:
            excluded |= set(qa_members)
        bot = config.get('botLogin') or ''
        if bot:
            excluded.add(bot)
        code_reviewed = any(login not in excluded for login in approved)

    if code_reviewed and qa_approved:
        bucket = 'qa-approved'
    elif code_reviewed:
        bucket = 'needs-qa'
    else:
        bucket = 'needs-review'

    flags = []
    if code_reviewed and qa_approved and _merge_verdict(pr)[0] == BLOCKED:
        flags.append(BLOCKED)

    return {
        'number': pr.get('number'),
        'title': pr.get('title') or '',
        'url': pr.get('url') or '',
        'bucket': bucket,
        'code_reviewed': code_reviewed,
        'qa_approved': qa_approved,
        'qa_unknown': qa_unknown,
        'flags': flags,
    }


def _classified(prs, reviews_by_pr, members, config):
    """`(pr, classify(...))` pairs in the input order."""
    reviews_by_pr = reviews_by_pr if isinstance(reviews_by_pr, dict) else {}
    for pr in prs:
        yield pr, classify(pr, reviews_by_pr.get(pr.get('number'), []), members, config)


def queue(prs, reviews_by_pr, members, config):
    """The classified PRs, one per input PR, in input order.

    The caller passes the repo-scoped queue it already narrowed to the bot's
    open, non-draft, non-testing, non-excluded PRs; this layer only decides each
    PR's approval bucket, so a classified PR carries `number`, `title`, `url`,
    `bucket`, `code_reviewed`, `qa_approved`, `qa_unknown` and `flags`.
    """
    return [item for _, item in _classified(prs, reviews_by_pr, members, config)]


def is_testing_pr(pr, config):
    """Broad testing-PR detection: configured names, the `Testing ` prefix, a branch.

    A PR from before the naming rule still has to be found, so any title
    starting with `Testing ` and any branch containing `testing` counts, as does
    a title or branch that matches the configured templates exactly.
    """
    config = _config(config)
    title = str(pr.get('title') or '')
    head = str(pr.get('head') or '')
    if title.startswith('Testing '):
        return True
    if 'testing' in head.lower():
        return True
    testing = config.get('testingPr')
    if not isinstance(testing, dict):
        return False
    for template, value in ((testing.get('titleTemplate'), title),
                            (testing.get('branchTemplate'), head)):
        if bratishka_config.match_date(template, value) is not None:
            return True
    return False


def is_excluded(pr, config):
    """Whether an `excludeTitlePatterns` entry drops the PR's title.

    An entry is a case-insensitive substring, or a case-insensitive regex when
    prefixed `re:`. An empty entry, or a regex this layer cannot compile, never
    matches.
    """
    config = _config(config)
    title = str(pr.get('title') or '')
    for pattern in config.get('excludeTitlePatterns') or []:
        if not isinstance(pattern, str) or not pattern:
            continue
        if pattern.startswith('re:'):
            expression = pattern[3:]
            if not expression:
                continue
            try:
                if re.search(expression, title, re.IGNORECASE):
                    return True
            except re.error:
                continue
        elif pattern.lower() in title.lower():
            return True
    return False


def pr_date(pr, config):
    """The date a testing PR's name spells, or the fallback to `created_at`.

    `bratishka_config.testing_date` reads the title and then the branch under
    the configured templates; a legacy name with no parseable date falls back to
    `created_at`, whose trailing `Z` is stripped before parsing. An absent or
    unparseable timestamp returns None.
    """
    found = bratishka_config.testing_date(
        str(pr.get('title') or ''), str(pr.get('head') or ''), _config(config))
    if found is not None:
        return found
    created = pr.get('created_at') or ''
    if isinstance(created, str) and created.endswith('Z'):
        created = created[:-1]
    try:
        return datetime.fromisoformat(created).date()
    except (TypeError, ValueError):
        return None


def testing_candidates(prs, reviews_by_pr, members, config):
    """The source PRs a testing PR may bundle: code-reviewed, not QA-approved.

    Draft PRs, testing PRs themselves and titles matched by
    `excludeTitlePatterns` are dropped. QA `unknown` is not approval, so such a
    PR stays a candidate and the caller reports the missing team read.
    """
    candidates = []
    for pr, item in _classified(prs, reviews_by_pr, members, config):
        if pr.get('draft'):
            continue
        if is_testing_pr(pr, config) or is_excluded(pr, config):
            continue
        if item['code_reviewed'] and not item['qa_approved']:
            candidates.append(item)
    return candidates


def merge_candidates(prs, reviews_by_pr, members, config):
    """Split the fully approved PRs into `ready`, `needs_update` and `blocked`.

    A PR needs both a real code review and a real QA approval; `unknown` is
    never approval. Blocked entries carry a `reason` naming the merge state or
    the non-`MERGEABLE` field, so the board can report what `gh` says.
    """
    groups = {'ready': [], 'needs_update': [], 'blocked': []}
    for pr, item in _classified(prs, reviews_by_pr, members, config):
        if not (item['code_reviewed'] and item['qa_approved']):
            continue
        group, reason = _merge_verdict(pr)
        if reason:
            item = dict(item, reason=reason)
        groups[group].append(item)
    return groups
