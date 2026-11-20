.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help deps run scan bundle site serve-site

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

site: ## Build the static site in site/ (Python runs in the browser, no server needed)
	$(PY) scry.py --site site

serve-site: ## Serve site/ on 127.0.0.1:8000 for a local test
	$(PY) -m http.server 8000 --bind 127.0.0.1 -d site
