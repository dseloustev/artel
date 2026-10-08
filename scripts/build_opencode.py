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
- A dispatch's `model` — the `subagent` tool's `model`, a concrete `provider/model[#variant]`;
  aliases (`sonnet`, `opus`, `fable`) do not resolve here, and there are no agent frontmatter
  defaults.
- Name the model explicitly on every dispatch the config covers; an omitted model inherits your
  session's model, often the most expensive one.
- Resolve a site from the host's `.artel/config.json` `models.opencode` with
  `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site <key>`; `null` means pass no
  model; an error (exit `2`, e.g. `invalid_config`) is reported and stops the run — it is not
  an inherit. The key is the site the skill text names, or `agents.<name>` for an agent dispatch
  — the review-forecaster's is `reviewForecaster`.
- `SendMessage` to an agent id — dispatch a fresh `subagent` to the same `artel-<name>` agent
  with the message as its prompt. OpenCode has no resume-by-id: the agent re-reads its
  context files, which are its state.
- `AskUserQuestion` — the `question` tool. It has no `preview` field: when an option carries
  a `preview`, put that sketch into the question text as a fenced block, under the option's
  label. A sketch the agent wrote as a ```mermaid block keeps that fence — the client renders
  the diagram; a `preview` sketch stays a monospace block.
- `EnterPlanMode` / "plan mode" — OpenCode has no plan-mode tool. Present the plan in the
  conversation and wait for the user's approval; the host's built-in `plan` agent is the
  user's to switch to, not yours.
- `EnterWorktree` / `ExitWorktree` — not available on OpenCode; follow the skill's
  **OpenCode:** instruction at that step instead.
- `$0`, `$1`, ..., `$ARGUMENTS` — the arguments this skill was invoked with (from the
  `/artel-<name>` command or the caller's request).
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
- A frontmatter `model` — no default on OpenCode; the dispatcher names your model.
- `SendMessage` — a fresh `subagent` dispatch to the same agent; your context files are your
  state, re-read them.
- `AskUserQuestion` — the `question` tool (you never prompt the user directly anyway). A
  `preview` sketch for a flow, a structure or a chart is written as a ```mermaid block (fence
  `mermaid`, not `preview`) — `flowchart`, `stateDiagram-v2`, `sequenceDiagram`, `classDiagram`,
  `erDiagram` or `xychart-beta`, kept small (about 12 nodes) — and the OpenChamber client
  renders it inside the question; other clients show the source. A screen-layout sketch stays
  plain ASCII: Mermaid cannot draw a wireframe.
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


def bake_root(text, root):
    """The `${CLAUDE_PLUGIN_ROOT}` rewrite alone — for the glossaries, which carry the
    Claude-dialect spellings bake() would rewrite."""
    return text.replace('${CLAUDE_PLUGIN_ROOT}', str(root))


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
    return name, assemble(bake(body, root, generated), bake_root(SKILL_GLOSSARY, root),
                          frontmatter)


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
    return name, assemble(bake(body, root, generated), bake_root(AGENT_GLOSSARY, root),
                          frontmatter)


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


