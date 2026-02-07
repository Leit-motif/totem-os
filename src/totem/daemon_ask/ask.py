from __future__ import annotations

from dataclasses import asdict, replace

from totem.daemon_search.config import load_daemon_search_config
from totem.daemon_search.db import connect as search_connect
from totem.daemon_search.engine import search_daemon
from totem.daemon_search.excerpts import ExcerptConfig, make_excerpt
from totem.daemon_search.models import SearchFilters, SearchHit

from .config import DaemonAskConfig
from .graph import GraphExpandConfig, graph_expand
from .models import DaemonAskResult
from .packer import PackConfig, pack_context
from .reason import build_answer
from .rerank import RerankConfig, rerank_and_filter
from .time import TemporalConfig, TemporalFeature, apply_temporal_layer
from .trace import trace_payload, write_trace

PIPELINE_VERSION = "phase3@ask_v1"


def _why_sources(
    *,
    retrieved_n: int,
    packed_n: int,
    graph_enabled: bool,
    time_mode: str,
    time_window_days: int | None,
) -> list[str]:
    bullets: list[str] = []
    bullets.append(f"Selected {packed_n}/{retrieved_n} hits under deterministic caps/budgets.")
    bullets.append("Primary hits come from hybrid FTS5 + vector search over indexed chunks.")
    if time_window_days is None:
        bullets.append(f"Applied temporal mode '{time_mode}' with deterministic decay (no hard time window).")
    else:
        bullets.append(f"Applied temporal mode '{time_mode}' with a {time_window_days}-day window.")
    if graph_enabled:
        bullets.append("Appended bounded 1-hop link neighbors (no reordering of primary hits).")
    return bullets[:4]


