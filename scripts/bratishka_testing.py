"""bratishka-testing: the testing-PR selection, its body and its coder prompt.

The read-only composition behind the daily testing PR (docs/bratishka-duty.md
`## Testing PR`): it selects the code-reviewed, not-QA-approved source PRs the
rules layer names, renders the #144-shaped body from their `Notes for QA` and
`Platforms to test` sections, and writes the prompt the coder receives. The
prompt ends with a unique `Run id: artel-<uuid>` marker, so the skill can find
the spawned task after a manual `/spawn`.

`plan` is the one selection entry point: it reads through the injected `gh`
client, narrows the source PRs with `bratishka_rules.testing_candidates`, and
returns the tasks, the base ref and sha, the rendered title and branch, the
prompt and the run id. `--include` narrows that selection and `--exclude` drops
from it; neither can add a PR the approval model did not select, because the
testing PR must never bundle an unreviewed or already-QA-approved change.

Nothing here posts, closes, merges or pushes: every function is pure over its
arguments, and the caller decides what to do with the plan.

Pure library: standard library only, no CLI, no config file, no third-party
dependencies, no network of its own.

Contract: docs/bratishka-duty.md (`## Testing PR`, `### Body`),
docs/config.md (`bratishka.testingPr`).
"""
import re
import uuid
from datetime import date

import bratishka_config
import bratishka_rules
from bratishka_gh import GhError

# The fallback platform line when the source PR declares no checklist.
NOT_SPECIFIED = 'Not specified in the source PR'
# The explicit sentence a source PR without QA notes gets in the body.
MISSING_NOTES = ('The source PR does not declare QA notes or platforms — fill this in '
                 'before handing the PR to QA.')
# `**Notes for QA**` and its siblings: a bold line that ends the current section.
BOLD_RE = re.compile(r'^\*\*[^*]+\*\*\s*$')
# A checked checklist entry, e.g. `- [x] Android`.
CHECKED_RE = re.compile(r'^[-*]\s*\[[xX]\]\s+(.+?)\s*$')
# Any checklist entry, checked or not, so an unchecked platform is not read as prose.
CHECKBOX_RE = re.compile(r'^[-*]\s*\[[ xX]\]\s*')
# The first ticket key in a source PR title, e.g. `AW-3320`.
TICKET_RE = re.compile(r'[A-Z][A-Z0-9]+-\d+')


def _config(value):
    return value if isinstance(value, dict) else {}


def _testing(config):
    """The `testingPr` block, or an empty dict when it is absent or mistyped."""
    block = _config(config).get('testingPr')
    return block if isinstance(block, dict) else {}


def _template(config, key):
    """A testing-PR template from the config, or its shipped default."""
    return (_testing(config).get(key)
            or bratishka_config.DEFAULTS['testingPr'][key])


def _section(body, title):
    """The lines under a bold `**title**` heading, or None when it is absent.

    A heading matches on its own line, case-insensitively. The section ends at
    the next bold line or Markdown heading, so the body's other sections never
    leak into the notes or the platform checklist.
    """
    lines = str(body or '').splitlines()
    needle = re.compile(r'^\*\*' + re.escape(title) + r'\*\*\s*$', re.IGNORECASE)
    for index, line in enumerate(lines):
        if needle.match(line.strip()):
            break
    else:
        return None
    section = []
    for line in lines[index + 1:]:
        stripped = line.strip()
        if BOLD_RE.match(stripped) or stripped.startswith('#'):
            break
        section.append(line)
    return section


def parse_notes(body):
    """The `**Notes for QA**` section verbatim, trimmed, or '' when absent."""
    section = _section(body, 'Notes for QA')
    if section is None:
        return ''
    while section and not section[0].strip():
        section.pop(0)
    while section and not section[-1].strip():
        section.pop()
    return '\n'.join(line.rstrip() for line in section)


def parse_platforms(body):
    """The checked entries of `**Platforms to test**`, comma-separated.

    An unchecked entry is ignored; a first line that is not a checklist entry
    at all (for example `Any platform`) is kept verbatim. A missing or empty
    section returns `Not specified in the source PR`.
    """
    section = _section(body, 'Platforms to test')
    if section is None:
        return NOT_SPECIFIED
    checked = []
    for line in section:
        stripped = line.strip()
        if not stripped:
            continue
        match = CHECKED_RE.match(stripped)
        if match:
            checked.append(match.group(1).strip())
            continue
        if CHECKBOX_RE.match(stripped):
            continue
        if not checked:
            return stripped
    if checked:
        return ', '.join(checked)
    return NOT_SPECIFIED


def ticket_of(title):
    """The first ticket key in a title, or an em dash when there is none."""
    match = TICKET_RE.search(str(title or ''))
    return match.group(0) if match else '—'


def _short(sha):
    """A commit SHA shortened to the seven characters a body cites."""
    value = str(sha or '')
    return value[:7] if len(value) > 7 else value


def _list_block(items, empty):
    """`items` as one bullet each, or the `empty` sentence as a single bullet."""
    values = [str(item).strip() for item in items or [] if str(item).strip()]
    if not values:
        return '- {}'.format(empty)
    return '\n'.join('- {}'.format(value) for value in values)


def _check_line(number, checks):
    """One source PR's CI line: green, the failing checks, or why it is unknown."""
    if checks is None:
        return '- **#{}** — checks not read'.format(number)
    if not checks:
        return '- **#{}** — no checks reported'.format(number)
    failing = [check for check in checks
               if isinstance(check, dict)
               and str(check.get('bucket') or '') in ('fail', 'cancel')]
    pending = [check for check in checks
               if isinstance(check, dict)
               and str(check.get('bucket') or '') == 'pending']
    if failing:
        detail = ', '.join(_check_name(check) for check in failing)
        return '- **#{}** — red: {}'.format(number, detail)
    if pending:
        detail = ', '.join(_check_name(check) for check in pending)
        return '- **#{}** — pending: {}'.format(number, detail)
    return '- **#{}** — green'.format(number)


def _check_name(check):
    """One check's name with its state when GitHub reports one."""
    name = '`{}`'.format(check.get('name') or 'unnamed check')
    state = str(check.get('state') or '')
    return '{} ({})'.format(name, state) if state else name


def _task_line(task):
    """The `## Tasks` line for one source PR."""
    return '- **{}** — [#{} {}]({})'.format(
        ticket_of(task.get('title')), task.get('number'),
        task.get('title') or '', task.get('url') or '')


def _notes_block(task):
    """One source PR's `### ticket — PR #n` notes and platform line."""
    body = task.get('body') or ''
    notes = parse_notes(body) or MISSING_NOTES
    return '### {} — PR #{}\n\n{}\n\n**Platforms to test:** {}'.format(
        ticket_of(task.get('title')), task.get('number'), notes,
        parse_platforms(body))


def build_body(tasks, config, day, base_ref, base_sha, checks_by_pr, merge_notes,
               verification):
    """The #144-shaped testing-PR body in its eight ordered parts.

    `tasks` are the selected source PRs (number, title, url, head, body),
    `checks_by_pr` maps a PR number to its `gh.checks` list, and `merge_notes`
    and `verification` are the lines the caller collected while building the
    branch. `config` is accepted so the host's testing-PR settings can travel
    with the call; the default shape is the fixed contract.
    """
    tasks = [task for task in tasks or [] if isinstance(task, dict)]
    checks_by_pr = checks_by_pr if isinstance(checks_by_pr, dict) else {}
    count = len(tasks)
    summary = (
        '{} — `origin/{}` at `{}`; {} source PR{}; the branches were merged '
        'without conflicts.'.format(
            day.isoformat(), base_ref, _short(base_sha), count,
            '' if count == 1 else 's'))
    blockquote = ('> **QA: leave feedback and approvals in the individual source PRs, '
                  'not in this testing PR** — it only bundles the changes.')
    if tasks:
        task_lines = '\n'.join(_task_line(task) for task in tasks)
        notes = '\n\n'.join(_notes_block(task) for task in tasks)
        ci = '\n'.join(_check_line(task.get('number'), checks_by_pr.get(task.get('number')))
                       for task in tasks)
    else:
        task_lines = '- No source PRs were selected.'
        notes = 'No source PRs were selected, so there are no QA notes.'
        ci = '- No source PRs were selected.'
    one_day = ('Single testing PR for {} — it lives one day and will be closed by the '
               'next duty reviewer.'.format(day.isoformat()))
    return '\n'.join([
        '## Summary', '', summary, '', blockquote, '',
        '## Tasks', '', task_lines, '',
        '## Notes for QA', '', notes, '',
        '## CI status of source PRs', '', ci, '',
        '## Merge notes', '', _list_block(merge_notes, 'No merge notes.'), '',
        '## Local verification', '',
        _list_block(verification, 'Verification was not run in this pass.'), '',
        '---', '', one_day,
    ])


