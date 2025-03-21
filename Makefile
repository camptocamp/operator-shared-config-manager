export DOCKER_BUILDKIT = 1

.PHONY: help
help: ## Display this help message
	@echo "Usage: make <target>"
	@echo
	@echo "Available targets:"
	@grep --extended-regexp --no-filename '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "	%-20s%s\n", $$1, $$2}'

.PHONY: build
build: ## Build the Docker image
	docker build --tag=camptocamp/sharedconfigmanager-operator docker
	docker tag camptocamp/sharedconfigmanager-operator ghcr.io/camptocamp/sharedconfigmanager-operator

.PHONY: build-test
build-test: ## Build the Docker image used to run the tests
	docker build --target=test --tag=camptocamp/sharedconfigmanager-operator-test docker

.PHONY: prospector
prospector: build-test ## Run the prospector checks
	docker run --rm camptocamp/sharedconfigmanager-operator-test prospector --output=pylint shared_config_manager_operator.py

.PHONY: prospector-fast
prospector-fast: ## Run the prospector checks without build the Docker image
	docker run --rm camptocamp/sharedconfigmanager-operator-test prospector --output=pylint shared_config_manager_operator.py

.PHONY: tests
tests: ## Run the tests
	pytest --verbose