def _hits_to_candidate_rows(
    hits: list[SearchHit],
    temporal_features: list[TemporalFeature | None] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    t_features = temporal_features or []
    for i, h in enumerate(hits, start=1):
        t = t_features[i - 1] if i - 1 < len(t_features) else None
        time_features = None
        if t is not None:
            time_features = {
                "mode": t.mode,
                "note_type": t.note_type,
                "reference_date": t.reference_date,
                "effective_date": t.effective_date,
                "age_days": int(t.age_days),
                "window_days": t.window_days,
                "within_window": bool(t.within_window),
                "half_life_days": float(t.half_life_days),
                "weight": float(t.weight),
                "decay": float(t.decay),
                "boost": float(t.boost),
                "base_score": float(t.base_score),
                "temporal_score": float(t.temporal_score),
            }
        rows.append(
            {
                "rank": i,
                "score": float(h.score),
                "rel_path": h.rel_path,
                "start_byte": int(h.start_byte),
                "end_byte": int(h.end_byte),
                "effective_date": str(h.effective_date),
                "expanded_context": bool(h.expanded_context),
                "time_features": time_features,
            }
        )
    return rows


def _apply_budget_snapshot(cfg: DaemonAskConfig, snapshot: dict | None) -> DaemonAskConfig:
    if not snapshot or not isinstance(snapshot, dict):
        return cfg
    section = snapshot.get("daemon_ask")
    if not isinstance(section, dict):
        return cfg

    def _int_or(value, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    return replace(
        cfg,
        top_k=_int_or(section.get("top_k"), cfg.top_k),
        per_file_cap=_int_or(section.get("per_file_cap"), cfg.per_file_cap),
        packed_max_chars=_int_or(section.get("packed_max_chars"), cfg.packed_max_chars),
    )


def _session_pins(
    conn,
    *,
    search_cfg,
    query: str,
    sources: list[dict],
) -> list[SearchHit]:
    if not sources:
        return []

    ex_cfg = ExcerptConfig(
        max_chars=search_cfg.excerpt_max_chars,
        before_chars=search_cfg.context_before_chars,
        after_chars=search_cfg.context_after_chars,
    )

    pinned: list[SearchHit] = []
    for s in sources:
        try:
            rel_path = str(s["rel_path"])
            start_byte = int(s["start_byte"])
            end_byte = int(s["end_byte"])
        except Exception:
            continue

        # Best-effort: only include if the chunk still exists in the index.
        row = conn.execute(
            """
            SELECT f.title AS title, f.mtime_ns AS mtime_ns,
                   COALESCE(f.fm_journal_date, date(CAST(f.mtime_ns / 1000000000 AS INTEGER), 'unixepoch')) AS effective_date,
                   c.heading_path AS heading_path
            FROM files f
            JOIN chunks c ON c.file_id = f.id
            WHERE f.rel_path = ? AND c.start_byte = ? AND c.end_byte = ?
            LIMIT 1
            """,
            (rel_path, start_byte, end_byte),
        ).fetchone()
        if row is None:
            continue

        data = (search_cfg.vault_root / rel_path).read_bytes()
        excerpt = make_excerpt(
            file_bytes=data,
            start_byte=start_byte,
            end_byte=end_byte,
            query=query,
            cfg=ex_cfg,
        )
        pinned.append(
            SearchHit(
                score=0.0,
                rel_path=rel_path,
                title=str(row["title"] or ""),
                heading_path=str(row["heading_path"] or ""),
                start_byte=start_byte,
                end_byte=end_byte,
                effective_date=str(row["effective_date"]),
                mtime_ns=int(row["mtime_ns"]),
                excerpt=excerpt,
                expanded_context=True,
            )
        )

    return pinned


def _hit_key(hit: SearchHit) -> tuple[str, int, int]:
    return (hit.rel_path, int(hit.start_byte), int(hit.end_byte))


def ask_daemon(
    cfg: DaemonAskConfig,
    *,
    query: str,
    graph: bool,
    quiet: bool,
    session_store: object | None = None,
    session_id: str | None = None,
    session_caps: dict | None = None,
    time_mode: str | None = None,
) -> DaemonAskResult:
    if time_mode is not None:
        candidate_mode = time_mode.strip().lower()
        if candidate_mode not in {"recent", "month", "year", "all", "hybrid"}:
            raise ValueError("Invalid --time value. Expected one of: recent|month|year|all|hybrid")

    # Reuse daemon_search config for scoring + excerpt behavior.
    search_cfg = load_daemon_search_config(cli_vault=str(cfg.vault_root), cli_db_path=str(cfg.db_path))

    session_before = None
    session_after = None
    session_rw_log: list[dict] = []

    effective_cfg = cfg
    if session_store is not None and session_id is not None:
        s = session_store.get_session(session_id)
        session_before = s.to_snapshot_dict() if s is not None else None
        session_rw_log.append({"op": "get_session", "session_id": session_id})
        if s is not None:
            effective_cfg = _apply_budget_snapshot(cfg, getattr(s, "retrieval_budget_snapshot", None))

    filters = SearchFilters(tags=[], tag_or=False, date_from=None, date_to=None)
    primary = search_daemon(
        search_cfg,
        query=query,
        top_k=int(effective_cfg.top_k),
        prefer_recent=False,
        filters=filters,
        expand_links=0,
    )

    hits: list[SearchHit] = list(primary)
    graph_enabled = bool(graph)

    conn = search_connect(search_cfg.db_path)
    try:
        if graph_enabled:
            expanded = graph_expand(
                conn,
                search_cfg=search_cfg,
                query=query,
                primary_hits=primary,
                cfg=GraphExpandConfig(expand_cap=effective_cfg.graph_expand_cap, rep_chunk_ord=effective_cfg.graph_rep_chunk_ord),
            )
            hits.extend(expanded)

        if session_store is not None and session_id is not None and session_before is not None:
            pinned = _session_pins(
                conn,
                search_cfg=search_cfg,
                query=query,
                sources=list(session_before.get("last_n_selected_sources") or []),
            )
            if pinned:
                hits.extend(pinned)
                session_rw_log.append({"op": "append_session_pins", "count": len(pinned)})
    finally:
        conn.close()

    temporal = apply_temporal_layer(
        hits,
        mode=time_mode,
        cfg=TemporalConfig(
            default_mode=effective_cfg.time_mode_default,
            window_recent_days=effective_cfg.time_window_recent_days,
            window_month_days=effective_cfg.time_window_month_days,
            window_year_days=effective_cfg.time_window_year_days,
            decay_half_life_days=effective_cfg.time_decay_half_life_days,
            weight_journal=effective_cfg.time_weight_journal,
            weight_evergreen=effective_cfg.time_weight_evergreen,
        ),
    )

    filtered = rerank_and_filter(
        temporal.hits,
        cfg=RerankConfig(per_file_cap=int(effective_cfg.per_file_cap), keep_expanded=True),
    )
    feature_by_key: dict[tuple[str, int, int], TemporalFeature] = {}
    for i, h in enumerate(temporal.hits):
        key = _hit_key(h)
        if key not in feature_by_key and i < len(temporal.features):
            feature_by_key[key] = temporal.features[i]
    filtered_features = [feature_by_key.get(_hit_key(h)) for h in filtered]

    packed = pack_context(filtered, cfg=PackConfig(packed_max_chars=int(effective_cfg.packed_max_chars)))

    why = _why_sources(
        retrieved_n=len(hits),
        packed_n=len(packed),
        graph_enabled=graph_enabled,
        time_mode=temporal.mode,
        time_window_days=temporal.window_days,
    )
    answer, citations, why_out = build_answer(
        query=query,
        packed=packed,
        include_why=(effective_cfg.include_why and not quiet),
        why_these_sources=why,
    )

    if session_store is not None and session_id is not None:
        caps = session_caps or {}
        qcap = int(caps.get("last_n_queries_cap", 0) or 0)
        scap = int(caps.get("last_n_sources_cap", 0) or 0)
        session_rw_log.append(session_store.append_query(session_id=session_id, query=query, ts_utc=None, cap=qcap))
        session_rw_log.append(
            session_store.set_selected_sources(
                session_id=session_id,
                selected_sources=[
                    {"rel_path": c.rel_path, "start_byte": int(c.start_byte), "end_byte": int(c.end_byte)}
                    for c in citations
                ],
                ts_utc=None,
                cap=scap,
            )
        )
        s2 = session_store.get_session(session_id)
        session_after = s2.to_snapshot_dict() if s2 is not None else None
        session_rw_log.append({"op": "get_session", "session_id": session_id, "phase": "after"})

    traces_dir = cfg.vault_root / cfg.traces_dir_rel
    payload = trace_payload(
        query=query,
        pipeline_version=PIPELINE_VERSION,
        ask_config=asdict(cfg),
        ask_config_effective=asdict(effective_cfg) if effective_cfg != cfg else None,
        search_config=asdict(search_cfg),
        graph_enabled=graph_enabled,
        candidates=_hits_to_candidate_rows(filtered, temporal_features=filtered_features),
        temporal_mode=temporal.mode,
        temporal_reference_date=temporal.reference_date,
        temporal_window_days=temporal.window_days,
        packed=packed,
        answer=answer,
        citations=citations,
        why_these_sources=why_out,
        session_before=session_before,
        session_after=session_after,
        session_rw_log=session_rw_log,
    )
    trace_path = write_trace(
        traces_dir=traces_dir,
        payload=payload,
        trace_prefix="ask",
        trace_dedupe_key=f"{query}\n{cfg.vault_root}\n{cfg.db_path}\n{graph_enabled}\n{session_id or ''}\n{PIPELINE_VERSION}",
    )

    return DaemonAskResult(
        answer=answer,
        citations=citations,
        why_these_sources=why_out,
        packed=packed,
        trace_path=str(trace_path),
    )
