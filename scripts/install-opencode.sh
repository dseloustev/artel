#!/usr/bin/env bash
# Install (or remove) the OpenCode build of artel.
#
#   scripts/install-opencode.sh            install / refresh
#   scripts/install-opencode.sh --remove   uninstall everything installed here
#
# Layout (details: docs/opencode.md):
#   ~/.config/opencode/artel/               full plugin copy (hooks, scripts, docs,
#                                           skills, agents, bridge plugin)
#   ~/.config/opencode/plugins/artel.ts     the bridge plugin
#   ~/.config/opencode/skills/artel-*/      generated skills
#   ~/.config/opencode/agents/artel-*.md    generated agents
#   ~/.config/opencode/commands/artel-*.md  generated command wrappers
#   ~/.config/opencode/.artel-install-manifest
#                                           every path the last install placed
#
# Refresh and uninstall remove exactly what the manifest records — never a blanket
# `rm -rf artel-*`, which would take an unrelated skill of the user's that happens to
# be named artel-something.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
OC="$CONFIG_HOME/opencode"
DEST="$OC/artel"
MANIFEST="$OC/.artel-install-manifest"

# Remove the previously installed artifacts, and only those. Paths are relative to
# $OC; anything absolute or climbing out of it is ignored rather than trusted.
prune_installed() {
  [ -f "$MANIFEST" ] || return 0
  local rel
  while IFS= read -r rel || [ -n "$rel" ]; do
    case "$rel" in
      '' | /* | *..*) continue ;;
    esac
    rm -rf "${OC:?}/$rel"
  done < "$MANIFEST"
  rm -f "$MANIFEST"
}

if [ "${1:-}" = "--remove" ]; then
  if [ -f "$MANIFEST" ]; then
    prune_installed
  else
    # Installed before manifests existed: fall back to the name sweep, but say so —
    # it removes every artel-* artifact in these directories, not just artel's.
    echo "artel: no install manifest at $MANIFEST" >&2
    echo "artel: falling back to removing every artel-* artifact under $OC" >&2
    rm -rf "$OC/skills"/artel-* 2>/dev/null || true
    rm -f "$OC/agents"/artel-*.md "$OC/commands"/artel-*.md 2>/dev/null || true
  fi
  rm -rf "$DEST" "$OC/plugins/artel.ts"
  echo "artel: removed from $OC"
  exit 0
fi

if [ ! -f "$SRC/.claude-plugin/plugin.json" ] || [ ! -f "$SRC/opencode/plugin/artel.ts" ]; then
  echo "artel: $SRC does not look like the artel checkout" >&2
  exit 1
fi

echo "artel: installing from $SRC into $OC"
mkdir -p "$OC/skills" "$OC/agents" "$OC/commands" "$OC/plugins"

# 0. Drop the previous install's artifacts (stale skills renamed or retired upstream
#    would otherwise linger next to the new ones).
prune_installed

# 1. Full plugin copy — the generator bakes $DEST into the generated text.
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC/hooks" "$SRC/scripts" "$SRC/docs" "$SRC/skills" "$SRC/agents" "$DEST/"
mkdir -p "$DEST/plugin"
cp "$SRC/opencode/plugin/artel.ts" "$DEST/plugin/artel.ts"
rm -rf "$DEST/hooks/__pycache__" "$DEST/scripts/__pycache__"

# 2. Generate the OpenCode artifacts (into $DEST/opencode/dist).
python3 "$DEST/scripts/build_opencode.py" --root "$DEST"

# 3. Place them into OpenCode's flat discovery directories, recording each one.
#    The `[ -e ]` guards make an empty glob a no-op instead of a `set -e` abort.
TMP_MANIFEST="$MANIFEST.tmp"
: > "$TMP_MANIFEST"
record() { printf '%s\n' "$1" >> "$TMP_MANIFEST"; }

SKILLS=0
for dir in "$DEST/opencode/dist/skills/"*/; do
  [ -d "$dir" ] || continue
  name="$(basename "$dir")"
  rm -rf "$OC/skills/$name"
  cp -R "$dir" "$OC/skills/$name"
  record "skills/$name"
  SKILLS=$((SKILLS + 1))
done

AGENTS=0
for file in "$DEST/opencode/dist/agents/"*.md; do
  [ -f "$file" ] || continue
  name="$(basename "$file")"
  cp "$file" "$OC/agents/$name"
  record "agents/$name"
  AGENTS=$((AGENTS + 1))
done

COMMANDS=0
for file in "$DEST/opencode/dist/commands/"*.md; do
  [ -f "$file" ] || continue
  name="$(basename "$file")"
  cp "$file" "$OC/commands/$name"
  record "commands/$name"
  COMMANDS=$((COMMANDS + 1))
done

# 4. The bridge plugin.
cp "$DEST/plugin/artel.ts" "$OC/plugins/artel.ts"
record "plugins/artel.ts"
record "artel"

mv "$TMP_MANIFEST" "$MANIFEST"

echo "artel: installed $SKILLS skills, $AGENTS agents, $COMMANDS commands"
echo "artel: restart opencode to load them; see docs/opencode.md"
