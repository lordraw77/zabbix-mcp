# zabbix-mcp

A **Model Context Protocol (MCP) server** that exposes Zabbix monitoring
capabilities as callable tools for AI agents and MCP-compatible clients.

---

## Features

- 20 tools across 9 categories (hosts, problems/triggers, items/history,
  maintenance, host groups, events, graphs, templates, inventory, actions,
  users)
- Transport: **stdio** — JSON-RPC 2.0 framing managed by the `mcp` library
- Protocol: **MCP 2024-11-05**
- Auth: API token (Zabbix 5.4+) or user/password, loaded from `.env`
- Bundled interactive CLI agent using NVIDIA, OpenRouter or Groq as LLM
  provider

---

## Requirements

- Python 3.11+
- A Zabbix instance reachable from the server process (5.4+ recommended for
  token auth)
- An API key for at least one LLM provider (if using the agent)

---

## Installation

```bash
git clone <repo-url> /opt/zabbix-mcp
cd /opt/zabbix-mcp

# Option A — system Python (no venv)
pip install -r requirements.txt

# Option B — virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Option C — Docker
docker build -t zabbix-mcp:latest .
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Zabbix connection

| Variable | Required | Description |
|---|---|---|
| `ZABBIX_URL` | yes | Full base URL, e.g. `https://zabbix.example.com` |
| `ZABBIX_TOKEN` | one of† | API token — create at *Administration → Users → API tokens* |
| `ZABBIX_USER` | one of† | Username for user/password auth |
| `ZABBIX_PASSWORD` | one of† | Password for user/password auth |
| `ZABBIX_VERIFY_SSL` | no | `true` (default) / `false` to skip TLS verification |

† Token auth takes priority when both are present.

### LLM provider (agent only)

