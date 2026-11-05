"""bratishka-config: the `bratishka` block and the testing-PR date templates.

Reads the `bratishka` block from `.artel/config.json`, merges it per key over
DEFAULTS, and renders or parses the `testingPr` title and branch templates. The
block is optional: a config without it behaves exactly like DEFAULTS, and
unknown keys are ignored. A wrong-typed value, an unknown `spawn.adapter`, an
uncompilable `re:` exclude pattern, a malformed `repo` or a template carrying no
date token raises ConfigError (`invalid_config`) naming its key.

Pure library: no CLI, no network, no third-party dependencies. The caller gets
the merged block and the date helpers only, and no call can mutate DEFAULTS.

Contract: docs/config.md (`bratishka`), docs/bratishka-duty.md (`## Testing PR`).
"""
import copy
import json
import re
from datetime import date
from pathlib import Path

DEFAULTS = {
    'dashboardUrl': '',
    'repo': '',
    'botLogin': 'bratishka-bot',
    'baseRef': '',
    'teams': {
        'codeReview': '',
        'qa': '',
    },
    'qaLogins': [],
    'excludeTitlePatterns': [],
    'fixCommand': '/agent',
    'spawn': {
        'adapter': 'manual',
        'tokenEnv': '',
        'mcpToolPrefix': '',
        'promptFile': '',
    },
    'testingPr': {
        'titleTemplate': 'Testing {dd}-{mm}-{yy}',
        'branchTemplate': 'feature/testing-{dd}-{mm}-{yy}',
        'deleteBranchOnCancel': False,
        'bodyFile': '',
    },
}

SPAWN_ADAPTERS = ('manual', 'client-api', 'mcp')
REPO_RE = re.compile(r'[^\s/]+/[^\s/]+')
TEMPLATE_TOKEN_RE = re.compile(r'\{(?:yyyy|yy|dd|mm)\}')
# Longest token first so `{yyyy}` is never mistaken for `{yy}` plus text.
_DATE_TOKENS = (
    ('yyyy', r'(?P<yyyy>\d{4})'),
    ('yy', r'(?P<yy>\d{2})'),
    ('dd', r'(?P<dd>\d{2})'),
    ('mm', r'(?P<mm>\d{2})'),
)
_SCALAR_KEYS = ('dashboardUrl', 'repo', 'botLogin', 'baseRef', 'qaLogins',
                'excludeTitlePatterns', 'fixCommand')
_SECTIONS = ('teams', 'spawn', 'testingPr')


class ConfigError(Exception):
    """An invalid `bratishka` block; `kind` is always `invalid_config`."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _refuse(key, expected, value):
    raise ConfigError('invalid_config',
                      '{} is not {}: {!r}'.format(key, expected, value))


def _string(value, key):
    if not isinstance(value, str):
        _refuse(key, 'a string', value)


def _string_list(value, key):
    if not isinstance(value, list):
        _refuse(key, 'an array of strings', value)
    for index, item in enumerate(value):
        if not isinstance(item, str):
            _refuse('{}[{}]'.format(key, index), 'a string', item)


def _merge(block, given):
    """Overlay the known keys of `given` onto `block`; unknown keys are ignored.

    A `null` value counts as absent, like a missing key, and an explicit section
    object is merged key by key so a partial one keeps the other defaults.
    """
    for key in _SCALAR_KEYS:
        value = given.get(key)
        if value is not None:
            block[key] = copy.deepcopy(value)
    for section in _SECTIONS:
        value = given.get(section)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ConfigError('invalid_config',
                              'bratishka.{} is not an object: {!r}'.format(section, value))
        for key in block[section]:
            if value.get(key) is not None:
                block[section][key] = copy.deepcopy(value[key])


def _validate(block):
    """Refuse every listed key whose value breaks docs/config.md's rules."""
    for key in ('dashboardUrl', 'repo', 'botLogin', 'baseRef', 'fixCommand'):
        _string(block[key], 'bratishka.' + key)
    _string_list(block['qaLogins'], 'bratishka.qaLogins')
    _string_list(block['excludeTitlePatterns'], 'bratishka.excludeTitlePatterns')
    for index, pattern in enumerate(block['excludeTitlePatterns']):
        if pattern.startswith('re:'):
            try:
                re.compile(pattern[3:])
            except re.error:
                raise ConfigError(
                    'invalid_config',
                    'bratishka.excludeTitlePatterns[{}] is not a valid regex: {!r}'.format(
                        index, pattern))
    if block['repo'] and REPO_RE.fullmatch(block['repo']) is None:
        raise ConfigError('invalid_config',
                          'bratishka.repo is not owner/name: {!r}'.format(block['repo']))

    for key in ('codeReview', 'qa'):
        _string(block['teams'][key], 'bratishka.teams.' + key)

    _string(block['spawn']['adapter'], 'bratishka.spawn.adapter')
    if block['spawn']['adapter'] not in SPAWN_ADAPTERS:
        raise ConfigError(
            'invalid_config',
            'bratishka.spawn.adapter is not one of {}: {!r}'.format(
                ', '.join(SPAWN_ADAPTERS), block['spawn']['adapter']))
    for key in ('tokenEnv', 'mcpToolPrefix', 'promptFile'):
        _string(block['spawn'][key], 'bratishka.spawn.' + key)

    for key in ('titleTemplate', 'branchTemplate'):
        template = block['testingPr'][key]
        _string(template, 'bratishka.testingPr.' + key)
        if TEMPLATE_TOKEN_RE.search(template) is None:
            raise ConfigError(
                'invalid_config',
                'bratishka.testingPr.{} has none of {{dd}}, {{mm}}, {{yy}}, '
                '{{yyyy}}: {!r}'.format(key, template))
    if not isinstance(block['testingPr']['deleteBranchOnCancel'], bool):
        _refuse('bratishka.testingPr.deleteBranchOnCancel', 'a boolean',
                block['testingPr']['deleteBranchOnCancel'])
    _string(block['testingPr']['bodyFile'], 'bratishka.testingPr.bodyFile')


