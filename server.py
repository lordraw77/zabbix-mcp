"""
server.py — Zabbix MCP Server
==============================
Implements a Model Context Protocol (MCP) server that exposes Zabbix
monitoring capabilities as callable tools.  An AI agent (or any MCP client)
can connect either over stdio or over HTTP with Server-Sent Events (SSE).

Transports
----------
  stdio (default)
    The process reads JSON-RPC 2.0 from stdin and writes to stdout.
    Spawn with: python server.py
    or:         python server.py --transport stdio

  HTTP/SSE
    An HTTP server (Starlette + uvicorn) listens on a TCP port.
    Clients connect to GET /sse to receive server events, and POST to
    /messages/ to send requests.
    Spawn with: python server.py --transport sse [--host 0.0.0.0] [--port 8000]
    or via env: MCP_TRANSPORT=sse MCP_HOST=0.0.0.0 MCP_PORT=8000 python server.py

Protocol   : MCP 2024-11-05
Auth       : credentials loaded from .env via python-dotenv
API backend: pyzabbix (thin Python wrapper over the Zabbix JSON-RPC API)

Tool categories (30 total)
---------------------------
  Hosts — read-only (3)
    list_hosts, get_host, search_hosts

  Triggers / Problems — read-only (2)
    get_problems, get_triggers

  Items / History — read-only (2)
    get_items, get_history

  Maintenance (4)
    get_maintenances, create_maintenance, update_maintenance, delete_maintenance

  Host Groups — read-only (1)
    get_host_groups

  Events (3)
    get_events, acknowledge_problem, get_top_hosts_by_problems

  Graphs — read-only (2)
    get_graphs, get_graph_items

  Templates — read-only (1)
    get_templates

  Inventory — read-only (1)
    get_host_inventory

  Actions — read-only (1)
    get_actions

  Users / Groups — read-only (2)
    get_users, get_user_groups

  Analytics — read-only (4)
    get_flapping_triggers, get_availability_report,
    get_trends, get_problem_duration_stats

  Operational — write (4)
    enable_host, disable_host,
    add_host_to_group, remove_host_from_group

Authentication
--------------
  Two modes are supported — token auth takes priority if both sets of
  credentials are present.

  API token (recommended, Zabbix 5.4+):
    ZABBIX_TOKEN               — API token created in Zabbix UI

  User / password (legacy):
    ZABBIX_USER                — Zabbix username
    ZABBIX_PASSWORD            — Zabbix password

Environment variables (via .env)
---------------------------------
  ZABBIX_URL                   — full base URL, e.g. https://zabbix.example.com
  ZABBIX_TOKEN                 — API token (token auth, takes priority)
  ZABBIX_USER                  — username (password auth)
  ZABBIX_PASSWORD              — password (password auth)
  ZABBIX_VERIFY_SSL            — true/false, whether to verify TLS (default true)

  MCP_TRANSPORT                — stdio (default) | sse
  MCP_HOST                     — bind address for SSE mode (default 0.0.0.0)
  MCP_PORT                     — TCP port for SSE mode (default 8000)
"""

import argparse
import os
import asyncio
import datetime
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from pyzabbix import ZabbixAPI
import util

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()


def _build_zabbix_client() -> ZabbixAPI:
    """
    Build and return a ZabbixAPI session using credentials from the environment.
    Token authentication is used when ZABBIX_TOKEN is present; otherwise
    user/password authentication is used.

    Raises:
        EnvironmentError: if required variables are missing.
    """
    url = os.getenv("ZABBIX_URL", "").strip()
    if not url:
        raise EnvironmentError("ZABBIX_URL is not set.")

    verify_ssl_raw = os.getenv("ZABBIX_VERIFY_SSL", "true").strip().lower()
    verify_ssl = verify_ssl_raw in ("true", "1", "yes")

    zapi = ZabbixAPI(url)
    zapi.session.verify = verify_ssl

    token = os.getenv("ZABBIX_TOKEN", "").strip()
    if token:
        zapi.login(api_token=token)
        return zapi

    user = os.getenv("ZABBIX_USER", "").strip()
    password = os.getenv("ZABBIX_PASSWORD", "").strip()
    if not user or not password:
        raise EnvironmentError(
            "No valid credentials found.  Set ZABBIX_TOKEN (recommended, Zabbix 5.4+) "
            "or ZABBIX_USER + ZABBIX_PASSWORD."
        )
    zapi.login(user=user, password=password)
    return zapi


# Module-level client — built once at import time and reused across all tool calls.
zapi = _build_zabbix_client()

server = Server("zabbix-mcp")

# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """Return the full catalogue of tools exposed by this MCP server."""
    return [

        # ── Hosts ──────────────────────────────────────────────────────────

        types.Tool(
            name="list_hosts",
            description=(
                "List Zabbix hosts with their status, availability and primary IP address. "
                "Optionally filter by host group ID or status (enabled/disabled)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "groupid": {
                        "type": "string",
                        "description": "Filter by host group ID (optional).",
                    },
                    "status": {
                        "type": "integer",
                        "enum": [0, 1],
                        "description": "0 = enabled hosts only, 1 = disabled hosts only (optional).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum hosts to return (default 100).",
                    },
                },
            },
        ),
        types.Tool(
            name="get_host",
            description=(
                "Retrieve full details of a single Zabbix host: interfaces, groups, "
                "templates, macros, inventory and monitoring status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID (use list_hosts or search_hosts to find it).",
                    },
                    "hostname": {
                        "type": "string",
                        "description": "Technical host name (used when hostid is not known).",
                    },
                },
            },
        ),
        types.Tool(
            name="search_hosts",
            description=(
                "Search for Zabbix hosts by name or IP address. "
                "The query is matched as a substring against both the technical host name "
                "and the visible display name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search string — matched against host name and visible name.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 50).",
                    },
                },
                "required": ["query"],
            },
        ),

        # ── Triggers / Problems ───────────────────────────────────────────

        types.Tool(
            name="get_problems",
            description=(
                "Return currently active (unresolved) problems in Zabbix. "
                "Results are sorted by severity descending, then by time descending. "
                "Optionally filter by minimum severity, host ID or host group ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_severity": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3, 4, 5],
                        "description": (
                            "Minimum severity to include: "
                            "0=Not classified, 1=Information, 2=Warning, "
                            "3=Average, 4=High, 5=Disaster (default 0)."
                        ),
                    },
                    "hostid": {
                        "type": "string",
                        "description": "Filter problems for a specific host ID (optional).",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Filter problems for a specific host group ID (optional).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum problems to return (default 100).",
                    },
                },
            },
        ),
        types.Tool(
            name="get_triggers",
            description=(
                "List triggers associated with a specific host. "
                "Optionally restrict to triggers currently in PROBLEM state. "
                "Results are sorted by severity descending."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID.",
                    },
                    "only_problems": {
                        "type": "boolean",
                        "description": "If true, return only triggers in PROBLEM state (default false).",
                    },
                    "min_severity": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3, 4, 5],
                        "description": "Minimum severity to include (default 0).",
                    },
                },
                "required": ["hostid"],
            },
        ),

        # ── Items / History ───────────────────────────────────────────────

        types.Tool(
            name="get_items",
            description=(
                "List monitoring items (metrics) for a Zabbix host. "
                "Shows item key, name, last collected value and unit. "
                "Optionally filter by name substring."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Filter items whose name contains this string (optional).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum items to return (default 100).",
                    },
                },
                "required": ["hostid"],
            },
        ),
        types.Tool(
            name="get_history",
            description=(
                "Retrieve recent collected values for a specific Zabbix item. "
                "Returns data points from the last N hours, sorted newest first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "itemid": {
                        "type": "string",
                        "description": "Zabbix item ID (use get_items to find it).",
                    },
                    "hours": {
                        "type": "number",
                        "description": "How many hours of history to retrieve (default 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum data points to return (default 100).",
                    },
                },
                "required": ["itemid"],
            },
        ),

        # ── Maintenance ───────────────────────────────────────────────────

        types.Tool(
            name="get_maintenances",
            description=(
                "List all Zabbix maintenance windows with their active period, "
                "type and associated hosts or host groups."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="create_maintenance",
            description=(
                "Create a one-time Zabbix maintenance window for one or more hosts. "
                "start_time must be an ISO 8601 datetime string (e.g. '2026-06-18T22:00:00'). "
                "The window stays active for duration_minutes minutes after start_time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Maintenance window name.",
                    },
                    "hostids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of host IDs to include in the maintenance.",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start datetime in ISO 8601 format, e.g. '2026-06-18T22:00:00'.",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration of the maintenance window in minutes.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional free-text description.",
                    },
                    "collect_data": {
                        "type": "boolean",
                        "description": "Whether to collect monitoring data during maintenance (default true).",
                    },
                },
                "required": ["name", "hostids", "start_time", "duration_minutes"],
            },
        ),
        types.Tool(
            name="delete_maintenance",
            description=(
                "Delete a Zabbix maintenance window by its ID. "
                "Use get_maintenances to find the maintenanceid."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "maintenanceid": {
                        "type": "string",
                        "description": "ID of the maintenance window to delete.",
                    },
                },
                "required": ["maintenanceid"],
            },
        ),

        # ── Analytics ─────────────────────────────────────────────────────

        types.Tool(
            name="get_flapping_triggers",
            description=(
                "Identify triggers that changed state most frequently (PROBLEM ↔ OK) "
                "in a given time window. High flap counts indicate noisy or misconfigured "
                "thresholds."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Time window in hours (default 24).",
                    },
                    "hostid": {
                        "type": "string",
                        "description": "Restrict to a specific host ID (optional).",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Restrict to a specific host group ID (optional).",
                    },
                    "min_flaps": {
                        "type": "integer",
                        "description": "Minimum number of state changes to include (default 2).",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "How many triggers to return (default 15).",
                    },
                },
            },
        ),
        types.Tool(
            name="get_availability_report",
            description=(
                "Calculate uptime percentage for each host in a time window. "
                "Downtime is computed from PROBLEM events and their recoveries. "
                "Use this for SLA-like questions: 'how available was X last month?'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Time window in hours (default 720 = 30 days).",
                    },
                    "hostids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of host IDs to report on (optional — omit for all hosts).",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Filter to a host group ID (optional).",
                    },
                    "min_severity": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3, 4, 5],
                        "description": "Minimum severity to count as downtime (default 3 = Average).",
                    },
                },
            },
        ),
        types.Tool(
            name="get_trends",
            description=(
                "Retrieve hourly aggregated trend data (min / avg / max) for a Zabbix item. "
                "Trends cover longer windows than history (days to months) without high resolution. "
                "Use for capacity-planning questions: 'how has CPU grown over the last month?'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "itemid": {
                        "type": "string",
                        "description": "Zabbix item ID (use get_items to find it).",
                    },
                    "hours": {
                        "type": "number",
                        "description": "How many hours of trends to retrieve (default 720 = 30 days).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum data points to return (default 200).",
                    },
                },
                "required": ["itemid"],
            },
        ),
        types.Tool(
            name="get_problem_duration_stats",
            description=(
                "For each trigger, compute total, average and maximum problem duration "
                "over a time window. Identifies which issues are longest-lasting and "
                "most impactful."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Time window in hours (default 168 = 7 days).",
                    },
                    "hostid": {
                        "type": "string",
                        "description": "Restrict to a specific host ID (optional).",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Restrict to a specific host group ID (optional).",
                    },
                    "min_severity": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3, 4, 5],
                        "description": "Minimum severity to include (default 0).",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "How many triggers to return, sorted by total downtime (default 15).",
                    },
                },
            },
        ),

        # ── Top hosts by problems ─────────────────────────────────────────

        types.Tool(
            name="get_top_hosts_by_problems",
            description=(
                "Return a ranked list of hosts ordered by number of PROBLEM events "
                "in the last N hours. Use this to answer questions like "
                "'which server had the most problems this week'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Time window in hours (default 168 = 7 days).",
                    },
                    "min_severity": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3, 4, 5],
                        "description": "Minimum severity to count (default 0).",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "How many hosts to return in the ranking (default 10).",
                    },
                },
            },
        ),

        # ── Host Groups ───────────────────────────────────────────────────

        types.Tool(
            name="get_host_groups",
            description=(
                "List all Zabbix host groups with their IDs and names. "
                "Use the returned groupid values to filter other tools such as "
                "list_hosts, get_problems and get_triggers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional substring filter on group name.",
                    },
                },
            },
        ),

        # ── Events ────────────────────────────────────────────────────────

        types.Tool(
            name="get_events",
            description=(
                "Retrieve historical Zabbix events (problems and recoveries) "
                "for a given time window. Results are sorted newest first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "How many hours back to look (default 24).",
                    },
                    "hostid": {
                        "type": "string",
                        "description": "Filter events for a specific host ID (optional).",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Filter events for a specific host group ID (optional).",
                    },
                    "min_severity": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3, 4, 5],
                        "description": "Minimum severity to include (default 0).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum events to return (default 100).",
                    },
                },
            },
        ),

        # ── Acknowledge ───────────────────────────────────────────────────

        types.Tool(
            name="acknowledge_problem",
            description=(
                "Acknowledge one or more Zabbix problems (events). "
                "Optionally add a message and/or close the problem if the trigger allows manual close."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "eventids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of event IDs to acknowledge (use get_problems to find them).",
                    },
                    "message": {
                        "type": "string",
                        "description": "Acknowledgement message (optional).",
                    },
                    "close": {
                        "type": "boolean",
                        "description": "Also close the problem if the trigger supports manual close (default false).",
                    },
                },
                "required": ["eventids"],
            },
        ),

        # ── Graphs ────────────────────────────────────────────────────────

        types.Tool(
            name="get_graphs",
            description=(
                "List graphs defined for a Zabbix host, including the graph name, "
                "dimensions and number of items plotted."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional substring filter on graph name.",
                    },
                },
                "required": ["hostid"],
            },
        ),
        types.Tool(
            name="get_graph_items",
            description=(
                "List the items (metrics) that make up a specific Zabbix graph."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "graphid": {
                        "type": "string",
                        "description": "Zabbix graph ID (use get_graphs to find it).",
                    },
                },
                "required": ["graphid"],
            },
        ),

        # ── Templates ─────────────────────────────────────────────────────

        types.Tool(
            name="get_templates",
            description=(
                "List Zabbix templates with their IDs and names. "
                "Optionally filter by name substring or by host ID to see which "
                "templates are linked to a specific host."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional substring filter on template name.",
                    },
                    "hostid": {
                        "type": "string",
                        "description": "Return only templates linked to this host ID (optional).",
                    },
                },
            },
        ),

        # ── Host Inventory ────────────────────────────────────────────────

        types.Tool(
            name="get_host_inventory",
            description=(
                "Return the inventory record for a Zabbix host: OS, hardware, "
                "location, contact, serial numbers and other asset fields."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID.",
                    },
                },
                "required": ["hostid"],
            },
        ),

        # ── Actions ───────────────────────────────────────────────────────

        types.Tool(
            name="get_actions",
            description=(
                "List Zabbix actions (alerting rules) with their names, status "
                "and the event source they react to (trigger, discovery, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional substring filter on action name.",
                    },
                },
            },
        ),

        # ── Users / User Groups ───────────────────────────────────────────

        types.Tool(
            name="get_users",
            description=(
                "List Zabbix users with their username, display name, role and "
                "the user groups they belong to."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional substring filter on username or name.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_user_groups",
            description=(
                "List Zabbix user groups with their IDs, names and GUI access level."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional substring filter on group name.",
                    },
                },
            },
        ),

        # ── Operational ───────────────────────────────────────────────────

        types.Tool(
            name="enable_host",
            description="Enable monitoring for a Zabbix host (set status to 0).",
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID to enable.",
                    },
                },
                "required": ["hostid"],
            },
        ),
        types.Tool(
            name="disable_host",
            description="Disable monitoring for a Zabbix host (set status to 1).",
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID to disable.",
                    },
                },
                "required": ["hostid"],
            },
        ),
        types.Tool(
            name="update_maintenance",
            description=(
                "Update an existing Zabbix maintenance window. "
                "All fields are optional — only provided fields are changed. "
                "Use get_maintenances to find the maintenanceid."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "maintenanceid": {
                        "type": "string",
                        "description": "ID of the maintenance window to update.",
                    },
                    "name": {
                        "type": "string",
                        "description": "New name (optional).",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "New start time in ISO 8601 format (optional).",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "New duration in minutes (optional).",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (optional).",
                    },
                    "collect_data": {
                        "type": "boolean",
                        "description": "Whether to collect data during maintenance (optional).",
                    },
                },
                "required": ["maintenanceid"],
            },
        ),
        types.Tool(
            name="add_host_to_group",
            description="Add a host to a Zabbix host group.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID.",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Zabbix host group ID.",
                    },
                },
                "required": ["hostid", "groupid"],
            },
        ),
        types.Tool(
            name="remove_host_from_group",
            description="Remove a host from a Zabbix host group.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hostid": {
                        "type": "string",
                        "description": "Zabbix host ID.",
                    },
                    "groupid": {
                        "type": "string",
                        "description": "Zabbix host group ID.",
                    },
                },
                "required": ["hostid", "groupid"],
            },
        ),
    ]

# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Dispatch an incoming tool call to the appropriate Zabbix API operation.
    Returns a list with a single TextContent block.
    """

    # ── Hosts ──────────────────────────────────────────────────────────────

    if name == "list_hosts":
        params = {
            "output": ["hostid", "host", "name", "status", "available"],
            "selectInterfaces": ["ip", "type", "main"],
            "limit": arguments.get("limit", 100),
            "sortfield": "host",
        }
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]
        if "status" in arguments:
            params["filter"] = {"status": arguments["status"]}

        hosts = zapi.host.get(**params)
        if not hosts:
            return [types.TextContent(type="text", text="No hosts found.")]

        lines = []
        for h in hosts:
            primary_ip = next(
                (i["ip"] for i in h.get("interfaces", []) if i.get("main") == "1"),
                "N/A",
            )
            lines.append(
                f"ID={h['hostid']} | {h['host']} | {h.get('name', '')} | "
                f"{util.host_status(h['status'])} | "
                f"available={util.host_available(h['available'])} | "
                f"ip={primary_ip}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_host":
        hostid = arguments.get("hostid")
        hostname = arguments.get("hostname")

        if not hostid and not hostname:
            return [types.TextContent(type="text", text="Provide hostid or hostname.")]

        params = {
            "output": "extend",
            "selectInterfaces": "extend",
            "selectGroups": ["groupid", "name"],
            "selectParentTemplates": ["templateid", "name"],
            "selectMacros": ["macro", "value"],
            "selectInventory": "extend",
        }
        if hostid:
            params["hostids"] = [hostid]
        else:
            params["filter"] = {"host": hostname}

        hosts = zapi.host.get(**params)
        if not hosts:
            return [types.TextContent(type="text", text="Host not found.")]

        h = hosts[0]
        lines = [
            f"hostid:     {h['hostid']}",
            f"host:       {h['host']}",
            f"name:       {h.get('name', '')}",
            f"status:     {util.host_status(h['status'])}",
            f"available:  {util.host_available(h['available'])}",
        ]

        if h.get("interfaces"):
            lines.append("interfaces:")
            for iface in h["interfaces"]:
                lines.append(
                    f"  ip={iface.get('ip', 'N/A')} dns={iface.get('dns', '')} "
                    f"port={iface.get('port', '')} main={iface.get('main', '0')}"
                )

        if h.get("groups"):
            lines.append("groups: " + ", ".join(g["name"] for g in h["groups"]))

        if h.get("parentTemplates"):
            lines.append("templates: " + ", ".join(t["name"] for t in h["parentTemplates"]))

        if h.get("macros"):
            lines.append("macros:")
            for m in h["macros"]:
                lines.append(f"  {m['macro']} = {m['value']}")

        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "search_hosts":
        query = arguments["query"]
        limit = arguments.get("limit", 50)

        hosts = zapi.host.get(
            output=["hostid", "host", "name", "status", "available"],
            selectInterfaces=["ip", "main"],
            search={"host": query, "name": query},
            searchByAny=True,
            searchWildcardsEnabled=True,
            sortfield="host",
            limit=limit,
        )
        if not hosts:
            return [types.TextContent(type="text", text=f"No hosts matching '{query}'.")]

        lines = []
        for h in hosts:
            primary_ip = next(
                (i["ip"] for i in h.get("interfaces", []) if i.get("main") == "1"),
                "N/A",
            )
            lines.append(
                f"ID={h['hostid']} | {h['host']} | {h.get('name', '')} | "
                f"{util.host_status(h['status'])} | ip={primary_ip}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Triggers / Problems ───────────────────────────────────────────────

    if name == "get_problems":
        limit = arguments.get("limit", 100)
        min_severity = arguments.get("min_severity", 0)

        params = {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged", "r_eventid"],
            "selectHosts": ["hostid", "host", "name"],
            "recent": False,
            "severities": list(range(min_severity, 6)),
            "sortfield": "eventid",
            "sortorder": "DESC",
            "limit": limit,
        }
        if "hostid" in arguments:
            params["hostids"] = [arguments["hostid"]]
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]

        problems = zapi.problem.get(**params)
        problems.sort(key=lambda p: (int(p["severity"]), int(p["clock"])), reverse=True)
        if not problems:
            return [types.TextContent(type="text", text="No active problems.")]

        lines = []
        for p in problems:
            ack = "ack" if p.get("acknowledged") == "1" else "unack"
            hosts_str = ", ".join(
                h.get("name") or h.get("host", "?") for h in p.get("hosts", [])
            ) or "?"
            lines.append(
                f"[{util.severity(p['severity'])}] {p['name']} | "
                f"host={hosts_str} | "
                f"time={util.ts(p['clock'])} | {ack} | "
                f"eventid={p['eventid']} triggerid={p['objectid']}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_triggers":
        hostid = arguments["hostid"]
        only_problems = arguments.get("only_problems", False)
        min_severity = arguments.get("min_severity", 0)

        params = {
            "hostids": [hostid],
            "output": ["triggerid", "description", "priority", "status", "value", "lastchange", "error"],
            "expandDescription": True,
            "sortfield": ["priority", "lastchange"],
            "sortorder": ["DESC", "DESC"],
            "min_severity": min_severity,
        }
        if only_problems:
            params["only_true"] = True
            params["filter"] = {"value": "1"}

        triggers = zapi.trigger.get(**params)
        if not triggers:
            return [types.TextContent(type="text", text="No triggers found.")]

        lines = []
        for t in triggers:
            lines.append(
                f"[{util.severity(t['priority'])}] {t['description']} | "
                f"state={util.trigger_value(t['value'])} | "
                f"lastchange={util.ts(t['lastchange'])} | "
                f"triggerid={t['triggerid']}"
                + (f" | error={t['error']}" if t.get("error") else "")
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Items / History ───────────────────────────────────────────────────

    if name == "get_items":
        hostid = arguments["hostid"]
        limit = arguments.get("limit", 100)

        params = {
            "hostids": [hostid],
            "output": ["itemid", "name", "key_", "lastvalue", "units", "value_type", "lastclock", "status"],
            "sortfield": "name",
            "limit": limit,
        }
        if "search" in arguments:
            params["search"] = {"name": arguments["search"]}

        items = zapi.item.get(**params)
        if not items:
            return [types.TextContent(type="text", text="No items found.")]

        lines = []
        for it in items:
            value_str = it.get("lastvalue", "N/A")
            if it.get("units"):
                value_str = f"{value_str} {it['units']}"
            lines.append(
                f"ID={it['itemid']} | {it['name']} | key={it['key_']} | "
                f"last={value_str} | type={util.item_value_type(it['value_type'])} | "
                f"updated={util.ts(it.get('lastclock', 0))}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_history":
        itemid = arguments["itemid"]
        hours = float(arguments.get("hours", 1))
        limit = arguments.get("limit", 100)

        # Resolve value_type for the item so we query the correct history table.
        items = zapi.item.get(itemids=[itemid], output=["value_type", "name", "units"])
        if not items:
            return [types.TextContent(type="text", text=f"Item {itemid} not found.")]

        item = items[0]
        value_type = int(item["value_type"])

        now = int(datetime.datetime.now().timestamp())
        time_from = now - int(hours * 3600)

        history = zapi.history.get(
            itemids=[itemid],
            history=value_type,
            time_from=time_from,
            time_till=now,
            output="extend",
            sortfield="clock",
            sortorder="DESC",
            limit=limit,
        )
        if not history:
            return [types.TextContent(type="text", text=f"No history for item {itemid} in the last {hours}h.")]

        units = f" {item['units']}" if item.get("units") else ""
        header = f"Item: {item['name']} (id={itemid}) — last {hours}h"
        lines = [header]
        for point in history:
            lines.append(f"  {util.ts(point['clock'])}  {point['value']}{units}")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Maintenance ───────────────────────────────────────────────────────

    if name == "get_maintenances":
        maintenances = zapi.maintenance.get(
            output="extend",
            selectHosts=["hostid", "host"],
            selectGroups=["groupid", "name"],
            sortfield="name",
        )
        if not maintenances:
            return [types.TextContent(type="text", text="No maintenance windows defined.")]

        lines = []
        for m in maintenances:
            hosts_str = ", ".join(h["host"] for h in m.get("hosts", []))
            groups_str = ", ".join(g["name"] for g in m.get("groups", []))
            scope = hosts_str or groups_str or "—"
            lines.append(
                f"ID={m['maintenanceid']} | {m['name']} | "
                f"{util.maintenance_type(m['maintenance_type'])} | "
                f"from={util.ts(m['active_since'])} till={util.ts(m['active_till'])} | "
                f"scope={scope}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "create_maintenance":
        name_val = arguments["name"]
        hostids = arguments["hostids"]
        start_time_str = arguments["start_time"]
        duration_minutes = int(arguments["duration_minutes"])
        description = arguments.get("description", "")
        collect_data = arguments.get("collect_data", True)

        start_dt = datetime.datetime.fromisoformat(start_time_str)
        start_ts = int(start_dt.timestamp())
        end_ts = start_ts + duration_minutes * 60
        maintenance_type_val = 0 if collect_data else 1

        result = zapi.maintenance.create(
            name=name_val,
            description=description,
            active_since=start_ts,
            active_till=end_ts,
            maintenance_type=maintenance_type_val,
            timeperiods=[{
                "timeperiod_type": 0,
                "start_date": start_ts,
                "period": duration_minutes * 60,
            }],
            hostids=hostids,
        )
        mid = result["maintenanceids"][0]
        return [types.TextContent(
            type="text",
            text=(
                f"Maintenance created: id={mid}\n"
                f"  name={name_val}\n"
                f"  from={util.ts(start_ts)} till={util.ts(end_ts)} "
                f"({util.duration(duration_minutes * 60)})\n"
                f"  type={util.maintenance_type(str(maintenance_type_val))}\n"
                f"  hosts={', '.join(hostids)}"
            ),
        )]

    if name == "delete_maintenance":
        maintenanceid = arguments["maintenanceid"]
        zapi.maintenance.delete([maintenanceid])
        return [types.TextContent(type="text", text=f"Maintenance {maintenanceid} deleted.")]

    # ── Top hosts by problems ─────────────────────────────────────────────────

    if name == "get_top_hosts_by_problems":
        from collections import Counter

        hours = float(arguments.get("hours", 168))
        min_severity = arguments.get("min_severity", 0)
        top_n = int(arguments.get("top_n", 10))

        now = int(datetime.datetime.now().timestamp())
        time_from = now - int(hours * 3600)

        events = zapi.event.get(
            source=0,
            object=0,
            value=1,  # PROBLEM events only
            time_from=time_from,
            time_till=now,
            severities=list(range(min_severity, 6)),
            selectHosts=["hostid", "host", "name"],
            output=["eventid", "severity"],
            limit=10000,
        )
        if not events:
            return [types.TextContent(type="text", text="No problem events found in this period.")]

        host_counts: Counter = Counter()
        host_labels: dict = {}
        for e in events:
            for h in e.get("hosts", []):
                hid = h["hostid"]
                host_counts[hid] += 1
                host_labels[hid] = h.get("name") or h.get("host", hid)

        if not host_counts:
            return [types.TextContent(type="text", text="No host information found in events.")]

        window = util.duration(int(hours * 3600))
        lines = [f"Top {top_n} hosts by problem count (last {window}):"]
        for rank, (hid, count) in enumerate(host_counts.most_common(top_n), start=1):
            lines.append(f"  {rank:>2}. {host_labels[hid]} — {count} problem(s)  [hostid={hid}]")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Host Groups ───────────────────────────────────────────────────────────

    if name == "get_host_groups":
        params: dict = {
            "output": ["groupid", "name"],
            "sortfield": "name",
        }
        if "search" in arguments:
            params["search"] = {"name": arguments["search"]}

        groups = zapi.hostgroup.get(**params)
        if not groups:
            return [types.TextContent(type="text", text="No host groups found.")]

        lines = [f"ID={g['groupid']} | {g['name']}" for g in groups]
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Events ────────────────────────────────────────────────────────────────

    if name == "get_events":
        hours = float(arguments.get("hours", 24))
        limit = arguments.get("limit", 100)
        min_severity = arguments.get("min_severity", 0)

        now = int(datetime.datetime.now().timestamp())
        time_from = now - int(hours * 3600)

        params = {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged", "value"],
            "selectHosts": ["hostid", "host", "name"],
            "source": 0,
            "object": 0,
            "time_from": time_from,
            "time_till": now,
            "severities": list(range(min_severity, 6)),
            "sortfield": "clock",
            "sortorder": "DESC",
            "limit": limit,
        }
        if "hostid" in arguments:
            params["hostids"] = [arguments["hostid"]]
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]

        events = zapi.event.get(**params)
        if not events:
            return [types.TextContent(type="text", text="No events found.")]

        _EVENT_VALUE = {"0": "OK", "1": "PROBLEM"}
        lines = []
        for e in events:
            state = _EVENT_VALUE.get(str(e.get("value", "0")), "?")
            ack = "ack" if e.get("acknowledged") == "1" else "unack"
            hosts_str = ", ".join(
                h.get("name") or h.get("host", "?") for h in e.get("hosts", [])
            ) or "?"
            lines.append(
                f"[{util.severity(e['severity'])}] [{state}] {e['name']} | "
                f"host={hosts_str} | time={util.ts(e['clock'])} | {ack} | eventid={e['eventid']}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Acknowledge ───────────────────────────────────────────────────────────

    if name == "acknowledge_problem":
        eventids = arguments["eventids"]
        message = arguments.get("message", "")
        close = arguments.get("close", False)

        # action bitmask: 2=acknowledge, 4=add message, 1=close
        action = 2
        if message:
            action |= 4
        if close:
            action |= 1

        kwargs: dict = {"eventids": eventids, "action": action}
        if message:
            kwargs["message"] = message

        zapi.event.acknowledge(**kwargs)
        closed_str = " and closed" if close else ""
        msg_str = f" with message: {message!r}" if message else ""
        return [types.TextContent(
            type="text",
            text=f"Acknowledged{closed_str} {len(eventids)} event(s){msg_str}.",
        )]

    # ── Graphs ────────────────────────────────────────────────────────────────

    if name == "get_graphs":
        hostid = arguments["hostid"]
        params = {
            "hostids": [hostid],
            "output": ["graphid", "name", "width", "height", "graphtype", "gitems"],
            "selectGraphItems": ["itemid"],
            "sortfield": "name",
        }
        if "search" in arguments:
            params["search"] = {"name": arguments["search"]}

        graphs = zapi.graph.get(**params)
        if not graphs:
            return [types.TextContent(type="text", text="No graphs found.")]

        _GRAPH_TYPE = {"0": "normal", "1": "stacked", "2": "pie", "3": "exploded"}
        lines = []
        for g in graphs:
            n_items = len(g.get("gitems") or [])
            gtype = _GRAPH_TYPE.get(str(g.get("graphtype", "0")), "?")
            lines.append(
                f"ID={g['graphid']} | {g['name']} | {gtype} | "
                f"{g.get('width', '?')}x{g.get('height', '?')} | items={n_items}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_graph_items":
        graphid = arguments["graphid"]
        gitems = zapi.graphitem.get(
            graphids=[graphid],
            output=["gitemid", "itemid", "color", "drawtype", "yaxisside"],
            selectItem=["itemid", "name", "key_", "units"],
        )
        if not gitems:
            return [types.TextContent(type="text", text="No items found for this graph.")]

        lines = []
        for gi in gitems:
            item = gi.get("item") or {}
            lines.append(
                f"itemid={gi['itemid']} | {item.get('name', '?')} | "
                f"key={item.get('key_', '?')} | units={item.get('units', '')} | "
                f"color=#{gi.get('color', '?')}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Templates ─────────────────────────────────────────────────────────────

    if name == "get_templates":
        params = {
            "output": ["templateid", "name", "description"],
            "sortfield": "name",
        }
        if "search" in arguments:
            params["search"] = {"name": arguments["search"]}
        if "hostid" in arguments:
            params["hostids"] = [arguments["hostid"]]

        templates = zapi.template.get(**params)
        if not templates:
            return [types.TextContent(type="text", text="No templates found.")]

        lines = []
        for t in templates:
            desc = f" | {t['description']}" if t.get("description") else ""
            lines.append(f"ID={t['templateid']} | {t['name']}{desc}")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Host Inventory ────────────────────────────────────────────────────────

    if name == "get_host_inventory":
        hostid = arguments["hostid"]
        hosts = zapi.host.get(
            hostids=[hostid],
            output=["hostid", "host", "name"],
            selectInventory="extend",
        )
        if not hosts:
            return [types.TextContent(type="text", text="Host not found.")]

        h = hosts[0]
        inv = h.get("inventory") or {}
        if not inv:
            return [types.TextContent(
                type="text",
                text=f"Host {h['host']} has no inventory data populated.",
            )]

        lines = [f"Inventory for {h['host']} (id={hostid}):"]
        for key, value in inv.items():
            if value:
                lines.append(f"  {key}: {value}")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Actions ───────────────────────────────────────────────────────────────

    if name == "get_actions":
        _EVENTSOURCE = {"0": "trigger", "1": "discovery", "2": "autoregistration", "3": "internal"}
        _ACTION_STATUS = {"0": "enabled", "1": "disabled"}

        params = {
            "output": ["actionid", "name", "eventsource", "status"],
            "sortfield": "name",
        }
        if "search" in arguments:
            params["search"] = {"name": arguments["search"]}

        actions = zapi.action.get(**params)
        if not actions:
            return [types.TextContent(type="text", text="No actions found.")]

        lines = []
        for a in actions:
            source = _EVENTSOURCE.get(str(a.get("eventsource", "0")), "?")
            status = _ACTION_STATUS.get(str(a.get("status", "0")), "?")
            lines.append(f"ID={a['actionid']} | {a['name']} | source={source} | {status}")
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Users ─────────────────────────────────────────────────────────────────

    if name == "get_users":
        params = {
            "output": ["userid", "username", "name", "surname", "roleid"],
            "selectUsrgrps": ["usrgrpid", "name"],
            "sortfield": "username",
        }
        if "search" in arguments:
            q = arguments["search"]
            params["search"] = {"username": q, "name": q}
            params["searchByAny"] = True

        users = zapi.user.get(**params)
        if not users:
            return [types.TextContent(type="text", text="No users found.")]

        lines = []
        for u in users:
            full_name = f"{u.get('name', '')} {u.get('surname', '')}".strip()
            groups = ", ".join(g["name"] for g in u.get("usrgrps", []))
            lines.append(
                f"ID={u['userid']} | {u['username']}"
                + (f" ({full_name})" if full_name else "")
                + (f" | groups={groups}" if groups else "")
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_user_groups":
        _GUI_ACCESS = {"0": "default", "1": "internal", "2": "LDAP", "3": "disabled"}
        _UG_STATUS = {"0": "enabled", "1": "disabled"}

        params = {
            "output": ["usrgrpid", "name", "gui_access", "users_status"],
            "sortfield": "name",
        }
        if "search" in arguments:
            params["search"] = {"name": arguments["search"]}

        groups = zapi.usergroup.get(**params)
        if not groups:
            return [types.TextContent(type="text", text="No user groups found.")]

        lines = []
        for g in groups:
            gui = _GUI_ACCESS.get(str(g.get("gui_access", "0")), "?")
            status = _UG_STATUS.get(str(g.get("users_status", "0")), "?")
            lines.append(
                f"ID={g['usrgrpid']} | {g['name']} | gui_access={gui} | {status}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Analytics ─────────────────────────────────────────────────────────────

    if name == "get_flapping_triggers":
        from collections import Counter

        hours = float(arguments.get("hours", 24))
        min_flaps = int(arguments.get("min_flaps", 2))
        top_n = int(arguments.get("top_n", 15))

        now = int(datetime.datetime.now().timestamp())
        time_from = now - int(hours * 3600)

        params = {
            "source": 0,
            "object": 0,
            "time_from": time_from,
            "time_till": now,
            "output": ["objectid", "name", "value", "clock"],
            "selectHosts": ["hostid", "host", "name"],
            "limit": 20000,
        }
        if "hostid" in arguments:
            params["hostids"] = [arguments["hostid"]]
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]

        events = zapi.event.get(**params)
        if not events:
            return [types.TextContent(type="text", text="No events found in this period.")]

        flap_count: Counter = Counter()
        trigger_meta: dict = {}
        for e in events:
            tid = e["objectid"]
            flap_count[tid] += 1
            if tid not in trigger_meta:
                hosts_str = ", ".join(
                    h.get("name") or h.get("host", "?") for h in e.get("hosts", [])
                )
                trigger_meta[tid] = {"name": e.get("name", tid), "hosts": hosts_str}

        top = [(tid, cnt) for tid, cnt in flap_count.most_common(top_n) if cnt >= min_flaps]
        if not top:
            return [types.TextContent(
                type="text",
                text=f"No triggers with ≥{min_flaps} state changes in the last {util.duration(int(hours * 3600))}.",
            )]

        window = util.duration(int(hours * 3600))
        lines = [f"Top {len(top)} flapping triggers (last {window}, min_flaps={min_flaps}):"]
        for rank, (tid, cnt) in enumerate(top, start=1):
            meta = trigger_meta.get(tid, {})
            lines.append(
                f"  {rank:>2}. [{cnt} changes] {meta.get('name', tid)} "
                f"| host={meta.get('hosts', '?')} | triggerid={tid}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_availability_report":
        hours = float(arguments.get("hours", 720))
        min_severity = arguments.get("min_severity", 3)

        now = int(datetime.datetime.now().timestamp())
        window_start = now - int(hours * 3600)

        params = {
            "source": 0,
            "object": 0,
            "value": 1,  # PROBLEM events only
            "time_from": window_start,
            "time_till": now,
            "severities": list(range(min_severity, 6)),
            "output": ["eventid", "clock", "r_eventid"],
            "selectHosts": ["hostid", "host", "name"],
            "limit": 20000,
        }
        if "hostids" in arguments:
            params["hostids"] = arguments["hostids"]
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]

        prob_events = zapi.event.get(**params)
        if not prob_events:
            return [types.TextContent(type="text", text="No problem events found in this period.")]

        # Fetch recovery clocks for all events that have r_eventid
        r_ids = [e["r_eventid"] for e in prob_events if e.get("r_eventid") and e["r_eventid"] != "0"]
        recovery_clock: dict = {}
        if r_ids:
            rec_events = zapi.event.get(
                eventids=r_ids,
                output=["eventid", "clock"],
            )
            recovery_clock = {e["eventid"]: int(e["clock"]) for e in rec_events}

        # Accumulate downtime per host
        host_downtime: dict = {}
        host_labels: dict = {}
        for e in prob_events:
            start = max(int(e["clock"]), window_start)
            r_eid = e.get("r_eventid", "0")
            end = recovery_clock.get(r_eid, now) if r_eid and r_eid != "0" else now
            end = min(end, now)
            duration_s = max(0, end - start)

            for h in e.get("hosts", []):
                hid = h["hostid"]
                host_downtime[hid] = host_downtime.get(hid, 0) + duration_s
                host_labels[hid] = h.get("name") or h.get("host", hid)

        window_seconds = now - window_start
        window_str = util.duration(window_seconds)
        lines = [f"Availability report — last {window_str} (severity ≥ {util.severity(str(min_severity))}):"]
        for hid, downtime in sorted(host_downtime.items(), key=lambda x: x[1], reverse=True):
            uptime_pct = 100.0 * (1 - downtime / window_seconds) if window_seconds else 100.0
            lines.append(
                f"  {host_labels[hid]:<40} uptime={uptime_pct:6.2f}%  "
                f"downtime={util.duration(downtime)}  [hostid={hid}]"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_trends":
        itemid = arguments["itemid"]
        hours = float(arguments.get("hours", 720))
        limit = arguments.get("limit", 200)

        # Resolve value_type to choose the correct trend table
        items = zapi.item.get(itemids=[itemid], output=["value_type", "name", "units"])
        if not items:
            return [types.TextContent(type="text", text=f"Item {itemid} not found.")]

        item = items[0]
        value_type = int(item["value_type"])

        now = int(datetime.datetime.now().timestamp())
        time_from = now - int(hours * 3600)

        # Zabbix stores float (0) and uint (3) trends; char/log/text have no trends
        if value_type not in (0, 3):
            return [types.TextContent(
                type="text",
                text=f"Item '{item['name']}' has value_type={util.item_value_type(str(value_type))} — trends only available for float and uint items.",
            )]

        trend_api = zapi.trend if value_type == 0 else zapi.trend_uint
        trends = trend_api.get(
            itemids=[itemid],
            time_from=time_from,
            time_till=now,
            output=["clock", "num", "value_min", "value_avg", "value_max"],
            sortfield="clock",
            sortorder="DESC",
            limit=limit,
        )
        if not trends:
            return [types.TextContent(
                type="text",
                text=f"No trend data for item {itemid} in the last {util.duration(int(hours * 3600))}.",
            )]

        units = f" {item['units']}" if item.get("units") else ""
        header = f"Trends for '{item['name']}' (id={itemid}) — last {util.duration(int(hours * 3600))}"
        lines = [header, f"  {'time':<20} {'min':>12} {'avg':>12} {'max':>12}  samples"]
        for t in trends:
            lines.append(
                f"  {util.ts(t['clock']):<20} "
                f"{t['value_min']:>12}{units} "
                f"{t['value_avg']:>12}{units} "
                f"{t['value_max']:>12}{units}  n={t['num']}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if name == "get_problem_duration_stats":
        from collections import defaultdict

        hours = float(arguments.get("hours", 168))
        min_severity = arguments.get("min_severity", 0)
        top_n = int(arguments.get("top_n", 15))

        now = int(datetime.datetime.now().timestamp())
        window_start = now - int(hours * 3600)

        params = {
            "source": 0,
            "object": 0,
            "value": 1,
            "time_from": window_start,
            "time_till": now,
            "severities": list(range(min_severity, 6)),
            "output": ["eventid", "objectid", "name", "clock", "r_eventid", "severity"],
            "selectHosts": ["hostid", "host", "name"],
            "limit": 20000,
        }
        if "hostid" in arguments:
            params["hostids"] = [arguments["hostid"]]
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]

        prob_events = zapi.event.get(**params)
        if not prob_events:
            return [types.TextContent(type="text", text="No problem events found in this period.")]

        r_ids = [e["r_eventid"] for e in prob_events if e.get("r_eventid") and e["r_eventid"] != "0"]
        recovery_clock: dict = {}
        if r_ids:
            rec_events = zapi.event.get(eventids=r_ids, output=["eventid", "clock"])
            recovery_clock = {e["eventid"]: int(e["clock"]) for e in rec_events}

        # Per-trigger stats
        trigger_durations: dict = defaultdict(list)
        trigger_meta2: dict = {}
        for e in prob_events:
            tid = e["objectid"]
            start = max(int(e["clock"]), window_start)
            r_eid = e.get("r_eventid", "0")
            end = recovery_clock.get(r_eid, now) if r_eid and r_eid != "0" else now
            end = min(end, now)
            dur = max(0, end - start)
            trigger_durations[tid].append(dur)
            if tid not in trigger_meta2:
                hosts_str = ", ".join(h.get("name") or h.get("host", "?") for h in e.get("hosts", []))
                trigger_meta2[tid] = {
                    "name": e.get("name", tid),
                    "hosts": hosts_str,
                    "severity": e.get("severity", "0"),
                }

        # Sort by total downtime
        ranked = sorted(trigger_durations.items(), key=lambda x: sum(x[1]), reverse=True)[:top_n]
        window_str = util.duration(int(hours * 3600))
        lines = [f"Problem duration stats — last {window_str}:"]
        lines.append(f"  {'trigger':<45} {'sev':<12} {'#':>4} {'total':>10} {'avg':>10} {'max':>10}  host")
        for tid, durs in ranked:
            meta = trigger_meta2.get(tid, {})
            total = sum(durs)
            avg = total // len(durs)
            mx = max(durs)
            sev = util.severity(meta.get("severity", "0"))
            name_str = meta.get("name", tid)[:44]
            lines.append(
                f"  {name_str:<45} {sev:<12} {len(durs):>4} "
                f"{util.duration(total):>10} {util.duration(avg):>10} {util.duration(mx):>10}"
                f"  {meta.get('hosts', '?')}"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Operational ───────────────────────────────────────────────────────────

    if name == "enable_host":
        hostid = arguments["hostid"]
        hosts = zapi.host.get(hostids=[hostid], output=["host"])
        if not hosts:
            return [types.TextContent(type="text", text=f"Host {hostid} not found.")]
        zapi.host.update(hostid=hostid, status=0)
        return [types.TextContent(type="text", text=f"Host '{hosts[0]['host']}' (id={hostid}) enabled.")]

    if name == "disable_host":
        hostid = arguments["hostid"]
        hosts = zapi.host.get(hostids=[hostid], output=["host"])
        if not hosts:
            return [types.TextContent(type="text", text=f"Host {hostid} not found.")]
        zapi.host.update(hostid=hostid, status=1)
        return [types.TextContent(type="text", text=f"Host '{hosts[0]['host']}' (id={hostid}) disabled.")]

    if name == "update_maintenance":
        maintenanceid = arguments["maintenanceid"]
        existing = zapi.maintenance.get(
            maintenanceids=[maintenanceid],
            output="extend",
            selectTimeperiods="extend",
        )
        if not existing:
            return [types.TextContent(type="text", text=f"Maintenance {maintenanceid} not found.")]

        m = existing[0]
        update: dict = {"maintenanceid": maintenanceid}

        if "name" in arguments:
            update["name"] = arguments["name"]
        if "description" in arguments:
            update["description"] = arguments["description"]
        if "collect_data" in arguments:
            update["maintenance_type"] = 0 if arguments["collect_data"] else 1

        # Recalculate time window if start_time or duration_minutes provided
        if "start_time" in arguments or "duration_minutes" in arguments:
            if "start_time" in arguments:
                start_dt = datetime.datetime.fromisoformat(arguments["start_time"])
                start_ts = int(start_dt.timestamp())
            else:
                start_ts = int(m["active_since"])

            if "duration_minutes" in arguments:
                duration_s = int(arguments["duration_minutes"]) * 60
            else:
                duration_s = int(m["active_till"]) - int(m["active_since"])

            update["active_since"] = start_ts
            update["active_till"] = start_ts + duration_s
            update["timeperiods"] = [{
                "timeperiod_type": 0,
                "start_date": start_ts,
                "period": duration_s,
            }]

        zapi.maintenance.update(**update)
        return [types.TextContent(type="text", text=f"Maintenance {maintenanceid} updated.")]

    if name == "add_host_to_group":
        hostid = arguments["hostid"]
        groupid = arguments["groupid"]
        zapi.hostgroup.massadd(
            hosts=[{"hostid": hostid}],
            groups=[{"groupid": groupid}],
        )
        return [types.TextContent(type="text", text=f"Host {hostid} added to group {groupid}.")]

    if name == "remove_host_from_group":
        hostid = arguments["hostid"]
        groupid = arguments["groupid"]
        zapi.hostgroup.massremove(
            hostids=[hostid],
            groupids=[groupid],
        )
        return [types.TextContent(type="text", text=f"Host {hostid} removed from group {groupid}.")]

    raise ValueError(f"Unknown tool: {name}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zabbix MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=os.getenv("MCP_TRANSPORT", "stdio"),
        help="Transport to use: stdio (default) or sse (HTTP/SSE)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "0.0.0.0"),
        help="Bind host for SSE mode (default 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8000")),
        help="TCP port for SSE mode (default 8000)",
    )
    return parser.parse_args()


async def _run_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _run_sse(host: str, port: int) -> None:
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(scope, receive, send):
        async with sse.connect_sse(scope, receive, send) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )

    async def asgi_app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return
        path = scope.get("path", "")
        if path == "/sse":
            await handle_sse(scope, receive, send)
        elif path.startswith("/messages/"):
            await sse.handle_post_message(scope, receive, send)

    uvicorn.run(asgi_app, host=host, port=port)


if __name__ == "__main__":
    args = _parse_args()
    if args.transport == "sse":
        _run_sse(args.host, args.port)
    else:
        asyncio.run(_run_stdio())
