#!/usr/bin/env python3
"""cc-session-export -- turn Claude Code transcripts into readable Markdown.

Claude Code already records every session as JSONL under
``~/.claude/projects/<slug>/<session-id>.jsonl``.  This script converts those
records into a plain-text archive that survives a wipe of ``~/.claude``:

    ~/claude-code-sessions/<project>/<date>-<time>-<title>-<id>.md

Only the standard library is used, so it runs on any machine with Python 3.8+.

Usage
-----
    export.py --hook            read hook JSON from stdin, export that session
    export.py --all             export every transcript found on this machine
    export.py FILE.jsonl ...    export specific transcripts
    export.py --all --force     re-export everything from scratch

Environment
-----------
    CC_SESSIONS_DIR     output root (default ~/claude-code-sessions)
    CC_SESSIONS_STATE   incremental-state dir (default <output root>/.state)
    CC_SESSIONS_USER    label for your own messages (default $USER)
    CC_CLAUDE_HOME      Claude Code data dir (default ~/.claude)
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

HOME = Path.home()

# Exports are append-only, so the state file remembers how far into each
# transcript we already got.  Bump this when the rendering changes in a way
# that makes old output inconsistent with new output; every session is then
# re-rendered from scratch on the next run.
FORMAT_VERSION = 1

MAX_HINT = 120

DASH = chr(0x2014)          # em dash
ICON_USER = chr(0x1F464)    # bust in silhouette
ICON_CLAUDE = chr(0x1F916)  # robot
ICON_TOOL = chr(0x1F527)    # wrench
ICON_AGENT = chr(0x1F9E9)   # puzzle piece
ICON_REWIND = chr(0x23EA)   # rewind
ICON_THOUGHT = chr(0x1F4AD) # thought balloon


# --------------------------------------------------------------------------- paths


def claude_home():
    return Path(os.environ.get("CC_CLAUDE_HOME", HOME / ".claude")).expanduser()


def out_root():
    return Path(os.environ.get("CC_SESSIONS_DIR", HOME / "claude-code-sessions")).expanduser()


def state_dir():
    env = os.environ.get("CC_SESSIONS_STATE")
    return Path(env).expanduser() if env else out_root() / ".state"


def user_label():
    name = os.environ.get("CC_SESSIONS_USER") or os.environ.get("USER") or "You"
    return name[:1].upper() + name[1:]


def short_path(value):
    """Render an absolute path relative to $HOME, for compact tool lines."""
    if not value:
        return ""
    try:
        rel = Path(value).relative_to(HOME)
    except ValueError:
        return str(value)
    return "~" if str(rel) == "." else "~/" + str(rel)


def project_dir_name(cwd):
    """Stable, collision-free folder name for a working directory.

    ``/home/me/workspace/docker/app`` -> ``workspace-docker-app``.  Using the
    full path (not just the basename) keeps two different ``apps/client``
    checkouts from landing in the same folder.
    """
    if not cwd:
        return "unknown"
    path = Path(cwd)
    try:
        rel = path.relative_to(HOME)
        parts = rel.parts
    except ValueError:
        parts = [p for p in path.parts if p not in ("/", "\\")]
    if not parts:
        return "home"
    return sanitize("-".join(parts)) or "unknown"


def sanitize(text):
    """ASCII-ish, filesystem-safe slug (Lithuanian diacritics are folded)."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:60]


# --------------------------------------------------------------------------- time


def parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def fmt_time(dt):
    return dt.strftime("%H:%M") if dt else "??:??"


# --------------------------------------------------------------------------- text


SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
STDOUT_RE = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.S)
BOILERPLATE_RE = re.compile(r"<fork-boilerplate>.*?</fork-boilerplate>", re.S)


