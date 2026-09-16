#!/usr/bin/env bash
# Claude Code hook: export the session that just produced an event.
#
# Claude Code feeds hooks a JSON payload on stdin containing `transcript_path`.
# The real work is handed to a detached background process so that a slow or
# broken export can never stall, or fail, the session it was triggered from.
# This script always exits 0.

set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON" ] || exit 0

STATE="${CC_SESSIONS_STATE:-${CC_SESSIONS_DIR:-$HOME/claude-code-sessions}/.state}"
mkdir -p "$STATE" 2>/dev/null || exit 0

PAYLOAD="$(mktemp "${TMPDIR:-/tmp}/cc-session-export.XXXXXXXX")" || exit 0
cat > "$PAYLOAD"

(
    # One exporter at a time: Stop and SessionEnd can fire back to back, and
    # the export appends to a file, so overlapping runs would interleave.
    if command -v flock >/dev/null 2>&1; then
        flock -w 60 "$STATE/export.lock" \
            "$PYTHON" "$DIR/export.py" --hook --quiet < "$PAYLOAD"
    else
        "$PYTHON" "$DIR/export.py" --hook --quiet < "$PAYLOAD"
    fi
    rm -f "$PAYLOAD"
) >/dev/null 2>&1 &

exit 0
