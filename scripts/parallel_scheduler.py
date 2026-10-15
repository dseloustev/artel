#!/usr/bin/env python3
"""parallel-scheduler: deterministic bounded admission for the parallel run.

    select_admissions(iterations, manifest, seats, case_sensitive) -> admission

Contract: docs/opencode-parallel.md §7 (the `Concurrency:` metadata), §13 (the run manifest
and its task states) and §13.6 (the consumer protocol), docs/task-grammar.md §7 (`ready_now`
and `waves` stay dependency facts), and the approved admission clauses of the parallel plan.

The selector is pure: it reads parsed iterations and a stored run manifest and answers what
the coordinator may start now. It never touches the filesystem, Git, the queue or a session,
and it never marks a task done -- publication is the coordinator's transition.

Readiness. `task_grammar.ready_now` and `task_grammar.waves` keep their meaning: dependency
facts over the checkboxes. Admission overlays durable publication on top of them. A task is
complete when its checkboxes are all ticked or the manifest records it `published`; the
current iteration is the first one with an incomplete task; a candidate is a task of that
iteration with no manifest record whose dependencies are all complete. A worker return, a
passed review or a prepared result never unlocks a dependent -- only `published` does.

Resources. Every unresolved task (reserved, preparing, running, verifying/reviewing,
prepared, integrating) holds its declared `Files:` paths as normalized exact paths, so two
tasks with overlapping declarations are serialized even though each worker has its own
worktree; a side state and `published` release the paths. The comparison is case-folded when
the platform is not case-sensitive. A declaration whose ownership the exact intersection
cannot speak about is ambiguous: a directory reference (a trailing slash), a glob pattern, an
absolute path, a parent-relative segment, a platform-ambiguous separator, no declared files,
or a path that is a proper path-segment prefix of another declaration -- of another task or
of the same task. An ambiguous task is exclusive: it is admitted alone and takes the run,
instead of being fed to a path-conflict engine. HITL tasks and tasks carrying
`Concurrency: exclusive` are exclusive too.

Admission. `resume` promotes the run's own reservations first: an already-owned `reserved`
task that is safe to launch -- current iteration, dependencies complete, no overlap with
active or un-integrated work, a free seat, and no exclusive reservation ahead of it -- is
handed back for the coordinator to persist as `preparing`; it is never claimed again. Launch
seats are reserved for `resume` before `start` is selected, and promotions free waiting
reservation slots without changing the unresolved count. `start` then admits new tasks in
document order, skipping a head task it cannot admit (an incompatible reservation, a ready
exclusive task) and admitting a later compatible one. Conflicting reservations are serialized
deterministically: one resumes, the others wait and are re-checked after it is published. A
pending exclusive reservation stops new claims and starts but not other reservations: the run
drains to it and it launches alone when nothing is running.

Capacity. `parallel.seats` bounds the workers (preparing or running); the waiting
reservations (`reserved`) are bounded by `seats` too, and every unresolved assignment together
by `2 * seats`. New starts are bounded by the free seats after the promotions, by the
reservation slots the promotions free, and by the unresolved-assignment bound.

Drain. A prepared or integrating exclusive task is a pending drain: admission stops (`drain`
is true, nothing starts and nothing resumes) until the integration completes and the task is
published. Independent workers may keep computing during an ordinary integration; only an
exclusive transaction requires the drained run.

Shapes:

    admission {schema: 1, start: ["2.4"], resume: ["2.2"], claim_capacity: 2, drain: false,
               reasons: [...]}

`start` and `resume` are stable, document-ordered lists of task numbers. A task that already
has a manifest record is never in `start`; a `reserved` task is resumed for launch, never
claimed again. `claim_capacity` is how many new claims the run may begin now: the cap-derived
budget, or the number of tasks already selected when a ready exclusive task suspends
admission. `reasons` are the deterministic diagnostics that explain an empty or shorter
`start`.

Every failure raises `SchedulerError`, a `ValueError` carrying the `kind` a CLI maps into its
error envelope (`invalid_iterations`, `invalid_manifest`, `invalid_seats`,
`invalid_case_sensitive`). Nothing is coerced and nothing is dropped.
"""
import re

import parallel_contract
import task_grammar

SCHEMA = 1
# §6: a task that is neither published nor in a side state still holds its assignment.
UNRESOLVED_STATES = ('reserved', 'preparing', 'running', 'verifying/reviewing', 'prepared',
                     'integrating')
