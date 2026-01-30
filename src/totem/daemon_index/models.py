from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DaemonIndexConfig:
    vault_root: Path
    db_path: Path
    exclude_globs: list[str]
    frontmatter_journal_date_key: str
    frontmatter_journal_date_formats: list[str]


@dataclass(frozen=True)
class FileRecord:
    rel_path: str
    title: str
    mtime_ns: int
    size_bytes: int
    content_hash: str
    fm_journal_date: Optional[str]


@dataclass(frozen=True)
class Heading:
    ord: int
    level: int
    text: str
    start_byte: int
    end_byte: int
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass(frozen=True)
class Outlink:
    ord: int
    target: str
    section: Optional[str]
    alias: Optional[str]
    raw: str
    start_byte: int
    end_byte: int
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass(frozen=True)
class ParsedMarkdown:
    fm_journal_date: Optional[str]
    fm_tags: list[str]
    inline_tags: list[str]
    headings: list[Heading]
    outlinks: list[Outlink]

