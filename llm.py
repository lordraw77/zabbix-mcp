"""
llm.py — shared LLM provider utilities
=======================================
Single source of truth for:
  • provider registry (base_url, api_key env, model env, default model)
  • AsyncOpenAI client factory
  • MCP server spawn parameters
  • MCP-to-OpenAI tool conversion
  • core agentic loop (Reason → Act → Observe)

All agents import from here; provider-specific logic lives only in this file.
"""

import asyncio
import json
import os
import sys
from collections.abc import Callable
from typing import Any

import dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters
from openai import AsyncOpenAI

dotenv.load_dotenv()

# ── Provider registry ─────────────────────────────────────────────────────────
# key  →  (base_url, api_key_env, model_env, default_model)

PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "nvidia": (
        "https://integrate.api.nvidia.com/v1",
        "ZABBIX_MCP_NVIDIA_API_KEY",
        "ZABBIX_MCP_NVIDIA_MODEL",
        "meta/llama-3.3-70b-instruct",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "ZABBIX_MCP_OPENROUTER_API_KEY",
        "ZABBIX_MCP_OPENROUTER_MODEL",
        "openrouter/auto",
    ),
    "groq": (
        "https://api.groq.com/openai/v1",
        "ZABBIX_MCP_GROQ_API_KEY",
        "ZABBIX_MCP_GROQ_MODEL",
        "llama-3.3-70b-versatile",
    ),
}


def build_client(
    provider: str | None = None,
    *,
    timeout: float | None = None,
) -> tuple[AsyncOpenAI, str]:
    """
    Build an AsyncOpenAI client and resolve the model name for *provider*.

    provider defaults to the ZABBIX_MCP_PROVIDER env var, then "nvidia".
    Returns (client, model_string).
    Calls sys.exit() with a clear message if a required env var is missing.
    """
    if provider is None:
        provider = os.getenv("ZABBIX_MCP_PROVIDER", "nvidia").strip().lower()

    if provider not in PROVIDERS:
        sys.exit(
            f"ERROR: unknown provider '{provider}'. "
            f"Valid options: {', '.join(PROVIDERS)}"
        )

    base_url, key_env, model_env, default_model = PROVIDERS[provider]

    api_key = os.getenv(key_env, "").strip()
    if not api_key:
        sys.exit(f"ERROR: {key_env} is not set.")

    model = os.getenv(model_env, default_model)

    kwargs: dict[str, Any] = {"base_url": base_url, "api_key": api_key}
    if timeout is not None:
        kwargs["timeout"] = timeout

    return AsyncOpenAI(**kwargs), model


def build_mcp_server_params() -> StdioServerParameters:
    """Return StdioServerParameters to spawn server.py with the current interpreter."""
    venv_python = os.path.abspath(".venv/bin/python")
    python = venv_python if os.path.exists(venv_python) else sys.executable
    return StdioServerParameters(
        command=python,
        args=[str(os.path.abspath("server.py"))],
        env={**os.environ},
    )


def mcp_tool_to_openai(tool) -> dict:
    """Convert an MCP Tool descriptor to the OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name":        tool.name,
            "description": tool.description or "",
            "parameters":  tool.inputSchema,
        },
    }


async def agentic_loop(
    *,
    client: AsyncOpenAI,
    model: str,
    mcp: ClientSession,
    question: str,
    system_prompt: str | None = None,
    max_iterations: int = 20,
    tool_timeout: float | None = None,
    on_thought: Callable[[str], None] | None = None,
    on_action: Callable[[str, dict], None] | None = None,
    on_observe: Callable[[str, str], None] | None = None,
) -> str:
    """
    Core agentic loop: send question to LLM, execute tool calls via MCP,
    repeat until the LLM produces a final text answer.

    Parameters
    ----------
    client          AsyncOpenAI pointed at any OpenAI-compatible endpoint.
    model           Model identifier string.
    mcp             Already-initialised MCP ClientSession.
    question        User's natural-language question.
    system_prompt   Optional system message prepended to the conversation.
    max_iterations  Hard cap on tool-call rounds.
    tool_timeout    Per-tool asyncio timeout in seconds; None = no timeout.
    on_thought      Called with the assistant's text content before tool calls.
    on_action       Called with (tool_name, args_dict) before each execution.
    on_observe      Called with (tool_name, result_text) after each execution.

    Returns the LLM's final plain-text answer.
    Raises RuntimeError for unrecoverable LLM errors (empty response, etc.).
    """
    tools_result = await mcp.list_tools()
    tools        = [mcp_tool_to_openai(t) for t in tools_result.tools]
    valid_names  = {t["function"]["name"] for t in tools}

    messages: list = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})

    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )

    for iteration in range(max_iterations):
        response = await client.chat.completions.create(**kwargs)

        if not response.choices:
            raise RuntimeError("Il provider LLM ha restituito una lista choices vuota.")

        msg           = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if msg is None:
            raise RuntimeError("Il provider LLM ha restituito un messaggio nullo.")

        if msg.content and on_thought:
            on_thought(msg.content)

        messages.append(msg)

        if finish_reason == "length":
            partial = (msg.content or "").strip()
            suffix  = "\n\n⚠️  Risposta troncata: finestra di contesto esaurita."
            return (partial + suffix) if partial else suffix

        if not msg.tool_calls:
            return msg.content or ""

        for call in msg.tool_calls:
            tool_name = call.function.name
            tool_id   = call.id or f"call_{iteration}_{tool_name}"

            if tool_name not in valid_names:
                result_text = (
                    f"Errore: il tool '{tool_name}' non esiste. "
                    f"Tool disponibili: {', '.join(sorted(valid_names))}"
                )
                messages.append({"role": "tool", "tool_call_id": tool_id, "content": result_text})
                continue

            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                result_text = (
                    f"Errore: argomenti non validi per '{tool_name}': {exc}. "
                    f"Raw: {call.function.arguments!r}"
                )
                messages.append({"role": "tool", "tool_call_id": tool_id, "content": result_text})
                continue

            if on_action:
                on_action(tool_name, args)

            try:
                if tool_timeout is not None:
                    result = await asyncio.wait_for(
                        mcp.call_tool(tool_name, args),
                        timeout=tool_timeout,
                    )
                else:
                    result = await mcp.call_tool(tool_name, args)

                result_text = "\n".join(
                    b.text for b in result.content if hasattr(b, "text")
                ) or "(il tool non ha restituito output)"

            except asyncio.TimeoutError:
                result_text = (
                    f"Errore: '{tool_name}' ha superato il timeout di {tool_timeout:.0f}s."
                )
            except Exception as exc:
                result_text = f"Errore nell'esecuzione di '{tool_name}': {exc}"

            if on_observe:
                on_observe(tool_name, result_text)

            messages.append({"role": "tool", "tool_call_id": tool_id, "content": result_text})

    last_text = next(
        (
            getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
            for m in reversed(messages)
            if (getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None))
            == "assistant"
        ),
        None,
    )
    suffix = f"\n\n⚠️  Fermato dopo {max_iterations} iterazioni (limite raggiunto)."
    return (last_text + suffix) if last_text else suffix
