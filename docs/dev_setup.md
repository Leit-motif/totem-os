# Totem OS Development Setup

This guide helps you set up a clean, reproducible development environment for Totem OS.

## Prerequisites

- macOS (for launchd support)
- Python 3.10+ (either `python3` or `python` command)

## Quick Setup

1. **Clone and enter the repository:**
   ```bash
   cd "/Users/amrit/Workspaces/Totem OS"
   ```

2. **Run the bootstrap script:**
   ```bash
   ./scripts/dev_bootstrap.sh
   ```

3. **Activate the virtual environment:**
   ```bash
   source .venv/bin/activate
   ```

4. **Verify installation:**
   ```bash
   which totem
   # Should show: /Users/amrit/Workspaces/Totem OS/.venv/bin/totem

   totem --version
   # Should show: Totem OS v0.1.0

   totem --help
   # Should show the full command help

   totem chatgpt doctor
   # Should work (all runtime dependencies are installed)
   ```

## Manual Setup (Alternative)

If you prefer step-by-step control:

1. **Create virtual environment:**
   ```bash
   make venv
   # or: python3 -m venv .venv
   ```

2. **Activate and install:**
   ```bash
   source .venv/bin/activate
   python -m pip install --upgrade pip setuptools wheel
   python -m pip install -e .
   # Note: This installs ALL runtime dependencies including requests and Google API packages
   ```

## Available Commands

Once set up, you can use:

```bash
# Show version
totem --version

# Show help
totem --help

# Initialize a new vault
totem init

# Test ChatGPT integration
totem chatgpt doctor

# Run ChatGPT ingestion (dry-run)
totem chatgpt ingest-latest-export --dry-run
```

## Makefile Targets

The repository includes a `Makefile` for common development tasks:

```bash
make venv              # Create virtual environment
make install           # Install Totem OS in editable mode
make test              # Run the test suite
make doctor-chatgpt    # Run ChatGPT integration diagnostics
make ingest-chatgpt-dry # Run ChatGPT ingestion in dry-run mode
make dev-setup         # Run complete development setup
make clean             # Remove virtual environment and cache files
```

## Example: Initialize a Test Vault

```bash
# Create a test vault
totem init --vault test_vault

# Navigate into it
cd test_vault

# Run commands from anywhere inside the vault
totem ledger tail --n 1

# Or from subdirectories
cd 90_system
totem ledger tail --n 1
```

## Troubleshooting

### Command not found: pip
The bootstrap script uses `python -m pip` instead of the `pip` command directly, so this shouldn't be an issue.

### Virtual environment not activating
Make sure you're using the correct shell and that `.venv/bin/activate` exists.

### totem command not found after activation
Run `which totem` to verify it's pointing to `.venv/bin/totem`. If not, try reinstalling:
```bash
source .venv/bin/activate
python -m pip install -e .
```

### Tests failing
Run `make test` to execute the full test suite, or:
```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## ChatGPT Integration Setup

For the ChatGPT features to work:

1. **Google API Credentials:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create OAuth 2.0 credentials for Gmail API
   - Save as `~/.config/totem/gmail_credentials.json`

2. **Obsidian Vault:**
   - Create or use an existing Obsidian vault
   - Update `obsidian_chatgpt_dir` and `obsidian_daily_dir` in your vault's `config.yaml`

3. **Test Integration:**
   ```bash
   totem chatgpt doctor
   ```

## Development Workflow

1. **Activate environment:** `source .venv/bin/activate`
2. **Make changes** to source code in `src/totem/`
3. **Run tests:** `make test`
4. **Test CLI:** `totem --help`
5. **Commit changes**

The editable install (`-e`) means changes are reflected immediately without reinstalling.