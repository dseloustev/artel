#!/usr/bin/env python3
"""Generate the OpenCode build of artel: a baked install copy and command wrappers.

The install root carries the canonical skills/, agents/ and docs/. Every `*.md`
under those three trees is baked in place: `${ARTEL_ROOT}` becomes the
install root, so an agent on OpenCode reads a real path, never an unset variable.
Python files are never rewritten: a hook docstring's token is a source-level pointer to a
contract file, and nothing expands it.

The build's only other output is the command wrappers under <out> (default
<root>/opencode/dist): one `/artel-<name>` wrapper per skill, whose body loads the skill and
passes its arguments. Skills and agents are not copied into <out> — the installer places them
from the baked trees themselves, so `dist` holds command wrappers and nothing else.
Canonical sources under skills/ and agents/ are never modified: a `--root` that is the source
checkout is refused before the first write. Contract: docs/opencode.md.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
MAX_DESCRIPTION = 1024


def is_source_checkout(root):
    """True when `root` is the checkout this repository is developed in, as opposed to an
    installed copy.

    The installer runs a copy of this script from inside the install root with
    `--root <install root>`, so `root` and the script's own directory coincide there too —
    a bare `root == source` test would break every installation. Three markers separate the
    two: the copy has no `tests/` tree, its `scripts/build_opencode.py` is a copy rather than
    the tracked file, and it is not a git checkout of this repository.
    """
    if not (root / 'tests').is_dir():
        return False
    if not (root / 'scripts' / 'build_opencode.py').is_file():
        return False
    try:
        tracked = subprocess.run(
            ['git', '-C', str(root), 'ls-files', '--error-unmatch',
             'scripts/build_opencode.py'],
            capture_output=True, text=True)
    except OSError:
        # No git to prove otherwise, and the tests/ tree says checkout: refuse rather than
        # risk baking the canonical placeholders in place.
        return True
    return tracked.returncode == 0


def parse_frontmatter(text):
    """(fields, body): flat `key: value` frontmatter — no YAML engine needed.

    Values keep everything after the first colon, outer quotes stripped. A value of
    exactly `>` or `>-` is a folded scalar: the lines after it that are indented
    deeper than the key (or blank) are consumed up to the first non-indented line
    and joined with single spaces. The body starts after the closing `---`
    marker. Files without frontmatter come back whole.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        return {}, text
    fields = {}
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == '---':
            return fields, ''.join(lines[index + 1:]).lstrip('\n')
        if ':' in line:
            key, value = line.split(':', 1)
            key_indent = len(key) - len(key.lstrip(' \t'))
            key, value = key.strip(), value.strip()
            if value in ('>', '>-'):
                folded = []
                index += 1
                while index < len(lines):
                    cont = lines[index]
                    cont_indent = len(cont) - len(cont.lstrip(' \t'))
                    if cont.strip() and cont_indent <= key_indent:
                        break
                    folded.append(cont.strip())
                    index += 1
                fields[key] = ' '.join(folded)
                continue
            fields[key] = value.strip('"\'')
        index += 1
    return fields, text


def bake(text, root):
    """Bake the install root into a body: `${ARTEL_ROOT}` becomes the install root."""
    return text.replace('${ARTEL_ROOT}', str(root))


def clip(value, limit):
    return value if len(value) <= limit else value[:limit - 3] + '...'


def yaml_quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def build_command(name, description, hint):
    # OpenCode shows only the description in its command list, so the skill's hint
    # travels there too; room is reserved for it inside MAX_DESCRIPTION (clip() needs
    # at least three characters) and the hint itself is never clipped.
    suffix = ' · args: ' + hint if hint else ''
    display = clip(description, max(3, MAX_DESCRIPTION - len(suffix))) + suffix
    if hint:
        args = ('Arguments (positional, per the skill\'s argument-hint `' + hint + '`):')
    else:
        args = 'Arguments (if any):'
    lines = [
        '---',
        'description: ' + yaml_quote(display),
        'agent: build',
        '---',
        '',
        'Load the `' + name + '` skill with the `skill` tool and execute it now.',
        args,
        '',
        '$ARGUMENTS',
    ]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Generate the OpenCode build of artel.')
    parser.add_argument('--root', required=True,
                        help='install root: its skills/, agents/ and docs/ are baked in '
                             'place, and generated paths are baked to it')
    parser.add_argument('--source', default=str(REPO_ROOT),
                        help='checkout to read skills/ from (default: this repo)')
    parser.add_argument('--out', default=None,
                        help='output directory for the command wrappers '
                             '(default: <root>/opencode/dist)')
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    source = Path(args.source).resolve()
    out = Path(args.out).resolve() if args.out else root / 'opencode' / 'dist'

    # Before the first write: baking the source checkout would replace the canonical
    # placeholders throughout the tree. The installer's own run is not this case — its copy
    # has no tests/ and no tracked builder (is_source_checkout).
    if is_source_checkout(root):
        print('build_opencode: refusing to bake {} — it is the source checkout; run the '
              'installer, or pass an install copy as --root'.format(root), file=sys.stderr)
        return 2

    skill_files = sorted((source / 'skills').glob('*/SKILL.md'))
    if not skill_files:
        print('build_opencode: no skills/ under {}'.format(source), file=sys.stderr)
        return 2
    agents = sorted(p for p in (source / 'agents').glob('*.md') if p.name != 'README.md')

    # The install copy is baked in place — Markdown only: a hook docstring's token is a
    # source-level pointer to a contract file, and nothing expands it.
    for tree in ('skills', 'agents', 'docs'):
        for path in sorted((root / tree).rglob('*.md')):
            path.write_text(bake(path.read_text(encoding='utf-8'), root), encoding='utf-8')

    # Command wrappers — the build's only output. Skills and agents are installed from the
    # baked trees above, so dist holds commands/ and nothing else.
    for skill_path in skill_files:
        fields, _ = parse_frontmatter(skill_path.read_text(encoding='utf-8'))
        name = fields['name']
        if not VALID_NAME.match(name):
            print('build_opencode: {} is not a legal OpenCode name'.format(name),
                  file=sys.stderr)
            return 2
        command = build_command(name, fields.get('description', ''),
                                fields.get('argument-hint', ''))
        commands_dir = out / 'commands'
        commands_dir.mkdir(parents=True, exist_ok=True)
        (commands_dir / (name + '.md')).write_text(command, encoding='utf-8')

    print('build_opencode: {} command wrappers -> {} ({} skills, {} agents baked in place)'
          .format(len(skill_files), out, len(skill_files), len(agents)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
