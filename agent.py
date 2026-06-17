"""
agent.py — Zabbix AI Agent (NVIDIA, interactive CLI)
=====================================================
Agentic loop via NVIDIA API (OpenAI-compatible).
Provider logic and the agentic loop live in llm.py.

Environment variables (from .env)
----------------------------------
  ZABBIX_MCP_NVIDIA_API_KEY    — NVIDIA API key (required)
  ZABBIX_MCP_NVIDIA_MODEL      — model override (default: meta/llama-3.3-70b-instruct)
  ZABBIX_MCP_PROVIDER          — override provider (default: nvidia)

  Zabbix credentials are also read from .env — see .env.example.

Usage
-----
  cd /opt/zabbix-mcp
  .venv/bin/python agent.py

  >>> Lista tutti gli host abilitati
  >>> Mostrami i problemi attivi con severità alta o superiore
  >>> Crea una manutenzione per l'host 10001 domani alle 23:00 per 60 minuti
  >>> exit
"""

import asyncio
import json
import os

import dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client

import llm

dotenv.load_dotenv()

_SYSTEM_PROMPT = """\
Sei un assistente esperto di monitoring Zabbix.
Rispondi in italiano, in modo chiaro e conciso.
Usa i tool disponibili per recuperare dati aggiornati prima di rispondere.
Quando mostri liste di host o problemi, formattale in modo leggibile.
"""

SERVER_PARAMS = llm.build_mcp_server_params()


async def ask(question: str) -> str:
    client, model = llm.build_client("nvidia")
    _result: str | None = None
    _error:  Exception | None = None

    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            try:
                _result = await llm.agentic_loop(
                    client=client,
                    model=model,
                    mcp=mcp,
                    question=question,
                    system_prompt=_SYSTEM_PROMPT,
                    on_action=_on_action,
                )
            except Exception as exc:
                _error = exc

    if _error is not None:
        raise _error
    return _result  # type: ignore[return-value]


def _on_action(name: str, args: dict) -> None:
    print(f"  [tool] {name}({json.dumps(args, ensure_ascii=False)})")


async def main():
    _, model = llm.build_client("nvidia")
    print(f"Zabbix AI Agent  |  provider=nvidia  model={model}")
    print("Type your request, or 'exit' / Ctrl-C to quit.\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            break

        try:
            answer = await ask(question)
        except Exception as exc:
            print(f"  [error] {exc}\n")
            continue

        print(f"\n{answer}\n")


if __name__ == "__main__":
    asyncio.run(main())
