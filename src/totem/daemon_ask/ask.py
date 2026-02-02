from __future__ import annotations

from dataclasses import asdict

from totem.daemon_search.config import load_daemon_search_config
from totem.daemon_search.db import connect as search_connect
from totem.daemon_search.engine import search_daemon
from totem.daemon_search.models import SearchFilters, SearchHit

from .config import DaemonAskConfig
from .graph import GraphExpandConfig, graph_expand
from .models import DaemonAskResult
from .packer import PackConfig, pack_context
from .reason import build_answer
from .rerank import RerankConfig, rerank_and_filter
from .trace import trace_payload, write_trace

PIPELINE_VERSION = "phase3@ask_v1"


def _why_sources(
    *,
    retrieved_n: int,
    packed_n: int,
    graph_enabled: bool,
) -> list[str]:
    bullets: list[str] = []
    bullets.append(f"Selected {packed_n}/{retrieved_n} hits under deterministic caps/budgets.")
    bullets.append("Primary hits come from hybrid FTS5 + vector search over indexed chunks.")
    if graph_enabled:
        bullets.append("Appended bounded 1-hop link neighbors (no reordering of primary hits).")
    return bullets[:4]


def _hits_to_candidate_rows(
    hits: list[SearchHit],
) -> list[dict]:
    rows: list[dict] = []
    for i, h in enumerate(hits, start=1):
        rows.append(
            {
                "rank": i,
                "score": float(h.score),
                "rel_path": h.rel_path,
                "start_byte": int(h.start_byte),
                "end_byte": int(h.end_byte),
                "effective_date": str(h.effective_date),
                "expanded_context": bool(h.expanded_context),
            }
        )
    return rows


def ask_daemon(
    cfg: DaemonAskConfig,
    *,
    query: str,
    graph: bool,
    quiet: bool,
) -> DaemonAskResult:
    # Reuse daemon_search config for scoring + excerpt behavior.
    search_cfg = load_daemon_search_config(cli_vault=str(cfg.vault_root), cli_db_path=str(cfg.db_path))

    filters = SearchFilters(tags=[], tag_or=False, date_from=None, date_to=None)
    primary = search_daemon(
        search_cfg,
        query=query,
        top_k=int(cfg.top_k),
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
                cfg=GraphExpandConfig(expand_cap=cfg.graph_expand_cap, rep_chunk_ord=cfg.graph_rep_chunk_ord),
            )
            hits.extend(expanded)
    finally:
        conn.close()

    filtered = rerank_and_filter(
        hits,
        cfg=RerankConfig(per_file_cap=int(cfg.per_file_cap), keep_expanded=True),
    )
    packed = pack_context(filtered, cfg=PackConfig(packed_max_chars=int(cfg.packed_max_chars)))

    why = _why_sources(retrieved_n=len(hits), packed_n=len(packed), graph_enabled=graph_enabled)
    answer, citations, why_out = build_answer(
        query=query,
        packed=packed,
        include_why=(cfg.include_why and not quiet),
        why_these_sources=why,
    )

    traces_dir = cfg.vault_root / cfg.traces_dir_rel
    payload = trace_payload(
        query=query,
        pipeline_version=PIPELINE_VERSION,
        ask_config=asdict(cfg),
        search_config=asdict(search_cfg),
        graph_enabled=graph_enabled,
        candidates=_hits_to_candidate_rows(filtered),
        packed=packed,
        answer=answer,
        citations=citations,
        why_these_sources=why_out,
    )
    trace_path = write_trace(
        traces_dir=traces_dir,
        payload=payload,
        trace_prefix="ask",
        trace_dedupe_key=f"{query}\n{cfg.vault_root}\n{cfg.db_path}\n{graph_enabled}\n{PIPELINE_VERSION}",
    )

    return DaemonAskResult(
        answer=answer,
        citations=citations,
        why_these_sources=why_out,
        packed=packed,
        trace_path=str(trace_path),
    )