def clean_user_text(text):
    """Strip harness plumbing from a user message, keeping what was typed."""
    if not text:
        return ""

    command = COMMAND_NAME_RE.search(text)
    if command:
        args = COMMAND_ARGS_RE.search(text)
        args = args.group(1).strip() if args else ""
        return "`{}{}`".format(command.group(1).strip(), " " + args if args else "")

    stdout = STDOUT_RE.search(text)
    if stdout and not STDOUT_RE.sub("", text).strip():
        body = stdout.group(1).strip()
        return "```\n{}\n```".format(body) if body else ""

    text = SYSTEM_REMINDER_RE.sub("", text)
    text = BOILERPLATE_RE.sub("", text)
    return text.strip()


def oneline(text, limit=MAX_HINT):
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def content_blocks(message):
    """Normalise ``message.content`` (string or block list) into a block list."""
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


# --------------------------------------------------------------------------- tools


def tool_hint(name, params):
    """One short line describing what a tool call did."""
    if not isinstance(params, dict):
        return ""

    def text(key):
        value = params.get(key)
        return value if isinstance(value, str) else ""

    if name == "Bash":
        return oneline(text("description") or text("command"))
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        return oneline(short_path(text("file_path")))
    if name == "MultiEdit":
        edits = params.get("edits")
        count = len(edits) if isinstance(edits, list) else 0
        return oneline("{} ({} edits)".format(short_path(text("file_path")), count))
    if name in ("Grep", "Glob"):
        where = text("path")
        return oneline(text("pattern") + (" in " + short_path(where) if where else ""))
    if name in ("Task", "Agent"):
        return oneline(text("description") or text("subagent_type"))
    if name == "WebFetch":
        return oneline(text("url"))
    if name == "WebSearch":
        return oneline(text("query"))
    if name == "Skill":
        return oneline((text("skill") + " " + text("args")).strip())
    if name == "TodoWrite":
        todos = params.get("todos")
        return "{} items".format(len(todos)) if isinstance(todos, list) else ""
    if name == "AskUserQuestion":
        questions = params.get("questions")
        if isinstance(questions, list) and questions:
            first = questions[0]
            if isinstance(first, dict):
                return oneline(first.get("question", ""))
        return ""
    if name == "Artifact":
        return oneline(text("action") or text("file_path"))

    for key in ("description", "query", "prompt", "command", "path", "url"):
        if text(key):
            return oneline(text(key))
    for value in params.values():
        if isinstance(value, str) and value.strip():
            return oneline(value)
    return ""


# --------------------------------------------------------------------------- state


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- reading


def read_records(path, offset):
    """Yield records from ``offset``, stopping at the last complete line.

    A transcript may be mid-write when a hook fires, so the trailing partial
    line is deliberately left for the next run.
    """
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        data = handle.read()
        consumed = offset

        cut = data.rfind("\n")
        if cut == -1:
            return records, consumed
        complete = data[: cut + 1]
        consumed = offset + len(complete.encode("utf-8"))

        for line in complete.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records, consumed


def session_title(records, current):
    """Best known title so far: a user-set /rename wins over the AI title."""
    title, kind = current.get("title"), current.get("title_kind")
    for record in records:
        kind_of = record.get("type")
        if kind_of == "custom-title" and record.get("customTitle"):
            title, kind = record["customTitle"], "custom"
        elif kind_of == "ai-title" and record.get("aiTitle") and kind != "custom":
            title, kind = record["aiTitle"], "ai"
    return title, kind


def first_directive(records):
    """The opening instruction of a sub-agent, used as its heading."""
    for record in records:
        if record.get("type") != "user":
            continue
        texts = [b.get("text", "") for b in content_blocks(record.get("message"))
                 if b.get("type") == "text"]
        body = clean_user_text("\n".join(t for t in texts if t))
        if body:
            return oneline(body.splitlines()[0], 80)
    return ""


def session_meta(records, current):
    """Fields for the front matter, taken from the first record that has them."""
    meta = dict(current)
    for record in records:
        for key, field in (("cwd", "cwd"), ("gitBranch", "branch"), ("version", "version")):
            if not meta.get(field) and record.get(key):
                meta[field] = record[key]
        if not meta.get("started"):
            stamp = parse_ts(record.get("timestamp"))
            if stamp:
                meta["started"] = stamp.isoformat()
    return meta


