#!/usr/bin/env bash
set -euo pipefail

python -m pip install -e ".[dev]"
ruff check src tests
mypy src
pytest -q
python scripts/validate_contracts.py

python -m build
echo "release artifacts built successfully"
