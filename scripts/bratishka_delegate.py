"""bratishka-delegate: the batch grammar behind delegating tickets to the bot.

The engine holds no tracker client: the tracker is reached only through the host's
MCP tools, which only a skill can call. So the skill gathers the facts
(`jira_get_issue`, `jira_get_transitions`) and this module decides from them --
which key is usable, which status move each one needs, what prompt each task
gets, and what the report says. One task, one branch and one pull request per
issue, all of them independent.

Every requested key ends in exactly one outcome (`OUTCOMES`), so a key can never
be dropped silently and one bad key never cancels a batch.

Pure library: no CLI, no network, no config loading, no third-party imports.

Contract: docs/bratishka-duty.md (`## Delegating a ticket`), docs/tracker-status.md
(the status resolution), docs/config.md (`bratishka.delegate`).
"""
import re

import bratishka_testing

# The outcome vocabulary; the report renders one row per requested key.
OUTCOMES = ('ready', 'invalid', 'needs-decision', 'blocked', 'rejected',
            'spawn-failed', 'failed', 'completed')
# The two moves one issue needs, in order.
INTENTS = ('start', 'handoff')
# A transition whose name reads like a move backwards needs confirming.
ROLLBACK_RE = re.compile(r'^(back|reopen|return|revert)\b', re.IGNORECASE)
ROLLBACK_WORDS = ('вернуть', 'назад', 'открыть заново')

# The alias lists of docs/tracker-status.md, in that file's precedence order.
INTENT_ALIASES = {
    'start': ('in progress', 'in development', 'in work', 'doing', 'start progress',
              'start work', 'в работе', 'в процессе', 'в разработке', 'начато',
              'выполняется'),
    'handoff': ('code review', 'in review', 'ready for review', 'ready for code review',
                'review', 'pr review', 'на ревью', 'на код-ревью', 'код-ревью', 'ревью',
                'на проверке', 'на рассмотрении'),
}

# A delegated task's prompt: self-contained, because the spawned session cannot
# see the chat. Placeholders are filled through `bratishka_testing.render_override`.
PROMPT_TEMPLATE = '''\
Implement ticket {key}: {summary}.

Context
- Repository: {repo}, base branch {base_ref}.
- Ticket: {url}

Description (verbatim from the tracker)
{description}

Constraints
- Fix the root cause, not the symptom: trace the reported problem to the defect and fix it
  at its origin. A guard, a retry or a UI patch that hides the symptom is not a fix. When the
  root cause cannot be fixed in this change, describe it in the pull request with the
  evidence, the reason it cannot be fixed here, and the follow-up; label the shipped change a
  mitigation.
- Create the branch `{branch}` and open the pull request with the title `{title}`.
- Fill the pull-request description from the repository's own template, in {language}: keep its
  headings, order and checklists, and replace every placeholder with real content.
- Request {reviewer} as a reviewer. Do not set an assignee on the pull request.
- Follow the repository's `AGENTS.md` and its documented conventions.
- Keep the change scoped to this ticket; no drive-by refactors.
- Run the repository's documented verification commands before pushing.
- Do not wait for CI after opening the pull request.

If anything in the ticket is ambiguous, pick the most reasonable reading, implement it, and state
the assumption in the pull-request description. Do not stop to ask questions.
'''


def parse_keys(values):
    """Every key in `values`, deduped, in the order the person gave them."""
    keys = []
    seen = set()
    for value in values or []:
        for token in re.split(r'[\s,;]+', str(value or '')):
            token = token.strip().upper()
            if token and token not in seen:
                seen.add(token)
                keys.append(token)
    return keys


def idempotency_key(key, day=None):
    """`<KEY>:agent`; a date suffix only for a deliberate second run of one key.

    The key is per issue on purpose: `idempotency_key` is active-scoped per API
    client, so one key shared by two issues would collapse them into one task.
    """
    base = '{}:agent'.format(key)
    return '{base}:{day}'.format(base=base, day=day.isoformat()) if day else base


def _norm(text):
    return ' '.join(str(text or '').split()).strip().lower()


def _transition(entry):
    to = entry.get('to')
    target = to.get('name') if isinstance(to, dict) else to
    return {'id': entry.get('id'), 'name': entry.get('name') or '',
            'to': str(target or '')}


def _all(transitions):
    return [_transition(entry) for entry in transitions or [] if isinstance(entry, dict)]


def _word(alias, text):
    """`alias` as a whole word inside `text` (both already normalized)."""
    return re.search(r'(?<![^\W_])' + re.escape(alias) + r'(?![^\W_])', text) is not None


def _stop(reason, candidates=()):
    return {'outcome': 'needs-decision', 'transition_id': None, 'target': '',
            'reason': reason, 'candidates': list(candidates)}


