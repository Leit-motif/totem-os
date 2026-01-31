import sqlite3
from pathlib import Path

from totem.daemon_index.indexer import index_daemon_vault
from totem.daemon_index.models import DaemonIndexConfig
from totem.daemon_embed.chunking import load_headings_for_file, plan_chunks_for_file
from totem.daemon_embed.models import ChunkingConfig


def _index_cfg(tmp_path: Path) -> DaemonIndexConfig:
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


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def test_heading_section_spans_and_utf8_round_trip(tmp_path: Path):
    cfg = _index_cfg(tmp_path)
    note = cfg.vault_root / "note.md"
    note.write_text("# Héading\nPara\n\nNext [[Tárget]]\n", encoding="utf-8")
    index_daemon_vault(cfg)

    conn = _conn(cfg.db_path)
    try:
        row = conn.execute("SELECT id, rel_path, size_bytes FROM files WHERE rel_path = 'note.md'").fetchone()
        file_id = int(row["id"])
        headings = load_headings_for_file(conn, file_id)
        planned = plan_chunks_for_file(
            vault_root=cfg.vault_root,
            rel_path="note.md",
            file_id=file_id,
            file_size_bytes=int(row["size_bytes"]),
            headings=headings,
            chunking=ChunkingConfig(min_bytes=0, max_bytes=4000, split_strategy="paragraph_then_window", include_preamble=False),
            embeddings_model="dummy",
        )
        assert len(planned) == 2  # paragraph split

        data = note.read_bytes()
        for ch in planned:
            # Slicing bytes must decode strict.
            data[ch.start_byte : ch.end_byte].decode("utf-8", errors="strict")
    finally:
        conn.close()


def test_window_splitting_is_deterministic_and_utf8_safe(tmp_path: Path):
    cfg = _index_cfg(tmp_path)
    note = cfg.vault_root / "big.md"
    # Ensure lots of multi-byte chars; splitting must not break UTF-8.
    payload = ("é" * 5000) + "\n"
    note.write_text("# H1\n" + payload, encoding="utf-8")
    index_daemon_vault(cfg)

    conn = _conn(cfg.db_path)
    try:
        row = conn.execute("SELECT id, size_bytes FROM files WHERE rel_path = 'big.md'").fetchone()
        file_id = int(row["id"])
        headings = load_headings_for_file(conn, file_id)
        planned1 = plan_chunks_for_file(
            vault_root=cfg.vault_root,
            rel_path="big.md",
            file_id=file_id,
            file_size_bytes=int(row["size_bytes"]),
            headings=headings,
            chunking=ChunkingConfig(min_bytes=0, max_bytes=200, split_strategy="paragraph_then_window", include_preamble=False),
            embeddings_model="dummy",
        )
        planned2 = plan_chunks_for_file(
            vault_root=cfg.vault_root,
            rel_path="big.md",
            file_id=file_id,
            file_size_bytes=int(row["size_bytes"]),
            headings=headings,
            chunking=ChunkingConfig(min_bytes=0, max_bytes=200, split_strategy="paragraph_then_window", include_preamble=False),
            embeddings_model="dummy",
        )
        assert [(c.start_byte, c.end_byte) for c in planned1] == [(c.start_byte, c.end_byte) for c in planned2]

        data = note.read_bytes()
        for ch in planned1:
            data[ch.start_byte : ch.end_byte].decode("utf-8", errors="strict")
            assert (ch.end_byte - ch.start_byte) <= 200
    finally:
        conn.close()

