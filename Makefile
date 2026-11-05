PYTHON ?= .venv/bin/python

.PHONY: install test lint check
install:
	python3 -m venv .venv
	.venv/bin/python -m pip install -e '.[dev]'
test:
	$(PYTHON) -m pytest
lint:
	$(PYTHON) -m ruff check src tests scripts
	$(PYTHON) -m ruff format --check src tests scripts
check: lint
	$(PYTHON) -m mypy
	$(MAKE) test
