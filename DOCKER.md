# Zabbix MCP Server — Docker Guide

## Overview

The MCP server supports two transports that can be selected at runtime:

| Transport | How it works | When to use |
|---|---|---|
| **stdio** (default) | JSON-RPC 2.0 over stdin/stdout — container is spawned by the MCP client | Claude Desktop, local agents, any MCP host that manages its own subprocess |
| **HTTP/SSE** | Starlette + uvicorn HTTP server; clients connect to `GET /sse` and send requests to `POST /messages/` | Remote access, multi-client setups, containers deployed independently |

### stdio architecture

```
┌─────────────────────┐   stdin/stdout   ┌───────────────────────┐
│  MCP Client         │ ◄──────────────► │  zabbix-mcp container │
│  (agent, Claude, …) │   JSON-RPC 2.0   │  python server.py     │
└─────────────────────┘                  └──────────┬────────────┘
                                                     │ HTTPS
                                                     ▼
                                          ┌──────────────────────┐
                                          │  Zabbix API          │
                                          └──────────────────────┘
```

### HTTP/SSE architecture

```
┌─────────────────────┐  GET  /sse        ┌───────────────────────┐
│  MCP Client         │ ────────────────► │  zabbix-mcp container │
│  (agent, Claude, …) │  POST /messages/  │  python server.py     │
└─────────────────────┘ ◄──────────────► │  (uvicorn :8000)      │
                          SSE events       └──────────┬────────────┘
                                                      │ HTTPS
                                                      ▼
                                           ┌──────────────────────┐
                                           │  Zabbix API          │
                                           └──────────────────────┘
```

---

## Quick start

### 1 — Build the image

```bash
docker build -t zabbix-mcp:latest .
# or via Make
make build
```

### 2 — Prepare credentials

```bash
cp .env.example .env
$EDITOR .env
```

Minimum required variables:

```env
ZABBIX_URL=https://zabbix.example.com
ZABBIX_TOKEN=your_api_token_here    # or ZABBIX_USER + ZABBIX_PASSWORD
```

---

## Docker Compose

A `docker-compose.yml` is provided with two pre-configured services:

| Service | Transport | Command |
|---|---|---|
| `zabbix-mcp-stdio` | stdio | `docker compose run --rm zabbix-mcp-stdio` |
| `zabbix-mcp-sse` | HTTP/SSE on `:8000` | `docker compose up zabbix-mcp-sse` |

```bash
# Build the image
docker compose build

# Smoke-test stdio (interactive)
docker compose run --rm zabbix-mcp-stdio

# Start SSE server (detached, restarts on failure)
docker compose up -d zabbix-mcp-sse

# Follow logs
docker compose logs -f zabbix-mcp-sse

# Stop
docker compose down
```

Override the port without editing the file:

```bash
MCP_PORT=9000 docker compose up zabbix-mcp-sse
```

---

## Running in stdio mode (default)

```bash
# Interactive smoke-test
docker run --rm -i --env-file .env zabbix-mcp:latest

# via Make
make run
```

The server starts and waits for JSON-RPC input on stdin. Press `Ctrl-C` to stop.

---

## Running in HTTP/SSE mode

```bash
# Bind to localhost:8000
docker run --rm \
  --env-file .env \
  -e MCP_TRANSPORT=sse \
  -p 8000:8000 \
  zabbix-mcp:latest

# Custom port (e.g. 9000)
docker run --rm \
  --env-file .env \
  -e MCP_TRANSPORT=sse \
  -e MCP_PORT=9000 \
  -p 9000:9000 \
  zabbix-mcp:latest

# via Make (default PORT=8000, override with PORT=9000 make run-sse)
make run-sse
make run-sse PORT=9000
```

The server exposes:

| Endpoint | Method | Description |
|---|---|---|
| `/sse` | `GET` | SSE stream — MCP client connects here to receive events |
| `/messages/` | `POST` | MCP client sends JSON-RPC requests here |

---

## Using with an MCP client

### Claude Desktop — stdio mode (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "zabbix": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/.env",
        "zabbix-mcp:latest"
      ]
    }
  }
}
```

### Claude Desktop — SSE mode

First start the container:

```bash
docker run -d --name zabbix-mcp \
  --env-file /absolute/path/to/.env \
  -e MCP_TRANSPORT=sse \
  -p 8000:8000 \
  zabbix-mcp:latest
```

Then configure Claude Desktop:

```json
{
  "mcpServers": {
    "zabbix": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### Python agent (`agent.py` / `llm.py`) — stdio mode

```python
def build_mcp_server_params() -> StdioServerParameters:
    env_file = os.path.abspath(".env")
    return StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "--env-file", env_file, "zabbix-mcp:latest"],
        env={**os.environ},
    )
```

### Python agent — SSE mode

```python
from mcp.client.sse import sse_client

async with sse_client("http://localhost:8000/sse") as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        # call tools …
```

---

## Environment variables reference

### Zabbix connection

| Variable | Required | Default | Description |
|---|---|---|---|
| `ZABBIX_URL` | yes | — | Full base URL of the Zabbix frontend |
| `ZABBIX_TOKEN` | one of† | — | API token (Zabbix 5.4+, recommended) |
| `ZABBIX_USER` | one of† | — | Username for user/password auth |
| `ZABBIX_PASSWORD` | one of† | — | Password for user/password auth |
| `ZABBIX_VERIFY_SSL` | no | `true` | Set `false` to skip TLS verification |

### Transport

| Variable | Default | Description |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |
| `MCP_HOST` | `0.0.0.0` | Bind address (SSE mode only) |
| `MCP_PORT` | `8000` | TCP port (SSE mode only) |

† Either `ZABBIX_TOKEN` **or** the pair `ZABBIX_USER` + `ZABBIX_PASSWORD` is required.
If both are present, the token takes priority.

---

## Image details

| Property | Value |
|---|---|
| Base image | `python:3.12-slim` |
| Working dir | `/app` |
| Entrypoint | `python server.py` |
| Exposed port | `8000` (used in SSE mode; ignored in stdio mode) |
| Credentials | mounted at runtime via `--env-file` |

### Files included in the image

```
/app/server.py   — MCP server (30 tools, stdio + SSE transports)
/app/util.py     — Zabbix formatting utilities
```

`agent.py`, `llm.py` and `.env` are **not** included — the agent runs on the
host and connects to the container over stdio or HTTP.

---

## Building for a specific platform

```bash
# For ARM hosts (e.g. Raspberry Pi, Apple Silicon)
docker build --platform linux/arm64 -t zabbix-mcp:latest .

# Multi-platform manifest (requires buildx)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t lordraw/zabbix-mcp:latest \
  --push .
```

---

## Updating the image

```bash
# Rebuild after changing server.py or requirements.txt
docker build --no-cache -t zabbix-mcp:latest .
```

---

## Security notes

- **Never bake credentials into the image.** Always pass them at runtime via
  `--env-file` or `-e`.
- In SSE mode the HTTP server is **unauthenticated** — bind only to trusted
  networks or place a reverse proxy with TLS and authentication in front.
- If your Zabbix instance uses a self-signed certificate, set
  `ZABBIX_VERIFY_SSL=false` — or better, mount the CA bundle and set
  `REQUESTS_CA_BUNDLE=/certs/ca.pem`.
- Use a **read-only API token** with the minimum required permissions when
  possible. Write access is required only for maintenance and operational tools.