| Variable | Description |
|---|---|
| `ZABBIX_MCP_PROVIDER` | Active provider: `nvidia` (default), `openrouter`, `groq` |
| `ZABBIX_MCP_NVIDIA_API_KEY` | NVIDIA Build API key — [build.nvidia.com](https://build.nvidia.com) |
| `ZABBIX_MCP_NVIDIA_MODEL` | Model override (default: `meta/llama-3.3-70b-instruct`) |
| `ZABBIX_MCP_OPENROUTER_API_KEY` | OpenRouter API key |
| `ZABBIX_MCP_OPENROUTER_MODEL` | Model override (default: `openrouter/auto`) |
| `ZABBIX_MCP_GROQ_API_KEY` | Groq API key |
| `ZABBIX_MCP_GROQ_MODEL` | Model override (default: `llama-3.3-70b-versatile`) |

---

## Running the server

The server communicates over stdio and is normally spawned by an MCP client.
You can also start it manually for debugging:

```bash
python server.py
# or with venv
.venv/bin/python server.py
# or with Docker
docker run --rm -i --env-file .env zabbix-mcp:latest
```

See [DOCKER.md](DOCKER.md) for full Docker usage and Claude Desktop integration.

---

## Running the agent

```bash
python agent.py
```

```
Zabbix AI Agent  |  provider=nvidia  model=meta/llama-3.3-70b-instruct
Type your request, or 'exit' / Ctrl-C to quit.

You: Show me all active problems with severity High or above
  [tool] get_problems({"min_severity": 4})
...
```

---

## Available tools

### Hosts

| Tool | Description |
|---|---|
| `list_hosts` | List hosts with status, availability and primary IP. Optional filters: `groupid`, `status`, `limit`. |
| `get_host` | Full detail for one host: interfaces, groups, templates, macros. Accepts `hostid` or `hostname`. |
| `search_hosts` | Substring search across host name and visible name. Required: `query`. |

### Triggers / Problems

| Tool | Description |
|---|---|
| `get_problems` | Active (unresolved) problems, sorted by severity desc. Optional filters: `min_severity`, `hostid`, `groupid`, `limit`. |
| `get_triggers` | Triggers for a host, sorted by severity. Required: `hostid`. Optional: `only_problems`, `min_severity`. |

### Items / History

| Tool | Description |
|---|---|
| `get_items` | Monitoring items for a host with last collected value and unit. Required: `hostid`. Optional: `search`, `limit`. |
| `get_history` | Recent data points for one item. Required: `itemid`. Optional: `hours` (default 1), `limit` (default 100). |

### Maintenance

| Tool | Description |
|---|---|
| `get_maintenances` | List all maintenance windows with scope and active period. |
| `create_maintenance` | Create a one-time maintenance window. Required: `name`, `hostids`, `start_time` (ISO 8601), `duration_minutes`. |
| `delete_maintenance` | Delete a maintenance window by `maintenanceid`. |

### Host Groups

| Tool | Description |
|---|---|
| `get_host_groups` | List all host groups with IDs and names. Optional: `search`. Use `groupid` results to filter `list_hosts`, `get_problems`, etc. |

### Events

| Tool | Description |
|---|---|
| `get_events` | Historical events (problems + recoveries) for a time window, including hostname. Optional: `hours` (default 24), `hostid`, `groupid`, `min_severity`, `limit`. |
| `acknowledge_problem` | Acknowledge one or more events by `eventids`. Optional: `message`, `close`. |
| `get_top_hosts_by_problems` | Ranked list of hosts by problem count in a time window. Optional: `hours` (default 168), `min_severity`, `top_n` (default 10). |

### Graphs

| Tool | Description |
|---|---|
| `get_graphs` | Graphs defined for a host. Required: `hostid`. Optional: `search`. |
| `get_graph_items` | Items (metrics) that make up a graph. Required: `graphid`. |

### Templates

| Tool | Description |
|---|---|
| `get_templates` | List templates. Optional: `search`, `hostid` (filter by linked host). |

### Inventory

| Tool | Description |
|---|---|
| `get_host_inventory` | Full inventory record for a host (OS, hardware, location, serial numbers). Required: `hostid`. |

### Actions

| Tool | Description |
|---|---|
| `get_actions` | List alerting actions with status and event source. Optional: `search`. |

### Users / Groups

| Tool | Description |
|---|---|
| `get_users` | List users with username, display name and group membership. Optional: `search`. |
| `get_user_groups` | List user groups with GUI access level and status. Optional: `search`. |

#### Severity codes

| Code | Label |
|---|---|
| 0 | Not classified |
| 1 | Information |
| 2 | Warning |
| 3 | Average |
| 4 | High |
| 5 | Disaster |

---

## Project structure

```
zabbix-mcp/
├── server.py          — MCP server (10 tools, stdio transport)
├── util.py            — Zabbix code → human-readable string converters
├── agent.py           — Interactive CLI agent
├── llm.py             — LLM provider registry and agentic loop
├── requirements.txt   — Python dependencies
├── Dockerfile         — Container image for the MCP server
├── DOCKER.md          — Docker usage and Claude Desktop integration guide
├── .env.example       — Environment variable template
└── .gitignore
```

---

## How the agentic loop works

```
User question
      │
      ▼
  LLM (Reason)  ──── tool_calls? ──► MCP server (Act)
      ▲                                     │
      │                                     │ result
      └─────────── tool message ◄───────────┘
                  (Observe)
      │
  finish_reason = "stop"
      │
      ▼
  Final answer
```

The loop runs up to 20 iterations by default and handles unknown tools, JSON
decode errors, and asyncio timeouts gracefully.

---

## Integrating with Claude Desktop

Add this block to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zabbix": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--env-file", "/path/to/.env", "zabbix-mcp:latest"]
    }
  }
}
```

Or, without Docker:

```json
{
  "mcpServers": {
    "zabbix": {
      "command": "/opt/zabbix-mcp/.venv/bin/python",
      "args": ["/opt/zabbix-mcp/server.py"]
    }
  }
}
```

---

## License

MIT
