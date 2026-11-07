
IMAGE := arthurym25/deployment-fraud-system
TAG ?= dev
PORT ?= 8000

.PHONY: lint test run-ci lint-cloudformation pull-image instantiate pull-and-instantiate build-image push-image

# CI: code quality and tests
lint:
	uv run ruff check src tests

test:
	uv run pytest

run-ci: lint test

lint-cloudformation:
	uv run cfn-lint infra/fraud-api.yaml

# Docker: local image management
pull-image:
	docker pull $(IMAGE):$(TAG)

instantiate:
	docker run --rm -p $(PORT):8000 $(IMAGE):$(TAG)
	
pull-and-instantiate: pull-image instantiate
