#!/usr/bin/env python3
"""Generate the OpenCode build of artel: a baked install copy and command wrappers.

The install root carries the canonical skills/, agents/ and docs/. Every `*.md`
under those three trees is baked in place: `${ARTEL_ROOT}` becomes the
install root and `/artel:<name>` references become `artel-<name>`, so an agent on
OpenCode reads a real path, never an unset variable. Python files are never
rewritten: a hook docstring's token is a source-level pointer to a contract file,
and nothing expands it.

Each skill is also emitted under <out> (default <root>/opencode/dist) with
OpenCode-legal frontmatter, its other Markdown files beside it, and a command
wrapper under <out>/commands that loads the skill and passes its arguments.
Canonical sources under skills/ and agents/ are never modified. Contract:
docs/opencode.md.
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
ARTEL_REF = re.compile(r'/artel:([a-z0-9-]+)')
MAX_DESCRIPTION = 1024
AGENTS_EXCLUDE = frozenset({'artel-seat'})  # Claude Code only: OpenCode runs the tail inline (docs/opencode.md)


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


def bake(body, root):
    """Claude dialect -> OpenCode dialect for a body: bake the install root,
    prefix `/artel:` references."""
    return ARTEL_REF.sub(r'artel-\1', bake_root(body, root))


def bake_root(text, root):
    """The `${ARTEL_ROOT}` rewrite alone; bake() layers the `/artel:` rewrite
    on top."""
    return text.replace('${ARTEL_ROOT}', str(root))


def clip(value, limit):
    return value if len(value) <= limit else value[:limit - 3] + '...'


def yaml_quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def assemble(body, frontmatter):
    parts = ['---']
    parts.extend(frontmatter)
    parts.append('---')
    parts.append('')
    parts.append(body.strip())
    return '\n'.join(parts) + '\n'


def build_skill(src_text, root):
    fields, body = parse_frontmatter(src_text)
    name = fields['name']
    description = ARTEL_REF.sub(r'artel-\1', fields.get('description', ''))
    frontmatter = [
        'name: ' + name,
        'description: ' + yaml_quote(clip(description, MAX_DESCRIPTION)),
        'license: MIT',
    ]
    if fields.get('disable-model-invocation') == 'true':
        # v2's manual-only switch: registered, loadable by id, absent from the model's list.
        frontmatter.extend(['metadata:', '  opencode/autoinvoke: false'])
    return name, assemble(bake(body, root), frontmatter)


def build_agent(src_text, root):
    """agents/<name>.md (Claude frontmatter: name/description/model) ->
    artel-<name>.md (OpenCode frontmatter: description/mode). The markdown file
    name is the agent name on OpenCode; model tiers are dropped (subagents
    inherit the caller's model — see docs/opencode.md)."""
    fields, body = parse_frontmatter(src_text)
    name = fields['name']
    description = ARTEL_REF.sub(r'artel-\1', fields.get('description', ''))
    frontmatter = [
        'description: ' + yaml_quote(clip(description, MAX_DESCRIPTION)),
        'mode: subagent',
    ]
    return name, assemble(bake(body, root), frontmatter)


def build_command(name, description, hint):
    description = ARTEL_REF.sub(r'artel-\1', description)
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
                        help='checkout to read skills/ and agents/ from (default: this repo)')
    parser.add_argument('--out', default=None,
                        help='output directory (default: <root>/opencode/dist)')
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    source = Path(args.source).resolve()
    out = Path(args.out).resolve() if args.out else root / 'opencode' / 'dist'

    skill_files = sorted((source / 'skills').glob('*/SKILL.md'))
    if not skill_files:
        print('build_opencode: no skills/ under {}'.format(source), file=sys.stderr)
        return 2

    # The install copy is baked in place — Markdown only: a hook docstring's token is a
    # source-level pointer to a contract file, and nothing expands it.
    for tree in ('skills', 'agents', 'docs'):
        for path in sorted((root / tree).rglob('*.md')):
            path.write_text(bake(path.read_text(encoding='utf-8'), root), encoding='utf-8')

    # Every other Markdown file under a skill's directory, and where its generated copy goes.
    companions = []
    for skill_path in skill_files:
        fields, _ = parse_frontmatter(skill_path.read_text(encoding='utf-8'))
        dest_dir = out / 'skills' / fields['name']
        for path in sorted(skill_path.parent.rglob('*.md')):
            if path != skill_path:
                companions.append((path, dest_dir / path.relative_to(skill_path.parent)))

    for skill_path in skill_files:
        src = skill_path.read_text(encoding='utf-8')
        name, text = build_skill(src, root)
        if not VALID_NAME.match(name):
            print('build_opencode: {} is not a legal OpenCode name'.format(name),
                  file=sys.stderr)
            return 2
        dest = out / 'skills' / name / 'SKILL.md'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')

        fields, _ = parse_frontmatter(src)
        command = build_command(name, fields.get('description', ''),
                                fields.get('argument-hint', ''))
        commands_dir = out / 'commands'
        commands_dir.mkdir(parents=True, exist_ok=True)
        (commands_dir / (name + '.md')).write_text(command, encoding='utf-8')

    # A skill's other Markdown files: baked like a body, with no frontmatter.
    for path, dest in companions:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(bake(path.read_text(encoding='utf-8'), root), encoding='utf-8')

    agent_files = sorted(p for p in (source / 'agents').glob('*.md')
                         if p.name != 'README.md' and p.stem not in AGENTS_EXCLUDE)

    for agent_path in agent_files:
        name, text = build_agent(agent_path.read_text(encoding='utf-8'), root)
        if not VALID_NAME.match(name):
            print('build_opencode: {} is not a legal OpenCode name'.format(name),
                  file=sys.stderr)
            return 2
        agents_dir = out / 'agents'
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / (name + '.md')).write_text(text, encoding='utf-8')

    print('build_opencode: {} skills, {} agents, {} other skill files -> {}'.format(
        len(skill_files), len(agent_files), len(companions), out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
