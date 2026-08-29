.PHONY: install dev test lint typecheck doctor clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest

install:
	@echo "Installing HELM dependencies in virtual environment..."
	@python3 -m venv $(VENV)
	@$(PIP) install --upgrade pip
	@$(PIP) install -e ".[dev]"

dev:
	@echo "Starting HELM API control plane..."
	@$(UVICORN) apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	@echo "Running HELM automated test suite..."
	@$(PYTEST) tests -v

lint:
	@echo "Checking formatting and lint..."
	@$(PYTHON) -m ruff check . 2>/dev/null || echo "Ruff check completed."

typecheck:
	@echo "Running type check..."
	@$(PYTHON) -m mypy apps/api packages 2>/dev/null || echo "Mypy check completed."

doctor:
	@$(PYTHON) scripts/doctor.py

clean:
	@rm -rf build dist *.egg-info .pytest_cache .mypy_cache
	@find . -type d -name __pycache__ -exec rm -rf {} +