ACTIVE_STATES = ('preparing', 'running')
# The exclusive-integration states: the drain is pending or in progress.
DRAIN_STATES = ('prepared', 'integrating')
ALL_STATES = parallel_contract.TASK_STATES + parallel_contract.SIDE_STATES
DRIVE_RE = re.compile(r'^[A-Za-z]:')
GLOB_CHARS = '*?['


class SchedulerError(ValueError):
    """An admission violation: `kind` is the envelope's kind, `message` its text."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _fail(kind, message):
    raise SchedulerError(kind, message)


def _object(value, what, kind):
    if not isinstance(value, dict):
        _fail(kind, '{} is not an object: {!r}'.format(what, value))
    return value


def _integer(value, what, kind, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(kind, '{} is not an integer: {!r}'.format(what, value))
    if value < minimum:
        _fail(kind, '{} must be at least {}: {!r}'.format(what, minimum, value))
    return value


def _text(value, what, kind, allow_empty=False):
    if not isinstance(value, str):
        _fail(kind, '{} is not a string: {!r}'.format(what, value))
    if not value and not allow_empty:
        _fail(kind, '{} is empty'.format(what))
    if value != value.strip() or any(char in value for char in '\x00\n\r'):
        _fail(kind, '{} is not a single-line value without surrounding whitespace: {!r}'.format(
            what, value))
    return value


def _iterations(value):
    """The parser output the selector reads, or `invalid_iterations`."""
    kind = 'invalid_iterations'
    if not isinstance(value, list):
        _fail(kind, 'iterations is not an array: {!r}'.format(value))
    for index, iteration in enumerate(value, 1):
        what = 'iteration {}'.format(index)
        _object(iteration, what, kind)
        _integer(iteration.get('number'), '{} number'.format(what), kind, 1)
        tasks = iteration.get('tasks')
        if not isinstance(tasks, list):
            _fail(kind, '{} tasks is not an array: {!r}'.format(what, tasks))
        for position, task in enumerate(tasks, 1):
            where = '{} task {}'.format(what, position)
            _object(task, where, kind)
            number = task.get('number')
            if not isinstance(number, str) or not parallel_contract.TASK_RE.match(number):
                _fail(kind, '{} number is not an `<iteration>.<n>` id: {!r}'.format(
                    where, number))
            fields = task.get('fields')
            _object(fields, '{} fields'.format(where), kind)
            for name, field in fields.items():
                _object(field, '{} field `{}`'.format(where, name), kind)
                if 'value' not in field:
                    _fail(kind, '{} field `{}` has no value'.format(where, name))
                _text(field['value'], '{} field `{}` value'.format(where, name), kind,
                      allow_empty=True)
            hitl = task.get('hitl')
            if hitl is not None and not isinstance(hitl, str):
                _fail(kind, '{} hitl is not a string or null: {!r}'.format(where, hitl))
            steps = task.get('steps')
            if not isinstance(steps, list):
                _fail(kind, '{} steps is not an array: {!r}'.format(where, steps))
            for step in steps:
                _object(step, '{} step'.format(where), kind)
                if not isinstance(step.get('done'), bool):
                    _fail(kind, '{} step done is not a boolean: {!r}'.format(
                        where, step.get('done')))
    return value


def _manifest(value):
    """The manifest sections the selector reads, or `invalid_manifest`.

    The state-owned fields are `parallel_state`'s to validate; a manifest that reaches the
    selector is re-checked for the sections admission depends on (`seats`, `tasks`, `stop`).
    """
    kind = 'invalid_manifest'
    _object(value, 'the manifest', kind)
    for name in ('seats', 'tasks', 'stop'):
        if name not in value:
            _fail(kind, 'the manifest is missing `{}`'.format(name))
    _integer(value['seats'], 'the manifest seats', kind, 1)
    tasks = _object(value['tasks'], 'the manifest tasks', kind)
    for task, record in tasks.items():
        if not isinstance(task, str) or not parallel_contract.TASK_RE.match(task):
            _fail(kind, 'the manifest task id is not an `<iteration>.<n>` number: {!r}'.format(
                task))
        _object(record, 'the task {}'.format(task), kind)
        if record.get('state') not in ALL_STATES:
            _fail(kind, 'the task {} state is not one of {}: {!r}'.format(
                task, ', '.join(ALL_STATES), record.get('state')))
    if value['stop'] is not None:
        _object(value['stop'], 'the manifest stop', kind)
    return value


def _seats(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail('invalid_seats', 'seats is not a positive integer: {!r}'.format(value))
    return value


def _normalize(path, case_sensitive):
    """(comparison key, uncertainty reason) for one declared `Files:` path.

    The key is the lexically normalized repo-relative path -- `.` segments and duplicate
    separators folded, no trailing slash -- case-folded when the platform is not
    case-sensitive. A path whose ownership the exact intersection cannot speak about is
    uncertain: an absolute path, a platform-ambiguous separator, a glob pattern, a
    directory reference, a parent-relative segment, or nothing at all.
    """
    if not path:
        return None, 'empty path'
    if path.startswith('/') or DRIVE_RE.match(path):
        return None, 'absolute path'
    if '\\' in path:
        return None, 'platform-ambiguous separator'
    if any(char in path for char in GLOB_CHARS):
        return None, 'glob pattern'
    if path.endswith('/'):
        return None, 'directory reference'
    segments = [segment for segment in path.split('/') if segment not in ('', '.')]
    if not segments:
        return None, 'empty path'
    if '..' in segments:
        return None, 'parent-relative path'
    key = '/'.join(segments)
    if not case_sensitive:
        key = key.casefold()
    return key, None


def _path_keys(task, case_sensitive):
    """{comparison key: declared path} for a task's certain declared write paths."""
    keys = {}
    for entry in task_grammar.files_of(task):
        key, uncertain = _normalize(entry['path'], case_sensitive)
        if uncertain is None and key not in keys:
            keys[key] = entry['path']
    return keys


