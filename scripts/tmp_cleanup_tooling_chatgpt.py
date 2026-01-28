#!/usr/bin/env python3
"""Temporary cleanup: remove tooling-routed ChatGPT notes from daemon vault.

Default is dry-run; pass --execute to delete files and update daily notes.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - fallback for older Python
    import tomli as tomllib  # type: ignore


DEFAULT_STATE_REL = "state/chatgpt_export_ingest_state.json"
DEFAULT_DAEMON_CHATGPT_DIR = "40_chatgpt/conversations"
DEFAULT_DAILY_ROOT = "5.0 Journal/5.1 Daily"

CHATGPT_BLOCK_START = "<!-- TOTEM:CHATGPT:START -->"
CHATGPT_BLOCK_END = "<!-- TOTEM:CHATGPT:END -->"


def _load_repo_config(repo_root: Path) -> dict:
    config_path = repo_root / ".totem" / "config.toml"
    if not config_path.exists():
        return {}
    with config_path.open("rb") as f:
        return tomllib.load(f) or {}


def _get_nested(config: dict, keys: list[str]) -> str | None:
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, str) else None


def _read_tooling_conversation_ids(state_path: Path) -> set[str]:
    if not state_path.exists():
        raise FileNotFoundError(f"State file not found: {state_path}")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    conversations = data.get("conversations") or {}
    tooling = {
        conv_id
        for conv_id, entry in conversations.items()
        if isinstance(entry, dict) and entry.get("destination_vault") == "tooling"
    }
    return tooling


def _extract_conversation_id(note_path: Path) -> str | None:
    try:
        content = note_path.read_text(encoding="utf-8")
    except OSError:
        return None
    in_frontmatter = False
    for line in content.splitlines():
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter and line.startswith("conversation_id:"):
            return line.split(":", 1)[1].strip()
    return None


def _collect_tooling_note_stems(tooling_relpaths: list[str]) -> set[str]:
    stems = set()
    for relpath in tooling_relpaths:
        stem = Path(relpath).with_suffix("").as_posix()
        stems.add(stem)
        stems.add(Path(relpath).stem)
    return stems


def _remove_tooling_links_from_block(lines: list[str], tooling_stems: set[str]) -> list[str]:
    cleaned: list[str] = []
    skip_following = False
    for line in lines:
        if skip_following:
            if line.startswith("  - "):
                continue
            skip_following = False

        if _line_has_tooling_link(line, tooling_stems):
            skip_following = True
            continue

        cleaned.append(line)
    return cleaned


def _line_has_tooling_link(line: str, tooling_stems: set[str]) -> bool:
    links = re.findall(r"\[\[([^\]]+)\]\]", line)
    for link in links:
        target = link.split("|", 1)[0].strip()
        if target in tooling_stems:
            return True
    return False


def _cleanup_daily_note(path: Path, tooling_stems: set[str], execute: bool) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    if CHATGPT_BLOCK_START not in content or CHATGPT_BLOCK_END not in content:
        return False

    before, rest = content.split(CHATGPT_BLOCK_START, 1)
    block, after = rest.split(CHATGPT_BLOCK_END, 1)
    block_lines = [CHATGPT_BLOCK_START] + block.splitlines() + [CHATGPT_BLOCK_END]
    cleaned_block_lines = _remove_tooling_links_from_block(block_lines, tooling_stems)

    if cleaned_block_lines == block_lines:
        return False

    new_content = before.rstrip("\n") + "\n" + "\n".join(cleaned_block_lines) + after
    if execute:
        path.write_text(new_content, encoding="utf-8")
    return True


def main() -> int:
    repo_root = Path.cwd()
    repo_config = _load_repo_config(repo_root)

    parser = argparse.ArgumentParser(
        description="Remove tooling-routed ChatGPT notes from daemon vault (dry-run by default)."
    )
    parser.add_argument(
        "--daemon-vault",
        default=_get_nested(repo_config, ["obsidian", "vaults", "daemon_path"]),
        help="Daemon vault path (default: .totem/config.toml)",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Path to ChatGPT ingest state JSON (default: <daemon>/state/chatgpt_export_ingest_state.json)",
    )
    parser.add_argument(
        "--daemon-chatgpt-dir",
        default=DEFAULT_DAEMON_CHATGPT_DIR,
        help="Daemon ChatGPT notes folder (relative to daemon vault)",
    )
    parser.add_argument(
        "--daily-root",
        default=DEFAULT_DAILY_ROOT,
        help="Daily notes root (relative to daemon vault)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform deletions and edits (otherwise dry-run)",
    )

    args = parser.parse_args()
    if not args.daemon_vault:
        raise SystemExit("Daemon vault not set. Use --daemon-vault or update .totem/config.toml.")

    daemon_vault = Path(args.daemon_vault).expanduser().resolve()
    state_path = Path(args.state_file).expanduser().resolve() if args.state_file else daemon_vault / DEFAULT_STATE_REL

    tooling_ids = _read_tooling_conversation_ids(state_path)

    tooling_relpaths = []
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    for conv_id, entry in (state_data.get("conversations") or {}).items():
        if conv_id in tooling_ids:
            relpath = entry.get("output_note_relpath")
            if isinstance(relpath, str):
                tooling_relpaths.append(relpath)
    tooling_stems = _collect_tooling_note_stems(tooling_relpaths)

    chatgpt_dir = daemon_vault / args.daemon_chatgpt_dir
    daily_root = daemon_vault / args.daily_root

    removed_notes = []
    for note_path in chatgpt_dir.rglob("*.md"):
        conv_id = _extract_conversation_id(note_path)
        if conv_id and conv_id in tooling_ids:
            removed_notes.append(note_path)
            if args.execute:
                note_path.unlink()

    updated_daily = []
    for note_path in daily_root.rglob("*.md"):
        if _cleanup_daily_note(note_path, tooling_stems, args.execute):
            updated_daily.append(note_path)

    action = "Deleted" if args.execute else "Would delete"
    print(f"{action} {len(removed_notes)} daemon notes routed to tooling.")
    action = "Updated" if args.execute else "Would update"
    print(f"{action} {len(updated_daily)} daily notes.")

    if removed_notes:
        print("Sample notes:")
        for path in removed_notes[:10]:
            print(f"  - {path}")
        if len(removed_notes) > 10:
            print(f"  ... and {len(removed_notes) - 10} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
