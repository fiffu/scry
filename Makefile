.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help deps site dev image serve

help: ## Show this help
	@grep -E '^[a-z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

deps: ## Install sc2reader, which build.py packs into the site
	$(PY) -m pip install --user -r requirements.txt

site: ## Write the static site to site/ with Docker (no local Python needed)
	docker build --target site --output site .

dev: ## Write the static site to site/ and serve it at http://127.0.0.1:8000 (needs sc2reader)
	$(PY) build.py site
	$(PY) -m http.server 8000 --bind 127.0.0.1 -d site

image: ## Build the Docker image that serves the static site
	docker build -t scry .

serve: image ## Serve the site at http://127.0.0.1:8000 with Docker
	docker run --rm -p 127.0.0.1:8000:80 scry
