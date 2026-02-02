from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .models import Heading, Outlink, ParsedMarkdown


_FRONTMATTER_DELIM = b"---"
_CODE_FENCE = b"```"


def _try_parse_date(value: str, formats: list[str]) -> Optional[str]:
    value = value.strip()
    if not value:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1].strip()
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return dt.strftime("%Y-%m-%d")
    return None


def _parse_frontmatter(
    data: bytes,
    journal_date_key: str,
    journal_date_formats: list[str],
) -> tuple[Optional[str], list[str], int]:
    if not (data.startswith(_FRONTMATTER_DELIM + b"\n") or data.startswith(_FRONTMATTER_DELIM + b"\r\n")):
        return None, [], 0

    # Find closing delimiter line
    offset = 0
    end = None
    line_end = data.find(b"\n")
    if line_end == -1:
        return None, [], 0
    offset = line_end + 1

    while offset < len(data):
        next_nl = data.find(b"\n", offset)
        if next_nl == -1:
            line = data[offset:]
            line_end_off = len(data)
        else:
            line = data[offset:next_nl]
            line_end_off = next_nl

        if line.rstrip(b"\r") == _FRONTMATTER_DELIM:
            end = line_end_off + 1 if next_nl != -1 else len(data)
            break
        offset = line_end_off + 1 if next_nl != -1 else len(data)

    if end is None:
        return None, [], 0

    fm_bytes = data[len(_FRONTMATTER_DELIM) : end].lstrip(b"\r\n")
    fm_lines = fm_bytes.splitlines()

    journal_date: Optional[str] = None
    tags: list[str] = []

    i = 0
    while i < len(fm_lines):
        raw_line = fm_lines[i]
        line = raw_line.strip()
        i += 1
        if not line or line.startswith(b"#"):
            continue

        m = re.match(rb"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key = m.group(1).decode("utf-8", errors="strict")
        value = m.group(2).decode("utf-8", errors="strict").strip()

        if key == journal_date_key:
            parsed = _try_parse_date(value, journal_date_formats)
            journal_date = parsed
            continue

        if key != "tags":
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner:
                parts = [p.strip() for p in inner.split(",")]
                for p in parts:
                    t = p.strip().strip('"').strip("'").lstrip("#").strip()
                    if t:
                        tags.append(t)
            continue

        if value:
            t = value.strip().strip('"').strip("'").lstrip("#").strip()
            if t:
                tags.append(t)
            continue

        # YAML list form:
        while i < len(fm_lines):
            item_line = fm_lines[i]
            if re.match(rb"^[A-Za-z0-9_-]+\s*:", item_line.strip()):
                break
            m_item = re.match(rb"^\s*-\s*(.+?)\s*$", item_line)
            if m_item:
                item = m_item.group(1).decode("utf-8", errors="strict").strip()
                item = item.strip('"').strip("'").lstrip("#").strip()
                if item:
                    tags.append(item)
            i += 1

    tags = sorted(set(tags))
    return journal_date, tags, end


def _iter_lines(data: bytes):
    offset = 0
    line_no = 1
    while True:
        nl = data.find(b"\n", offset)
        if nl == -1:
            yield line_no, offset, data[offset:]
            break
        yield line_no, offset, data[offset:nl]
        offset = nl + 1
        line_no += 1


def _outside_inline_code_segments(line: bytes) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    in_code = False
    seg_start = 0
    for idx, b in enumerate(line):
        if b != 0x60:  # `
            continue
        if in_code:
            seg_start = idx + 1
            in_code = False
        else:
            if seg_start < idx:
                segments.append((seg_start, idx))
            in_code = True
    if not in_code and seg_start < len(line):
        segments.append((seg_start, len(line)))
    return segments


_INLINE_TAG_RE = re.compile(r"(^|[\s\(\[\{<\"':;.,!?])#([A-Za-z0-9_/-]+)")


def parse_markdown_bytes(
    data: bytes,
    *,
    journal_date_key: str,
    journal_date_formats: list[str],
) -> ParsedMarkdown:
    fm_journal_date, fm_tags, content_start = _parse_frontmatter(
        data, journal_date_key=journal_date_key, journal_date_formats=journal_date_formats
    )

    headings: list[Heading] = []
    outlinks: list[Outlink] = []
    inline_tags: list[str] = []

    in_fence = False
    heading_ord = 0
    outlink_ord = 0

    line_no_base = data[:content_start].count(b"\n") + 1

    for line_no, line_start, line in _iter_lines(data[content_start:]):
        file_line_no = line_no_base + (line_no - 1)
        absolute_line_start = content_start + line_start
        stripped = line.lstrip(b" \t")
        if stripped.startswith(_CODE_FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        # Headings (ATX)
        m = re.match(rb"^[ \t]{0,3}(#{1,6})[ \t]+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).decode("utf-8", errors="strict").strip()
            # Strip optional closing hashes (commonmark-ish)
            text = re.sub(r"\s+#+\s*$", "", text).strip()
            start_byte = absolute_line_start + (len(line) - len(line.lstrip(b" \t")))
            end_byte = absolute_line_start + len(line)
            headings.append(
                Heading(
                    ord=heading_ord,
                    level=level,
                    text=text,
                    start_byte=start_byte,
                    end_byte=end_byte,
                    start_line=file_line_no,
                    end_line=file_line_no,
                )
            )
            heading_ord += 1

        # Outlinks + inline tags outside inline code
        for seg_start, seg_end in _outside_inline_code_segments(line):
            seg = line[seg_start:seg_end]

            # Outlinks
            idx = 0
            while True:
                open_i = seg.find(b"[[", idx)
                if open_i == -1:
                    break
                close_i = seg.find(b"]]", open_i + 2)
                if close_i == -1:
                    break
                raw_bytes = seg[open_i : close_i + 2]
                inner = seg[open_i + 2 : close_i].strip(b" \t")
                try:
                    inner_str = inner.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    idx = close_i + 2
                    continue
                left, alias = (inner_str.split("|", 1) + [None])[:2]
                alias = alias.strip() if alias is not None else None
                if alias == "":
                    alias = None
                target_part = left
                section = None
                if "#" in left:
                    target_part, section_part = left.split("#", 1)
                    section_part = section_part.strip()
                    section = section_part if section_part else None
                target = target_part.strip()
                if not target:
                    idx = close_i + 2
                    continue

                abs_start = absolute_line_start + seg_start + open_i
                abs_end = absolute_line_start + seg_start + close_i + 2
                outlinks.append(
                    Outlink(
                        ord=outlink_ord,
                        target=target,
                        section=section,
                        alias=alias,
                        raw=raw_bytes.decode("utf-8", errors="strict"),
                        start_byte=abs_start,
                        end_byte=abs_end,
                        start_line=file_line_no,
                        end_line=file_line_no,
                    )
                )
                outlink_ord += 1
                idx = close_i + 2

            # Inline tags
            seg_text = seg.decode("utf-8", errors="ignore")
            for match in _INLINE_TAG_RE.finditer(seg_text):
                name = match.group(2)
                if name:
                    inline_tags.append(name)

    inline_tags = sorted(set(inline_tags))
    return ParsedMarkdown(
        fm_journal_date=fm_journal_date,
        fm_tags=fm_tags,
        inline_tags=inline_tags,
        headings=headings,
        outlinks=outlinks,
    )
