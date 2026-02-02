from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from . import db as dbmod
from .excerpts import ExcerptConfig, make_excerpt
from .fts import fts_query
from .models import Candidate, DaemonSearchConfig, SearchFilters, SearchHit
from .scoring import min_max_normalize, recency_boost
from .vector import embed_query, vector_search


def _now_utc_date() -> datetime.date:
    return datetime.now(timezone.utc).date()


def _candidate_key(row: sqlite3.Row) -> str:
    # Stable key for fusion: chunk_id is stable.
    return str(row["chunk_id"])


def search_daemon(
    cfg: DaemonSearchConfig,
    *,
    query: str,
    top_k: int,
    prefer_recent: bool,
    filters: SearchFilters,
    expand_links: int,
) -> list[SearchHit]:
    top_k = int(top_k)
    if top_k <= 0:
        return []

    conn = dbmod.connect(cfg.db_path)
    try:
        dbmod.ensure_milestones_1_2_present(conn)

        allowed_file_ids = dbmod.resolve_allowed_file_ids_by_tags(
            conn,
            tags=filters.tags,
            tag_or=filters.tag_or,
        )

        lex_limit = max(top_k * 5, top_k)
        vec_limit = max(top_k * 5, top_k)

        lex_rows = fts_query(
            conn,
            query=query,
            limit=lex_limit,
            allowed_file_ids=allowed_file_ids,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )

        allowed_chunk_hashes = dbmod.get_chunk_hashes_for_filters(
            conn,
            allowed_file_ids=allowed_file_ids,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )

        query_vec = embed_query(query, dim=cfg.dim)
        vec_hits = vector_search(
            conn,
            query_vector=query_vec,
            model=cfg.model,
            dim=cfg.dim,
            top_k=vec_limit,
            allowed_chunk_hashes=allowed_chunk_hashes,
        )

        # Map chunk_hash -> vec_score
        vec_by_hash = {h: float(s) for (h, s) in vec_hits}

        # Build candidate map, prefer row data from lexical (includes offsets + effective_date).
        candidates: dict[str, Candidate] = {}

        lex_raw_by_key: dict[str, float] = {}
        for r in lex_rows:
            key = _candidate_key(r)
            bm25 = float(r["bm25"])
            # Invert bm25 so larger is better.
            lex_raw = -bm25
            lex_raw_by_key[key] = lex_raw
            candidates[key] = Candidate(
                file_id=int(r["file_id"]),
                rel_path=str(r["rel_path"]),
                title=str(r["title"] or ""),
                mtime_ns=int(r["mtime_ns"]),
                effective_date=str(r["effective_date"]),
                chunk_id=str(r["chunk_id"]),
                chunk_hash=str(r["chunk_hash"]),
                heading_path=str(r["heading_path"] or ""),
                start_byte=int(r["start_byte"]),
                end_byte=int(r["end_byte"]),
                lex_raw=lex_raw,
                vec_raw=None,
            )

        # Add vector candidates (we must fetch metadata from DB).
        if vec_by_hash:
            qmarks = ",".join(["?"] * len(vec_by_hash))
            rows = conn.execute(
                f"""
                SELECT
                  c.file_id AS file_id,
                  f.rel_path AS rel_path,
                  f.title AS title,
                  f.mtime_ns AS mtime_ns,
                  COALESCE(f.fm_journal_date, date(CAST(f.mtime_ns / 1000000000 AS INTEGER), 'unixepoch')) AS effective_date,
                  c.chunk_id AS chunk_id,
                  c.chunk_hash AS chunk_hash,
                  c.heading_path AS heading_path,
                  c.start_byte AS start_byte,
                  c.end_byte AS end_byte
                FROM chunks c
                JOIN files f ON f.id = c.file_id
                WHERE c.chunk_hash IN ({qmarks})
                ORDER BY f.rel_path ASC, c.ord ASC, c.start_byte ASC
                """,
                (*vec_by_hash.keys(),),
            ).fetchall()

            for r in rows:
                key = _candidate_key(r)
                ch = str(r["chunk_hash"])
                vec_raw = vec_by_hash.get(ch)
                if key in candidates:
                    prev = candidates[key]
                    candidates[key] = Candidate(
                        file_id=prev.file_id,
                        rel_path=prev.rel_path,
                        title=prev.title,
                        mtime_ns=prev.mtime_ns,
                        effective_date=prev.effective_date,
                        chunk_id=prev.chunk_id,
                        chunk_hash=prev.chunk_hash,
                        heading_path=prev.heading_path,
                        start_byte=prev.start_byte,
                        end_byte=prev.end_byte,
                        lex_raw=prev.lex_raw,
                        vec_raw=vec_raw,
                    )
                else:
                    candidates[key] = Candidate(
                        file_id=int(r["file_id"]),
                        rel_path=str(r["rel_path"]),
                        title=str(r["title"] or ""),
                        mtime_ns=int(r["mtime_ns"]),
                        effective_date=str(r["effective_date"]),
                        chunk_id=str(r["chunk_id"]),
                        chunk_hash=ch,
                        heading_path=str(r["heading_path"] or ""),
                        start_byte=int(r["start_byte"]),
                        end_byte=int(r["end_byte"]),
                        lex_raw=None,
                        vec_raw=vec_raw,
                    )

        # Normalize within candidate sets.
        lex_vals = {k: float(v.lex_raw) for k, v in candidates.items() if v.lex_raw is not None}
        vec_vals = {k: float(v.vec_raw) for k, v in candidates.items() if v.vec_raw is not None}
        lex_norm = min_max_normalize(lex_vals)
        vec_norm = min_max_normalize(vec_vals)

        now_date = _now_utc_date()

        scored: list[tuple[float, Candidate]] = []
        for k, c in candidates.items():
            score = (cfg.hybrid_weight_lex * lex_norm.get(k, 0.0)) + (cfg.hybrid_weight_vec * vec_norm.get(k, 0.0))
            if prefer_recent:
                score += recency_boost(
                    effective_date=c.effective_date,
                    now_utc=now_date,
                    half_life_days=cfg.prefer_recent_half_life_days,
                    weight=cfg.prefer_recent_weight,
                )
            scored.append((float(score), c))

        # Locked tie-break order: score DESC, mtime_ns DESC, rel_path ASC, chunk_start_byte ASC
        scored.sort(key=lambda t: (-t[0], -t[1].mtime_ns, t[1].rel_path, t[1].start_byte))

        primary = scored[:top_k]

        # Excerpts: bounded, chunk-based.
        ex_cfg = ExcerptConfig(
            max_chars=cfg.excerpt_max_chars,
            before_chars=cfg.context_before_chars,
            after_chars=cfg.context_after_chars,
        )
        hits: list[SearchHit] = []
        for score, c in primary:
            data = (cfg.vault_root / c.rel_path).read_bytes()
            excerpt = make_excerpt(
                file_bytes=data,
                start_byte=c.start_byte,
                end_byte=c.end_byte,
                query=query,
                cfg=ex_cfg,
            )
            hits.append(
                SearchHit(
                    score=score,
                    rel_path=c.rel_path,
                    title=c.title,
                    heading_path=c.heading_path,
                    start_byte=c.start_byte,
                    end_byte=c.end_byte,
                    effective_date=c.effective_date,
                    mtime_ns=c.mtime_ns,
                    excerpt=excerpt,
                    expanded_context=False,
                )
            )

        # Optional 1-hop expansion: append only, no reordering of primary hits.
        if expand_links:
            cap = min(int(cfg.expand_links_cap), int(expand_links) if int(expand_links) > 0 else int(cfg.expand_links_cap))
            primary_file_ids = [c.file_id for _, c in primary]
            neighbors = dbmod.get_expansion_neighbors(conn, file_ids=primary_file_ids, cap=cap)
            if neighbors:
                # Map expanded file to a deterministic representative chunk (ord=0).
                for n in neighbors:
                    fid = int(n["file_id"])
                    rep = conn.execute(
                        """
                        SELECT heading_path, start_byte, end_byte
                        FROM chunks
                        WHERE file_id = ?
                        ORDER BY ord ASC, start_byte ASC
                        LIMIT 1
                        """,
                        (fid,),
                    ).fetchone()
                    if rep is None:
                        continue
                    rel_path = str(n["rel_path"])
                    data = (cfg.vault_root / rel_path).read_bytes()
                    excerpt = make_excerpt(
                        file_bytes=data,
                        start_byte=int(rep["start_byte"]),
                        end_byte=int(rep["end_byte"]),
                        query=query,
                        cfg=ex_cfg,
                    )
                    hits.append(
                        SearchHit(
                            score=0.0,
                            rel_path=rel_path,
                            title=str(n["title"] or ""),
                            heading_path=str(rep["heading_path"] or ""),
                            start_byte=int(rep["start_byte"]),
                            end_byte=int(rep["end_byte"]),
                            effective_date=str(n["effective_date"]),
                            mtime_ns=int(n["mtime_ns"]),
                            excerpt=excerpt,
                            expanded_context=True,
                        )
                    )

        return hits
    finally:
        conn.close()