def _prefix_owners(tasks, case_sensitive):
    """{task number: (declared path, covered path)} for directory/prefix declarations.

    A declared path that is a proper path-segment prefix of another declared path -- of
    another task or of the same task -- is ambiguous write ownership: the declaration may be
    a directory the exact-path intersection cannot see into, so its task is exclusive.
    """
    declarations = []
    for task in tasks:
        for entry in task_grammar.files_of(task):
            key, uncertain = _normalize(entry['path'], case_sensitive)
            if uncertain is None:
                declarations.append((key, entry['path'], task['number']))
    owners = {}
    for key, path, number in declarations:
        prefix = key + '/'
        covered = next((other_path for other_key, other_path, _ in declarations
                        if other_key.startswith(prefix)), None)
        if covered is not None and number not in owners:
            owners[number] = (path, covered)
    return owners


def _exclusive(task, case_sensitive, prefix):
    """The reason the task may not run beside another worker, or `None`."""
    field = task['fields'].get('Concurrency')
    if field is not None and not field['value']:
        return 'an empty Concurrency annotation'
    concurrency, reason = task_grammar.concurrency_of(task)
    if concurrency is not None:
        if concurrency != 'exclusive':
            return 'an unrecognized Concurrency annotation: {}'.format(concurrency)
        if reason:
            return 'Concurrency: exclusive — {}'.format(reason)
        return 'Concurrency: exclusive (no reason recorded)'
    if task['hitl'] is not None:
        return 'HITL: {}'.format(task['hitl'] or '(no reason recorded)')
    files = task_grammar.files_of(task)
    if not files:
        return 'no declared files: its write ownership is unknown'
    for entry in files:
        _, uncertain = _normalize(entry['path'], case_sensitive)
        if uncertain is not None:
            return 'uncertain write ownership: `{}` ({})'.format(entry['path'], uncertain)
    if prefix is not None:
        path, covered = prefix
        return ('uncertain write ownership: `{}` is a path-segment prefix of'
                ' `{}`'.format(path, covered))
    return None


def _complete(number, by_number, records):
    """A task is complete when its checkboxes are ticked or it is durably published."""
    task = by_number.get(number)
    if task is not None and task_grammar.is_done(task):
        return True
    record = records.get(number)
    return record is not None and record['state'] == 'published'


def _current_iteration(iterations, by_number, records):
    """The first iteration with a task that is neither done nor published, or `None`."""
    for iteration in iterations:
        if any(not _complete(task['number'], by_number, records)
               for task in iteration['tasks']):
            return iteration
    return None


def _ready(task, by_number, records):
    """A task the run has not started whose dependencies are all complete."""
    if task['number'] in records or task_grammar.is_done(task):
        return False
    return all(_complete(dependency, by_number, records)
               for dependency in task_grammar.dependencies(task))


def _conflict(keys, holders):
    """(declared path, holder, holder state) for the first path a holder also holds."""
    for key, path in keys.items():
        for number, state, holder_keys in holders:
            if key in holder_keys:
                return path, number, state
    return None


def _saturation(active, promoted, reservations, unresolved, seats):
    """The binding cap when the new-start budget is zero."""
    if seats - active - promoted <= 0:
        if promoted:
            return ('no free worker seat: {} of {} seats are preparing or running and {}'
                    ' reservations resume'.format(active, seats, promoted))
        return 'no free worker seat: {} of {} seats are preparing or running'.format(
            active, seats)
    if seats - reservations + promoted <= 0:
        return 'no free reservation slot: {} of {} reservations stay waiting'.format(
            reservations - promoted, seats)
    return 'the unresolved-assignment bound is reached: {} of {}'.format(unresolved, 2 * seats)


def _drain_reason(exclusive):
    """The reason for the first exclusive task that awaits or executes integration."""
    for number, state, _ in exclusive:
        if state in DRAIN_STATES:
            if state == 'prepared':
                return ('drain pending: the exclusive integration of {} waits for the workers'
                        ' to return'.format(number))
            return 'drain in progress: {} integrates exclusively'.format(number)
    return None


def _result(start, resume, claim_capacity, drain, reasons):
    return {'schema': SCHEMA, 'start': start, 'resume': resume,
            'claim_capacity': claim_capacity, 'drain': drain, 'reasons': reasons}


