"""Autonomous distillation with append-only canon writes.

Milestone 4: LLM distillation with reversible audit trail.
Principle: System writes first (append-only), human veto/undo later.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .capture import generate_unique_filename
from .ledger import LedgerWriter
from .llm import LLMClient
from .models.distill import (
    AppliedFile,
    CanonWriteRecord,
    DistillResult,
    EntityMention,
)
from .paths import VaultPaths


# Delimiter markers for inserted blocks (enables reliable undo)
BLOCK_START_MARKER = "<!-- TOTEM_BLOCK_START:{write_id} -->"
BLOCK_END_MARKER = "<!-- TOTEM_BLOCK_END:{write_id} -->"


def compute_content_hash(text: str) -> str:
    """Compute SHA256 hash of text for integrity verification.
    
    Args:
        text: Text content to hash
        
    Returns:
        Hex-encoded SHA256 hash string
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_routed_items(
    vault_paths: VaultPaths,
    date_str: str,
    limit: int = 20,
) -> list[dict]:
    """Load routed items from 10_derived/routed/YYYY-MM-DD/.
    
    Args:
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        limit: Maximum number of items to load
        
    Returns:
        List of routed item dictionaries with raw_text added
    """
    routed_folder = vault_paths.routed_date_folder(date_str)
    
    if not routed_folder.exists():
        return []
    
    items = []
    json_files = sorted(routed_folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    
    for json_path in json_files[:limit]:
        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
            
            # Load raw text from original capture
            raw_path_str = item.get("raw_file_path", "")
            if raw_path_str:
                raw_file_path = vault_paths.root / raw_path_str
                if raw_file_path.exists() and raw_file_path.is_file():
                    item["raw_text"] = raw_file_path.read_text(encoding="utf-8")
                else:
                    item["raw_text"] = ""
            else:
                item["raw_text"] = ""
            
            items.append(item)
        except (json.JSONDecodeError, KeyError) as e:
            # Skip malformed files
            continue
    
    return items


def write_distill_artifact(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    date_str: str,
) -> Path:
    """Write distillation result to 10_derived/distill/YYYY-MM-DD/<capture_id>.json.
    
    Args:
        distill_result: Distillation result to write
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        Path to written distill artifact
    """
    distill_folder = vault_paths.distill_date_folder(date_str)
    distill_folder.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename (no overwrite)
    output_path = generate_unique_filename(
        distill_folder,
        distill_result.capture_id,
        ".json"
    )
    
    output_path.write_text(distill_result.model_dump_json(indent=2), encoding="utf-8")
    
    return output_path


def append_to_daily_note(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    date_str: str,
    write_id: str,
) -> tuple[str, str]:
    """Append distillation to 20_memory/daily/YYYY-MM-DD.md.
    
    Args:
        distill_result: Distillation result
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        write_id: Write ID for block markers
        
    Returns:
        Tuple of (relative path, inserted text)
    """
    daily_path = vault_paths.daily_note_path(date_str)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Build the block to insert
    now_utc = datetime.now(timezone.utc)
    time_str = now_utc.strftime("%H:%M")
    
    lines = [
        BLOCK_START_MARKER.format(write_id=write_id),
        f"### Totem Distill ({time_str} UTC) — {distill_result.capture_id[:8]}",
        "",
        f"**Summary:** {distill_result.summary}",
        "",
    ]
    
    if distill_result.key_points:
        lines.append("**Key Points:**")
        for point in distill_result.key_points[:5]:
            lines.append(f"- {point}")
        lines.append("")
    
    if distill_result.entities:
        lines.append("**Entities:**")
        for entity in distill_result.entities[:7]:
            note_str = f" ({entity.note})" if entity.note else ""
            lines.append(f"- {entity.name} [{entity.kind.value}]{note_str}")
        lines.append("")
    
    if distill_result.tasks:
        lines.append("**Tasks:**")
        for task in distill_result.tasks[:7]:
            priority_marker = {"high": "[HIGH]", "med": "[MED]", "low": "[LOW]"}.get(task.priority.value, "")
            due_str = f" (due: {task.due_date})" if task.due_date else ""
            lines.append(f"- [ ] {priority_marker} {task.text}{due_str}")
        lines.append("")
    
    lines.append(BLOCK_END_MARKER.format(write_id=write_id))
    lines.append("")
    
    inserted_text = "\n".join(lines)
    
    # Append to file (create if doesn't exist)
    if daily_path.exists():
        existing = daily_path.read_text(encoding="utf-8")
        daily_path.write_text(existing + "\n" + inserted_text, encoding="utf-8")
    else:
        # Create new daily note with header
        header = f"# Daily Notes — {date_str}\n\n"
        daily_path.write_text(header + inserted_text, encoding="utf-8")
    
    return str(daily_path.relative_to(vault_paths.root)), inserted_text


def append_tasks_to_todo(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    date_str: str,
    write_id: str,
) -> tuple[str, str] | None:
    """Append tasks to 30_tasks/todo.md under AI Draft Tasks section.
    
    Deduplicates tasks within that section by exact match.
    
    Args:
        distill_result: Distillation result
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        write_id: Write ID for block markers
        
    Returns:
        Tuple of (relative path, inserted text) or None if no tasks
    """
    if not distill_result.tasks:
        return None
    
    todo_path = vault_paths.todo_file
    
    # Build the block to insert
    lines = [
        BLOCK_START_MARKER.format(write_id=write_id),
        f"## AI Draft Tasks ({date_str})",
        "",
    ]
    
    for task in distill_result.tasks[:7]:
        priority_marker = {"high": "[HIGH]", "med": "[MED]", "low": "[LOW]"}.get(task.priority.value, "")
        due_str = f" (due: {task.due_date})" if task.due_date else ""
        lines.append(f"- [ ] {priority_marker} {task.text}{due_str}")
    
    lines.append("")
    lines.append(BLOCK_END_MARKER.format(write_id=write_id))
    lines.append("")
    
    inserted_text = "\n".join(lines)
    
    # Read existing content
    if todo_path.exists():
        existing = todo_path.read_text(encoding="utf-8")
        
        # Simple deduplication: check if exact task text already exists
        new_tasks_text = []
        for task in distill_result.tasks[:7]:
            if task.text not in existing:
                priority_marker = {"high": "[HIGH]", "med": "[MED]", "low": "[LOW]"}.get(task.priority.value, "")
                due_str = f" (due: {task.due_date})" if task.due_date else ""
                new_tasks_text.append(f"- [ ] {priority_marker} {task.text}{due_str}")
        
        if not new_tasks_text:
            # All tasks already exist
            return None
        
        # Rebuild inserted_text with only new tasks
        lines = [
            BLOCK_START_MARKER.format(write_id=write_id),
            f"## AI Draft Tasks ({date_str})",
            "",
        ]
        lines.extend(new_tasks_text)
        lines.append("")
        lines.append(BLOCK_END_MARKER.format(write_id=write_id))
        lines.append("")
        inserted_text = "\n".join(lines)
        
        todo_path.write_text(existing + "\n" + inserted_text, encoding="utf-8")
    else:
        # Create new todo file
        header = "# Totem OS - Next Actions\n\n"
        todo_path.write_text(header + inserted_text, encoding="utf-8")
    
    return str(todo_path.relative_to(vault_paths.root)), inserted_text


def update_entities_json(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    write_id: str,
) -> tuple[str, str] | None:
    """Update 20_memory/entities.json with new entities.
    
    Append-only: only adds entities if name+kind not already present.
    
    Args:
        distill_result: Distillation result
        vault_paths: VaultPaths instance
        write_id: Write ID for tracking
        
    Returns:
        Tuple of (relative path, JSON string of added entities) or None if no new entities
    """
    if not distill_result.entities:
        return None
    
    entities_path = vault_paths.entities_file
    
    # Load existing entities
    if entities_path.exists():
        try:
            existing = json.loads(entities_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except json.JSONDecodeError:
            existing = []
    else:
        existing = []
    
    # Build set of existing name+kind pairs
    existing_keys = {(e.get("name", "").lower(), e.get("kind", "")) for e in existing}
    
    # Find new entities
    new_entities = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    for entity in distill_result.entities[:7]:
        key = (entity.name.lower(), entity.kind.value)
        if key not in existing_keys:
            new_entities.append({
                "name": entity.name,
                "kind": entity.kind.value,
                "note": entity.note,
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "source_capture_ids": [distill_result.capture_id],
                "write_id": write_id,
            })
            existing_keys.add(key)
    
    if not new_entities:
        return None
    
    # Append new entities
    existing.extend(new_entities)
    entities_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    
    return str(entities_path.relative_to(vault_paths.root)), json.dumps(new_entities)


def build_daily_note_text(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    date_str: str,
    write_id: str,
) -> tuple[str, str]:
    """Build daily note text without writing to file.
    
    Args:
        distill_result: Distillation result
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        write_id: Write ID for block markers
        
    Returns:
        Tuple of (relative path, text that would be inserted)
    """
    daily_path = vault_paths.daily_note_path(date_str)
    
    now_utc = datetime.now(timezone.utc)
    time_str = now_utc.strftime("%H:%M")
    
    lines = [
        BLOCK_START_MARKER.format(write_id=write_id),
        f"### Totem Distill ({time_str} UTC) — {distill_result.capture_id[:8]}",
        "",
        f"**Summary:** {distill_result.summary}",
        "",
    ]
    
    if distill_result.key_points:
        lines.append("**Key Points:**")
        for point in distill_result.key_points[:5]:
            lines.append(f"- {point}")
        lines.append("")
    
    if distill_result.entities:
        lines.append("**Entities:**")
        for entity in distill_result.entities[:7]:
            note_str = f" ({entity.note})" if entity.note else ""
            lines.append(f"- {entity.name} [{entity.kind.value}]{note_str}")
        lines.append("")
    
    if distill_result.tasks:
        lines.append("**Tasks:**")
        for task in distill_result.tasks[:7]:
            priority_marker = {"high": "[HIGH]", "med": "[MED]", "low": "[LOW]"}.get(task.priority.value, "")
            due_str = f" (due: {task.due_date})" if task.due_date else ""
            lines.append(f"- [ ] {priority_marker} {task.text}{due_str}")
        lines.append("")
    
    lines.append(BLOCK_END_MARKER.format(write_id=write_id))
    lines.append("")
    
    inserted_text = "\n".join(lines)
    return str(daily_path.relative_to(vault_paths.root)), inserted_text


def build_todo_text(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    date_str: str,
    write_id: str,
) -> tuple[str, str] | None:
    """Build todo text without writing to file.
    
    Args:
        distill_result: Distillation result
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        write_id: Write ID for block markers
        
    Returns:
        Tuple of (relative path, text that would be inserted) or None if no tasks
    """
    if not distill_result.tasks:
        return None
    
    todo_path = vault_paths.todo_file
    
    lines = [
        BLOCK_START_MARKER.format(write_id=write_id),
        f"## AI Draft Tasks ({date_str})",
        "",
    ]
    
    for task in distill_result.tasks[:7]:
        priority_marker = {"high": "[HIGH]", "med": "[MED]", "low": "[LOW]"}.get(task.priority.value, "")
        due_str = f" (due: {task.due_date})" if task.due_date else ""
        lines.append(f"- [ ] {priority_marker} {task.text}{due_str}")
    
    lines.append("")
    lines.append(BLOCK_END_MARKER.format(write_id=write_id))
    lines.append("")
    
    inserted_text = "\n".join(lines)
    return str(todo_path.relative_to(vault_paths.root)), inserted_text


def build_entities_text(
    distill_result: DistillResult,
    vault_paths: VaultPaths,
    write_id: str,
) -> tuple[str, str] | None:
    """Build entities JSON text without writing to file.
    
    Args:
        distill_result: Distillation result
        vault_paths: VaultPaths instance
        write_id: Write ID for tracking
        
    Returns:
        Tuple of (relative path, JSON string of entities to add) or None if no new entities
    """
    if not distill_result.entities:
        return None
    
    entities_path = vault_paths.entities_file
    
    # Load existing entities to check for duplicates
    if entities_path.exists():
        try:
            existing = json.loads(entities_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except json.JSONDecodeError:
            existing = []
    else:
        existing = []
    
    existing_keys = {(e.get("name", "").lower(), e.get("kind", "")) for e in existing}
    
    new_entities = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    for entity in distill_result.entities[:7]:
        key = (entity.name.lower(), entity.kind.value)
        if key not in existing_keys:
            new_entities.append({
                "name": entity.name,
                "kind": entity.kind.value,
                "note": entity.note,
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "source_capture_ids": [distill_result.capture_id],
                "write_id": write_id,
            })
            existing_keys.add(key)
    
    if not new_entities:
        return None
    
    return str(entities_path.relative_to(vault_paths.root)), json.dumps(new_entities, indent=2)


def process_distillation_dry_run(
    routed_item: dict,
    llm_client: LLMClient,
    vault_paths: VaultPaths,
    date_str: str,
) -> tuple[DistillResult, list[AppliedFile], str]:
    """Process distillation in dry-run mode (no canon writes).
    
    Generates distill artifact and builds what would be written,
    but does not modify daily notes, todo, or entities files.
    
    Args:
        routed_item: Dictionary containing routed capture data
        llm_client: LLM client for distillation
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        Tuple of (DistillResult, list of AppliedFile that would be written, distill_path)
    """
    write_id = str(uuid.uuid4())
    capture_id = routed_item.get("capture_id", "unknown")
    
    # Step 1: Call LLM to distill
    distill_result = llm_client.distill(routed_item)
    
    # Step 2: Write distill artifact (this is useful even in dry-run)
    distill_artifact_path = write_distill_artifact(distill_result, vault_paths, date_str)
    distill_relative_path = str(distill_artifact_path.relative_to(vault_paths.root))
    
    # Step 3: Build what would be written (but don't write)
    would_apply: list[AppliedFile] = []
    
    # 3a: Daily note
    daily_result = build_daily_note_text(distill_result, vault_paths, date_str, write_id)
    if daily_result:
        path, text = daily_result
        would_apply.append(AppliedFile(
            path=path,
            inserted_text=text,
            content_hash=compute_content_hash(text),
            mode="append"
        ))
    
    # 3b: Todo
    todo_result = build_todo_text(distill_result, vault_paths, date_str, write_id)
    if todo_result:
        path, text = todo_result
        would_apply.append(AppliedFile(
            path=path,
            inserted_text=text,
            content_hash=compute_content_hash(text),
            mode="append"
        ))
    
    # 3c: Entities
    entities_result = build_entities_text(distill_result, vault_paths, write_id)
    if entities_result:
        path, text = entities_result
        would_apply.append(AppliedFile(
            path=path,
            inserted_text=text,
            content_hash=compute_content_hash(text),
            mode="append"
        ))
    
    return distill_result, would_apply, distill_relative_path


def write_canon_write_record(
    write_id: str,
    capture_id: str,
    applied_files: list[AppliedFile],
    distill_path: str,
    vault_paths: VaultPaths,
    date_str: str,
) -> Path:
    """Write CanonWriteRecord to 90_system/traces/writes/YYYY-MM-DD/<write_id>.json.
    
    Args:
        write_id: Unique write identifier
        capture_id: Source capture identifier
        applied_files: List of applied file records
        distill_path: Relative path to distill artifact
        vault_paths: VaultPaths instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        Path to written trace file
    """
    traces_folder = vault_paths.traces_writes_date_folder(date_str)
    traces_folder.mkdir(parents=True, exist_ok=True)
    
    record = CanonWriteRecord(
        write_id=write_id,
        ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        capture_id=capture_id,
        applied_files=applied_files,
        distill_path=distill_path,
        can_undo=True,
    )
    
    trace_path = traces_folder / f"{write_id}.json"
    trace_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    
    return trace_path


def process_distillation(
    routed_item: dict,
    llm_client: LLMClient,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
    date_str: str,
) -> tuple[DistillResult, CanonWriteRecord]:
    """Process distillation for a single routed item.
    
    Args:
        routed_item: Dictionary containing routed capture data
        llm_client: LLM client for distillation
        vault_paths: VaultPaths instance
        ledger_writer: LedgerWriter instance
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        Tuple of (DistillResult, CanonWriteRecord)
    """
    # Generate write ID
    write_id = str(uuid.uuid4())
    capture_id = routed_item.get("capture_id", "unknown")
    
    # Step 1: Call LLM to distill
    distill_result = llm_client.distill(routed_item)
    
    # Step 2: Write distill artifact
    distill_artifact_path = write_distill_artifact(distill_result, vault_paths, date_str)
    distill_relative_path = str(distill_artifact_path.relative_to(vault_paths.root))
    
    # Step 3: Apply canon writes (append-only)
    applied_files: list[AppliedFile] = []
    modified_paths: list[str] = []
    
    # 3a: Append to daily note
    daily_result = append_to_daily_note(distill_result, vault_paths, date_str, write_id)
    if daily_result:
        path, text = daily_result
        applied_files.append(AppliedFile(
            path=path,
            inserted_text=text,
            content_hash=compute_content_hash(text),
            mode="append"
        ))
        modified_paths.append(path)
    
    # 3b: Append tasks to todo
    todo_result = append_tasks_to_todo(distill_result, vault_paths, date_str, write_id)
    if todo_result:
        path, text = todo_result
        applied_files.append(AppliedFile(
            path=path,
            inserted_text=text,
            content_hash=compute_content_hash(text),
            mode="append"
        ))
        modified_paths.append(path)
    
    # 3c: Update entities.json
    entities_result = update_entities_json(distill_result, vault_paths, write_id)
    if entities_result:
        # For entities, we track the added JSON but undo is more complex
        # Mark as potentially not reversible for entities
        path, text = entities_result
        applied_files.append(AppliedFile(
            path=path,
            inserted_text=text,
            content_hash=compute_content_hash(text),
            mode="append"
        ))
        modified_paths.append(path)
    
    # Step 4: Write CanonWriteRecord trace
    trace_path = write_canon_write_record(
        write_id=write_id,
        capture_id=capture_id,
        applied_files=applied_files,
        distill_path=distill_relative_path,
        vault_paths=vault_paths,
        date_str=date_str,
    )
    
    # Step 5: Append ledger event
    ledger_payload = {
        "capture_id": capture_id,
        "route_label": distill_result.route_label,
        "confidence": distill_result.confidence,
        "distill_path": distill_relative_path,
        "modified_files": modified_paths,
        "write_id": write_id,
        "engine": llm_client.engine_name,
    }
    
    # Add provider/model if real client
    if llm_client.provider_model:
        ledger_payload["provider_model"] = llm_client.provider_model
    
    ledger_writer.append_event(
        event_type="DISTILL_APPLIED",
        capture_id=capture_id,
        payload=ledger_payload,
    )
    
    # Load and return the CanonWriteRecord
    record = CanonWriteRecord.model_validate_json(trace_path.read_text(encoding="utf-8"))
    
    return distill_result, record


def undo_canon_write(
    write_id: str,
    vault_paths: VaultPaths,
    ledger_writer: LedgerWriter,
) -> list[str]:
    """Undo a canon write by removing inserted blocks.
    
    Args:
        write_id: Write ID to undo
        vault_paths: VaultPaths instance
        ledger_writer: LedgerWriter instance
        
    Returns:
        List of files that were modified during undo
    """
    # Find the CanonWriteRecord
    record_path = None
    for date_folder in vault_paths.traces_writes.iterdir():
        if date_folder.is_dir():
            candidate = date_folder / f"{write_id}.json"
            if candidate.exists():
                record_path = candidate
                break
    
    if not record_path:
        raise FileNotFoundError(f"CanonWriteRecord not found for write_id: {write_id}")
    
    record = CanonWriteRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
    
    if not record.can_undo:
        raise ValueError(f"Write {write_id} is marked as not undoable")
    
    modified_files = []
    warnings = []
    
    for applied_file in record.applied_files:
        file_path = vault_paths.root / applied_file.path
        
        if not file_path.exists():
            warnings.append(f"File not found: {applied_file.path}")
            continue
        
        # For entities.json, skip undo (too complex to reverse merge safely)
        if applied_file.path.endswith("entities.json"):
            warnings.append(f"Skipping entities.json undo (manual review needed)")
            continue
        
        content = file_path.read_text(encoding="utf-8")
        
        # Find and remove the inserted block using markers
        start_marker = BLOCK_START_MARKER.format(write_id=write_id)
        end_marker = BLOCK_END_MARKER.format(write_id=write_id)
        
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker)
        
        if start_idx == -1 or end_idx == -1:
            warnings.append(f"Block markers not found in {applied_file.path}")
            continue
        
        # Extract the block content (including markers) for hash verification
        end_idx_with_marker = end_idx + len(end_marker)
        # Include trailing newline in the block for hash comparison
        block_end = end_idx_with_marker
        while block_end < len(content) and content[block_end] == "\n":
            block_end += 1
        
        # The inserted_text includes the trailing newline, so we need to match
        block_content = content[start_idx:block_end]
        
        # Verify content hash if available (for backwards compatibility, check if hash exists)
        if hasattr(applied_file, 'content_hash') and applied_file.content_hash:
            current_hash = compute_content_hash(block_content)
            if current_hash != applied_file.content_hash:
                warnings.append(
                    f"Hash mismatch in {applied_file.path}: file was manually edited. "
                    f"Expected {applied_file.content_hash[:16]}..., got {current_hash[:16]}..."
                )
                # Skip this file to avoid data loss from manual edits
                continue
        
        # Remove the block (including markers and trailing newline)
        # Also remove leading newline if present
        remove_start = start_idx
        if remove_start > 0 and content[remove_start - 1] == "\n":
            remove_start -= 1
        
        new_content = content[:remove_start] + content[block_end:]
        file_path.write_text(new_content, encoding="utf-8")
        modified_files.append(applied_file.path)
    
    # Mark record as undone (update can_undo to false)
    record_dict = record.model_dump()
    record_dict["can_undo"] = False
    record_dict["undone_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_path.write_text(json.dumps(record_dict, indent=2), encoding="utf-8")
    
    # Append ledger event
    ledger_writer.append_event(
        event_type="DISTILL_UNDONE",
        capture_id=record.capture_id,
        payload={
            "write_id": write_id,
            "modified_files": modified_files,
            "warnings": warnings,
        },
    )
    
    return modified_files