TRANSFORMS = [
    ('implementer', 'SKILL.md', """\
`--model <sonnet|opus|fable>`: dispatch the agent on this model instead of its frontmatter
`opus`. It may appear in any position; strip it before reading `$0`. Any other value is an
invocation error — report it and stop. A dispatch refused for that model (not available, or not
allowed on this host) is re-dispatched once without it. The orchestrator passes the flag to step
up a fix round that follows a failed one (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §5);
otherwise the skill chooses the model itself (below).""", """\
`--model <provider/model[#variant]>`: dispatch the agent on this concrete model. It may appear
in any position; strip it before reading `$0`. Any other value is an invocation error — report
it and stop. A dispatch refused for that model (not available, or not allowed on this host) is
re-dispatched once without it. The orchestrator passes the flag to step up a fix round that
follows a failed one (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §5); otherwise the skill
resolves the model itself (below)."""),

    ('implementer', 'SKILL.md', """\
**Choosing the model** — after the spec-store read and before the Agent call, first match wins:

1. `--model` given → that model.
2. **Fix-list dispatch** — the invocation names `## Code Review Fixes`, `## Runtime Fixes`,
   `## Verify Fixes` or Final Verification → no model (the frontmatter `opus`), no helper run.
3. **Iteration dispatch** → run the route helper (`route --next`) over the tasklist in scope
   (`<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md` on a phase-scoped run,
   `<specs.dir>/<TICKET_ID>/tasklist.md` otherwise):

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py route --tasklist <the tasklist> --ticket-key <TICKET_ID> --next

   (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py route --tasklist - --ticket-key <TICKET_ID> --next`)
   A non-null `data.model` → that model; `null` or exit `2` → no model.

The chosen model is passed as the Agent call's `model`. A call refused for its model is
re-dispatched once without one.""", """\
**Choosing the model** — after the spec-store read and before the `subagent` call, first match
wins:

1. `--model` given → that model.
2. **Fix-list dispatch** — the invocation names `## Code Review Fixes`, `## Runtime Fixes`,
   `## Verify Fixes` or Final Verification → resolve `implementer.fix`
   (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site implementer.fix`); a `null`
   model → no model, no helper run.
3. **Iteration dispatch** → run the route helper (`route --next`) over the tasklist in scope
   (`<specs.dir>/<TICKET_ID>/phase-<PHASE_NUM>/tasks.md` on a phase-scoped run,
   `<specs.dir>/<TICKET_ID>/tasklist.md` otherwise):

       python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py route --tasklist <the tasklist> --ticket-key <TICKET_ID> --next

   (kartoteka path: `set -o pipefail; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/spec_store.py get <the tasklist> | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/tasklist_tasks.py route --tasklist - --ticket-key <TICKET_ID> --next`)
   take its `route_effective` (the helper's `model` alias is not used here) and resolve
   `implementer.<route_effective>`
   (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site implementer.<route_effective>`);
   a `null` route, a route-helper exit `2` or a `null` model → no model; a resolver exit `2` is
   reported and stops the run.

The chosen model is passed as the `subagent` call's `model`. A call refused for its model is
re-dispatched once without one."""),

    ('implementer', 'SKILL.md', """\
Add one `Model:` line to the relayed contract, after `Verify iterations:` and before
`Deviations:`, written so an orchestrator can append its value to a journal line verbatim:
`Model: sonnet` when the helper's model matched the task worked; `Model: sonnet (predicted task
2.3 at route light; worked task 2.4)` when it did not (compare the completion's first line);
`Model: opus (frontmatter: fix list)` — or `no ready task`, `route lookup failed: <kind>`;
`Model: fable (--model)`; `Model: opus (frontmatter: sonnet refused)` after a refused
re-dispatch. `HITL:` and `DEVIATION` returns carry no `Model:` line.""", """\
Add one `Model:` line to the relayed contract, after `Verify iterations:` and before
`Deviations:`, written so an orchestrator can append its value to a journal line verbatim:
`Model: <value>` — the model the dispatch ran on — with the `(predicted task 2.3 at route
light; worked task 2.4)` suffix when the prediction missed (compare the completion's first
line); or `Model: none` with the reason — `fix list: implementer.fix unset`, `no ready task`,
`route lookup failed: <kind>`, or `model refused` after a refused re-dispatch. `HITL:` and
`DEVIATION` returns carry no `Model:` line."""),

    ('run-reviewer', 'SKILL.md', """\
`--model <sonnet|opus|fable>`: dispatch the agent on this model instead of its frontmatter
`opus`. It may appear in any position; strip it before reading `$0` and the mode flags. Any
other value is an invocation error — report it and stop. A dispatch refused for that model
(not available, or not allowed on this host) is re-dispatched once without it. When one was
given, every mode's Agent call — ticket, task and plan — carries `model` set to the `--model`
value; no flag → no `model`, on the frontmatter `opus`. The orchestrator passes it to scale
the review to its scope (`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §16.4).""", """\
`--model <provider/model[#variant]>`: dispatch the agent on this concrete model. It may appear
in any position; strip it before reading `$0` and the mode flags. Any other value is an
invocation error — report it and stop. A dispatch refused for that model (not available, or not
allowed on this host) is re-dispatched once without it. When one was given, every mode's
`subagent` call — ticket, task and plan — carries `model` set to the `--model` value; no flag →
resolve the mode's site with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site
<the mode's site>` (task → `reviewer.task`, ticket → `reviewer.phase`, plan → `reviewer.plan`)
and pass it when non-null; a null model → no `model` on the call. The orchestrator passes the
flag for re-reviews (resolved from `reviewer.reReview`) to scale the review to its scope
(`${CLAUDE_PLUGIN_ROOT}/docs/autonomous-run.md` §16.4)."""),

    ('run-reviewer', 'SKILL.md',
     '`model` set to the `--model` value when one was given, description '
     '`"Review changes for <TICKET_ID>"`',
     '`model` set to the `--model` value when one was given, else the resolved '
     '`reviewer.phase`, left out when neither, description `"Review changes for <TICKET_ID>"`'),

    ('run-reviewer', 'SKILL.md', """\
`model` set to the `--model` value when
one was given, description `"Review task for <TICKET_ID>: <task title>"`""", """\
`model` set to the `--model` value when
one was given, else the resolved `reviewer.task`, left out when neither, description `"Review task for <TICKET_ID>: <task title>"`"""),

    ('run-reviewer', 'SKILL.md', """\
`model` set to the `--model` value when one was
given, description `"Review plan for <TICKET_ID>"`""", """\
`model` set to the `--model` value when one was
given, else the resolved `reviewer.plan`, left out when neither, description `"Review plan for <TICKET_ID>"`"""),

    ('feature-development', 'tail.md',
     'A `full` task gets the §16.2 wrapper: `diff` + `Skill: run-reviewer --task …` with '
     '`--model sonnet` (plus `--local` when this run was invoked with it) after its completion, '
     'at most one `## Code Review Fixes` implementer round (`MAX_TASK_REVIEW_ROUNDS = 1`, counted '
     'toward `counters.correction_rounds`), one `task review` journal entry (`; model sonnet`); '
     'a `light` task gets none.',
     'A `full` task gets the §16.2 wrapper: `diff` + `Skill: run-reviewer --task …` with '
     '`--model <value>` — resolve `reviewer.task` (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/'
     'models.py resolve --site reviewer.task`); no flag when null — (plus `--local` when this '
     'run was invoked with it) after its completion, at most one `## Code Review Fixes` '
     'implementer round (`MAX_TASK_REVIEW_ROUNDS = 1`, counted toward '
     '`counters.correction_rounds`), one `task review` journal entry (`; model <value>` — '
     '`none` when no flag was passed); a `light` task gets none.'),

    ('feature-development', 'tail.md',
     'plus `--model fable` when the findings come from a `review.md` whose `**Review round:**` '
     'is 2 or more — autonomous-run.md §5',
     'plus `--model <value>` when the findings come from a `review.md` whose `**Review round:**` '
     'is 2 or more — resolve `implementer.stepUp` (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/'
     'models.py resolve --site implementer.stepUp`); no flag when null, and the implementer\'s '
     '`implementer.fix` applies — autonomous-run.md §5'),

    ('feature-development', 'tail.md',
     '→ re-review (the re-review passes `--model sonnet`; journal the round with `; model sonnet`).',
     '→ re-review (the re-review passes `--model <value>` — resolve `reviewer.reReview` '
     '(`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site reviewer.reReview`); no '
     'flag when null, and ticket mode\'s `reviewer.phase` applies — and journals the round with '
     '`; model <value>`, `none` when no flag was passed).'),

    ('feature-development', 'tail.md',
     'plus `--model fable` on the second round',
     'plus `--model <value>` on the second round — resolve `implementer.stepUp` (`python3 '
     '${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site implementer.stepUp`); no flag when '
     'null'),

    ('deep-review', 'SKILL.md', """\
- `model`: `"fable"` — the whole-branch review before a pull request, the one review on the most
  capable tier (`${CLAUDE_PLUGIN_ROOT}/docs/agents.md` `## Models`); a dispatch refused for its
  model is re-dispatched once without one""", """\
- `model`: resolve `reviewer.deepReview`
  (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/models.py resolve --site reviewer.deepReview`) and set
  it when non-null — the whole-branch review before a pull request is the one review on the most
  capable tier (`${CLAUDE_PLUGIN_ROOT}/docs/agents.md` `## Models`); a dispatch refused for its
  model is re-dispatched once without one"""),
]


def missing_transforms(source):
    """Every (folder, rel, source) entry whose canonical text is absent from the source tree."""
    missing = []
    for folder, rel, src, _ in TRANSFORMS:
        try:
            text = (source / 'skills' / folder / rel).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            missing.append((folder, rel, src))
            continue
        if src not in text:
            missing.append((folder, rel, src))
    return missing


def transform(folder, rel, text):
    """Apply the ordered exact-text transforms for one generated file."""
    for entry_folder, entry_rel, src, replacement in TRANSFORMS:
        if (entry_folder, entry_rel) == (folder, rel):
            text = text.replace(src, replacement)
    return text


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

    missing = missing_transforms(source)
    if missing:
        for folder, rel, src in missing:
            print('build_opencode: transform source absent: {}/{}: {}'.format(
                folder, rel, src.splitlines()[0]), file=sys.stderr)
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
                companions.append((folder.name, rel, path, dest_dir / rel))

    for skill_path in skill_files:
        src = transform(skill_path.parent.name, 'SKILL.md',
                        skill_path.read_text(encoding='utf-8'))
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
    for folder, rel, path, dest in companions:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(bake(transform(folder, rel, path.read_text(encoding='utf-8')),
                             root, generated), encoding='utf-8')

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
