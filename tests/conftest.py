"""Pytest fixtures for Totem OS tests."""

import tempfile
from pathlib import Path

import pytest

from totem.config import TotemConfig
from totem.paths import VaultPaths


@pytest.fixture
def temp_vault(tmp_path):
    """Create a temporary vault for testing.
    
    Args:
        tmp_path: pytest's built-in temporary directory fixture
        
    Returns:
        Path to temporary vault root
    """
    vault_root = tmp_path / "test_vault"
    vault_root.mkdir()
    return vault_root


@pytest.fixture
def vault_config(temp_vault):
    """Create TotemConfig pointing to temporary vault.
    
    Args:
        temp_vault: Temporary vault root path
        
    Returns:
        TotemConfig instance
    """
    return TotemConfig(vault_path=temp_vault)


@pytest.fixture
def vault_paths(vault_config):
    """Create VaultPaths for temporary vault.
    
    Args:
        vault_config: TotemConfig instance
        
    Returns:
        VaultPaths instance
    """
    paths = VaultPaths.from_config(vault_config)
    
    # Create necessary directories
    for directory in paths.get_all_directories():
        directory.mkdir(parents=True, exist_ok=True)
    
    # Create empty ledger file
    paths.ledger_file.touch()
    
    return paths
