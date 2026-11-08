PYTHON ?= python
PIP ?= pip
PYTEST ?= pytest

.PHONY: install install-dev lint typecheck test test-unit test-integration schema validate-contracts clean

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

lint:
	ruff check src tests

typecheck:
	mypy src

test:
	$(PYTEST) -q

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
