# Execute targets with `make <target> [env]`  e.g. `make bundle-deploy prod`
# Run `make help` to list all available targets.

.PHONY: help

help: ## Show this help message
	@echo ""
	@echo "Usage: make <target> [env]   (env = dev | uat | prod)"
	@echo ""
	@echo "Bundle"
	@echo "------"
	@grep -E '^bundle-[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-28s %s\n", $$1, $$2}'
	@echo ""
	@echo "Source connectivity"
	@echo "-------------------"
	@grep -E '^check-[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-28s %s\n", $$1, $$2}'
	@echo ""
	@echo "Development"
	@echo "-----------"
	@grep -E '^(build|clean|install|lint|test)[a-zA-Z_-]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-28s %s\n", $$1, $$2}'
	@echo ""
	@echo "Release"
	@echo "-------"
	@grep -E '^(publish|release|serve)[a-zA-Z_-]*:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-28s %s\n", $$1, $$2}'
	@echo ""

check-sources: ## Ping all sources in sources.yml — requires env  e.g. make check-sources uat
	$(eval TARGET ?= $(word 2,$(MAKECMDGOALS)))
	@test -n "$(TARGET)" || (echo "Error: target env is required. Example: make check-sources uat"; exit 1)
	DATABRICKS_CONFIG_PROFILE=$(TARGET) python -m adm check

bundle-validate: ## Validate bundle config for target env  e.g. make bundle-validate prod
	$(eval TARGET ?= $(word 2,$(MAKECMDGOALS)))
	@test -n "$(TARGET)" || (echo "Error: target env is required. Example: make bundle-validate uat"; exit 1)
	databricks bundle validate -t $(TARGET)

generate-jobs: ## Generate Databricks jobs from sources.yml (sources with deploy_job: true)
	python scripts/generate_source_jobs.py

bundle-deploy: generate-jobs ## Build wheel + deploy jobs to target env  e.g. make bundle-deploy prod
	$(eval TARGET ?= $(word 2,$(MAKECMDGOALS)))
	@test -n "$(TARGET)" || (echo "Error: target env is required. Example: make bundle-deploy uat"; exit 1)
	databricks bundle deploy -t $(TARGET)

bundle-destroy: ## Destroy bundle-managed jobs for target env (Model Serving is NOT affected)
	$(eval TARGET ?= $(word 2,$(MAKECMDGOALS)))
	@test -n "$(TARGET)" || (echo "Error: target env is required. Example: make bundle-destroy prod"; exit 1)
	@echo "Destroying bundle resources for target: $(TARGET)"
	@echo "This will delete jobs deployed by this bundle. Model Serving endpoints are NOT affected."
	databricks bundle destroy -t $(TARGET)

dev uat prod:
	@true

build: ## Build the Python wheel
	bash run.sh build

clean: ## Remove build artifacts
	bash run.sh clean

install: ## Install dev dependencies into the active Python environment
	bash run.sh install

lint: ## Run pre-commit linting checks (black, isort, flake8, pylint, mypy)
	bash run.sh lint

lint-ci: ## Run linting in CI mode (no auto-fix)
	bash run.sh lint:ci

publish-prod: ## Publish wheel to production PyPI
	bash run.sh publish:prod

publish-test: ## Publish wheel to test PyPI
	bash run.sh publish:test

release-prod: ## Cut a production release
	bash run.sh release:prod

release-test: ## Cut a test release
	bash run.sh release:test

serve-coverage-report: ## Serve the HTML coverage report locally
	bash run.sh serve-coverage-report

test-ci: ## Run tests in CI mode
	bash run.sh test:ci

test-quick: ## Run tests skipping slow markers
	bash run.sh test:quick

test: ## Run all unit tests
	bash run.sh run-tests

test-wheel-locally: ## Build and install the wheel locally, then run tests against it
	bash run.sh test:wheel-locally
