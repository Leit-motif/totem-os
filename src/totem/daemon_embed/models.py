from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ChunkingConfig:
    min_bytes: int
    max_bytes: int
    split_strategy: str  # "paragraph_then_window"
    include_preamble: bool

    def signature(self) -> str:
        return f"min={self.min_bytes};max={self.max_bytes};split={self.split_strategy};preamble={int(self.include_preamble)}"


@dataclass(frozen=True)
class EmbeddingsConfig:
    backend: str  # "sqlite"
    model: str
    dim: int


@dataclass(frozen=True)
class DaemonEmbedConfig:
    vault_root: Path
    db_path: Path
    chunking: ChunkingConfig
    embeddings: EmbeddingsConfig


@dataclass(frozen=True)
class PlannedChunk:
    file_id: int
    heading_id: Optional[int]
    heading_path: str
    ord: int
    start_byte: int
    end_byte: int
    text_hash: str
    chunk_id: str
    chunk_hash: str
    byte_len: int


@dataclass(frozen=True)
class VectorRecord:
    chunk_hash: str
    model: str
    dim: int
    vector: bytes  # float32 little-endian bytes


@dataclass(frozen=True)
class DaemonEmbedSummary:
    files_considered: int
    files_rechunked: int
    chunks_upserted: int
    chunks_embedded: int
    files_embedded: int
    dangling_embeddings_deleted: int

