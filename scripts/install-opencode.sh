#!/usr/bin/env bash
# Install (or remove) the OpenCode build of artel.
#
#   scripts/install-opencode.sh            install / refresh
#   scripts/install-opencode.sh --remove   uninstall everything installed here
#
# Layout (details: docs/opencode.md):
#   ~/.config/opencode/artel/               full plugin copy (hooks, scripts, docs,
#                                           skills, agents, bridge plugin)
#   ~/.config/opencode/plugins/artel.ts     the bridge plugin (finds the copy above)
#   ~/.config/opencode/skills/artel-*/      generated skills
#   ~/.config/opencode/agents/artel-*.md    generated agents
#   ~/.config/opencode/commands/artel-*.md  generated command wrappers
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
OC="$CONFIG_HOME/opencode"
DEST="$OC/artel"

if [ "${1:-}" = "--remove" ]; then
  rm -rf "$DEST" "$OC/plugins/artel.ts"
  rm -rf "$OC/skills"/artel-* 2>/dev/null || true
  rm -f "$OC/agents"/artel-*.md 2>/dev/null || true
  rm -f "$OC/commands"/artel-*.md 2>/dev/null || true
  echo "artel: removed from $OC"
  exit 0
fi

if [ ! -f "$SRC/.claude-plugin/plugin.json" ] || [ ! -f "$SRC/opencode/plugin/artel.ts" ]; then
  echo "artel: $SRC does not look like the artel checkout" >&2
  exit 1
fi

echo "artel: installing from $SRC into $OC"
mkdir -p "$OC/skills" "$OC/agents" "$OC/commands" "$OC/plugins"

# 1. Full plugin copy — the generator bakes $DEST into the generated text.
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC/hooks" "$SRC/scripts" "$SRC/docs" "$SRC/skills" "$SRC/agents" "$DEST/"
mkdir -p "$DEST/plugin"
cp "$SRC/opencode/plugin/artel.ts" "$DEST/plugin/artel.ts"
rm -rf "$DEST/hooks/__pycache__" "$DEST/scripts/__pycache__"

# 2. Generate the OpenCode artifacts (into $DEST/opencode/dist).
python3 "$DEST/scripts/build_opencode.py" --root "$DEST"

# 3. Place them into OpenCode's flat discovery directories (stale copies first).
rm -rf "$OC/skills"/artel-*
rm -f "$OC/agents"/artel-*.md "$OC/commands"/artel-*.md
cp -R "$DEST/opencode/dist/skills/." "$OC/skills/"
cp "$DEST/opencode/dist/agents/"*.md "$OC/agents/"
cp "$DEST/opencode/dist/commands/"*.md "$OC/commands/"

# 4. The bridge plugin.
cp "$DEST/plugin/artel.ts" "$OC/plugins/artel.ts"

SKILLS="$(find "$OC/skills" -maxdepth 1 -type d -name 'artel-*' | wc -l | tr -d ' ')"
AGENTS="$(find "$OC/agents" -maxdepth 1 -name 'artel-*.md' | wc -l | tr -d ' ')"
COMMANDS="$(find "$OC/commands" -maxdepth 1 -name 'artel-*.md' | wc -l | tr -d ' ')"
echo "artel: installed $SKILLS skills, $AGENTS agents, $COMMANDS commands"
echo "artel: restart opencode to load them; see docs/opencode.md"
