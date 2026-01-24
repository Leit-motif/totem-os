# Totem OS Development Makefile

.PHONY: help venv install test doctor-chatgpt ingest-chatgpt-dry clean dev-setup

# Default target
help:
	@echo "Totem OS Development Commands:"
	@echo "  make venv           - Create Python virtual environment"
	@echo "  make install        - Install Totem OS in editable mode"
	@echo "  make test           - Run test suite"
	@echo "  make doctor-chatgpt - Run ChatGPT integration diagnostics"
	@echo "  make ingest-chatgpt-dry - Run ChatGPT ingestion in dry-run mode"
	@echo "  make dev-setup      - Run complete development setup"
	@echo "  make clean          - Remove virtual environment and cache files"

# Create virtual environment
venv:
	@echo "[INFO] Creating virtual environment..."
	@if command -v python3 >/dev/null 2>&1; then \
		python3 -m venv .venv; \
	elif command -v python >/dev/null 2>&1; then \
		python -m venv .venv; \
	else \
		echo "[ERR] No Python executable found"; \
		exit 1; \
	fi
	@echo "[OK] Virtual environment created at .venv"

# Install in editable mode
install: venv
	@echo "[INFO] Installing Totem OS in editable mode..."
	@source .venv/bin/activate && python -m pip install --upgrade pip setuptools wheel
	@source .venv/bin/activate && python -m pip install -e .
	@echo "[OK] Totem OS installed (editable)"

# Run tests
test: install
	@echo "[INFO] Running test suite..."
	@source .venv/bin/activate && python -m pytest tests/ -v

# Run ChatGPT doctor
doctor-chatgpt: install
	@echo "[INFO] Running ChatGPT integration diagnostics..."
	@source .venv/bin/activate && totem chatgpt doctor

# Run ChatGPT ingestion dry-run
ingest-chatgpt-dry: install
	@echo "[INFO] Running ChatGPT ingestion in dry-run mode..."
	@source .venv/bin/activate && totem chatgpt ingest-latest-export --dry-run

# Complete development setup
dev-setup:
	@echo "[INFO] Running complete development setup..."
	@./scripts/dev_bootstrap.sh

# Clean up
clean:
	@echo "[INFO] Cleaning up..."
	@rm -rf .venv
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "[OK] Cleanup complete"