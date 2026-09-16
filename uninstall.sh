#!/usr/bin/env bash
# Remove the cc-session-export hooks and the installed exporter.
# Exported Markdown in ~/claude-code-sessions is never touched.

set -euo pipefail

CLAUDE_HOME="${CC_CLAUDE_HOME:-$HOME/.claude}"
INSTALL_DIR="${CC_INSTALL_DIR:-$CLAUDE_HOME/cc-session-export}"
SETTINGS="${CC_SETTINGS:-$CLAUDE_HOME/settings.json}"

PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || { echo "error: python3 is required" >&2; exit 1; }

if [ -f "$SETTINGS" ]; then
    CC_SETTINGS="$SETTINGS" "$PYTHON" - <<'PY'
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

settings_path = Path(os.environ["CC_SETTINGS"])
try:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
except ValueError:
    raise SystemExit("error: {} is not valid JSON".format(settings_path))

backup = settings_path.with_suffix(".json.bak-{}".format(datetime.now().strftime("%Y%m%d-%H%M%S")))
shutil.copy2(settings_path, backup)

removed = 0
hooks = settings.get("hooks")
if isinstance(hooks, dict):
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(entries, list):
                kept = [e for e in entries
                        if not (isinstance(e, dict) and "cc-session-export" in str(e.get("command", "")))]
                removed += len(entries) - len(kept)
                if not kept:
                    continue          # drop a group that held only our hook
                group["hooks"] = kept
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]          # drop an event we were the only user of
    if not hooks:
        del settings["hooks"]

tmp = settings_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(settings_path)
print("removed {} hook(s); backup: {}".format(removed, backup.name))
PY
fi

rm -rf "$INSTALL_DIR"
[ -L "$HOME/.local/bin/cc-sessions" ] && rm -f "$HOME/.local/bin/cc-sessions" || true
echo "removed $INSTALL_DIR"
echo "your exported sessions were left untouched."
