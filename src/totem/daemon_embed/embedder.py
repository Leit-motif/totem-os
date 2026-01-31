from __future__ import annotations

import hashlib
import struct


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