def _indent(text, prefix):
    """Every non-blank line of `text` prefixed with `prefix`."""
    return '\n'.join(prefix + line if line.strip() else line
                     for line in text.splitlines())


def _prompt_task(task):
    """One source PR's block in the coder prompt, notes and platforms included."""
    body = task.get('body') or ''
    notes = parse_notes(body) or 'None declared in the source PR.'
    return '\n'.join([
        '- {} — PR #{}, head branch {}'.format(
            ticket_of(task.get('title')), task.get('number'),
            task.get('head') or ''),
        '',
        '  Notes for QA:',
        _indent(notes, '  '),
        '',
        '  Platforms to test: {}'.format(parse_platforms(body)),
    ])


def build_prompt(tasks, config, day, base_ref, run_id):
    """The #144-shaped coder prompt, ending with the unique `Run id:` marker.

    Every task carries its head branch, notes and platforms, so the coder never
    has to re-derive them from the source PRs. The prompt names the exact title
    and branch to create and the body rules, and forbids merging the testing PR
    or touching a source PR.
    """
    config = _config(config)
    tasks = [task for task in tasks or [] if isinstance(task, dict)]
    stamp = day.isoformat()
    title = bratishka_config.render(_template(config, 'titleTemplate'), day)
    branch = bratishka_config.render(_template(config, 'branchTemplate'), day)
    repo = config.get('repo') or ''
    repo_line = ('Repository: `{}`'.format(repo) if repo
                 else "Repository: this checkout's origin")
    task_blocks = ('\n\n'.join(_prompt_task(task) for task in tasks)
                   if tasks else '- No source PRs are selected.')
    return '\n'.join([
        '# Daily testing PR — {}'.format(stamp), '',
        repo_line,
        'Base: `origin/{}`'.format(base_ref),
        'Date: {}'.format(stamp), '',
        'Create the daily testing PR that bundles the source PRs below.', '',
        task_blocks, '',
        'Steps:', '',
        '1. Fetch `origin` and create `{}` from `origin/{}`. Never push to the base '
        'branch and never force-push.'.format(branch, base_ref),
        '2. Merge the listed head branches into the testing branch in the order shown. '
        'Merge only these heads: do not resolve a conflict by force or by dropping '
        "changes, and do not rebase or edit a source branch. If a merge conflicts, stop "
        'and report the conflicting files.',
        '3. Open one pull request against `{}` with the exact title `{}` and head `{}`. '
        'Build its body in these ordered parts:'.format(base_ref, title, branch),
        '   - `## Summary` — the date, `origin/{}` at the base sha, the source-PR '
        'count, and that the branches were merged without conflicts.'.format(base_ref),
        '   - A blockquote before the task list: **QA: leave feedback and approvals in '
        'the individual source PRs, not in this testing PR** — it only bundles the '
        'changes.',
        '   - `## Tasks` — one line per source PR: `- **<ticket>** — [#<n> <title>](<url>)`.',
        '   - `## Notes for QA` — per source PR, `### <ticket> — PR #<n>`, the notes '
        'given above verbatim, then `**Platforms to test:** <platforms>`. Use the notes '
        'and platforms above; do not re-derive them from the source PRs. A source PR '
        'without notes gets the explicit sentence saying so.',
        '   - `## CI status of source PRs` — per source PR, green or the failing check '
        'and its reason; never present a red source check as resolved.',
        '   - `## Merge notes` — clean merges, files a merge re-introduced, and anything '
        'the source-PR owner should know.',
        "   - `## Local verification` — the repository's verification commands and "
        'their results; report a missing tool instead of hiding it.',
        '   - A final line outside every heading: `Single testing PR for {} — it lives '
        'one day and will be closed by the next duty reviewer.`'.format(stamp),
        '4. Do not merge the testing PR. Do not edit, close, rebase or force-update any '
        'source PR, and do not push to `origin/{}`.'.format(base_ref),
        "5. Run the repository's verification commands and report their results in "
        '`## Local verification`; then return the testing PR URL and the CI status of '
        'the source PRs.',
        '',
        'Run id: {}'.format(run_id),
    ])


