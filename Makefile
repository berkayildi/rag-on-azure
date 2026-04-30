.DEFAULT_GOAL := help

.PHONY: help init fmt validate plan apply deploy up down outputs logs test lint precommit clean

BICEP_FILES := $(shell find infra -name '*.bicep' -type f 2>/dev/null)

help: ## Print available targets and descriptions
	@awk 'BEGIN {FS = ":.*## "; printf "Usage:\n  make <target>\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*## / {printf "  %-10s  %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: ## One-time auth setup (azd auth login + az login check)
	@echo "==> azd auth login"
	@azd auth login
	@echo "==> verifying az login"
	@az account show >/dev/null 2>&1 && echo "az: signed in" || { echo "==> az login required"; az login; }

fmt: ## Format every .bicep file in infra/
	@if [ -z "$(BICEP_FILES)" ]; then echo "no .bicep files found"; exit 0; fi
	@for f in $(BICEP_FILES); do \
		echo "==> az bicep format $$f"; \
		az bicep format --file $$f || exit $$?; \
	done

validate: ## Compile every .bicep file (build-as-lint)
	@if [ -z "$(BICEP_FILES)" ]; then echo "no .bicep files found"; exit 0; fi
	@for f in $(BICEP_FILES); do \
		echo "==> az bicep build $$f"; \
		az bicep build --file $$f --stdout >/dev/null || exit $$?; \
	done
	@echo "==> bicep ok"

plan: ## Preview changes via az deployment group what-if
	@RG=$$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null); \
	LOC=$$(azd env get-value AZURE_LOCATION 2>/dev/null); \
	ENV=$$(azd env get-value AZURE_ENV_NAME 2>/dev/null); \
	if [ -z "$$RG" ]; then echo "AZURE_RESOURCE_GROUP not set on azd env — run 'azd env new' first"; exit 1; fi; \
	echo "==> what-if against $$RG ($$LOC, env=$$ENV)"; \
	az deployment group what-if -g $$RG -f infra/main.bicep -p infra/main.parameters.json

apply: ## Provision infrastructure (azd provision, no service deploy)
	@ENV=$$(azd env get-value AZURE_ENV_NAME 2>/dev/null); \
	echo ""; echo "About to provision in $$ENV..."; \
	for i in 3 2 1; do printf " %s" $$i; sleep 1; done; echo ""; \
	azd provision --no-prompt

deploy: ## Deploy service image only — Day 5+
	@ENV=$$(azd env get-value AZURE_ENV_NAME 2>/dev/null); \
	echo ""; echo "About to deploy service to $$ENV..."; \
	for i in 3 2 1; do printf " %s" $$i; sleep 1; done; echo ""; \
	azd deploy --no-prompt

up: ## Provision + deploy combined — Day 5+
	@ENV=$$(azd env get-value AZURE_ENV_NAME 2>/dev/null); \
	echo ""; echo "About to up (provision + deploy) in $$ENV..."; \
	for i in 3 2 1; do printf " %s" $$i; sleep 1; done; echo ""; \
	azd up --no-prompt

down: ## Tear down everything (azd down --purge --force)
	@ENV=$$(azd env get-value AZURE_ENV_NAME 2>/dev/null); \
	echo ""; echo "About to TEAR DOWN $$ENV (purge + force)..."; \
	for i in 3 2 1; do printf " %s" $$i; sleep 1; done; echo ""; \
	azd down --purge --force --no-prompt

outputs: ## Print azd env values (FQDN, endpoints, etc.)
	@azd env get-values

logs: ## Tail Container App logs
	@RG=$$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null); \
	ENV=$$(azd env get-value AZURE_ENV_NAME 2>/dev/null); \
	if [ -z "$$RG" ] || [ -z "$$ENV" ]; then echo "azd env not initialised"; exit 1; fi; \
	APP=rag-$$ENV-ca; \
	echo "==> az containerapp logs show -g $$RG -n $$APP --follow"; \
	az containerapp logs show -g $$RG -n $$APP --follow

test: ## Run pytest (skips if no test files yet)
	@if ! find app/tests -type f \( -name 'test_*.py' -o -name '*_test.py' \) 2>/dev/null | grep -q .; then \
		echo "test: skipped — no source files yet"; exit 0; \
	fi; \
	pytest app/tests

lint: ## Run ruff + mypy (skips if no Python files yet)
	@if ! find app/src ingest/src -type f -name '*.py' 2>/dev/null | grep -q .; then \
		echo "lint: skipped — no source files yet"; exit 0; \
	fi; \
	ruff check app ingest && mypy --strict app ingest

precommit: ## Run pre-commit on all files
	pre-commit run --all-files

clean: ## Remove caches and build artefacts
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name build -o -name dist -o -name '*.egg-info' \) -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "==> cleaned"
