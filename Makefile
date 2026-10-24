.PHONY: run-lint run-format run-test run-ci

run-lint:
	uv run ruff check src tests

run-format:
	uv run ruff format --check src tests

run-test:
	uv run pytest tests

run-ci: run-lint run-format run-test
