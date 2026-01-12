"""Typer-based CLI for Totem OS."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .capture import ingest_file_capture, ingest_text_capture
from .config import TotemConfig
from .distill import (
    load_routed_items,
    process_distillation,
    process_distillation_dry_run,
    undo_canon_write,
)
from .ledger import LedgerWriter, read_ledger_tail
from .llm import get_llm_client
from .paths import VaultPaths
from .route import process_capture_routing

app = typer.Typer(
    name="totem",
    help="Totem OS - Local-first personal cognitive operating system",
    add_completion=False,
)

console = Console()


@app.command()
def init(
    vault_path: str = typer.Option(
        None,
        "--vault",
        "-v",
        help="Path to vault directory (default: TOTEM_VAULT_PATH env or ./totem_vault)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Re-initialize even if vault already exists",
    ),
):
    """Initialize a new Totem OS vault with directory structure and system files.
    
    This command is idempotent - it will not overwrite existing data.
    """
    # Load configuration from environment or use defaults
    if vault_path:
        config = TotemConfig(vault_path=Path(vault_path))
    else:
        config = TotemConfig.from_env()
    
    vault_root = config.vault_path
    paths = VaultPaths.from_config(config)
    
    # Check if vault already exists
    if vault_root.exists() and not force:
        # Check if it looks like a vault (has system directory)
        if paths.system.exists():
            console.print(f"[yellow]Vault already exists at:[/yellow] {vault_root}")
            console.print("[yellow]Running in idempotent mode - will only create missing items[/yellow]")
        else:
            console.print(f"[yellow]Directory exists but is not a vault:[/yellow] {vault_root}")
            console.print("[yellow]Initializing vault structure...[/yellow]")
    else:
        console.print(f"[green]Initializing new Totem OS vault at:[/green] {vault_root}")
    
    # Create all directories (idempotent - won't fail if exists)
    directories_created = []
    for directory in paths.get_all_directories():
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            directories_created.append(directory)
    
    if directories_created:
        console.print(f"[green]+[/green] Created {len(directories_created)} directories")
    else:
        console.print("[dim]All directories already exist[/dim]")
    
    # Create config.yaml if it doesn't exist
    if not paths.config_file.exists():
        paths.config_file.write_text(config.to_yaml_str())
        console.print(f"[green]+[/green] Created config: {paths.config_file}")
    else:
        console.print(f"[dim]Config already exists: {paths.config_file}[/dim]")
    
    # Create empty ledger.jsonl if it doesn't exist
    if not paths.ledger_file.exists():
        paths.ledger_file.touch()
        console.print(f"[green]+[/green] Created ledger: {paths.ledger_file}")
    else:
        console.print(f"[dim]Ledger already exists: {paths.ledger_file}[/dim]")
    
    # Create empty entities.json if it doesn't exist
    if not paths.entities_file.exists():
        paths.entities_file.write_text("[]")
        console.print(f"[green]+[/green] Created entities: {paths.entities_file}")
    else:
        console.print(f"[dim]Entities file already exists: {paths.entities_file}[/dim]")
    
    # Create empty todo.md if it doesn't exist
    if not paths.todo_file.exists():
        todo_template = """# Totem OS - Next Actions

<!--
Max 3 actions at a time.
Format: - [ ] action description
-->

"""
        paths.todo_file.write_text(todo_template)
        console.print(f"[green]+[/green] Created todo: {paths.todo_file}")
    else:
        console.print(f"[dim]Todo file already exists: {paths.todo_file}[/dim]")
    
    # Create empty principles.md if it doesn't exist
    if not paths.principles_file.exists():
        principles_template = """# Personal Principles

<!--
This file captures your evolving principles and values.
Updated by distillation when decisions reveal patterns.
-->