# --------------------------------------------------------------------------- rendering


def render(records, state, include_thinking, standalone=False):
    """Render records to Markdown, continuing where the last run stopped."""
    lines = []
    speaker = state.get("last_speaker")
    last_kind = state.get("last_kind")
    you = user_label()

    def header(who, stamp, kind="text"):
        """Open a speaker section, or separate this block from the previous one.

        Consecutive tool lines stay inside one blockquote, so a turn reads as a
        single list of actions rather than a stack of one-line quotes.
        """
        nonlocal speaker, last_kind
        if speaker != who:
            if lines or state.get("offset"):
                lines.append("")
            label = you if who == "user" else "Claude"
            icon = ICON_USER if who == "user" else ICON_CLAUDE
            lines.append("## " + icon + " " + label + " " + DASH + " " + fmt_time(stamp))
            lines.append("")
            speaker = who
        elif not (kind == "tool" and last_kind == "tool"):
            lines.append("")
        last_kind = kind

    for record in records:
        kind = record.get("type")
        if kind not in ("user", "assistant"):
            continue
        if record.get("isMeta"):
            continue

        stamp = parse_ts(record.get("timestamp"))
        sidechain = bool(record.get("isSidechain"))
        blocks = content_blocks(record.get("message"))

        if kind == "user":
            if sidechain and not standalone:
                continue  # sub-agent prompts belong to the sub-agent's own file
            texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            body = clean_user_text("\n".join(t for t in texts if t))
            if not body:
                continue
            if record.get("isCompactSummary"):
                header("user", stamp)
                lines.append("<details><summary>" + ICON_REWIND + " Context compacted " + DASH + " summary of the earlier conversation</summary>")
                lines.append("")
                lines.append(body)
                lines.append("")
                lines.append("</details>")
                continue
            header("user", stamp)
            lines.append(body)
            continue

        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                body = (block.get("text") or "").strip()
                if not body:
                    continue
                header("assistant", stamp)
                if sidechain and not standalone:
                    lines.append("> " + ICON_AGENT + " *sub-agent:* " + oneline(body, 300))
                else:
                    lines.append(body)
            elif block_type == "thinking" and include_thinking:
                body = (block.get("thinking") or "").strip()
                if not body:
                    continue
                header("assistant", stamp)
                lines.append("<details><summary>" + ICON_THOUGHT + " Thinking</summary>")
                lines.append("")
                lines.append(body)
                lines.append("")
                lines.append("</details>")
            elif block_type == "tool_use":
                name = block.get("name") or "tool"
                header("assistant", stamp, "tool")
                hint = tool_hint(name, block.get("input"))
                prefix = "> " + (ICON_AGENT + ICON_TOOL if sidechain and not standalone else ICON_TOOL)
                lines.append("{} **{}**{}".format(prefix, name, ": " + hint if hint else ""))

    state["last_speaker"] = speaker
    state["last_kind"] = last_kind
    return lines


def front_matter(session_id, meta, title, parent=None):
    started = parse_ts(meta.get("started"))
    lines = ["---"]
    lines.append("session_id: {}".format(session_id))
    if parent:
        lines.append("parent_session: {}".format(parent))
    if title:
        lines.append("title: {}".format(json.dumps(title, ensure_ascii=False)))
    if meta.get("cwd"):
        lines.append("cwd: {}".format(meta["cwd"]))
    if meta.get("branch"):
        lines.append("git_branch: {}".format(meta["branch"]))
    if meta.get("version"):
        lines.append("claude_code_version: {}".format(meta["version"]))
    if started:
        lines.append("started: {}".format(started.isoformat(timespec="seconds")))
    lines.append("---")
    lines.append("")
    lines.append("# {}".format(title or "Session {}".format(session_id[:8])))
    if meta.get("cwd"):
        lines.append("")
        lines.append("*{}*".format(short_path(meta["cwd"])))
    lines.append("")
    return lines


