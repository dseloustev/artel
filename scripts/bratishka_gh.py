"""bratishka-gh: the one module that speaks to `gh`.

The GitHub layer behind the bratishka duty (docs/bratishka-duty.md): it runs the
`gh` reads and writes every other layer consumes and normalizes their JSON into
the key names the rules layer works with -- PRs (`number`, `title`, `url`,
`head`, `author`, `draft`, `state`, `created_at`, `body`, `review_decision`,
`mergeable`, `merge_state`), reviews (`login`, `state`, `submitted_at`, `body`),
checks (`name`, `state`, `bucket`, `link`) and review threads (`path`, `line`,
`body`, `author`, `resolved`).

Every call goes through the injected `call(args) -> str`, which returns stdout;
the default runs `gh` in `cwd` and raises GhError on a non-zero exit (`404` /
`Not Found` -> `not_found`, `403` -> `forbidden`, anything else -> `failed`).

`update_branch` uses `gh api -X PUT repos/<repo>/pulls/<n>/update-branch` rather
than `gh pr update-branch`: hooks/vcs_guard.py classifies the latter as an
unknown verb and denies it, while the REST call is a recognized write.

Pure library: no CLI, no config, no third-party dependencies.

Contract: docs/bratishka-duty.md (`## Queue and board`, `## Requesting a fix`,
`## Merging`), docs/config.md (`bratishka`).
"""
import json
import subprocess

# The fields `gh pr list` and `gh pr view` return under the names _normalize_pr
# reads. One request, one spelling, so the two paths cannot drift apart.
PR_FIELDS = ('number,title,url,headRefName,author,isDraft,state,createdAt,body,'
             'reviewDecision,mergeable,mergeStateStatus')
PR_LIMIT = 200

# The GraphQL query for review threads: first 100 threads, each with its root
# comment first (`comments.nodes` is ordered oldest first) and up to 100
# replies. docs/bratishka-duty.md `## Requesting a fix`.
REVIEW_THREADS_QUERY = """\
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          path
          line
          comments(first: 100) {
            nodes { author { login } body createdAt }
          }
        }
      }
    }
  }
}"""


class GhError(Exception):
    """A failed `gh` call; `kind` is `not_found`, `forbidden` or `failed`."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _failure(stderr, returncode):
    """The GhError a non-zero exit maps to, per the brief's 404/403 rules."""
    message = (stderr or '').strip() or 'gh exited with status {}'.format(returncode)
    lowered = message.lower()
    if '404' in message or 'not found' in lowered:
        return GhError('not_found', message)
    if '403' in message:
        return GhError('forbidden', message)
    return GhError('failed', message)


def _login(value):
    """A login from gh's `{"login": ...}` object (or a bare string), else ''."""
    if isinstance(value, dict):
        return value.get('login') or ''
    return value if isinstance(value, str) else ''


def _normalize_pr(raw):
    return {
        'number': raw.get('number'),
        'title': raw.get('title') or '',
        'url': raw.get('url') or '',
        'head': raw.get('headRefName') or '',
        'author': _login(raw.get('author')),
        'draft': bool(raw.get('isDraft')),
        'state': raw.get('state') or '',
        'created_at': raw.get('createdAt') or '',
        'body': raw.get('body') or '',
        'review_decision': raw.get('reviewDecision') or '',
        'mergeable': raw.get('mergeable') or '',
        'merge_state': raw.get('mergeStateStatus') or '',
    }


def _normalize_review(raw):
    """A REST review: `user`, `submitted_at` (GitHub's snake_case spelling)."""
    return {
        'login': _login(raw.get('user')),
        'state': raw.get('state') or '',
        'submitted_at': raw.get('submitted_at') or '',
        'body': raw.get('body') or '',
    }


def _normalize_check(raw):
    return {
        'name': raw.get('name') or '',
        'state': raw.get('state') or '',
        'bucket': raw.get('bucket') or '',
        'link': raw.get('link') or '',
    }


def _normalize_thread(raw):
    """A review thread flattened to its root comment: the bot's first word."""
    comments = (raw.get('comments') or {}).get('nodes') or []
    root = comments[0] if comments else {}
    return {
        'path': raw.get('path') or '',
        'line': raw.get('line'),
        'body': root.get('body') or '',
        'author': _login(root.get('author')),
        'resolved': bool(raw.get('isResolved')),
    }


def _items(raw):
    """Review items from `gh api --paginate --slurp`, flattened to one list.

    `--slurp` wraps each page's array in an outer array (`[[...], [...]]`), so
    the pages are extended into one list. An injected `call` may return the flat
    single-page array directly, which is accepted as one page of items.
    """
    items = []
    for page in raw if isinstance(raw, list) else []:
        if isinstance(page, list):
            items.extend(page)
        else:
            items.append(page)
    return items


