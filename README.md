# Totem OS Monorepo

Monorepo scaffold for Totem OS.

## Structure
- `apps/api` – Python (LangGraph) services
- `apps/web` – Next.js PWA (foundation later)
- `packages/shared` – Shared TS types/utils (later)
- `packages/config` – Shared configs (later)

## Development
- Node workspaces: pnpm
- Python: use system Python 3.10+; scripts set `PYTHONPATH`

### Commands
- `pnpm dev:web` – starts web (once created)
- `pnpm dev:api` – runs placeholder API

### Windows notes
If PowerShell execution is restricted, run once in the current session:
```
Set-ExecutionPolicy -Scope Process Bypass -Force
```

## Next steps
- Add linting/formatting and pre-commit hooks
- Add Dockerfiles
- Configure CI/CD workflows
