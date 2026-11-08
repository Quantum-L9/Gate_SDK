# Gate_SDK repository-owned Make implementation (Repo.mk).
#
# REPOSITORY-OWNED: hand-maintained, never generated. The root Makefile is the
# l9-ci-core Repository Execution V2 facade (make-v1, .l9/repo-workflow.json),
# reproduced byte-for-byte. It includes this file and routes the portable
# phases setup / validate / check / test to the repo-* leaves at the end.
#
# Every target the root Makefile owned before the facade (issue #72) lives here
# unchanged, except `test`: that name is now the facade verb and routes to
# repo-test. The facade also owns `help`, `setup`, `validate` and `check`, so
# this file must never define those names.

PYTHON ?= python
PIP ?= pip
PYTEST ?= pytest

.PHONY: install install-dev lint typecheck test-unit test-integration schema validate-contracts clean

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

lint:
	ruff check src tests

typecheck:
	mypy src

test-unit:
	$(PYTEST) -q tests/transport tests/security tests/gate tests/runtime tests/orchestrator

test-integration:
	$(PYTEST) -q tests/integration

schema:
	$(PYTHON) scripts/generate_schema.py

validate-contracts:
	$(PYTHON) scripts/validate_contracts.py

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info

# The hash-locked install CI runs (issue #53). Regenerate with `make lock`.
.PHONY: install-ci lock validate-locks

install-ci:
	$(PIP) install --require-hashes --only-binary :all: -r requirements/build.lock
	$(PIP) install --require-hashes --only-binary :all: -r requirements/dev.lock
	$(PIP) install --no-index --no-deps --no-build-isolation -e .
	$(PIP) check

lock:
	$(PYTHON) scripts/lock_requirements.py

validate-locks:
	$(PYTHON) scripts/validate_locks.py

# ---------------------------------------------------------------------------
# Repository Execution V2 leaves (make-v1 facade phases)
#
# Each leaf mirrors a ci.yml gate and runs every tool through the repository
# virtualenv interpreter, so the phase result never depends on PATH.
#   setup    -> hash-locked install, same commands as every ci.yml gate
#   validate -> gate 2 (contracts, locks, release identity)
#   check    -> gate 1 (ruff lint, ruff format check, mypy)
#   test     -> gate 4 (pytest)
# ---------------------------------------------------------------------------
REPO_VENV := $(CURDIR)/.venv
REPO_PYTHON := $(REPO_VENV)/bin/python

.PHONY: repo-setup repo-validate repo-check repo-test

repo-setup: ## Create .venv and install the hash-locked dev toolchain (ci.yml install step)
	test -x "$(REPO_PYTHON)" || python3 -m venv "$(REPO_VENV)"
	$(MAKE) --no-print-directory install-ci PIP="$(REPO_PYTHON) -m pip"

repo-validate: ## ci.yml gate 2: contracts, dependency locks, release identity
	"$(REPO_PYTHON)" scripts/validate_contracts.py
	"$(REPO_PYTHON)" scripts/validate_locks.py
	"$(REPO_PYTHON)" scripts/validate_release_identity.py

repo-check: ## ci.yml gate 1: ruff lint, ruff format check, mypy
	"$(REPO_PYTHON)" -m ruff check src tests scripts
	"$(REPO_PYTHON)" -m ruff format --check src tests scripts
	"$(REPO_PYTHON)" -m mypy src

repo-test: ## ci.yml gate 4: pytest
	"$(REPO_PYTHON)" -m pytest -q --tb=short
