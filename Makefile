.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help deps run scan bundle

help: ## Show this help
	@grep -E '^[a-z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

deps: ## Install the replay parser (sc2reader)
	$(PY) -m pip install --user -r requirements.txt

run: ## Scan this folder and open the page in a browser
	$(PY) scry.py --open

scan: ## Update the database only, then exit
	$(PY) scry.py --scan-only

bundle: ## Write the one-file build to dist/scry.py
	$(PY) scry.py --bundle dist/scry.py
