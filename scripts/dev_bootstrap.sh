#!/bin/bash
# Totem OS Development Environment Bootstrap Script
# Creates .venv, installs dependencies, and verifies setup

set -e  # Exit on any error

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="$REPO_ROOT/.venv"

echo "[INFO] Totem OS Dev Bootstrap - Repository: $REPO_ROOT"

# Detect Python executable
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
    echo "[OK] Found python3: $(which python3)"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
    echo "[OK] Found python: $(which python)"
else
    echo "[ERR] No Python executable found. Please install Python 3.10+"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo "[INFO] Python version: $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_PATH" ]; then
    echo "[INFO] Creating virtual environment at $VENV_PATH"
    $PYTHON_CMD -m venv "$VENV_PATH"
    echo "[OK] Virtual environment created"
else
    echo "[OK] Virtual environment already exists at $VENV_PATH"
fi

# Activate virtual environment
echo "[INFO] Activating virtual environment"
source "$VENV_PATH/bin/activate"

# Verify activation
if [ "$VIRTUAL_ENV" != "$VENV_PATH" ]; then
    echo "[ERR] Failed to activate virtual environment"
    exit 1
fi
echo "[OK] Virtual environment activated"

# Upgrade pip, setuptools, wheel
echo "[INFO] Upgrading pip, setuptools, wheel"
python -m pip install --upgrade pip setuptools wheel
echo "[OK] pip, setuptools, wheel upgraded"

# Install Totem in editable mode
echo "[INFO] Installing Totem OS in editable mode"
cd "$REPO_ROOT"
python -m pip install -e .
echo "[OK] Totem OS installed (editable)"

# Verify installation
echo ""
echo "[INFO] Verification:"
echo "Python: $(which python)"
echo "Python version: $(python --version)"
echo "Totem: $(which totem)"
echo ""

# Test totem --help
echo "[INFO] Testing totem --help:"
if totem --help &> /dev/null; then
    echo "[OK] totem --help works"
else
    echo "[ERR] totem --help failed"
    exit 1
fi

# Test totem --version
echo "[INFO] Testing totem --version:"
if totem --version &> /dev/null; then
    echo "[OK] totem --version works"
else
    echo "[ERR] totem --version failed"
    exit 1
fi

echo ""
echo "[SUCCESS] Totem OS development environment is ready!"
echo ""
echo "Next steps:"
echo "  source .venv/bin/activate"
echo "  totem --help"
echo "  totem init  # to create a vault"
echo "  totem chatgpt doctor  # to test ChatGPT integration"