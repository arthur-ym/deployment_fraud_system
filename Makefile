
IMAGE := arthurym25/deployment-fraud-system
TAG ?= dev
PORT ?= 8000

.PHONY: lint test run-ci pull-image instantiate pull-and-instantiate build-image push-image

# CI: code quality and tests
lint:
	uv run ruff check .

test:
	uv run pytest

run-ci: lint test

# Docker: local image management
pull-image:
	docker pull $(IMAGE):$(TAG)

instantiate:
	docker run --rm -p $(PORT):8000 $(IMAGE):$(TAG)
	
pull-and-instantiate: pull-image instantiate
