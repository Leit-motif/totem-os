from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import db as dbmod
from .models import DaemonIndexConfig, FileRecord
from .parser import parse_markdown_bytes


@dataclass(frozen=True)
class DaemonIndexSummary:
    scanned: int
    updated: int
    unchanged: int
    deleted: int


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_excluded(rel_posix: str, exclude_globs: list[str]) -> bool:
    for pat in exclude_globs:
        if fnmatch.fnmatchcase(rel_posix, pat):
            return True
    return False


def _iter_markdown_files(vault_root: Path, exclude_globs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for p in vault_root.rglob("*.md"):
        try:
            rel = p.relative_to(vault_root)
        except ValueError:
            continue
        rel_posix = rel.as_posix()
        if _is_excluded(rel_posix, exclude_globs):
            continue
        paths.append(p)
    paths.sort(key=lambda p: p.relative_to(vault_root).as_posix())
    return paths


def _sha256_hex(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def index_daemon_vault(cfg: DaemonIndexConfig, *, full: bool = False) -> DaemonIndexSummary:
    conn = dbmod.connect(cfg.db_path)
    try:
        if full:
            dbmod.drop_schema(conn)
        dbmod.create_schema(conn)

        scanned = 0
        updated = 0
        unchanged = 0

        disk_paths = _iter_markdown_files(cfg.vault_root, cfg.exclude_globs)
        disk_rel_paths: set[str] = set()

        for abs_path in disk_paths:
            scanned += 1
            rel_posix = abs_path.relative_to(cfg.vault_root).as_posix()
            disk_rel_paths.add(rel_posix)

            st = abs_path.stat()
            mtime_ns = int(st.st_mtime_ns)
            size_bytes = int(st.st_size)

            row = dbmod.get_file_row(conn, rel_posix)
            if row is not None and int(row["mtime_ns"]) == mtime_ns and int(row["size_bytes"]) == size_bytes:
                unchanged += 1
                continue

            data = abs_path.read_bytes()
            content_hash = _sha256_hex(data)
            now = _iso_utc_now()

            if row is not None and row["content_hash"] == content_hash:
                with conn:
                    dbmod.update_file_metadata_only(conn, int(row["id"]), mtime_ns, size_bytes, now)
                updated += 1
                continue

            parsed = parse_markdown_bytes(
                data,
                journal_date_key=cfg.frontmatter_journal_date_key,
                journal_date_formats=cfg.frontmatter_journal_date_formats,
            )
            record = FileRecord(
                rel_path=rel_posix,
                title=abs_path.stem,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                content_hash=content_hash,
                fm_journal_date=parsed.fm_journal_date,
            )
            with conn:
                file_id = dbmod.upsert_file(conn, record, now)
                dbmod.replace_headings(conn, file_id, parsed.headings)
                dbmod.replace_outlinks(conn, file_id, parsed.outlinks)
                dbmod.replace_file_tags(conn, file_id, parsed.fm_tags, parsed.inline_tags)
            updated += 1

        deleted = 0
        existing_rel_paths = set(dbmod.list_file_rel_paths(conn))
        missing = sorted(existing_rel_paths - disk_rel_paths)
        if missing:
            with conn:
                deleted = dbmod.delete_files_by_rel_path(conn, missing)

        return DaemonIndexSummary(scanned=scanned, updated=updated, unchanged=unchanged, deleted=deleted)
    finally:
        conn.close()

