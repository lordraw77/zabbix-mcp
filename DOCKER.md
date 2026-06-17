# Zabbix MCP Server — Docker Guide

## Overview

The MCP server runs as a **stdio process**: the container reads JSON-RPC 2.0
messages from `stdin` and writes responses to `stdout`. No network port is
exposed. The MCP client (Claude Desktop, an agent, or any other MCP host)
spawns the container and communicates over its standard streams.

```
┌─────────────────────┐   stdin/stdout   ┌───────────────────────┐
│  MCP Client         │ ◄──────────────► │  zabbix-mcp container │
│  (agent, Claude, …) │   JSON-RPC 2.0   │  python server.py     │
└─────────────────────┘                  └──────────┬────────────┘
                                                     │ HTTPS
                                                     ▼
                                          ┌──────────────────────┐
                                          │  Zabbix API          │
                                          │  (JSON-RPC endpoint) │
                                          └──────────────────────┘
```

---

## Quick start

### 1 — Build the image

```bash
docker build -t zabbix-mcp:latest .
```

### 2 — Prepare credentials

Copy `.env.example` to `.env` and fill in your Zabbix connection details.
Never commit `.env` to version control.

```bash
cp .env.example .env
$EDITOR .env
```

Minimum required variables:

```env
ZABBIX_URL=https://zabbix.example.com
ZABBIX_TOKEN=your_api_token_here    # or ZABBIX_USER + ZABBIX_PASSWORD
```

### 3 — Smoke-test (interactive stdin)

```bash
docker run --rm -i --env-file .env zabbix-mcp:latest
```

The server starts and waits for JSON-RPC input on stdin.
Press `Ctrl-C` to stop.

---

## Using with an MCP client

### Claude Desktop (`claude_desktop_config.json`)

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

Restart Claude Desktop after editing the config.

### Python agent (`agent.py` / `llm.py`)

Override `build_mcp_server_params()` in `llm.py` to spawn Docker instead of
the local venv:

```python
def build_mcp_server_params() -> StdioServerParameters:
    env_file = os.path.abspath(".env")
    return StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "--env-file", env_file, "zabbix-mcp:latest"],
        env={**os.environ},
    )
```

Or set the `ZABBIX_MCP_USE_DOCKER` variable and handle the switch in code.

---

## Environment variables reference

All variables are passed via `--env-file` or individual `-e` flags.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ZABBIX_URL` | yes | — | Full base URL of the Zabbix frontend |
| `ZABBIX_TOKEN` | one of† | — | API token (Zabbix 5.4+, recommended) |
| `ZABBIX_USER` | one of† | — | Username for user/password auth |
| `ZABBIX_PASSWORD` | one of† | — | Password for user/password auth |
| `ZABBIX_VERIFY_SSL` | no | `true` | Set `false` to skip TLS verification |

† Either `ZABBIX_TOKEN` **or** the pair `ZABBIX_USER` + `ZABBIX_PASSWORD` is required.
If both are present, the token takes priority.

---

## Image details

| Property | Value |
|---|---|
| Base image | `python:3.12-slim` |
| Working dir | `/app` |
| Entrypoint | `python server.py` |
| Exposed ports | none |
| Credentials | mounted at runtime via `--env-file` |

### Files included in the image

```
/app/server.py   — MCP server (10 tools)
/app/util.py     — Zabbix formatting utilities
```

`agent.py`, `llm.py` and `.env` are **not** included — the agent runs on the
host and connects to the container over stdio.

---

## Building for a specific platform

```bash
# For ARM hosts (e.g. Raspberry Pi, Apple Silicon)
docker build --platform linux/arm64 -t zabbix-mcp:latest .

# Multi-platform manifest (requires buildx)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t yourrepo/zabbix-mcp:latest \
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
- If your Zabbix instance uses a self-signed certificate, set
  `ZABBIX_VERIFY_SSL=false` — or better, mount the CA bundle and set
  `REQUESTS_CA_BUNDLE=/certs/ca.pem`.
- Use a **read-only API token** with the minimum required permissions when
  possible. The MCP server only needs read access for most tools; write access
  is required only for `create_maintenance` and `delete_maintenance`.
