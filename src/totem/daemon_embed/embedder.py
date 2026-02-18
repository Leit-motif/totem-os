from __future__ import annotations

import hashlib
import os
import struct
from typing import Protocol

import requests


class TextEmbedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed_text(self, text: str) -> bytes: ...


class DeterministicSha256Embedder:
    """Deterministic placeholder embedder.

    Produces a float32 vector derived from SHA-256 of the UTF-8 bytes.
    This is inspectable, stable, and requires no external dependencies.
    """

    def __init__(self, dim: int):
        if dim <= 0:
            raise ValueError("embeddings_dim must be > 0")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed_text(self, text: str) -> bytes:
        data = text.encode("utf-8")
        floats: list[float] = []
        counter = 0
        while len(floats) < self._dim:
            h = hashlib.sha256()
            h.update(counter.to_bytes(4, "little"))
            h.update(data)
            digest = h.digest()
            for i in range(0, len(digest), 4):
                if len(floats) >= self._dim:
                    break
                word = int.from_bytes(digest[i : i + 4], "little", signed=False)
                # Map to [-1, 1] deterministically.
                floats.append(((word / 0xFFFFFFFF) * 2.0) - 1.0)
            counter += 1

        return struct.pack("<" + ("f" * self._dim), *floats)


class OpenAIEmbeddingsEmbedder:
    """Production embedder via OpenAI Embeddings API.

    Requires OPENAI_API_KEY in environment.
    """

    def __init__(self, *, model: str, dim: int, api_key: str | None = None, timeout_seconds: int = 30):
        if dim <= 0:
            raise ValueError("embeddings_dim must be > 0")
        self._model = model
        self._dim = dim
        self._timeout_seconds = int(timeout_seconds)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is required for embeddings backend='openai'")
        self._url = os.environ.get("TOTEM_OPENAI_EMBEDDINGS_URL", "https://api.openai.com/v1/embeddings")

    @property
    def dim(self) -> int:
        return self._dim

    def embed_text(self, text: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": text,
            "encoding_format": "float",
            "dimensions": self._dim,
        }
        resp = requests.post(self._url, headers=headers, json=payload, timeout=self._timeout_seconds)
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenAI embeddings request failed: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise RuntimeError("OpenAI embeddings response missing data[0]")
        emb = items[0].get("embedding") if isinstance(items[0], dict) else None
        if not isinstance(emb, list):
            raise RuntimeError("OpenAI embeddings response missing embedding list")
        if len(emb) != self._dim:
            raise RuntimeError(f"Embedding dimension mismatch: expected {self._dim}, got {len(emb)}")

        floats = [float(x) for x in emb]
        return struct.pack("<" + ("f" * self._dim), *floats)


def create_text_embedder(*, backend: str, model: str, dim: int) -> TextEmbedder:
    backend_n = (backend or "").strip().lower()

    # Keep backward compatibility with existing defaults/configs.
    if backend_n in {"", "sqlite", "dummy-sha256", "deterministic"}:
        return DeterministicSha256Embedder(dim)

    if backend_n == "openai":
        return OpenAIEmbeddingsEmbedder(model=model, dim=dim)

    raise ValueError(f"Unsupported embeddings backend: {backend}")


def mean_float32_le(vectors: list[bytes], *, dim: int, weights: list[float] | None = None) -> bytes:
    if not vectors:
        raise ValueError("Cannot compute mean of empty vector list")
    if weights is not None and len(weights) != len(vectors):
        raise ValueError("weights must match vectors length")

    accum = [0.0] * dim
    denom = 0.0
    for idx, v in enumerate(vectors):
        vals = struct.unpack("<" + ("f" * dim), v)
        w = float(weights[idx]) if weights is not None else 1.0
        denom += w
        for j in range(dim):
            accum[j] += vals[j] * w

    if denom == 0.0:
        raise ValueError("Denominator is zero in weighted mean")
    out = [x / denom for x in accum]
    return struct.pack("<" + ("f" * dim), *out)
