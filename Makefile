IMAGE   := lordraw/zabbix-mcp
# Use the nearest git tag (e.g. v1.2.0); fall back to "dev" when outside a repo.
VERSION := $(shell git describe --tags --always 2>/dev/null || echo "dev")
PORT    ?= 8000

.PHONY: help build push run run-sse shell lint install test clean

help:          ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ── Docker ────────────────────────────────────────────────────────────────────

build:         ## Build the Docker image (tags: :VERSION and :latest)
	docker build \
		-t $(IMAGE):$(VERSION) \
		-t $(IMAGE):latest \
		.
	@echo "Built $(IMAGE):$(VERSION) and $(IMAGE):latest"

push:          ## Push :VERSION and :latest to Docker Hub (docker login required)
	docker push $(IMAGE):$(VERSION)
	docker push $(IMAGE):latest

run:           ## Run the MCP server over stdio (reads .env)
	docker run --rm -i --env-file .env $(IMAGE):$(VERSION)

run-sse:       ## Run the MCP server over HTTP/SSE on PORT (default 8000)
	docker run --rm \
		--env-file .env \
		-e MCP_TRANSPORT=sse \
		-e MCP_PORT=$(PORT) \
		-p $(PORT):$(PORT) \
		$(IMAGE):$(VERSION)

compose-sse:   ## Start the SSE server via docker compose (detached)
	MCP_PORT=$(PORT) docker compose up -d zabbix-mcp-sse

compose-down:  ## Stop all compose services
	docker compose down

shell:         ## Open a shell inside the image for debugging
	docker run --rm -it --env-file .env --entrypoint /bin/bash $(IMAGE):$(VERSION)

# ── Local dev ─────────────────────────────────────────────────────────────────

install:       ## Create .venv and install dependencies
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

lint:          ## Run ruff linter (install with: pip install ruff)
	.venv/bin/ruff check server.py util.py agent.py llm.py

test:          ## Smoke-test: start the server and check it responds to initialize
	@echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0"}}}' \
		| timeout 5 .venv/bin/python server.py 2>/dev/null | python3 -m json.tool \
		&& echo "Server OK" \
		|| echo "Server did not respond (check .env)"

clean:         ## Remove __pycache__, *.pyc and .venv
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -o -name "*.pyo" | xargs rm -f 2>/dev/null; true
	rm -rf .venv
