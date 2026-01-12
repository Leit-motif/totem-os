"""Capture ingress for Totem OS."""

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .ledger import LedgerWriter
from .models.capture import CaptureMeta


def generate_unique_filename(directory: Path, base_name: str, extension: str = "") -> Path:
    """Generate a unique filename by adding suffix if collision occurs.
    
    Args:
        directory: Target directory
        base_name: Base filename (without extension)
        extension: File extension (including dot, e.g., '.txt')
        
    Returns:
        Path object with unique filename
    """
    candidate = directory / f"{base_name}{extension}"
    if not candidate.exists():
        return candidate

    # Try suffixes: _1, _2, _3, ...
    counter = 1
    while True:
        candidate = directory / f"{base_name}_{counter}{extension}"
        if not candidate.exists():
            return candidate
        counter += 1


def ingest_text_capture(
    vault_inbox: Path,
    text: str,
    ledger_writer: LedgerWriter,
    date_str: str,
) -> tuple[Path, Path, str]:
    """Ingest a text capture into the vault inbox.
    
    Args:
        vault_inbox: Path to vault 00_inbox directory
        text: Text content to capture
        ledger_writer: LedgerWriter instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        Tuple of (raw_file_path, meta_file_path, capture_id)
    """
    # Ensure date subfolder exists (SPEC: 00_inbox/YYYY-MM-DD/)
    date_folder = vault_inbox / date_str
    date_folder.mkdir(parents=True, exist_ok=True)

    # Generate unique capture ID
    capture_id = str(uuid.uuid4())

    # Generate timestamp-based filename
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = f"capture_{timestamp}"

    # Generate unique raw file path (handle collisions)
    raw_path = generate_unique_filename(date_folder, base_name, ".txt")

    # Write raw text file
    raw_path.write_text(text, encoding="utf-8")

    # Generate meta file path
    meta_path = raw_path.with_suffix(raw_path.suffix + ".meta.json")

    # Create metadata
    meta = CaptureMeta(
        id=capture_id,
        created_at=datetime.now(timezone.utc),
        source="cli_text",
        type="text",
        files=[raw_path.name],
        context=None,
        origin={"command": "totem capture --text"},
    )

    # Write meta JSON
    meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")

    # Append ledger event
    ledger_writer.append_event(
        event_type="CAPTURE_INGESTED",
        capture_id=capture_id,
        payload={
            "source": "cli_text",
            "raw_path": str(raw_path.relative_to(vault_inbox.parent)),
            "meta_path": str(meta_path.relative_to(vault_inbox.parent)),
            "date": date_str,
        },
    )

    return raw_path, meta_path, capture_id


def ingest_file_capture(
    vault_inbox: Path,
    source_file_path: Path,
    ledger_writer: LedgerWriter,
    date_str: str,
) -> tuple[Path, Path, str]:
    """Ingest a file capture into the vault inbox.
    
    Args:
        vault_inbox: Path to vault 00_inbox directory
        source_file_path: Path to source file to copy
        ledger_writer: LedgerWriter instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        Tuple of (raw_file_path, meta_file_path, capture_id)
    """
    if not source_file_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_file_path}")

    # Ensure date subfolder exists
    date_folder = vault_inbox / date_str
    date_folder.mkdir(parents=True, exist_ok=True)

    # Generate unique capture ID
    capture_id = str(uuid.uuid4())

    # Use original filename as base, handle collisions
    original_name = source_file_path.stem
    original_ext = source_file_path.suffix

    # Generate unique raw file path
    raw_path = generate_unique_filename(date_folder, original_name, original_ext)

    # Copy file to vault
    shutil.copy2(source_file_path, raw_path)

    # Generate meta file path
    meta_path = raw_path.with_suffix(raw_path.suffix + ".meta.json")

    # Determine content type from extension
    content_type = _infer_content_type(original_ext)

    # Create metadata
    meta = CaptureMeta(
        id=capture_id,
        created_at=datetime.now(timezone.utc),
        source="cli_file",
        type=content_type,
        files=[raw_path.name],
        context=None,
        origin={
            "command": "totem capture --file",
            "original_path": str(source_file_path.absolute()),
        },
    )

    # Write meta JSON
    meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")

    # Append ledger event
    ledger_writer.append_event(
        event_type="CAPTURE_INGESTED",
        capture_id=capture_id,
        payload={
            "source": "cli_file",
            "raw_path": str(raw_path.relative_to(vault_inbox.parent)),
            "meta_path": str(meta_path.relative_to(vault_inbox.parent)),
            "date": date_str,
            "original_filename": source_file_path.name,
        },
    )

    return raw_path, meta_path, capture_id


def _infer_content_type(extension: str) -> str:
    """Infer content type from file extension.
    
    Args:
        extension: File extension (including dot)
        
    Returns:
        Content type string
    """
    ext_lower = extension.lower()
    
    if ext_lower in [".txt"]:
        return "text"
    elif ext_lower in [".md", ".markdown"]:
        return "markdown"
    elif ext_lower in [".mp3", ".wav", ".m4a", ".ogg", ".flac"]:
        return "audio"
    elif ext_lower in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]:
        return "image"
    elif ext_lower == ".pdf":
        return "pdf"
    elif ext_lower == ".json":
        return "json"
    else:
        return "other"
