#!/usr/bin/env python3
"""models: resolve one OpenCode dispatch site's model from the host's config.

Reads `models.opencode` from `.artel/config.json` (docs/config.md) and answers
the model configured for a dispatch site, or `null` when the site is unset.
Contacts nothing; a later task's generated prose calls this CLI and nothing
else from this module.

Site vocabulary: `implementer.light`, `implementer.full`, `implementer.fix`,
`implementer.stepUp`, `reviewer.task`, `reviewer.phase`, `reviewer.reReview`,
`reviewer.plan`, `reviewer.deepReview`, `reviewForecaster`, `agents.<name>`
(`[a-z0-9-]+`; `implementer`, `reviewer` and `review-forecaster` are reserved
under `agents` -- those three resolve by their own fixed sites instead).

A configured value is a non-empty string `provider/model` with an optional
`#variant`: exactly one `/`, no whitespace, non-empty halves, at most one `#`
after the model half with a non-empty variant.

Exit codes: 0 answered (including a `null` model), 2 error envelope
(`invalid_argument` / `invalid_config` / `internal_error`).
Contract: docs/config.md (`models`), docs/superpowers/specs/2026-10-07-opencode-model-selection-design.md

Usage: models resolve --site <key> [--repo <dir>]
"""
import json
import re
import sys
import time
from pathlib import Path

VALUE_RE = re.compile(r'^[^\s/#]+/[^\s/#]+(#[^\s#]+)?$')

SITES = frozenset({
    'implementer.light', 'implementer.full', 'implementer.fix', 'implementer.stepUp',
    'reviewer.task', 'reviewer.phase', 'reviewer.reReview', 'reviewer.plan',
    'reviewer.deepReview', 'reviewForecaster',
})
AGENT_SITE = re.compile(r'^agents\.([a-z0-9-]+)$')
RESERVED_AGENTS = frozenset({'implementer', 'reviewer', 'review-forecaster'})


def value_ok(value):
    return isinstance(value, str) and VALUE_RE.match(value) is not None


def site_key(site):
    """`site` as the key tuple resolve_site walks, or None for an unknown site."""
    if site in SITES:
        return tuple(site.split('.')) if '.' in site else (site,)
    match = AGENT_SITE.match(site)
    if match:
        return ('agents', match.group(1))
    return None


def envelope(ok, elapsed_ms, data=None, error=None, verb='models-resolve'):
    out = {'ok': ok, 'verb': verb, 'elapsed_ms': elapsed_ms}
    if error is not None:
        out['error'] = error
    else:
        out['data'] = data
    return json.dumps(out)


def read_models(repo, fail):
    """`models.opencode` as a dict, or None after reporting the failure.

    Missing file -> {} (no sites configured). Unreadable/malformed JSON, or a
    config root / `models` / `models.opencode` / `agents` that is present
    (non-null) and not an object -> invalid_config and None.
    """
    path = Path(repo) / '.artel' / 'config.json'
    if not path.is_file():
        return {}
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        fail('invalid_config', 'unreadable .artel/config.json: {}'.format(path))
        return None
    if config is not None and not isinstance(config, dict):
        fail('invalid_config', 'the config root is not an object: {!r}'.format(config))
        return None
    models = (config or {}).get('models')
    if models is not None and not isinstance(models, dict):
        fail('invalid_config', 'models is not an object: {!r}'.format(models))
        return None
    opencode = (models or {}).get('opencode')
    if opencode is not None and not isinstance(opencode, dict):
        fail('invalid_config', 'models.opencode is not an object: {!r}'.format(opencode))
        return None
    agents = (opencode or {}).get('agents')
    if agents is not None and not isinstance(agents, dict):
        fail('invalid_config', 'models.opencode.agents is not an object: {!r}'.format(agents))
        return None
    return opencode or {}


def resolve_site(block, site):
    """(model, problem) for `site` against `block` (read_models' result).

    `problem` is an invalid_config message, or None when the site simply has
    no value. Only the resolved site's value is validated -- unused sibling
    values in the block never reach here.
    """
    key = site_key(site)
    if key[0] == 'agents':
        name = key[1]
        if name in RESERVED_AGENTS:
            return None, None
        node = block.get('agents')
        if node is None:
            return None, None
        value = node.get(name)
    else:
        node = block
        path = []
        for part in key:
            path.append(part)
            if node is None:
                return None, None
            value = node.get(part)
            if part != key[-1]:
                if value is not None and not isinstance(value, dict):
                    return None, 'models.opencode.{} is not an object: {!r}'.format(
                        '.'.join(path), value)
                node = value
    if value is None:
        return None, None
    if not value_ok(value):
        return None, 'models.opencode.{} is not a provider/model value: {!r}'.format(
            '.'.join(key), value)
    return value, None


def main(argv=None):
    start = time.monotonic()

    def elapsed():
        return int((time.monotonic() - start) * 1000)

    def fail(kind, message):
        print(envelope(False, elapsed(), error={'kind': kind, 'message': message}))
        return 2

    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return fail('invalid_argument', 'missing command')
    if argv[0] != 'resolve':
        return fail('invalid_argument', 'unknown command: {}'.format(argv[0]))

    site = None
    repo = '.'
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == '--site':
            i += 1
            site = argv[i] if i < len(argv) else ''
        elif arg == '--repo':
            i += 1
            repo = argv[i] if i < len(argv) else ''
        else:
            return fail('invalid_argument', 'unknown flag: {}'.format(arg))
        i += 1
    if site == '':
        return fail('invalid_argument', 'missing value for --site <key>')
    if not repo:
        return fail('invalid_argument', 'missing value for --repo <dir>')
    if site is None:
        return fail('invalid_argument', 'missing required --site <key>')
    if site_key(site) is None:
        return fail('invalid_argument', 'unknown site: {}'.format(site))

    block = read_models(repo, fail)
    if block is None:
        return 2
    model, problem = resolve_site(block, site)
    if problem:
        return fail('invalid_config', problem)
    print(envelope(True, elapsed(), data={'site': site, 'model': model}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(json.dumps({'ok': False, 'verb': 'models-resolve', 'elapsed_ms': 0,
                          'error': {'kind': 'internal_error', 'message': str(exc)}}))
        sys.exit(2)
