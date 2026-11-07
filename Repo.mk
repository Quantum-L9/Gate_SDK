# Gate_SDK repository-owned Make implementation (Repository Execution V2).
#
# REPOSITORY-OWNED: hand-maintained, never generated. The root Makefile is the
# generated l9-ci-core make-v1 facade (.l9/repo-workflow.json): it includes
# this file and routes setup / validate / check / test to the repo-* leaves
# below. Do not edit the Makefile; regenerate it from l9-ci-core
# (`python3 -m tools.l9_repo --workspace <this repo> reconcile`).
#
# Organization CI (Analyze (central Core)) runs `make setup validate check
# test` on a depth-1 checkout with the runner's system python3, which refuses
# `pip install` (PEP 668). repo-setup therefore provisions an isolated .venv,
# and every leaf runs tools through that interpreter (`-m <tool>`), never a
# bare name resolved from PATH.

VENV ?= .venv
BOOTSTRAP_PYTHON ?= python3
VENV_PYTHON := $(VENV)/bin/python

# Repository-local targets kept from the pre-V2 Makefile, unchanged.
PYTHON ?= python
PIP ?= pip
PYTEST ?= pytest

.PHONY: repo-setup repo-validate repo-check repo-test

repo-setup: ## Create .venv and install the project with its dev toolchain
	$(BOOTSTRAP_PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e ".[dev]"

repo-validate: ## Validate contracts and release identity (read-only)
	$(VENV_PYTHON) scripts/validate_contracts.py
	$(VENV_PYTHON) scripts/validate_release_identity.py

repo-check: ## ruff check, ruff format --check, mypy (CI gate 1)
	$(VENV_PYTHON) -m ruff check src tests scripts
	$(VENV_PYTHON) -m ruff format --check src tests scripts
	$(VENV_PYTHON) -m mypy src

repo-test: ## The full pytest suite (CI gate 4)
	$(VENV_PYTHON) -m pytest -q --tb=short

.PHONY: install install-dev lint typecheck test-unit test-integration schema validate-contracts clean

install: ## pip install -e .
	$(PIP) install -e .

install-dev: ## pip install -e ".[dev]"
	$(PIP) install -e ".[dev]"

lint: ## ruff check src tests
	ruff check src tests

typecheck: ## mypy src
	mypy src

test-unit: ## Unit-test subset
	$(PYTEST) -q tests/transport tests/security tests/gate tests/runtime tests/orchestrator

test-integration: ## Integration-test subset
	$(PYTEST) -q tests/integration

schema: ## Regenerate contracts/transport-packet.schema.json
	$(PYTHON) scripts/generate_schema.py

validate-contracts: ## Validate contracts
	$(PYTHON) scripts/validate_contracts.py

clean: ## Remove caches and build outputs
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
