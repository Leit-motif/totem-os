from totem.daemon_ask.models import Citation, PackedExcerpt
from totem.daemon_ask.reason import build_answer


def _packed() -> list[PackedExcerpt]:
    return [
        PackedExcerpt(
            citation=Citation(rel_path="a.md", start_byte=0, end_byte=10),
            title="A",
            heading_path="",
            effective_date="2026-01-01",
            excerpt="alpha",
            score=1.0,
            expanded_context=False,
        ),
        PackedExcerpt(
            citation=Citation(rel_path="b.md", start_byte=10, end_byte=20),
            title="B",
            heading_path="",
            effective_date="2026-01-02",
            excerpt="beta",
            score=0.9,
            expanded_context=False,
        ),
    ]


def test_build_answer_sources_off_hides_citations_block():
    answer, citations, _ = build_answer(
        query="q",
        packed=_packed(),
        include_why=False,
        why_these_sources=[],
        sources_mode="off",
    )
    assert len(citations) == 2
    assert "Citations:" not in answer
    assert "[a.md:0-10]" not in answer


def test_build_answer_sources_always_shows_citations():
    answer, _, _ = build_answer(
        query="q",
        packed=_packed(),
        include_why=False,
        why_these_sources=[],
        sources_mode="always",
    )
    assert "Citations:" in answer
    assert "[a.md:0-10]" in answer
    assert "[b.md:10-20]" in answer
