.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help deps run scan bundle image serve site

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

image: ## Build the Docker image that serves the static site
	docker build -t scry .

serve: image ## Serve the static site at http://127.0.0.1:8000 (Docker)
	docker run --rm -p 127.0.0.1:8000:80 scry

site: ## Write the static site to site/ with Docker (no local Python needed)
	docker build --target site --output site .
