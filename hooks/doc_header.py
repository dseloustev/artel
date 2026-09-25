"""The spec-trail document header: artel's thin YAML block (design 2026-09-24 §1).

Read with kartoteka's byte rules (frontmatter.split): `---\\n` at byte 0, closed
by the first `\\n---\\n` after it. LF only -- a CRLF block is no block, to
kartoteka and here alike. artel's header is flat `key: value` scalars in a fixed
order, so this reads lines, not YAML: stdlib only, and nothing here can expand
(kartoteka refuses YAML aliases for exactly that reason).

A shared library module, not a hook: scripts/spec_store.py and
hooks/knowledge_mirror.py import it.
"""
import re

OPEN = '---\n'
CLOSE = '\n---\n'
_FIELD = re.compile(r'([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*?))?[ \t]*\Z')
# What kartoteka reads as this very integer: no sign, no quotes, no leading zero.
_VERSION = re.compile(r'(?:0|[1-9][0-9]*)\Z')


def split(text):
    """(block, body): the block's text without its two rules, and everything after
    the closing rule byte for byte; (None, text) when there is no block."""
    if text.startswith(OPEN):
        end = text.find(CLOSE, len(OPEN))
        if end != -1:
            return text[len(OPEN):end], text[end + len(CLOSE):]
    return None, text


def fields(text):
    """{key: value} for the leading block's `key: value` lines, or None without a
    block. A comment, a blank or an indented line is not a field; a repeated key
    takes its last value, as YAML does."""
    block, _ = split(text)
    if block is None:
        return None
    found = {}
    for line in block.split('\n'):
        match = _FIELD.match(line)
        if match:
            found[match.group(1)] = (match.group(2) or '').strip()
    return found


def version(text):
    """The header's `version` as an int, or None: no block, no version line, or a
    value kartoteka would not read as that integer (quoted, signed, `03`, `3.0`,
    an alias). A reader treats None as "no usable header"."""
    value = (fields(text) or {}).get('version')
    return int(value) if value is not None and _VERSION.match(value) else None


def set_version(text, number):
    """`text` with every `version:` line of its block set to `number`, every other
    byte kept. ValueError when there is no block or it has no version line."""
    block, body = split(text)
    if block is None:
        raise ValueError('no header block')
    lines = block.split('\n')
    hits = []
    for i, line in enumerate(lines):
        match = _FIELD.match(line)
        if match and match.group(1) == 'version':
            hits.append(i)
    if not hits:
        raise ValueError('the header block has no version line')
    for i in hits:
        lines[i] = 'version: {}'.format(number)
    return OPEN + '\n'.join(lines) + CLOSE + body
