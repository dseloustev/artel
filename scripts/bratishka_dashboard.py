"""bratishka-dashboard: the read-only client for the Bratishka dashboard.

The optional observation channel behind the duty board and the fix
correlation (docs/bratishka-duty.md `## Queue and board`,
`## Requesting a fix`, `## Safety and degradation`): it reads the dashboard's
unauthenticated endpoints -- `/api/v1/dashboard/overview`,
`/api/v1/dashboard/tasks` and `/api/v1/dashboard/tasks/<id>` -- over
`urllib` and returns their JSON as the dashboard sends it (`task_id`,
`description`, `created_at`, `initiator`, `head_ref`, `result_status`). No
request carries credentials; the read endpoints take none.

The dashboard is never a dependency: every failed request appends one line to
`errors` and returns `None` (or `[]` from `tasks`), so no read path can stop on
an unreachable host. The board loses its live-task section and the fix skill
falls back to PR polling; everything else keeps working.

`find_task_for_pr` is the single correlation entry point: a bot task's
description carries `PR <owner>/<repo>#<n>`, `initiator` is the dashboard-side
filter for the caller's GitHub login, and `since` is the posting time in ISO
8601 UTC, compared lexically against `created_at` because both are UTC stamps.
`active_tasks_for_pr` is the safety read: every `active`/`queued` task for the
PR, whichever reviewer started it, so a live task is never hidden by a newer
completed one.

Pure library: no CLI, no config, no third-party dependencies.

Contract: docs/bratishka-duty.md (`## Queue and board`, `## Requesting a fix`,
`## Safety and degradation`), docs/config.md (`bratishka.dashboardUrl`).
"""
import http.client
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

API = '/api/v1/dashboard'
# The task states that count as live bot work.
ACTIVE_STATES = ('active', 'queued')

# The filters `/tasks` understands; anything else a caller passes is not sent.
TASK_FILTERS = ('status', 'preset', 'repo', 'initiator', 'search', 'limit')


def _one_line(exc):
    """`exc` flattened to one line, so `errors` stays one entry per failure."""
    return ' '.join(str(exc).split()) or exc.__class__.__name__


def _newest(tasks):
    """The task with the greatest `created_at`; ISO 8601 UTC stamps sort lexically."""
    if not tasks:
        return None
    return max(tasks, key=lambda task: str(task.get('created_at') or ''))


class Dashboard:
    """A read-only dashboard client; every failure lands in `errors`, never raises."""

    def __init__(self, base_url, opener=None, timeout=5):
        self.base_url = (base_url or '').rstrip('/')
        self.opener = opener if opener is not None else urlopen
        self.timeout = timeout
        self.errors = []

    def get(self, path, params=None):
        """One GET, decoded from JSON; a failure is recorded and None returned.

        `params` drops None values, so callers pass optional filters directly.
        A connection error, an HTTP error, a timeout, a truncated body, a
        malformed body and an unknown URL all take the same path: one `errors`
        line, `None` back.
        """
        query = {key: value for key, value in (params or {}).items() if value is not None}
        url = self.base_url + path
        if query:
            url += '?' + urlencode(query)
        try:
            with self.opener(url, timeout=self.timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except (URLError, HTTPError, http.client.HTTPException, OSError, ValueError) as exc:
            self.errors.append('GET {}: {}'.format(url, _one_line(exc)))
            return None

    def overview(self):
        """The dashboard's counters object, or None when it cannot be read."""
        payload = self.get(API + '/overview')
        return payload if isinstance(payload, dict) else None

    def tasks(self, **filters):
        """The task list, narrowed by the dashboard's own filters.

        `status`, `preset`, `repo`, `initiator`, `search` and `limit` are the
        filters the endpoint knows; a None value is omitted. A failed request
        reads as an empty list -- a board with no live tasks, not an error the
        caller has to catch.
        """
        params = {name: filters[name] for name in TASK_FILTERS
                  if filters.get(name) is not None}
        payload = self.get(API + '/tasks', params)
        if isinstance(payload, dict):
            payload = payload.get('tasks')
        if not isinstance(payload, list):
            return []
        return [task for task in payload if isinstance(task, dict)]

    def task(self, task_id):
        """One task's record, or None; the id is quoted as a path segment."""
        payload = self.get('{}/tasks/{}'.format(API, quote(str(task_id), safe='')))
        return payload if isinstance(payload, dict) else None

    def find_task(self, run_id):
        """The newest task whose description carries `run_id`, or None.

        The dashboard's `search` narrows the list by the run id; the marker is
        also checked locally, because search can match other fields.
        """
        matches = [task for task in self.tasks(search=run_id)
                   if run_id in (task.get('description') or '')]
        return _newest(matches)

    def find_task_for_pr(self, repo, number, initiator=None, since=None):
        """The newest task whose description names `PR <repo>#<number>`, or None.

        `initiator` is passed to the dashboard's own filter for the caller's
        GitHub login. `since` (ISO 8601 UTC) is compared lexically against
        `created_at`; a task with no `created_at` cannot prove it is new enough
        and is dropped when `since` is given. This is the post-action
        correlation read: it is caller-scoped and returns one task, not the
        safety guard's complete live-task list.
        """
        needle = 'PR {}#{}'.format(repo, number)
        matches = []
        for task in self.tasks(repo=repo, initiator=initiator):
            if needle not in (task.get('description') or ''):
                continue
            if since is not None and (task.get('created_at') or '') < since:
                continue
            matches.append(task)
        return _newest(matches)

    def active_tasks_for_pr(self, repo, number):
        """Every `active`/`queued` task whose description names the PR.

        Unlike `find_task_for_pr` this is not scoped to an initiator and never
        collapses the answer to the newest task: a newer completed task must
        not mask an older live one, and a task another reviewer started still
        blocks a second request. The dashboard's `status` filter is applied
        server-side and checked locally, so a test double that ignores filters
        still yields only live tasks.
        """
        needle = 'PR {}#{}'.format(repo, number)
        found = []
        seen = set()
        for state in ACTIVE_STATES:
            for task in self.tasks(repo=repo, status=state):
                if not isinstance(task, dict):
                    continue
                if needle not in (task.get('description') or ''):
                    continue
                if str(task.get('status') or state) not in ACTIVE_STATES:
                    continue
                key = task.get('task_id')
                if key is None:
                    key = (task.get('description'), task.get('created_at'),
                           task.get('initiator'))
                if key in seen:
                    continue
                seen.add(key)
                found.append(task)
        return found
