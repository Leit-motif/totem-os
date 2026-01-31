from pathlib import Path

from totem.daemon_search.excerpts import ExcerptConfig, make_excerpt


def test_excerpt_bounds_and_utf8(tmp_path: Path):
    # Ensure multi-byte chars and bounds respected.
    text = "héllo " * 200
    data = text.encode("utf-8")

    excerpt = make_excerpt(
        file_bytes=data,
        start_byte=0,
        end_byte=len(data),
        query="héllo",
        cfg=ExcerptConfig(max_chars=50, before_chars=10, after_chars=20),
    )
    assert len(excerpt) <= 52  # allows leading/trailing ellipsis

