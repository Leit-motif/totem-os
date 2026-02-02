import os
import sqlite3
from pathlib import Path

from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_index.parser import parse_markdown_bytes


def _cfg(tmp_path: Path) -> DaemonIndexConfig:
    vault_root = tmp_path / "daemon_vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    db_path = vault_root / "state" / "daemon_index.sqlite"
    return DaemonIndexConfig(
        vault_root=vault_root,
        db_path=db_path,
        exclude_globs=["state/**", ".git/**"],
        frontmatter_journal_date_key="date",
        frontmatter_journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"],
    )


def _q(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_utf8_byte_offsets_round_trip_for_heading_and_outlink():
    data = (
        "---\n"
        "date: 01-30-2026\n"
        "tags: [alpha]\n"
        "---\n"
        "# Héading\n"
        "See [[Tárget#Séc|Álias]] end\n"
    ).encode("utf-8")

    parsed = parse_markdown_bytes(
        data, journal_date_key="date", journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"]
    )
    assert parsed.fm_journal_date == "2026-01-30"
    assert len(parsed.headings) == 1
    assert len(parsed.outlinks) == 1

    h = parsed.headings[0]
    o = parsed.outlinks[0]

    assert data[h.start_byte : h.end_byte] == "# Héading".encode("utf-8")
    assert data[o.start_byte : o.end_byte] == "[[Tárget#Séc|Álias]]".encode("utf-8")


def test_frontmatter_date_parsing_accepts_both_formats_and_normalizes():
    data_md = ("---\ndate: 01-30-2026\n---\n# X\n").encode("utf-8")
    parsed_md = parse_markdown_bytes(
        data_md, journal_date_key="date", journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"]
    )
    assert parsed_md.fm_journal_date == "2026-01-30"

    data_iso = ("---\ndate: 2026-01-30\n---\n# X\n").encode("utf-8")
    parsed_iso = parse_markdown_bytes(
        data_iso, journal_date_key="date", journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"]
    )
    assert parsed_iso.fm_journal_date == "2026-01-30"

    data_bad = ("---\ndate: 2026/01/30\n---\n# X\n").encode("utf-8")
    parsed_bad = parse_markdown_bytes(
        data_bad, journal_date_key="date", journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"]
    )
    assert parsed_bad.fm_journal_date is None


def test_incremental_touch_does_not_rewrite_derived_rows(tmp_path: Path):
    cfg = _cfg(tmp_path)
    note = cfg.vault_root / "a.md"
    note.write_text("# A\nSee [[X]]\n", encoding="utf-8")

    summary1 = index_daemon_vault(cfg)
    assert summary1.scanned == 1
    assert summary1.updated == 1

    heading_ids_1 = [r["id"] for r in _q(cfg.db_path, "SELECT id FROM headings ORDER BY id")]
    outlink_ids_1 = [r["id"] for r in _q(cfg.db_path, "SELECT id FROM outlinks ORDER BY id")]
    assert heading_ids_1
    assert outlink_ids_1

    st = note.stat()
    os.utime(note, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    summary2 = index_daemon_vault(cfg)
    assert summary2.scanned == 1
    assert summary2.unchanged == 0
    assert summary2.updated == 1

    heading_ids_2 = [r["id"] for r in _q(cfg.db_path, "SELECT id FROM headings ORDER BY id")]
    outlink_ids_2 = [r["id"] for r in _q(cfg.db_path, "SELECT id FROM outlinks ORDER BY id")]
    assert heading_ids_2 == heading_ids_1
    assert outlink_ids_2 == outlink_ids_1


def test_deletion_cascades_dependent_rows(tmp_path: Path):
    cfg = _cfg(tmp_path)
    a = cfg.vault_root / "a.md"
    b = cfg.vault_root / "b.md"
    a.write_text("# A\nSee [[X]]\n", encoding="utf-8")
    b.write_text("# B\nSee [[Y]]\n", encoding="utf-8")

    summary1 = index_daemon_vault(cfg)
    assert summary1.scanned == 2
    assert summary1.updated == 2

    b.unlink()

    summary2 = index_daemon_vault(cfg)
    assert summary2.deleted == 1
    files = _q(cfg.db_path, "SELECT rel_path FROM files ORDER BY rel_path")
    assert [r["rel_path"] for r in files] == ["a.md"]
    assert _q(cfg.db_path, "SELECT COUNT(*) AS c FROM headings")[0]["c"] == 1
    assert _q(cfg.db_path, "SELECT COUNT(*) AS c FROM outlinks")[0]["c"] == 1


def test_outlinks_ignored_in_code_fences_and_inline_code():
    data = (
        "See [[Keep]]\n"
        "`inline [[Nope]]`\n"
        "```python\n"
        "[[Nope2]]\n"
        "```\n"
    ).encode("utf-8")

    parsed = parse_markdown_bytes(
        data, journal_date_key="date", journal_date_formats=["%Y-%m-%d", "%m-%d-%Y"]
    )
    assert [o.target for o in parsed.outlinks] == ["Keep"]

