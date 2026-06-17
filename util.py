"""
util.py — Formatting utilities for Zabbix metric values
=========================================================
The Zabbix API returns raw numeric codes and Unix timestamps for most fields.
This module converts those values into compact, human-readable strings suitable
for display in terminal output or LLM responses.
"""

import datetime


_SEVERITY = {
    "0": "Not classified",
    "1": "Information",
    "2": "Warning",
    "3": "Average",
    "4": "High",
    "5": "Disaster",
}

_HOST_STATUS = {
    "0": "enabled",
    "1": "disabled",
}

_HOST_AVAILABLE = {
    "0": "unknown",
    "1": "available",
    "2": "unavailable",
}

_TRIGGER_VALUE = {
    "0": "OK",
    "1": "PROBLEM",
}

_MAINTENANCE_TYPE = {
    "0": "with data collection",
    "1": "no data collection",
}

_ITEM_VALUE_TYPE = {
    "0": "float",
    "1": "char",
    "2": "log",
    "3": "uint",
    "4": "text",
}


def severity(code: str) -> str:
    return _SEVERITY.get(str(code), f"severity={code}")


def host_status(code: str) -> str:
    return _HOST_STATUS.get(str(code), f"status={code}")


def host_available(code: str) -> str:
    return _HOST_AVAILABLE.get(str(code), f"available={code}")


def trigger_value(code: str) -> str:
    return _TRIGGER_VALUE.get(str(code), f"value={code}")


def maintenance_type(code: str) -> str:
    return _MAINTENANCE_TYPE.get(str(code), f"type={code}")


def item_value_type(code: str) -> str:
    return _ITEM_VALUE_TYPE.get(str(code), f"vtype={code}")


def ts(unix_ts: str | int) -> str:
    """Convert a Unix timestamp to a human-readable local datetime string."""
    if not unix_ts or int(unix_ts) == 0:
        return "N/A"
    return datetime.datetime.fromtimestamp(int(unix_ts)).strftime("%Y-%m-%d %H:%M:%S")


def duration(seconds: int) -> str:
    """Convert a duration in seconds to a compact human-readable string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h"