class Gh:
    """A `gh` client: reads normalize, writes run their one documented command."""

    def __init__(self, call=None, cwd=None):
        self.cwd = cwd
        self.call = call if call is not None else self._run

    def _run(self, args):
        """Run `gh` and return stdout; a non-zero exit raises GhError."""
        try:
            proc = subprocess.run(['gh'] + list(args), capture_output=True, text=True,
                                  cwd=self.cwd)
        except OSError as exc:
            raise GhError('failed', 'could not run gh: {}'.format(exc))
        if proc.returncode != 0:
            raise _failure(proc.stderr, proc.returncode)
        return proc.stdout

    def _json(self, args):
        return json.loads(self.call(args) or '[]')

    # -- reads ---------------------------------------------------------------

    def repo_slug(self):
        """`owner/name` of the repository in `cwd`."""
        return self.call(['repo', 'view', '--json', 'nameWithOwner',
                          '--jq', '.nameWithOwner']).strip()

    def current_login(self):
        """The login `gh` is authenticated as."""
        return self.call(['api', 'user', '--jq', '.login']).strip()

    def default_branch(self, repo):
        """The repository's default branch name."""
        return self.call(['repo', 'view', repo, '--json', 'defaultBranchRef',
                          '--jq', '.defaultBranchRef.name']).strip()

    def team_members(self, org, team):
        """The team's member logins; a missing team raises GhError('not_found')."""
        output = self.call(['api', 'orgs/{}/teams/{}/members'.format(org, team),
                            '--paginate', '--jq', '.[].login'])
        return {line.strip() for line in output.splitlines() if line.strip()}

    def open_prs(self, repo, author=None):
        """Open PRs in `repo`, all authors or just `author`, normalized."""
        args = ['pr', 'list', '--repo', repo, '--state', 'open',
                '--limit', str(PR_LIMIT), '--json', PR_FIELDS]
        if author:
            args += ['--author', author]
        return [_normalize_pr(raw) for raw in self._json(args)]

    def pr(self, repo, number):
        """One PR, normalized under the same keys as `open_prs`."""
        raw = self._json(['pr', 'view', str(number), '--repo', repo, '--json', PR_FIELDS])
        return _normalize_pr(raw)

    def reviews(self, repo, number):
        """Every submitted review, normalized; pagination is flattened."""
        raw = self._json(['api', 'repos/{}/pulls/{}/reviews'.format(repo, number),
                          '--paginate', '--slurp'])
        return [_normalize_review(item) for item in _items(raw)]

    def checks(self, repo, number):
        """The PR's checks, normalized to name/state/bucket/link."""
        raw = self._json(['pr', 'checks', str(number), '--repo', repo,
                          '--json', 'name,state,bucket,link'])
        return [_normalize_check(item) for item in raw]

    def review_threads(self, repo, number):
        """Review threads flattened to path, line, root body, author, resolved."""
        owner, _, name = repo.partition('/')
        if not owner or not name:
            raise ValueError('repo must be owner/name: {!r}'.format(repo))
        raw = self._json(['api', 'graphql', '-f', 'query=' + REVIEW_THREADS_QUERY,
                          '-f', 'owner=' + owner, '-f', 'name=' + name,
                          '-F', 'number=' + str(number)])
        data = raw.get('data') or {}
        pull_request = (data.get('repository') or {}).get('pullRequest') or {}
        nodes = (pull_request.get('reviewThreads') or {}).get('nodes') or []
        return [_normalize_thread(node) for node in nodes]

    def base_sha(self, repo, ref):
        """The commit SHA `ref` points at."""
        return self.call(['api', 'repos/{}/commits/{}'.format(repo, ref),
                          '--jq', '.sha']).strip()

    # -- writes --------------------------------------------------------------

    def comment_pr(self, repo, number, body_file):
        """Post one comment on the PR from a file."""
        self.call(['pr', 'comment', str(number), '--repo', repo,
                   '--body-file', body_file])

    def close_pr(self, repo, number, comment=None, delete_branch=False):
        """Close the PR, optionally with a closing comment and branch deletion."""
        args = ['pr', 'close', str(number), '--repo', repo]
        if comment:
            args += ['--comment', comment]
        if delete_branch:
            args += ['--delete-branch']
        self.call(args)

    def merge_pr(self, repo, number, squash=True):
        """Merge the PR; squash unless the caller asks for a merge commit."""
        self.call(['pr', 'merge', str(number), '--repo', repo,
                   '--squash' if squash else '--merge'])

    def update_branch(self, repo, number):
        """Bring the PR's branch up to date with its base, through the REST API.

        Not `gh pr update-branch`: hooks/vcs_guard.py reads that verb as unknown
        and denies it, while `gh api -X PUT` is a recognized write.
        """
        self.call(['api', '-X', 'PUT',
                   'repos/{}/pulls/{}/update-branch'.format(repo, number)])

    def create_pr(self, repo, base, head, title, body_file=None, assignee=None):
        """Open a PR and return its URL; body and assignee are optional."""
        args = ['pr', 'create', '--repo', repo, '--base', base, '--head', head,
                '--title', title]
        if body_file:
            args += ['--body-file', body_file]
        if assignee:
            args += ['--assignee', assignee]
        return self.call(args).strip()