def new_run_id():
    """A unique `artel-<uuid4>` marker for one coder task."""
    return 'artel-{}'.format(uuid.uuid4())


def _team_members(gh, org, slug):
    """A team's member logins, or None when the slug is unset or unreadable."""
    if not slug:
        return None
    try:
        return gh.team_members(org, slug)
    except GhError:
        return None


def _numbers(values):
    """The integer PR numbers in `values`, silently skipping anything else."""
    numbers = set()
    for value in values or []:
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            continue
    return numbers


def plan(config, gh, include=None, exclude=None, day=None):
    """Select the source PRs and compose today's testing-PR plan.

    `include`, when non-empty, keeps only the named candidates and `exclude`
    drops the named ones; both run after `testing_candidates`, so the approval
    model stays the gate. The returned dict carries `repo`, `tasks`, `base_ref`,
    `base_sha`, `title`, `branch`, `prompt` and `run_id`; `day` defaults to the
    local date and exists so a caller can pin the name in a test.
    """
    config = _config(config)
    repo = config.get('repo') or gh.repo_slug()
    if not repo:
        raise GhError('failed', 'could not resolve the repository; set bratishka.repo')
    base_ref = config.get('baseRef') or gh.default_branch(repo)
    day = day or date.today()
    org = repo.split('/')[0]

    open_prs = gh.open_prs(repo, config.get('botLogin') or '')
    eligible = [pr for pr in open_prs
                if not pr.get('draft')
                and not bratishka_rules.is_testing_pr(pr, config)
                and not bratishka_rules.is_excluded(pr, config)]
    reviews = {pr.get('number'): gh.reviews(repo, pr.get('number'))
               for pr in eligible}
    teams = _config(config.get('teams'))
    members = {'codeReview': _team_members(gh, org, teams.get('codeReview')),
               'qa': _team_members(gh, org, teams.get('qa'))}

    candidates = bratishka_rules.testing_candidates(eligible, reviews, members, config)
    by_number = {pr.get('number'): pr for pr in eligible}
    keep = _numbers(include)
    drop = _numbers(exclude)
    tasks = []
    for item in candidates:
        number = item.get('number')
        if keep and number not in keep:
            continue
        if number in drop:
            continue
        pr = by_number.get(number) or {}
        record = dict(item)
        record['head'] = pr.get('head') or ''
        record['body'] = pr.get('body') or ''
        tasks.append(record)
    tasks.sort(key=lambda item: (item.get('number') is None, item.get('number')))

    run_id = new_run_id()
    return {
        'repo': repo,
        'tasks': tasks,
        'base_ref': base_ref,
        'base_sha': gh.base_sha(repo, base_ref) or '',
        'title': bratishka_config.render(_template(config, 'titleTemplate'), day),
        'branch': bratishka_config.render(_template(config, 'branchTemplate'), day),
        'prompt': build_prompt(tasks, config, day, base_ref, run_id),
        'run_id': run_id,
    }
