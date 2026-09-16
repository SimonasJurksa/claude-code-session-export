# cc-session-export

Keep a readable, permanent copy of every Claude Code conversation.

Claude Code already records each session as JSONL under
`~/.claude/projects/<slug>/<session-id>.jsonl`, but that archive is machine
oriented and lives inside `~/.claude` — reinstall, clean up or move machine and
it is gone. This tool mirrors those transcripts into plain Markdown:

```
~/claude-code-sessions/
├── workspace-docker-myapp/
│   ├── 2026-09-14-1032-fix-login-redirect-a1b2c3d4.md
│   ├── 2026-09-16-0915-add-search-index-9f8e7d6c.md
│   └── 2026-09-16-0915-add-search-index-9f8e7d6c.subagents/
│       └── workflows/wf_55be0317/agent-a8d6e2c8b228.md
└── home/
    └── 2026-09-16-1048-shell-setup-c288d470.md
```

Folders are named after the working directory the session ran in, so two
`apps/client` checkouts never collide. Work that Claude delegated to sub-agents
is archived too, in a `.subagents` folder beside the session it belongs to.

Each file holds what was said, plus a one-line trace of every tool call:

```markdown
## 👤 Simonas — 10:48

Fix the login redirect, it loops on expired sessions.

## 🤖 Claude — 10:48

> 🔧 **Bash**: Run the auth test suite
> 🔧 **Read**: ~/workspace/app/src/auth/session.py
> 🔧 **Edit**: ~/workspace/app/src/auth/session.py

The loop came from `redirect_to` still pointing at the guarded route …
```

Full tool output is left out on purpose — it is what makes the raw JSONL
unreadable (and enormous). The result greps well and reads like a conversation.

## Install

Requires Python 3.8+ (any Ubuntu/Debian has it) and Claude Code. No pip
packages, no daemon, no container.

```bash
git clone https://github.com/SimonasJurksa/claude-code-session-export.git
cd claude-code-session-export
./install.sh --backfill
```

`--backfill` also converts every session already on the machine; drop it to
start from the next session. `./install.sh --dry-run` shows what it would touch.

The installer copies `export.py` and `hook.sh` into
`~/.claude/cc-session-export/` and registers two hooks in
`~/.claude/settings.json` (backing the file up first, and leaving any hooks you
already have alone):

| Hook | When it fires | Why |
| --- | --- | --- |
| `Stop` | after each Claude turn | the archive stays current even if the session is killed |
| `SessionEnd` | when a session closes | catches the final turn |

The hook returns immediately and does the export in a detached background
process, so it never slows down or breaks a session. Exports are append-only
and track a byte offset per session, so each run only parses what is new — a
40 MB transcript costs milliseconds per turn, not a full re-read.

Restart any running Claude Code session afterwards to pick up the hooks.

## Manual use

```bash
cc-sessions --all              # export every transcript on this machine
cc-sessions --all --force      # re-render everything from scratch
cc-sessions path/to/x.jsonl    # export one transcript
cc-sessions --all --thinking   # include Claude's thinking blocks
cc-sessions --all --no-subagents   # sessions only, skip delegated agents
```

(`cc-sessions` is symlinked into `~/.local/bin` if that directory exists;
otherwise call `~/.claude/cc-session-export/export.py`.)

## Configuration

Set these in your shell profile, or in `env` in `~/.claude/settings.json`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CC_SESSIONS_DIR` | `~/claude-code-sessions` | where Markdown is written |
| `CC_SESSIONS_STATE` | `$CC_SESSIONS_DIR/.state` | per-session progress files |
| `CC_SESSIONS_USER` | `$USER` | label used for your own messages |
| `CC_CLAUDE_HOME` | `~/.claude` | Claude Code data directory |

## Uninstall

```bash
./uninstall.sh
```

Removes the hooks (backing up `settings.json`) and the installed copy. Your
exported Markdown is never touched.

## Notes

- **Nothing is sent anywhere.** It is a local file conversion, run by a local
  hook.
- **Treat the output as sensitive.** Transcripts can contain file contents,
  hostnames, tokens you pasted. Think twice before syncing
  `~/claude-code-sessions` to anywhere public.
- **Why no Docker image?** Hooks run on the host and the script needs
  `~/.claude` and your home directory anyway. A container would add mounts and
  a runtime dependency for zero benefit. One stdlib-only Python file is the
  portable unit here.
- **Sessions that were never used** (opened and closed without a prompt) are
  skipped rather than written as empty files.
- **Renaming a session** (`/rename`, or Claude titling it) moves the exported
  file and its `.subagents` folder to match, instead of leaving a stale copy.
- **Re-running is safe.** The installer is idempotent, and the exporter resumes
  where it left off (it also re-renders a session from scratch if the
  transcript was rewritten, or the output format changed).

## License

MIT