"""
        paths.principles_file.write_text(principles_template)
        console.print(f"[green]+[/green] Created principles: {paths.principles_file}")
    else:
        console.print(f"[dim]Principles file already exists: {paths.principles_file}[/dim]")
    
    console.print()
    console.print("[bold green]Vault initialization complete![/bold green]")
    console.print(f"[dim]Vault location:[/dim] {vault_root.absolute()}")


@app.command()
def capture(
    text: str = typer.Option(
        None,
        "--text",
        "-t",
        help="Capture text content directly",
    ),
    file: str = typer.Option(
        None,
        "--file",
        "-f",
        help="Capture file by copying into vault inbox",
    ),
    vault_path: str = typer.Option(
        None,
        "--vault",
        "-v",
        help="Path to vault directory (default: TOTEM_VAULT_PATH env or ./totem_vault)",
    ),
    date: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Date for inbox folder (YYYY-MM-DD, default: today)",
    ),
):
    """Capture text or file into the vault inbox.
    
    Creates raw file + .meta.json sidecar in 00_inbox/YYYY-MM-DD/.
    Appends CAPTURE_INGESTED event to ledger.jsonl.
    """
    # Validate: exactly one of --text or --file must be provided
    if not text and not file:
        console.print("[red]Error: Must provide either --text or --file[/red]")
        raise typer.Exit(code=1)
    
    if text and file:
        console.print("[red]Error: Cannot provide both --text and --file[/red]")
        raise typer.Exit(code=1)

    # Load vault configuration
    if vault_path:
        config = TotemConfig(vault_path=Path(vault_path))
    else:
        config = TotemConfig.from_env()
    
    paths = VaultPaths.from_config(config)

    # Check if vault exists
    if not paths.system.exists():
        console.print(f"[red]Error: Vault not initialized at {config.vault_path}[/red]")
        console.print("[yellow]Run 'totem init' first[/yellow]")
        raise typer.Exit(code=1)

    # Determine date string (today if not provided)
    if date:
        date_str = date
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Initialize ledger writer
    ledger_writer = LedgerWriter(paths.ledger_file)

    try:
        if text:
            # Ingest text capture
            raw_path, meta_path, capture_id = ingest_text_capture(
                vault_inbox=paths.inbox,
                text=text,
                ledger_writer=ledger_writer,
                date_str=date_str,
            )
            console.print("[green]Captured text:[/green]")
            console.print(f"  Raw:  {raw_path.relative_to(paths.root)}")
            console.print(f"  Meta: {meta_path.relative_to(paths.root)}")
            console.print(f"  ID:   {capture_id}")
        
        elif file:
            # Ingest file capture
            source_path = Path(file)
            raw_path, meta_path, capture_id = ingest_file_capture(
                vault_inbox=paths.inbox,
                source_file_path=source_path,
                ledger_writer=ledger_writer,
                date_str=date_str,
            )
            console.print("[green]Captured file:[/green]")
            console.print(f"  Raw:  {raw_path.relative_to(paths.root)}")
            console.print(f"  Meta: {meta_path.relative_to(paths.root)}")
            console.print(f"  ID:   {capture_id}")

    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error during capture: {e}[/red]")
        raise typer.Exit(code=1)


ledger_app = typer.Typer(help="Ledger commands")
app.add_typer(ledger_app, name="ledger")


@ledger_app.command("tail")
def ledger_tail(
    n: int = typer.Option(
        20,
        "--n",
        help="Number of recent events to display",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Show full payloads with JSON pretty-print",
    ),
    vault_path: str = typer.Option(
        None,
        "--vault",
        "-v",
        help="Path to vault directory (default: TOTEM_VAULT_PATH env or ./totem_vault)",
    ),
):
    """Display the last N events from the ledger.
    
    Shows recent ledger events with timestamps, types, and payloads.
    Skips malformed lines with warnings.
    Use --full to see complete payloads with JSON formatting.
    """
    # Load vault configuration
    if vault_path:
        config = TotemConfig(vault_path=Path(vault_path))
    else:
        config = TotemConfig.from_env()
    
    paths = VaultPaths.from_config(config)

    # Check if vault exists
    if not paths.system.exists():
        console.print(f"[red]Error: Vault not initialized at {config.vault_path}[/red]")
        console.print("[yellow]Run 'totem init' first[/yellow]")
        raise typer.Exit(code=1)

    # Read ledger tail
    events = read_ledger_tail(paths.ledger_file, n=n)

    if not events:
        console.print("[dim]No events in ledger[/dim]")
        return

    if full:
        # Full mode: show each event with pretty-printed JSON
        console.print(f"[bold]Last {len(events)} Ledger Event(s)[/bold]\n")
        for i, event in enumerate(events, 1):
            console.print(f"[cyan]Event {i}/{len(events)}[/cyan]")
            console.print(f"  [dim]Event ID:[/dim]    {event.event_id}")
            console.print(f"  [dim]Run ID:[/dim]      {event.run_id}")
            console.print(f"  [dim]Timestamp:[/dim]   {event.ts.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            console.print(f"  [dim]Event Type:[/dim]  [magenta]{event.event_type}[/magenta]")
            console.print(f"  [dim]Capture ID:[/dim]  {event.capture_id or '-'}")
            console.print(f"  [dim]Payload:[/dim]")
            
            import json
            payload_json = json.dumps(event.payload, indent=2)
            for line in payload_json.split('\n'):
                console.print(f"    {line}")
            console.print()
    else:
        # Table mode: compact view with truncation
        table = Table(title=f"Last {len(events)} Ledger Event(s)")
        table.add_column("Timestamp (UTC)", style="cyan", no_wrap=True)
        table.add_column("Event Type", style="magenta")
        table.add_column("Capture ID", style="yellow")
        table.add_column("Payload", style="dim")

        for event in events:
            # Format timestamp with UTC indicator
            ts_str = event.ts.strftime("%Y-%m-%d %H:%M:%S")
            
            # Format capture ID
            capture_id_str = event.capture_id[:8] + "..." if event.capture_id else "-"
            
            # Format payload (truncate if too long)
            payload_str = str(event.payload)
            if len(payload_str) > 60:
                payload_str = payload_str[:57] + "..."
            
            table.add_row(ts_str, event.event_type, capture_id_str, payload_str)

        console.print(table)


@app.command()
def route(
    vault_path: str = typer.Option(
        None,
        "--vault",
        "-v",
        help="Path to vault directory (default: TOTEM_VAULT_PATH env or ./totem_vault)",
    ),
    date: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Date for inbox folder (YYYY-MM-DD, default: today)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of captures to process",
    ),
    engine: str = typer.Option(
        "auto",
        "--engine",
        "-e",
        help="Routing engine: 'rule', 'llm', 'hybrid', or 'auto' (default: auto - hybrid if API key present, else rule)",
    ),
    llm_engine: str = typer.Option(
        "auto",
        "--llm-engine",
        help="LLM engine for llm/hybrid: 'fake', 'openai', 'anthropic', or 'auto' (default: auto)",
    ),
    no_short_circuit: bool = typer.Option(
        False,
        "--no-short-circuit",
        help="Hybrid mode: always call LLM even if rule confidence is high (for A/B testing)",
    ),
):
    """Route captures using rule-based, LLM, or hybrid routing.
    
    Reads raw captures from 00_inbox/YYYY-MM-DD/, applies routing logic,
    and writes outputs to either routed/ or review_queue/ based on confidence.
    
    Engine modes:
    - rule: Deterministic keyword-based heuristics only
    - llm: LLM-based classification only
    - hybrid: Rule first, LLM fallback if rule confidence < threshold
    - auto: hybrid if API key present, else rule
    
    Use --no-short-circuit to force hybrid mode to always call LLM (for A/B testing).
    """
    from .llm.router import has_llm_api_key
    
    # Validate engine option
    valid_engines = ["rule", "llm", "hybrid", "auto"]
    if engine not in valid_engines:
        console.print(f"[red]Error: Invalid engine '{engine}'. Must be one of: {', '.join(valid_engines)}[/red]")
        raise typer.Exit(code=1)
    
    # Load vault configuration
    if vault_path:
        config = TotemConfig(vault_path=Path(vault_path))
    else:
        config = TotemConfig.from_env()
    
    paths = VaultPaths.from_config(config)

    # Check if vault exists
    if not paths.system.exists():
        console.print(f"[red]Error: Vault not initialized at {config.vault_path}[/red]")
        console.print("[yellow]Run 'totem init' first[/yellow]")
        raise typer.Exit(code=1)

    # Determine effective engine and display info
    effective_engine = engine
    if engine == "auto":
        if has_llm_api_key():
            effective_engine = "hybrid"
            console.print("[dim]Auto-detected API key: using hybrid engine[/dim]")
        else:
            effective_engine = "rule"
            console.print("[dim]No API key found: using rule engine[/dim]")
    else:
        console.print(f"[dim]Using {engine} engine[/dim]")
    
    # Display no-short-circuit mode
    if no_short_circuit:
        if effective_engine in ("hybrid", "auto"):
            console.print("[yellow]--no-short-circuit: LLM will always be called (A/B testing mode)[/yellow]")
        else:
            console.print("[dim]Note: --no-short-circuit only affects hybrid mode[/dim]")
    
    # Check if LLM is requested but no API key
    if effective_engine in ("llm", "hybrid") and llm_engine != "fake" and not has_llm_api_key():
        if llm_engine == "auto":
            console.print("[dim]No API key found: using fake LLM router[/dim]")
        else:
            console.print(f"[red]Error: LLM engine '{llm_engine}' requested but no API key found[/red]")
            console.print("[yellow]Set OPENAI_API_KEY or ANTHROPIC_API_KEY, or use --llm-engine fake[/yellow]")
            raise typer.Exit(code=1)

    # Determine date string (today if not provided)
    if date:
        date_str = date
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Find inbox date folder
    inbox_date_folder = paths.inbox_date_folder(date_str)
    
    if not inbox_date_folder.exists():
        console.print(f"[yellow]No inbox folder found for {date_str}[/yellow]")
        console.print(f"[dim]Expected: {inbox_date_folder}[/dim]")
        return

    # Find all raw capture files (exclude .meta.json files)
    all_files = []
    for file_path in inbox_date_folder.iterdir():
        if file_path.is_file() and not file_path.name.endswith(".meta.json"):
            all_files.append(file_path)
    
    if not all_files:
        console.print(f"[yellow]No capture files found in {inbox_date_folder.name}/[/yellow]")
        return

    # Sort by modification time (newest first)
    all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    
    # Apply limit
    files_to_process = all_files[:limit]
    
    if len(all_files) > limit:
        console.print(f"[dim]Found {len(all_files)} captures, processing {limit} (use --limit to change)[/dim]\n")
    else:
        console.print(f"[dim]Found {len(files_to_process)} capture(s) to process[/dim]\n")

    # Initialize ledger writer
    ledger_writer = LedgerWriter(paths.ledger_file)

    # Process each capture
    results = []
    routed_count = 0
    review_count = 0

    for raw_file in files_to_process:
        # Find corresponding meta file
        meta_file = raw_file.with_suffix(raw_file.suffix + ".meta.json")
        
        if not meta_file.exists():
            console.print(f"[yellow]Warning: No meta file for {raw_file.name}, skipping[/yellow]")
            continue

        try:
            # Process routing with specified engine
            output_path, was_routed = process_capture_routing(
                raw_file_path=raw_file,
                meta_file_path=meta_file,
                vault_root=paths.root,
                config=config,
                ledger_writer=ledger_writer,
                date_str=date_str,
                engine=effective_engine,
                llm_engine=llm_engine,
                no_short_circuit=no_short_circuit,
            )
            
            # Read the output to get details for display
            import json
            output_data = json.loads(output_path.read_text(encoding="utf-8"))
            
            results.append({
                "capture_id": output_data["capture_id"],
                "route": output_data["route_label"],
                "confidence": output_data["confidence"],
                "was_routed": was_routed,
                "output_path": output_path.relative_to(paths.root),
            })
            
            if was_routed:
                routed_count += 1
            else:
                review_count += 1

        except Exception as e:
            console.print(f"[red]Error processing {raw_file.name}: {e}[/red]")
            continue

    # Display results table
    if results:
        table = Table(title=f"Routing Results for {date_str} (engine: {effective_engine})")
        table.add_column("Capture ID", style="cyan")
        table.add_column("Route", style="magenta")
        table.add_column("Confidence", style="yellow")
        table.add_column("Destination", style="green")

        for result in results:
            capture_id_short = result["capture_id"][:8] + "..."
            confidence_str = f"{result['confidence']:.2f}"
            destination = "routed" if result["was_routed"] else "review_queue"
            
            table.add_row(
                capture_id_short,
                result["route"],
                confidence_str,
                destination
            )

        console.print(table)
        console.print()
        console.print(f"[bold green]Summary:[/bold green] {routed_count} routed, {review_count} flagged for review")
    else:
        console.print("[yellow]No captures were processed[/yellow]")


@app.command()
def distill(
    vault_path: str = typer.Option(
        None,
        "--vault",
        "-v",
        help="Path to vault directory (default: TOTEM_VAULT_PATH env or ./totem_vault)",
    ),
    date: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Date for processing (YYYY-MM-DD, default: today)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of routed items to process",
    ),
    engine: str = typer.Option(
        "auto",
        "--engine",
        "-e",
        help="LLM engine: 'fake', 'real', 'openai', 'anthropic', or 'auto' (default: auto)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview distillation without writing canon files",
    ),
):
    """Distill routed captures using LLM and apply canon writes.
    
    Reads from 10_derived/routed/YYYY-MM-DD/, processes through LLM distillation,
    and writes results to distill/, daily notes, todo, and entities.
    All writes are append-only with undo support via 'totem undo'.
    
    Use --dry-run to preview what would be written without applying changes.
    """
    # Load vault configuration
    if vault_path:
        config = TotemConfig(vault_path=Path(vault_path))
    else:
        config = TotemConfig.from_env()
    
    paths = VaultPaths.from_config(config)

    # Check if vault exists
    if not paths.system.exists():
        console.print(f"[red]Error: Vault not initialized at {config.vault_path}[/red]")
        console.print("[yellow]Run 'totem init' first[/yellow]")
        raise typer.Exit(code=1)

    # Determine date string (today if not provided)
    if date:
        date_str = date
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Get LLM client
    try:
        llm_client = get_llm_client(engine)
        console.print(f"[dim]Using LLM engine: {llm_client.engine_name}[/dim]")
        if llm_client.provider_model:
            console.print(f"[dim]Provider/model: {llm_client.provider_model}[/dim]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)

    # Load routed items
    routed_items = load_routed_items(paths, date_str, limit=limit)
    
    if not routed_items:
        console.print(f"[yellow]No routed items found for {date_str}[/yellow]")
        console.print(f"[dim]Expected: {paths.routed_date_folder(date_str)}[/dim]")
        return

    if dry_run:
        console.print(f"[yellow]DRY-RUN MODE[/yellow] — Found {len(routed_items)} routed item(s) to preview\n")
    else:
        console.print(f"[dim]Found {len(routed_items)} routed item(s) to process[/dim]\n")

    # Initialize ledger writer (only used in non-dry-run mode)
    ledger_writer = LedgerWriter(paths.ledger_file)

    # Process each routed item
    results = []
    for item in routed_items:
        capture_id = item.get("capture_id", "unknown")
        capture_id_short = capture_id[:8] + "..."
        
        try:
            console.print(f"[cyan]Processing:[/cyan] {capture_id_short}")
            
            if dry_run:
                # Dry-run mode: generate but don't write canon files
                distill_result, would_apply, distill_path = process_distillation_dry_run(
                    routed_item=item,
                    llm_client=llm_client,
                    vault_paths=paths,
                    date_str=date_str,
                )
                
                results.append({
                    "capture_id": capture_id,
                    "confidence": distill_result.confidence,
                    "summary": distill_result.summary[:50] + "..." if len(distill_result.summary) > 50 else distill_result.summary,
                    "tasks_count": len(distill_result.tasks),
                    "entities_count": len(distill_result.entities),
                    "would_modify": len(would_apply),
                    "distill_path": distill_path,
                    "would_apply": would_apply,
                })
                
                console.print(f"  [green]+[/green] Distilled (confidence: {distill_result.confidence:.2f})")
                console.print(f"    Distill artifact: [dim]{distill_path}[/dim]")
                console.print(f"    [yellow]Would write to {len(would_apply)} file(s):[/yellow]")
                for af in would_apply:
                    console.print(f"      - {af.path}")
            else:
                # Normal mode: write canon files
                distill_result, write_record = process_distillation(
                    routed_item=item,
                    llm_client=llm_client,
                    vault_paths=paths,
                    ledger_writer=ledger_writer,
                    date_str=date_str,
                )
                
                results.append({
                    "capture_id": capture_id,
                    "write_id": write_record.write_id,
                    "confidence": distill_result.confidence,
                    "summary": distill_result.summary[:50] + "..." if len(distill_result.summary) > 50 else distill_result.summary,
                    "tasks_count": len(distill_result.tasks),
                    "entities_count": len(distill_result.entities),
                    "modified_files": len(write_record.applied_files),
                })
                
                console.print(f"  [green]+[/green] Distilled (confidence: {distill_result.confidence:.2f})")
                console.print(f"    Write ID: [yellow]{write_record.write_id[:8]}...[/yellow]")
            
        except Exception as e:
            console.print(f"  [red]x Error: {e}[/red]")
            continue

    # Summary
    console.print()
    if results:
        if dry_run:
            table = Table(title=f"Dry-Run Preview — {date_str}")
            table.add_column("Capture", style="cyan")
            table.add_column("Conf", style="green")
            table.add_column("Tasks", style="magenta")
            table.add_column("Entities", style="blue")
            table.add_column("Would Write", style="yellow")
            
            for r in results:
                table.add_row(
                    r["capture_id"][:8] + "...",
                    f"{r['confidence']:.2f}",
                    str(r["tasks_count"]),
                    str(r["entities_count"]),
                    str(r["would_modify"]),
                )
            
            console.print(table)
            console.print()
            console.print(f"[bold yellow]DRY-RUN Summary:[/bold yellow] {len(results)} item(s) would be distilled")
            console.print("[dim]Distill artifacts were created. Canon files were NOT modified.[/dim]")
            console.print("[dim]Run without --dry-run to apply changes.[/dim]")
        else:
            table = Table(title=f"Distillation Results — {date_str}")
            table.add_column("Capture", style="cyan")
            table.add_column("Write ID", style="yellow")
            table.add_column("Conf", style="green")
            table.add_column("Tasks", style="magenta")
            table.add_column("Entities", style="blue")
            
            for r in results:
                table.add_row(
                    r["capture_id"][:8] + "...",
                    r["write_id"][:8] + "...",
                    f"{r['confidence']:.2f}",
                    str(r["tasks_count"]),
                    str(r["entities_count"]),
                )
            
            console.print(table)
            console.print()
            console.print(f"[bold green]Summary:[/bold green] {len(results)} item(s) distilled")
            console.print("[dim]Use 'totem undo --write-id <ID>' to reverse any write[/dim]")
    else:
        console.print("[yellow]No items were successfully distilled[/yellow]")


@app.command()
def undo(
    vault_path: str = typer.Option(
        None,
        "--vault",
        "-v",
        help="Path to vault directory (default: TOTEM_VAULT_PATH env or ./totem_vault)",
    ),
    write_id: str = typer.Option(
        ...,
        "--write-id",
        "-w",
        help="Write ID (UUID) to undo",
    ),
):
    """Undo a canon write by removing inserted blocks.
    
    Reverses the append-only writes from a distillation operation.
    The write ID can be found in the distillation output or ledger.
    """
    # Load vault configuration
    if vault_path:
        config = TotemConfig(vault_path=Path(vault_path))
    else:
        config = TotemConfig.from_env()
    
    paths = VaultPaths.from_config(config)

    # Check if vault exists
    if not paths.system.exists():
        console.print(f"[red]Error: Vault not initialized at {config.vault_path}[/red]")
        console.print("[yellow]Run 'totem init' first[/yellow]")
        raise typer.Exit(code=1)

    # Initialize ledger writer
    ledger_writer = LedgerWriter(paths.ledger_file)

    try:
        console.print(f"[cyan]Undoing write:[/cyan] {write_id}")
        
        modified_files = undo_canon_write(
            write_id=write_id,
            vault_paths=paths,
            ledger_writer=ledger_writer,
        )
        
        console.print()
        console.print("[bold green]Undo successful![/bold green]")
        
        if modified_files:
            console.print("[dim]Modified files:[/dim]")
            for path in modified_files:
                console.print(f"  - {path}")
        else:
            console.print("[yellow]No files were modified[/yellow]")
        
        console.print()
        console.print("[dim]Note: entities.json changes require manual review[/dim]")
        
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[dim]Check the write ID is correct (from distill output or ledger)[/dim]")
        raise typer.Exit(code=1)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def version():
    """Show Totem OS version."""
    from . import __version__
    console.print(f"Totem OS v{__version__}")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
