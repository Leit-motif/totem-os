import struct

import pytest

from totem.daemon_embed.embedder import (
    DeterministicSha256Embedder,
    OpenAIEmbeddingsEmbedder,
    create_text_embedder,
)


def test_create_text_embedder_sqlite_maps_to_deterministic():
    emb = create_text_embedder(backend="sqlite", model="dummy-sha256", dim=8)
    assert isinstance(emb, DeterministicSha256Embedder)
    vec = emb.embed_text("hello")
    vals = struct.unpack("<" + ("f" * 8), vec)
    assert len(vals) == 8


def test_create_text_embedder_openai_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_text_embedder(backend="openai", model="text-embedding-3-small", dim=8)


def test_openai_embedder_parses_embedding_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}

    def _fake_post(url, headers, json, timeout):  # noqa: ANN001
        assert "Authorization" in headers
        assert json["model"] == "text-embedding-3-small"
        assert json["dimensions"] == 4
        return _Resp()

    monkeypatch.setattr("totem.daemon_embed.embedder.requests.post", _fake_post)

    emb = OpenAIEmbeddingsEmbedder(model="text-embedding-3-small", dim=4)
    vec = emb.embed_text("hello")
    vals = struct.unpack("<ffff", vec)
    assert vals == pytest.approx((0.1, 0.2, 0.3, 0.4), rel=1e-6)
