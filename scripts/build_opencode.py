#!/usr/bin/env python3
"""Generate the OpenCode build of artel: `artel-`-prefixed skills, command
wrappers and agents under <out> (default <root>/opencode/dist).

OpenCode discovers skills, agents and commands from flat config directories
(~/.config/opencode/{skills,agents,commands}) with no plugin namespace, so
every generated artifact is prefixed `artel-`. Skill and agent bodies are
written in artel's Claude Code dialect; the generator prepends a host glossary
to each one and makes exactly three mechanical rewrites: ${CLAUDE_PLUGIN_ROOT}
is baked to the install root, `/artel:<name>` references in bodies become
`artel-<name>`, and `/artel:<name>` prefixes in the frontmatter descriptions
of skills, commands and agents become `artel-<name>` (`/ast-index:` references
are left alone). A skill's other Markdown files — feature-development's head and
tail files, templates, references — are emitted next to its generated SKILL.md,
baked the same way but with no frontmatter and no glossary, and a
`${CLAUDE_PLUGIN_ROOT}/skills/<name>/<file>` pointer to a file this build emits
is rewritten to the generated copy. Canonical sources under skills/ and agents/
are never modified. Contract: docs/opencode.md.
"""
import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKILL_GLOSSARY = """
<OPENCODE-HOST-NOTES>
This is the OpenCode 2.x build of artel. The body below is written in artel's Claude Code
dialect; this glossary translates it. Apply it throughout:

- `Skill: <name>` or "invoke the `<name>` skill" — call the `skill` tool with `artel-<name>`
  and follow it.
- A `/artel:<name>` command — the `artel-<name>` skill (TUI: `/artel-<name>`); those
  references are already rewritten below.
- The `Agent` tool with `subagent_type: "<name>"` — the `subagent` tool with the `artel-<name>`
  agent.
- `SendMessage` to an agent id — dispatch a fresh `subagent` to the same `artel-<name>` agent
  with the message as its prompt. OpenCode has no resume-by-id: the agent re-reads its
  context files, which are its state.
- `AskUserQuestion` — the `question` tool. It has no `preview` field: when an option carries
  a `preview`, put that sketch into the question text as a fenced block, under the option's
  label.
- `EnterPlanMode` / "plan mode" — OpenCode has no plan-mode tool. Present the plan in the
  conversation and wait for the user's approval; the host's built-in `plan` agent is the
  user's to switch to, not yours.
- `EnterWorktree` / `ExitWorktree` — not available on OpenCode; follow the skill's
  **OpenCode:** instruction at that step instead.
- `$0`, `$1`, ..., `$ARGUMENTS` — the arguments this skill was invoked with (from the
  `/artel-<name>` command or the caller's request).
- `CLAUDE.md` — the host project's conventions doc (on OpenCode that is `AGENTS.md`; OpenCode
  reads no `CLAUDE.md`).
- `/init` — refresh the host project's conventions doc by loading the
  `artel-agents-md-generator` skill.
- Tool names in the body (`Read`, `Edit`, `Write`, `Grep`, `Bash`) are Claude Code's; on
  OpenCode 2.x they are `read`, `edit`, `write`, `grep`, `shell`.
- MCP tool names (`mcp__tracker__*`, `search_knowledge`, ...) — this session's MCP tools,
  resolved per `.artel/config.json` exactly as the body says (OpenCode 2.x configures
  servers under `mcp.servers`).
</OPENCODE-HOST-NOTES>
"""

AGENT_GLOSSARY = """
<OPENCODE-HOST-NOTES>
OpenCode 2.x build of artel. The body below is written in artel's Claude Code dialect;
glossary:

- The `Agent` tool / `subagent_type` — the `subagent` tool (artel agents are named
  `artel-<name>`).
- `SendMessage` — a fresh `subagent` dispatch to the same agent; your context files are your
  state, re-read them.
- `AskUserQuestion` — the `question` tool (you never prompt the user directly anyway).
- `CLAUDE.md` — the host project's conventions doc (on OpenCode that is `AGENTS.md`; OpenCode
  reads no `CLAUDE.md`).
- Tool names (`Read`, `Edit`, `Write`, `Grep`, `Bash`) — `read`, `edit`, `write`, `grep`, `shell`.
</OPENCODE-HOST-NOTES>
"""

VALID_NAME = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
ARTEL_REF = re.compile(r'/artel:([a-z0-9-]+)')
SKILL_FILE_REF = re.compile(r'\$\{CLAUDE_PLUGIN_ROOT\}/skills/([a-z0-9-]+)/([\w./-]+)')
MAX_DESCRIPTION = 1024
AGENTS_EXCLUDE = frozenset({'seat'})  # Claude Code only: OpenCode runs the tail inline (docs/opencode.md)


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


def bake(body, root, generated=None):
    """Claude dialect -> OpenCode dialect for a body: bake the install root,
    prefix `/artel:` references. `generated` maps (skill folder, relative path)
    to the generated copy of every Markdown file this build emits under a
    skill's directory: a `${CLAUDE_PLUGIN_ROOT}/skills/<name>/<file>` pointer to
    one of them lands on that copy, which is baked, instead of on the
    Claude-dialect source. Any other plugin-root path resolves under the install
    root. Everything else is the glossary's job."""
    def land(match):
        target = (generated or {}).get((match.group(1), match.group(2)))
        return str(target) if target else match.group(0)

    body = SKILL_FILE_REF.sub(land, body)
    body = body.replace('${CLAUDE_PLUGIN_ROOT}', str(root))
    return ARTEL_REF.sub(r'artel-\1', body)


def clip(value, limit):
    return value if len(value) <= limit else value[:limit - 3] + '...'


def yaml_quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def assemble(body, glossary, frontmatter):
    parts = ['---']
    parts.extend(frontmatter)
    parts.append('---')
    parts.append('')
    parts.append(glossary.strip())
    parts.append('')
    parts.append(body.strip())
    return '\n'.join(parts) + '\n'


def build_skill(src_text, root, generated=None):
    fields, body = parse_frontmatter(src_text)
    name = 'artel-' + fields['name']
    description = ARTEL_REF.sub(r'artel-\1', fields.get('description', ''))
    frontmatter = [
        'name: ' + name,
        'description: ' + yaml_quote(clip(description, MAX_DESCRIPTION)),
        'license: MIT',
    ]
    if fields.get('disable-model-invocation') == 'true':
        # v2's manual-only switch: registered, loadable by id, absent from the model's list.
        frontmatter.extend(['metadata:', '  opencode/autoinvoke: false'])
    return name, assemble(bake(body, root, generated), SKILL_GLOSSARY, frontmatter)


def build_agent(src_text, root, generated=None):
    """agents/<name>.md (Claude frontmatter: name/description/model) ->
    artel-<name>.md (OpenCode frontmatter: description/mode). The markdown file
    name is the agent name on OpenCode; model tiers are dropped (subagents
    inherit the caller's model — see docs/opencode.md)."""
    fields, body = parse_frontmatter(src_text)
    name = 'artel-' + fields['name']
    description = ARTEL_REF.sub(r'artel-\1', fields.get('description', ''))
    frontmatter = [
        'description: ' + yaml_quote(clip(description, MAX_DESCRIPTION)),
        'mode: subagent',
    ]
    return name, assemble(bake(body, root, generated), AGENT_GLOSSARY, frontmatter)


def build_command(name, description, hint):
    description = ARTEL_REF.sub(r'artel-\1', description)
    if hint:
        args = ('Arguments (positional, per the skill\'s argument-hint `' + hint + '`):')
    else:
        args = 'Arguments (if any):'
    lines = [
        '---',
        'description: ' + yaml_quote(clip(description, MAX_DESCRIPTION)),
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
                        help='install root the generated files live beside '
                             '(paths are baked to it)')
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

    # Every Markdown file under a skill's directory, and where its generated copy goes.
    # bake() rewrites a pointer into a skill's directory against this map.
    generated = {}
    companions = []
    for skill_path in skill_files:
        fields, _ = parse_frontmatter(skill_path.read_text(encoding='utf-8'))
        folder = skill_path.parent
        dest_dir = out / 'skills' / ('artel-' + fields['name'])
        for path in sorted(folder.rglob('*.md')):
            rel = path.relative_to(folder).as_posix()
            generated[(folder.name, rel)] = dest_dir / rel
            if path != skill_path:
                companions.append((path, dest_dir / rel))

    for skill_path in skill_files:
        src = skill_path.read_text(encoding='utf-8')
        name, text = build_skill(src, root, generated)
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

    # A skill's other Markdown files: baked like a body, with no frontmatter and no
    # glossary — their reader already has the glossary through the skill's SKILL.md.
    for path, dest in companions:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(bake(path.read_text(encoding='utf-8'), root, generated),
                        encoding='utf-8')

    agent_files = sorted(p for p in (source / 'agents').glob('*.md')
                         if p.name != 'README.md' and p.stem not in AGENTS_EXCLUDE)

    for agent_path in agent_files:
        name, text = build_agent(agent_path.read_text(encoding='utf-8'), root, generated)
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
