Param()
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Ensure Python finds our src package
$env:PYTHONPATH = "apps/api/src"

# Run placeholder API entrypoint
python -m api.main


