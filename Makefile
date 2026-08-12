.PHONY: test test-integration test-all typecheck lint format docs-check docker-build docker-build-source docker-run help clean

AFDS_VALIDATOR := scripts/vendor/afds_validate_b54fc6b2.py

help:
	@echo "Available targets:"
	@echo "  test              - Run unit tests"
	@echo "  test-integration  - Run integration tests (requires HA_URL + HA_TOKEN)"
	@echo "  test-all          - Run all test suites (unit, smoke, e2e, integration)"
	@echo "  typecheck         - Run strict mypy on server.py and tools/"
	@echo "  lint              - Run ruff linter"
	@echo "  format            - Format code with ruff"
	@echo "  docs-check        - Validate all Markdown under explicit AFDS governance"
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
	mypy server.py tools/ --strict

lint:
	ruff check .

format:
	ruff format .

docker-build:
	docker build -t ha-mcp-readonly:latest .

docker-build-run:
	docker compose -f docker-compose.build.yml up -d

docs-check:
	@set -eu; \
		docs="$$(find docs -type f -name '*.md' | sort)"; \
		python3 $(AFDS_VALIDATOR) README.md CHANGELOG.md AGENTS.md CONTRIBUTING.md SECURITY.md $$docs

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage coverage.xml