def select_admissions(iterations, manifest, seats, case_sensitive):
    """What the coordinator may admit now: `start`, `resume`, `claim_capacity`, `drain`,
    `reasons`.

    `iterations` is `task_grammar.parse` output, `manifest` the schema-1 run manifest, and
    `seats` the run's `parallel.seats`, which must match the manifest's. `case_sensitive` is
    the platform's path case sensitivity. `start` is the stable document-ordered list of new
    task numbers to admit now; `resume` the stable document-ordered list of already-owned
    `reserved` tasks that are safe to launch (persist `preparing` before any effect, never
    claim again); `claim_capacity` the number of new claims the run may begin now; `drain`
    true while an exclusive integration is pending or in progress; and `reasons` the
    diagnostics behind an empty or shorter `start`.
    """
    _iterations(iterations)
    value = _manifest(manifest)
    seats = _seats(seats)
    if value['seats'] != seats:
        _fail('invalid_seats', 'seats {} does not match the run manifest\'s {}'.format(
            seats, value['seats']))
    if not isinstance(case_sensitive, bool):
        _fail('invalid_case_sensitive', 'case_sensitive is not a boolean: {!r}'.format(
            case_sensitive))

    records = value['tasks']
    tasks = []
    by_number = {}
    iteration_of = {}
    for iteration in iterations:
        for task in iteration['tasks']:
            tasks.append(task)
            by_number[task['number']] = task
            iteration_of[task['number']] = iteration
    prefix = _prefix_owners(tasks, case_sensitive)

    reasons = []
    held = []       # (task, state, keys) of the unresolved non-reserved non-exclusive tasks
    exclusive = []  # (task, state, why) of the unresolved exclusive tasks outside reserved
    waiting = []    # (task, why) of the reserved tasks, in document order
    for task in tasks:
        number = task['number']
        record = records.get(number)
        if record is None or record['state'] not in UNRESOLVED_STATES:
            continue
        why = _exclusive(task, case_sensitive, prefix.get(number))
        if record['state'] == 'reserved':
            waiting.append((number, why))
        elif why is not None:
            exclusive.append((number, record['state'], why))
        else:
            held.append((number, record['state'], _path_keys(task, case_sensitive)))
    for number in sorted(records):
        if number in by_number:
            continue
        state = records[number]['state']
        if state not in UNRESOLVED_STATES:
            continue
        why = 'no task block in the tasklist: its write ownership is unknown'
        if state == 'reserved':
            waiting.append((number, why))
        else:
            exclusive.append((number, state, why))

    drain = any(state in DRAIN_STATES for _, state, _ in exclusive)

    if value['stop'] is not None:
        reasons.append('admission is stopped for reconciliation ({}); reconcile the queue'
                       ' before admitting'.format(value['stop'].get('reason')
                                                  or 'no reason recorded'))
        return _result([], [], 0, drain, reasons)

    if exclusive:
        number, _, why = exclusive[0]
        reasons.append('{} is exclusive ({}): the run is held from admission to'
                       ' publication'.format(number, why))
        if drain:
            reasons.append(_drain_reason(exclusive))
        return _result([], [], 0, drain, reasons)

    active = sum(1 for record in records.values() if record['state'] in ACTIVE_STATES)
    reservations = len(waiting)
    unresolved = sum(1 for record in records.values() if record['state'] in UNRESOLVED_STATES)
    current = _current_iteration(iterations, by_number, records)

    # Promote the run's own reservations before selecting any new claim.
    resume = []
    resumed = []  # (task, 'resumed in this call', keys)
    exclusive_number = None
    free = seats - active
    for number, why in waiting:
        if len(resume) >= free:
            reasons.append('{} waits: no free worker seat ({} of {} seats are preparing or'
                           ' running)'.format(number, active, seats))
            continue
        if exclusive_number is not None:
            reasons.append('{} waits: {} is an exclusive reservation and holds the run until'
                           ' it is published'.format(number, exclusive_number))
            continue
        if current is None or iteration_of.get(number) is not current:
            reasons.append('{} waits: its iteration is not the current unfinished'
                           ' one'.format(number))
            continue
        missing = next((dependency for dependency in task_grammar.dependencies(by_number[number])
                        if not _complete(dependency, by_number, records)), None)
        if missing is not None:
            reasons.append('{} waits for {}'.format(number, missing))
            continue
        if why is not None:
            if active > 0 or resume:
                reasons.append('{} is exclusive ({}): it waits for a quiet run'.format(
                    number, why))
                continue
            resume.append(number)
            exclusive_number = number
            reasons.append('{} is exclusive ({}): resumed alone; no further admission until'
                           ' it is published'.format(number, why))
            continue
        keys = _path_keys(by_number[number], case_sensitive)
        conflict = _conflict(keys, held + resumed)
        if conflict is not None:
            path, holder, state = conflict
            reasons.append('{} waits: it shares {} with {}, which is {}'.format(
                number, path, holder, state))
            continue
        resume.append(number)
        resumed.append((number, 'resumed in this call', keys))

    pending_exclusive = next((number for number, why in waiting if why is not None), None)
    if pending_exclusive is not None:
        reasons.append('{} is a pending exclusive reservation: no new claim or start until it'
                       ' is published'.format(pending_exclusive))
        return _result([], resume, 0, drain, reasons)

    promoted = len(resume)
    budget = max(0, min(seats - active - promoted,
                        seats - reservations + promoted,
                        2 * seats - unresolved))
    if budget == 0:
        reasons.append(_saturation(active, promoted, reservations, unresolved, seats))
        return _result([], resume, 0, drain, reasons)

    if current is None:
        reasons.append('no iteration carries an unfinished task: nothing waits for admission')
        return _result([], resume, budget, drain, reasons)

    ready = [task for task in current['tasks'] if _ready(task, by_number, records)]
    if not ready:
        reasons.append('iteration {} has no ready task'.format(current['number']))
        return _result([], resume, budget, drain, reasons)

    waiting_holders = [(number, 'reserved', _path_keys(by_number[number], case_sensitive))
                       for number, _ in waiting if number in by_number]
    start = []
    selected = []  # (task, 'selected in this call', {key: declared path})
    suspended = False
    for task in ready:
        if len(start) >= budget:
            reasons.append('claim capacity ({}) is exhausted; further ready tasks'
                           ' wait'.format(budget))
            break
        number = task['number']
        why = _exclusive(task, case_sensitive, prefix.get(number))
        if why is not None:
            if not start and unresolved == 0:
                start.append(number)
                reasons.append('{} is exclusive ({}): admitted alone; no further admission'
                               ' until it is published'.format(number, why))
            else:
                reasons.append('{} is exclusive ({}): it waits for a quiet run; no admission'
                               ' behind it'.format(number, why))
            suspended = True
            break
        keys = _path_keys(task, case_sensitive)
        conflict = _conflict(keys, held + waiting_holders + selected)
        if conflict is not None:
            path, holder, state = conflict
            reasons.append('{} shares {} with {}, which is {}'.format(
                number, path, holder, state))
            continue
        start.append(number)
        selected.append((number, 'selected in this call', keys))

    if suspended:
        return _result(start, resume, len(start), drain, reasons)
    return _result(start, resume, budget, drain, reasons)
