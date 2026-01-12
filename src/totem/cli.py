"""Typer-based CLI for Totem OS."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .capture import ingest_file_capture, ingest_text_capture
from .config import TotemConfig
from .ledger import LedgerWriter, read_ledger_tail
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
):
    """Route captures using deterministic keyword heuristics.
    
    Reads raw captures from 00_inbox/YYYY-MM-DD/, applies keyword-based routing,
    and writes outputs to either routed/ or review_queue/ based on confidence.
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
            # Process routing
            output_path, was_routed = process_capture_routing(
                raw_file_path=raw_file,
                meta_file_path=meta_file,
                vault_root=paths.root,
                config=config,
                ledger_writer=ledger_writer,
                date_str=date_str,
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
        table = Table(title=f"Routing Results for {date_str}")
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
def version():
    """Show Totem OS version."""
    from . import __version__
    console.print(f"Totem OS v{__version__}")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
