#!/usr/bin/env bash
# Install cc-session-export: copy the exporter into Claude Code's data dir and
# register the hooks that keep ~/claude-code-sessions up to date.
#
#   ./install.sh              install and register hooks
#   ./install.sh --backfill   ...and export every session already on this machine
#   ./install.sh --dry-run    show what would change, write nothing

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CC_CLAUDE_HOME:-$HOME/.claude}"
INSTALL_DIR="${CC_INSTALL_DIR:-$CLAUDE_HOME/cc-session-export}"
SETTINGS="${CC_SETTINGS:-$CLAUDE_HOME/settings.json}"
SESSIONS_DIR="${CC_SESSIONS_DIR:-$HOME/claude-code-sessions}"
EVENTS="Stop SessionEnd"

BACKFILL=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --backfill) BACKFILL=1 ;;
        --dry-run)  DRY_RUN=1 ;;
        -h|--help)  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
    echo "error: python3 is required (sudo apt install python3)" >&2
    exit 1
fi
"$PYTHON" - <<'PY' || { echo "error: Python 3.8+ is required" >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 8) else 1)
PY

if [ ! -d "$CLAUDE_HOME" ]; then
    echo "error: $CLAUDE_HOME not found -- is Claude Code installed for this user?" >&2
    exit 1
fi

HOOK_CMD="$INSTALL_DIR/hook.sh"

echo "cc-session-export"
echo "  exporter : $INSTALL_DIR"
echo "  settings : $SETTINGS"
echo "  sessions : $SESSIONS_DIR"
echo "  hooks    : $EVENTS"
echo

if [ "$DRY_RUN" -eq 1 ]; then
    echo "--dry-run: nothing written."
    exit 0
fi

mkdir -p "$INSTALL_DIR" "$SESSIONS_DIR"
install -m 0755 "$SOURCE/export.py" "$INSTALL_DIR/export.py"
install -m 0755 "$SOURCE/hook.sh"   "$INSTALL_DIR/hook.sh"
echo "installed exporter"

# Merge the hooks into settings.json without disturbing anything already there.
CC_SETTINGS="$SETTINGS" CC_HOOK_CMD="$HOOK_CMD" CC_EVENTS="$EVENTS" "$PYTHON" - <<'PY'
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

settings_path = Path(os.environ["CC_SETTINGS"])
command = os.environ["CC_HOOK_CMD"]
events = os.environ["CC_EVENTS"].split()

settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except ValueError:
        raise SystemExit("error: {} is not valid JSON; fix it and re-run".format(settings_path))
    if not isinstance(settings, dict):
        raise SystemExit("error: {} is not a JSON object".format(settings_path))
    backup = settings_path.with_suffix(".json.bak-{}".format(datetime.now().strftime("%Y%m%d-%H%M%S")))
    shutil.copy2(settings_path, backup)
    print("backed up settings to {}".format(backup.name))

hooks = settings.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit("error: 'hooks' in settings.json is not an object")

added = []
for event in events:
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise SystemExit("error: hooks.{} is not a list".format(event))
    # Idempotent: re-running the installer must not stack duplicate hooks,
    # and an older install under a different path is replaced, not doubled.
    existing = False
    for group in groups:
        entries = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "cc-session-export" in str(entry.get("command", "")):
                entry["command"] = command
                existing = True
    if not existing:
        groups.append({"hooks": [{"type": "command", "command": command}]})
        added.append(event)

settings_path.parent.mkdir(parents=True, exist_ok=True)
tmp = settings_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(settings_path)
print("registered hooks: {}".format(", ".join(added)) if added else "hooks already registered (refreshed paths)")
PY

# A convenience CLI, when the user has a personal bin directory on PATH.
if [ -d "$HOME/.local/bin" ]; then
    ln -sf "$INSTALL_DIR/export.py" "$HOME/.local/bin/cc-sessions"
    echo "linked ~/.local/bin/cc-sessions"
fi

if [ "$BACKFILL" -eq 1 ]; then
    echo
    echo "exporting existing sessions (this can take a minute)..."
    "$PYTHON" "$INSTALL_DIR/export.py" --all --quiet
    count=$(find "$SESSIONS_DIR" -name '*.md' | wc -l)
    echo "done: $count session file(s) in $SESSIONS_DIR"
fi

echo
echo "Installed. New sessions export automatically; restart any running"
echo "Claude Code session to pick up the hooks."