def resolve_status(transitions, intent, override='', current=''):
    """Resolve one move, or say why it stops (docs/tracker-status.md).

    `outcome` is `resolved`, `skip` (the issue is already there), `confirm` (the
    transition reads like a rollback) or `needs-decision` (nothing matched, or
    two statuses did). An override is matched exactly and never falls back to
    the aliases, so a typo stops instead of moving to another status.
    """
    if intent not in INTENT_ALIASES:
        raise ValueError('unknown intent: {!r}'.format(intent))
    found = _all(transitions)
    if override:
        wanted = _norm(override)
        matches = [item for item in found
                   if _norm(item['to']) == wanted or _norm(item['name']) == wanted]
        if not matches:
            return _stop('no available transition reaches {!r}'.format(override), found)
        if len({item['to'] for item in matches}) > 1:
            return _stop('two transitions match {!r}'.format(override), matches)
        return _decide(matches[0], current)

    aliases = INTENT_ALIASES[intent]
    exact = [item for item in found
             if _norm(item['to']) in aliases or _norm(item['name']) in aliases]
    if not exact:
        exact = [item for item in found
                 if any(_word(alias, _norm(item['to'])) or _word(alias, _norm(item['name']))
                        for alias in aliases)]
    targets = {item['to'] for item in exact}
    if len(targets) == 1:
        return _decide(exact[0], current)
    if len(targets) > 1:
        return _stop('several statuses match the {} aliases'.format(intent), exact)
    return _stop('nothing matches the {} aliases'.format(intent), found)


def _decide(transition, current):
    result = {'outcome': 'resolved', 'transition_id': transition['id'],
              'target': transition['to'], 'reason': '', 'candidates': [transition]}
    if current and _norm(transition['to']) == _norm(current):
        result.update(outcome='skip', reason='already in {!r}'.format(transition['to']))
        return result
    name = transition['name']
    if ROLLBACK_RE.match(name.strip()) or any(
            word in _norm(name) for word in ROLLBACK_WORDS):
        result.update(outcome='confirm',
                      reason='the transition {!r} reads like a move backwards'.format(name))
    return result


def issue_facts(issue):
    """`(summary, description, url, current_status, done)` from one issue payload."""
    fields = issue.get('fields') if isinstance(issue, dict) else None
    fields = fields if isinstance(fields, dict) else {}
    status = fields.get('status') if isinstance(fields.get('status'), dict) else {}
    category = status.get('statusCategory') if isinstance(status.get('statusCategory'),
                                                          dict) else {}
    done = _norm(category.get('key')) == 'done'
    return {
        'summary': str(fields.get('summary') or '').strip(),
        'description': str(fields.get('description') or '').strip(),
        'url': str(issue.get('url') or issue.get('self') or '').strip(),
        'current': str(status.get('name') or '').strip(),
        'done': done,
    }


def issue_outcome(issue):
    """`ready`, `invalid` or `needs-decision` from one issue payload alone."""
    if not isinstance(issue, dict) or not issue_facts(issue)['summary']:
        return 'invalid'
    return 'needs-decision' if issue_facts(issue)['done'] else 'ready'


def batch_row(key, outcome, **fields):
    """One report row: the outcome plus whatever else is known about the key."""
    row = {'key': key, 'outcome': outcome}
    row.update({name: value for name, value in fields.items() if value is not None})
    return row


def counts(rows):
    """How many rows carry each outcome, for the closing line of a report."""
    result = {}
    for row in rows:
        outcome = row.get('outcome') or 'invalid'
        result[outcome] = result.get(outcome, 0) + 1
    return result


def needs_confirmation(rows, max_batch):
    """Whether the batch is larger than `delegate.maxBatch` ready issues."""
    ready = [row for row in rows if row.get('outcome') == 'ready']
    limit = max_batch if isinstance(max_batch, int) and max_batch > 0 else 5
    return len(ready) > limit


def prompt_values(facts, key, repo, base_ref, branch, title, reviewer, language):
    """The values `PROMPT_TEMPLATE` fills, one dict per issue."""
    return {
        'base_ref': base_ref or 'the repository default',
        'branch': branch,
        'description': facts['description'] or 'Not provided in the tracker.',
        'key': key,
        'language': language or 'en',
        'repo': repo,
        'reviewer': reviewer or 'the requesting user',
        'summary': facts['summary'],
        'title': title,
        'url': facts['url'] or 'not provided',
    }


def compose_prompt(values):
    """The prompt one spawned task receives, with every placeholder filled."""
    return bratishka_testing.render_override(PROMPT_TEMPLATE, values)


def slug(text, limit=5):
    """A short lower-case slug from a ticket summary, never empty."""
    words = re.findall(r'[a-z0-9]+', _norm(text))
    return '-'.join(words[:limit]) or 'change'


def branch_and_title(key, facts):
    """The branch and pull-request title one issue's task is asked to use."""
    name = slug(facts['summary'])
    return '{}-{}'.format(key, name), '{} {}'.format(key, name.replace('-', ' '))