def load(repo):
    """The merged `bratishka` block for the host repo at `repo`.

    A missing `.artel/config.json` returns a deep copy of DEFAULTS, so callers
    cannot mutate the defaults. An unreadable or malformed file, a `bratishka`
    value that is not an object, or a value that fails validation raises
    ConfigError (`invalid_config`).
    """
    block = copy.deepcopy(DEFAULTS)
    path = Path(repo) / '.artel' / 'config.json'
    if not path.is_file():
        return block
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ConfigError('invalid_config',
                          'unreadable .artel/config.json: {}'.format(path))
    if not isinstance(document, dict):
        raise ConfigError('invalid_config',
                          'the config root is not an object: {!r}'.format(document))
    given = document.get('bratishka')
    if given is None:
        return block
    if not isinstance(given, dict):
        raise ConfigError('invalid_config',
                          'bratishka is not an object: {!r}'.format(given))
    _merge(block, given)
    _validate(block)
    if block['dashboardUrl'].endswith('/'):
        block['dashboardUrl'] = block['dashboardUrl'][:-1]
    return block


def render(template, day):
    """`template` with its date tokens filled from `day`, e.g. `Testing 01-11-26`."""
    return (template
            .replace('{yyyy}', '{:04d}'.format(day.year))
            .replace('{yy}', '{:02d}'.format(day.year % 100))
            .replace('{mm}', '{:02d}'.format(day.month))
            .replace('{dd}', '{:02d}'.format(day.day)))


def _date_pattern(template):
    pattern = re.escape(template)
    for token, group in _DATE_TOKENS:
        pattern = pattern.replace('\\{' + token + '\\}', group)
    return pattern


def match_date(template, value):
    """The date `value` spells under `template`, or None when it spells none.

    Matching is a full match: the literal text, including separators, must be
    exactly what the template says. A two-digit year means 2000 + yy, and an
    impossible calendar date yields None rather than raising.
    """
    if not isinstance(template, str) or not isinstance(value, str):
        return None
    try:
        match = re.fullmatch(_date_pattern(template), value)
    except re.error:
        return None
    if match is None:
        return None
    groups = match.groupdict()
    if groups.get('dd') is None or groups.get('mm') is None:
        return None
    if groups.get('yyyy') is not None:
        year = int(groups['yyyy'])
    elif groups.get('yy') is not None:
        year = 2000 + int(groups['yy'])
    else:
        return None
    try:
        return date(year, int(groups['mm']), int(groups['dd']))
    except ValueError:
        return None


def testing_date(title, branch, config):
    """The date a testing PR's title or branch spells, title first, or None."""
    testing = config.get('testingPr') if isinstance(config, dict) else None
    if not isinstance(testing, dict):
        return None
    for template, value in ((testing.get('titleTemplate'), title),
                            (testing.get('branchTemplate'), branch)):
        found = match_date(template, value)
        if found is not None:
            return found
    return None
