"""SessionStart(startup|clear|compact): inject the `using-artel` router skill plus a
three-line host status into the session. Inert without .artel/config.json. Fails open —
a session must never fail to start because of a convenience."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hook_common as h  # noqa: E402

SKILL_PATH = h.PLUGIN_ROOT / 'skills' / 'using-artel' / 'SKILL.md'

HEADER = ('<EXTREMELY_IMPORTANT>\n'
          'This repository is configured for artel. Below is your `artel:using-artel` skill '
          '— how to route ticket, feature, queue and knowledge requests. For every other '
          'skill, use the `Skill` tool.')
FOOTER = '</EXTREMELY_IMPORTANT>'


def strip_frontmatter(text):
    """The body after a leading `---` … `---` block; the text unchanged when there is none.
    The injected block is prose for the model, not a skill file."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            return ''.join(lines[index + 1:]).lstrip('\n')
    return text


def active_ticket_pointer(config):
    """First non-empty line of <specs.dir>/.active_ticket, verbatim, or None.

    Verbatim on purpose: the phase suffix is the useful part, and
    hook_common.resolve_active_ticket strips it by contract."""
    specs_dir = (config.get('specs') or {}).get('dir') or 'specs/.current'
    pointer = Path(specs_dir) / '.active_ticket'
    try:
        for line in pointer.read_text(encoding='utf-8').splitlines():
            if line.strip():
                return line.strip()
    except (OSError, ValueError):
        return None
    return None


def host_status(config):
    knowledge = config.get('knowledge') or {}
    adapter = knowledge.get('adapter') or 'none'
    base_url = knowledge.get('baseUrl') or ''
    adapter_line = 'knowledge.adapter: {}'.format(adapter)
    if base_url:
        adapter_line += ' ({})'.format(base_url)
    ticket = active_ticket_pointer(config) or 'none'
    return '\n'.join([
        'Host status:',
        '- config: present (.artel/config.json)',
        '- ' + adapter_line,
        '- active ticket: ' + ticket,
    ])


def build_context(config, skill_text):
    return '\n\n'.join([HEADER, host_status(config), strip_frontmatter(skill_text).rstrip(), FOOTER])


def _run():
    if not h.CONFIG_PATH.exists():
        return 0  # unconfigured host: nothing to route, zero footprint
    config = h.load_config()
    skill_text = SKILL_PATH.read_text(encoding='utf-8')
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': build_context(config, skill_text),
    }}))
    return 0


def main():
    try:
        return _run()
    except Exception as exc:
        print('using-artel hook error (skipping): {}'.format(exc), file=sys.stderr)
        return 0


if __name__ == '__main__':
    sys.exit(main())
