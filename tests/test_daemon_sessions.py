from __future__ import annotations

from pathlib import Path

from totem.daemon_ask.ask import _apply_budget_snapshot
from totem.daemon_ask.config import DaemonAskConfig
from totem.daemon_sessions.store import DaemonSessionStore


def test_sessions_persist_and_cap(tmp_path: Path) -> None:
    db = tmp_path / "sessions.sqlite"
    store = DaemonSessionStore(db)

    s1 = store.create_session(retrieval_budget_snapshot={"daemon_ask": {"top_k": 7}})
    assert s1.session_id
    assert store.get_current_session_id() == s1.session_id

    store.append_query(session_id=s1.session_id, query="q1", ts_utc="2026-02-02T00:00:01+00:00", cap=2)
    store.append_query(session_id=s1.session_id, query="q2", ts_utc="2026-02-02T00:00:02+00:00", cap=2)
    store.append_query(session_id=s1.session_id, query="q3", ts_utc="2026-02-02T00:00:03+00:00", cap=2)

    s1b = store.get_session(s1.session_id)
    assert s1b is not None
    assert [x["query"] for x in s1b.last_n_queries] == ["q2", "q3"]

    store.set_selected_sources(
        session_id=s1.session_id,
        selected_sources=[
            {"rel_path": "a.md", "start_byte": 0, "end_byte": 10},
            {"rel_path": "b.md", "start_byte": 5, "end_byte": 9},
        ],
        ts_utc="2026-02-02T00:00:04+00:00",
        cap=1,
    )
    s1c = store.get_session(s1.session_id)
    assert s1c is not None
    assert s1c.last_n_selected_sources == [{"rel_path": "b.md", "start_byte": 5, "end_byte": 9}]


def test_apply_budget_snapshot_overrides_cfg() -> None:
    cfg = DaemonAskConfig(
        vault_root=Path("/v"),
        db_path=Path("/v/db.sqlite"),
        top_k=10,
        per_file_cap=3,
        packed_max_chars=8000,
        traces_dir_rel="90_system/traces/daemon_ask",
        include_why=True,
        graph_default_on=False,
        graph_expand_cap=10,
        graph_rep_chunk_ord=0,
    )
    eff = _apply_budget_snapshot(cfg, {"daemon_ask": {"top_k": "2", "per_file_cap": 1, "packed_max_chars": 123}})
    assert eff.top_k == 2
    assert eff.per_file_cap == 1
    assert eff.packed_max_chars == 123