def target_path(session_id, meta, title):
    started = parse_ts(meta.get("started"))
    stamp = started.strftime("%Y-%m-%d-%H%M") if started else "undated"
    slug = sanitize(title) if title else ""
    name = "{}-{}-{}.md".format(stamp, slug, session_id[:8]) if slug \
        else "{}-{}.md".format(stamp, session_id[:8])
    return out_root() / project_dir_name(meta.get("cwd")) / name


# --------------------------------------------------------------------------- export


def replace_head(path, head):
    """Swap the front matter of an already-written export in place.

    Renaming a session (or learning its AI title late) changes the heading as
    well as the filename, and the body is append-only, so only the block above
    the first ``## `` section is rewritten.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return
    marker = re.search(r"^## ", content, re.M)
    rest = content[marker.start():] if marker else ""
    text = "\n".join(head).rstrip() + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n" + rest if rest else text)


def prune_empty(folder):
    """Remove directories a move left empty, never stepping outside the archive."""
    root = out_root().resolve()
    try:
        folder = folder.resolve()
    except OSError:
        return
    while folder != root and root in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def export_one(transcript, force=False, include_thinking=False, verbose=False, parent=None):
    """Export a single transcript, appending only what is new. True if it wrote."""
    transcript = Path(transcript)
    if not transcript.is_file():
        return False

    session_id = transcript.stem
    key = "{}--{}".format(parent["session_id"], session_id) if parent else session_id
    state_file = state_dir() / "{}.json".format(key)
    state = load_state(state_file)

    if force or state.get("format") != FORMAT_VERSION:
        state = {"format": FORMAT_VERSION}

    offset = int(state.get("offset") or 0)
    try:
        size = transcript.stat().st_size
    except OSError:
        return False
    if offset > size:  # transcript was rewritten or truncated
        state = {"format": FORMAT_VERSION}
        offset = 0
    if state.get("path"):
        known = Path(state["path"])
        current = child_path(transcript, parent) if parent \
            else target_path(session_id, state, state.get("title"))
        if known != current and known.exists():
            current.parent.mkdir(parents=True, exist_ok=True)
            try:
                known.rename(current)
                prune_empty(known.parent)
                state["path"] = str(current)
                save_state(state_file, state)
            except OSError:
                pass

    if offset == size and state.get("path") and Path(state["path"]).exists():
        return False  # nothing new since last run

    records, consumed = read_records(transcript, offset)
    if not records and offset != 0:
        return False

    title, title_kind = session_title(records, state)
    meta = session_meta(records, state)
    if parent:
        title = title or state.get("title") or first_directive(records) or session_id
        destination = child_path(transcript, parent)
    else:
        destination = target_path(session_id, meta, title)

    previous = state.get("path")
    fresh = offset == 0 or not previous or not Path(previous).exists()

    if not fresh and Path(previous) != destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            Path(previous).rename(destination)  # title changed mid-session
        except OSError:
            fresh = True

    body = render(records, state, include_thinking, standalone=bool(parent))
    head = front_matter(session_id, meta, title,
                        parent=parent["session_id"] if parent else None)

    if fresh and not body:
        return False  # nothing said here (e.g. a workflow journal): write no file

    if fresh:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write("\n".join(head + body).rstrip() + "\n")
        wrote = True
    else:
        if state.get("head") != head:
            replace_head(destination, head)
        if body:
            with open(destination, "a", encoding="utf-8") as handle:
                handle.write("\n".join(body).rstrip() + "\n")
        wrote = bool(body)

    state.update(
        format=FORMAT_VERSION,
        offset=consumed,
        path=str(destination),
        head=head,
        title=title,
        title_kind=title_kind,
        cwd=meta.get("cwd"),
        branch=meta.get("branch"),
        version=meta.get("version"),
        started=meta.get("started"),
    )
    save_state(state_file, state)

    if verbose and wrote:
        print(short_path(destination))
    return wrote


def export(transcript, force=False, include_thinking=False, verbose=False, subagents=True):
    """Export a session and, unless disabled, the sub-agents it delegated to."""
    transcript = Path(transcript)
    wrote = export_one(transcript, force=force, include_thinking=include_thinking,
                       verbose=verbose)
    if not subagents:
        return wrote

    children = child_transcripts(transcript)
    if not children:
        return wrote

    # The session file may have had nothing new while a sub-agent kept working,
    # so take the session's destination from its state rather than from above.
    state = load_state(state_dir() / "{}.json".format(transcript.stem))
    if not state.get("path"):
        return wrote
    info = {"session_id": transcript.stem, "path": state["path"],
            "folder": transcript.parent / transcript.stem}

    for child in children:
        try:
            if export_one(child, force=force, include_thinking=include_thinking,
                          verbose=verbose, parent=info):
                wrote = True
        except Exception:
            pass  # one broken sub-agent file must not sink the session
    return wrote


def child_transcripts(transcript):
    """Sub-agent and workflow transcripts stored beside a session.

    Claude Code keeps them under ``<session-id>/subagents/...`` next to the
    session's own JSONL; they hold the work a delegated agent actually did.
    """
    folder = transcript.parent / transcript.stem
    if not folder.is_dir():
        return []
    return sorted(folder.rglob("*.jsonl"))


def child_path(transcript, parent):
    """Mirror a sub-agent transcript under ``<session-file>.subagents/``.

    ``<session>/subagents/workflows/wf_1/agent-x.jsonl`` becomes
    ``<session>.subagents/workflows/wf_1/agent-x.md`` -- the folder layout is
    kept so agents from one workflow stay together.
    """
    destination = Path(parent["path"])
    root = destination.parent / (destination.stem + ".subagents")
    parts = list(transcript.relative_to(parent["folder"]).parts)
    if parts and parts[0] == "subagents":
        parts = parts[1:]
    parts[-1] = Path(parts[-1]).stem + ".md"
    return root.joinpath(*parts)


def all_transcripts():
    root = claude_home() / "projects"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)


def from_hook(payload):
    """Extract the transcript path from a Claude Code hook payload."""
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    path = data.get("transcript_path")
    if path:
        return Path(path).expanduser()
    session = data.get("session_id")
    if session:
        for candidate in (claude_home() / "projects").glob("*/{}.jsonl".format(session)):
            return candidate
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export Claude Code sessions to Markdown.")
    parser.add_argument("transcripts", nargs="*", help="transcript .jsonl files")
    parser.add_argument("--hook", action="store_true", help="read hook JSON from stdin")
    parser.add_argument("--all", action="store_true", help="export every transcript found")
    parser.add_argument("--force", action="store_true", help="re-render from scratch")
    parser.add_argument("--thinking", action="store_true", help="include thinking blocks")
    parser.add_argument("--no-subagents", dest="subagents", action="store_false",
                        help="skip sub-agent and workflow transcripts")
    parser.add_argument("-q", "--quiet", action="store_true", help="print nothing")
    args = parser.parse_args(argv)

    targets = [Path(t).expanduser() for t in args.transcripts]

    if args.hook:
        found = from_hook(sys.stdin.read())
        if found:
            targets.append(found)
    if args.all:
        targets.extend(all_transcripts())

    if not targets:
        if not args.hook:
            parser.error("nothing to export: pass files, --all, or --hook")
        return 0

    written = 0
    for transcript in targets:
        try:
            if export(transcript, force=args.force, include_thinking=args.thinking,
                      verbose=not args.quiet and not args.hook, subagents=args.subagents):
                written += 1
        except Exception as error:  # never break the session a hook fires from
            if not args.quiet and not args.hook:
                print("skipped {}: {}".format(transcript, error), file=sys.stderr)

    if not args.quiet and not args.hook:
        print("exported {} session(s) to {}".format(written, short_path(out_root())))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