def closeout(key, task, pr):
    """One finished task's outcome, with the gaps a send-back would fix.

    The pull request is the artifact: a task whose completion gate stayed red
    while its pull request exists is `completed` with the gate as a gap, never a
    failed delegation. `pr` is the normalized pull request, or None.
    """
    task = task if isinstance(task, dict) else {}
    status = str(task.get('status') or '')
    detail = task.get('result_detail')
    detail = detail if isinstance(detail, dict) else {}
    gaps = []
    if pr is None:
        gaps.append('no pull request was opened')
    else:
        if not str(pr.get('body') or '').strip():
            gaps.append('the pull-request body is empty')
        if pr.get('review_requests') is False:
            gaps.append('the reviewer is not requested')
    if status == 'completed':
        outcome = 'completed'
    elif status == 'failed' and pr is not None:
        outcome = 'completed'
        gaps.append('the completion gate failed: {}'.format(
            detail.get('error') or 'final_tools_gate_not_satisfied'))
    else:
        outcome = 'failed'
        gaps.append('the task is not in a terminal successful state: {}'.format(
            status or 'unknown'))
    return {
        'key': key,
        'outcome': outcome,
        'pr': pr,
        'gaps': gaps,
        'gate_failed': detail.get('error') == 'final_tools_gate_not_satisfied',
        'send_back': pr is not None and bool(gaps),
    }


def render_value(value):
    """A report cell: empty rather than the word `None`."""
    return '' if value is None else str(value)


def plan(config, keys, issues, transitions, day, repo='', language='en'):
    """The whole batch: outcomes, resolved moves, prompts and spawn arguments.

    `issues` and `transitions` are the facts the skill gathered, keyed by issue
    key. A key missing from either one is `invalid` or `needs-decision` -- never
    a guess. Each ready spawn call carries its own filled prompt, because the
    spawned session cannot see the chat.
    """
    config = config if isinstance(config, dict) else {}
    delegate = config.get('delegate') if isinstance(config.get('delegate'), dict) else {}
    repo = repo or str(config.get('repo') or '')
    reviewer = str(delegate.get('reviewerLogin') or '')
    base_ref = str(config.get('baseRef') or '')
    max_batch = delegate.get('maxBatch')
    rows = []
    spawn_calls = []
    for key in keys:
        issue = (issues or {}).get(key)
        outcome = issue_outcome(issue)
        if outcome == 'invalid':
            rows.append(batch_row(key, 'invalid', reason='the tracker returned no such issue'))
            continue
        facts = issue_facts(issue)
        if outcome == 'needs-decision':
            rows.append(batch_row(key, 'needs-decision', current=facts['current'],
                                  reason='the issue is in a done category: reopen it first'))
            continue
        moves = {}
        blocked = None
        intent = 'start'
        for intent in INTENTS:
            override = str(delegate.get('{}Status'.format(intent)) or '')
            moves[intent] = resolve_status((transitions or {}).get(key), intent,
                                           override=override,
                                           current=facts['current'] if intent == 'start' else '')
            if moves[intent]['outcome'] == 'needs-decision':
                blocked = moves[intent]
                break
        if blocked is not None:
            rows.append(batch_row(key, 'needs-decision', current=facts['current'],
                                  reason='{}: {}'.format(intent, blocked['reason']),
                                  candidates=blocked['candidates']))
            continue
        branch, title = branch_and_title(key, facts)
        values = prompt_values(facts, key, repo, base_ref, branch, title, reviewer, language)
        rows.append(batch_row(key, 'ready', current=facts['current'], branch=branch,
                              title=title, start=moves['start'], handoff=moves['handoff']))
        spawn_calls.append({
            'key': key,
            'repo_url': ('https://github.com/{}.git'.format(repo) if repo else ''),
            'repo': repo,
            'base_ref': base_ref,
            'preset': 'coder',
            'task_kind': 'agent',
            'context': {'jira_task': key},
            'idempotency_key': idempotency_key(key),
            'prompt': compose_prompt(values),
        })
    return {
        'day': day.isoformat(),
        'repo': repo,
        'reviewer': reviewer,
        'rows': rows,
        'spawn_calls': spawn_calls,
        'counts': counts(rows),
        'needs_confirmation': needs_confirmation(rows, max_batch),
        'max_batch': max_batch if isinstance(max_batch, int) else 5,
    }


def report(rows):
    """The batch table, one row per requested key, in the order given."""
    return [[row.get('key', ''), render_value(row.get('outcome')),
             render_value(row.get('branch')), render_value(_move(row, 'start')),
             render_value(_move(row, 'handoff')), render_value(row.get('reason'))]
            for row in rows]


def _move(row, intent):
    move = row.get(intent)
    if not isinstance(move, dict):
        return ''
    return '{} -> {}'.format(move.get('transition_id') or '-', move.get('target') or '?')
