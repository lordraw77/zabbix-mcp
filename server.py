"""
server.py — Zabbix MCP Server
==============================
Implements a Model Context Protocol (MCP) server that exposes Zabbix
monitoring capabilities as callable tools.  An AI agent (or any MCP client)
connects to this process over stdio and can query hosts, active problems,
item metrics, history and maintenance windows.

Transport  : stdio  (JSON-RPC 2.0 framing managed by the mcp library)
Protocol   : MCP 2024-11-05
Auth       : credentials loaded from .env via python-dotenv
API backend: pyzabbix (thin Python wrapper over the Zabbix JSON-RPC API)

Tool categories (10 total)
---------------------------
  Hosts — read-only (3)
    list_hosts, get_host, search_hosts

  Triggers / Problems — read-only (2)
    get_problems, get_triggers

  Items / History — read-only (2)
    get_items, get_history

  Maintenance (3)
    get_maintenances, create_maintenance, delete_maintenance

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
"""

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
            "recent": False,
            "severities": list(range(min_severity, 6)),
            "sortfield": ["severity", "clock"],
            "sortorder": ["DESC", "DESC"],
            "limit": limit,
        }
        if "hostid" in arguments:
            params["hostids"] = [arguments["hostid"]]
        if "groupid" in arguments:
            params["groupids"] = [arguments["groupid"]]

        problems = zapi.problem.get(**params)
        if not problems:
            return [types.TextContent(type="text", text="No active problems.")]

        lines = []
        for p in problems:
            ack = "ack" if p.get("acknowledged") == "1" else "unack"
            lines.append(
                f"[{util.severity(p['severity'])}] {p['name']} | "
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

    raise ValueError(f"Unknown tool: {name}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
