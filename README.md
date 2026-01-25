# Totem OS

Totem OS is a local-first personal cognitive operating system.

Its purpose is to:
- absorb daily cognitive entropy,
- distill it into small, actionable structure,
- and maintain a curated, inspectable memory over time.

This project prioritizes:
- trust mechanisms over cleverness,
- restartability over completeness,
- and human agency over automation.

## v0.1 Scope
The initial version focuses exclusively on backend functionality:
- raw capture ingestion (append-only),
- deterministic distillation into daily logs and next actions,
- confidence-gated memory updates,
- full auditability via an append-only ledger.

No frontend, embeddings, or automation beyond this core loop are included in v0.1.

## Architecture Overview

Totem OS is a CLI-first, file-backed pipeline. Everything is stored in a local vault
directory and all actions are append-only or reversible.

High-level flow:
1. Capture raw inputs into `00_inbox/YYYY-MM-DD/` with `.meta.json` sidecars.
2. Route captures into structured JSON under `10_derived/routed/`.
3. Distill routed items into structured summaries/tasks and append blocks into canon
   files (`20_memory/daily/*.md`, `30_tasks/todo.md`), with undo markers.
4. Review proposals in an explicit approve/veto/correct loop (no silent writes).
5. Ledger every event into `90_system/ledger.jsonl` (append-only audit trail).

Key components:
- **CLI entrypoint**: `src/totem/cli.py`
- **Config + paths**: `src/totem/config.py`, `src/totem/paths.py`
- **Capture**: `src/totem/capture.py`
- **Routing**: `src/totem/route.py`, `src/totem/llm/router.py`
- **Distill**: `src/totem/distill.py`, `src/totem/llm/client.py`
- **Review queue**: `src/totem/review.py`
- **Ledger**: `src/totem/ledger.py`
- **Integrations**: ChatGPT export (`src/totem/chatgpt/*`), Omi sync (`src/totem/omi/*`)

## Command Map (Current CLI)

Run `totem --help` for the full option list. Dates default to **today (UTC)** unless
otherwise specified.

Top-level commands:
- `totem link-vault <path>`: link an existing vault to this repo (`.totem/config.toml`).
- `totem init [--force]`: create the vault structure and system files (idempotent).
- `totem capture --text "..." [--date YYYY-MM-DD]`: capture raw text into inbox.
- `totem capture --file /path/to/file [--date YYYY-MM-DD]`: capture a file into inbox.
- `totem route [--date YYYY-MM-DD] [--engine rule|llm|hybrid|auto] [--limit N]`: classify captures.
- `totem distill [--date YYYY-MM-DD] [--engine fake|openai|anthropic|auto] [--limit N]`: distill routed items.
- `totem undo --write-id <UUID>`: reverse a distillation write by block marker.
- `totem review [--date YYYY-MM-DD] [--limit N] [--dry-run]`: interactive approve/veto/correct loop.
- `totem intent --text "..."`: run the intent arbiter on a single input.
- `totem version` (or `totem --version`): print version.

Ledger subcommands:
- `totem ledger tail [--n N] [--full]`: show recent ledger events.

Omi subcommands:
- `totem omi sync [--date YYYY-MM-DD | --all] [--no-write-daily-note]`: sync Omi transcripts.

ChatGPT export subcommands:
- `totem chatgpt ingest-latest-export [--dry-run] [--debug]`: ingest newest Gmail export.
- `totem chatgpt ingest-from-zip /path/to/export.zip`: ingest a local export zip.
- `totem chatgpt ingest-from-downloads [--downloads-dir PATH] [--limit N]`: scan Downloads for export zips.
- `totem chatgpt backfill-metadata [--limit N] [--dry-run]`: add metadata to existing notes.
- `totem chatgpt doctor`: run diagnostics for ChatGPT ingestion setup.
- `totem chatgpt install-launchd`: install macOS LaunchAgent for scheduled ingestion.

## Development Setup

For development setup instructions, see [docs/dev_setup.md](docs/dev_setup.md).

Quick start:
```bash
./scripts/dev_bootstrap.sh
source .venv/bin/activate
totem --version
```
