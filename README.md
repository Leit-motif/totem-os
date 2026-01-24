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

## Development Setup

For development setup instructions, see [docs/dev_setup.md](docs/dev_setup.md).

Quick start:
```bash
./scripts/dev_bootstrap.sh
source .venv/bin/activate
totem --version
```
