.PHONY: test test-integration test-all typecheck lint format docs-check docker-build docker-build-source docker-run help clean

help:
	@echo "Available targets:"
	@echo "  test              - Run unit tests"
	@echo "  test-integration  - Run integration tests (requires HA_URL + HA_TOKEN)"
	@echo "  test-all          - Run all test suites (unit, smoke, e2e, integration)"
	@echo "  typecheck         - Run mypy strict type checking on tools/"
	@echo "  lint              - Run ruff linter"
	@echo "  format            - Format code with ruff"
	@echo "  docs-check        - Validate documentation against AFDS standard"
	@echo "  docker-build      - Build Docker image"
	@echo "  docker-build-run  - Build from source and run"
	@echo "  clean             - Remove cache files"

test:
	pytest tests/unit/ -v --tb=short --cov=. --cov-report=term

test-integration:
	pytest tests/integration/ -v

test-all:
	pytest tests/unit/ tests/smoke/ tests/e2e/ tests/integration/ -q

typecheck:
	mypy tools/ --strict

lint:
	ruff check .

format:
	ruff format .

docker-build:
	docker build -t ha-mcp-readonly:latest .

docker-build-run:
	docker compose -f docker-compose.build.yml up -d

docs-check:
	python3 /var/apps/ai-skills/skills/afds-doc-writer/docs_validate.py --config afds_config.yaml docs/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml
