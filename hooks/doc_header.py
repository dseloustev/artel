"""The spec-trail document header: artel's thin YAML block (design 2026-09-24 §1).

Read with kartoteka's byte rules (frontmatter.split): `---\\n` at byte 0, closed
by the first `\\n---\\n` after it. LF only -- a CRLF block is no block, to
kartoteka and here alike. artel's header is flat `key: value` scalars in a fixed
order, so this reads lines, not YAML: stdlib only, and nothing here can expand
(kartoteka refuses YAML aliases for exactly that reason).

A shared library module, not a hook: scripts/spec_store.py imports it (and a
hook may too, from a later plan).
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


# `grep -m1 'Status:'`, the read the gates used before the header: the first line
# carrying `Status:`, its value the first upper-case token after it.
_LEGACY_STATUS = re.compile(r'Status:\**[ \t]*([A-Z][A-Z0-9_]*)')


def status(text):
    """The document's gate state: the header's `status:` when it names one, else --
    for a document written before the header, read for one release (design §1.3)
    -- the value on its first `Status:` line. None when neither declares one."""
    declared = (fields(text) or {}).get('status')
    if declared:
        return declared
    for line in body(text).split('\n'):
        if 'Status:' in line:
            match = _LEGACY_STATUS.search(line)
            return match.group(1) if match else None
    return None


def body(text):
    """The document without its leading header block: what leaves artel for a pull
    request. A document without one is returned whole."""
    return split(text)[1]
