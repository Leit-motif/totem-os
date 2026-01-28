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
- `totem ingest --source omi --full-history`: full-history Omi ingest (records manifest).
- `totem ingest --source chatgpt --full-history`: full-history ChatGPT ingest (records manifest).
- `totem ingest --all --full-history`: full-history ingest for both sources.
- `totem ingest-report`: print a concise manifest summary.
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
- `totem omi sync [--date YYYY-MM-DD | --all] [--no-write-daily-note] [--include-action-items]`: sync Omi transcripts (action items are opt-in).

ChatGPT export subcommands (local ZIP only; Gmail flow removed):
- `totem chatgpt ingest-from-zip /path/to/export.zip`: ingest a local export zip.
- `totem chatgpt ingest-from-downloads [--downloads-dir PATH] [--limit N]`: scan Downloads for export zips.
- `totem chatgpt backfill-metadata [--limit N] [--dry-run]`: add metadata to existing notes.

## Full-History Ingest + Manifest

The canonical ingestion manifest is stored at:
`90_system/ingest_manifest.jsonl`

Each ingestion run appends a record with:
- source (`omi` or `chatgpt`)
- run_id
- ingestion window (start/end)
- counts (discovered / ingested / skipped / errored)
- last_successful_item_timestamp
- error summary
- app/version metadata

### Run full-history ingestion
```
totem ingest --source omi --full-history
totem ingest --source chatgpt --full-history
totem ingest --all --full-history
```
Use `--include-action-items` to opt into Omi action items in the daily note block.

For ChatGPT, full-history ingestion reads the most recent local export ZIP from
`~/Downloads` (use `totem chatgpt ingest-from-zip` for an explicit file).

### Report status
```
totem ingest-report
```

### Resumability
- Omi full-history runs resume from the last successful timestamp in the manifest.
- ChatGPT runs use local ZIP ingestion from Downloads; idempotency is ensured by content hashes and stable filenames.

### ChatGPT Vault Routing
ChatGPT conversations are routed into daemon vs tooling vaults at ingest time using deterministic heuristics.
Configure the vault roots and routing thresholds in your vault config:
```
obsidian:
  vaults:
    daemon_path: "/path/to/Daemon"
    tooling_path: "/path/to/Tooling"

chatgpt_export:
  obsidian_chatgpt_dir: "40_chatgpt/conversations"
  tooling_chatgpt_dir: "ChatGPT/Tooling"
  routing:
    code_fence_min: 2
    code_ratio_min: 0.25
    keywords_any: ["python", "docker", "traceback"]
    enable_stacktrace_detection: true
```

### Idempotency validation
Run ingestion twice and compare outputs:
```
pytest tests/test_ingest_manifest.py -k "resume"
```

## Development Setup

For development setup instructions, see [docs/dev_setup.md](docs/dev_setup.md).

Quick start:
```bash
./scripts/dev_bootstrap.sh
source .venv/bin/activate
totem --version
```
