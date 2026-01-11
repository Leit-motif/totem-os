"""Typer-based CLI for Totem OS."""

import sys
from pathlib import Path

import typer
from rich.console import Console

from .config import TotemConfig
from .paths import VaultPaths

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
def version():
    """Show Totem OS version."""
    from . import __version__
    console.print(f"Totem OS v{__version__}")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
