FROM python:3.12-slim

LABEL maintainer="alessandro.pioli@gmail.com"
LABEL description="Zabbix MCP Server — stdio and HTTP/SSE transports"

WORKDIR /app

# Install deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY server.py util.py ./

# Zabbix connection — pass at runtime via --env-file, never bake credentials
ENV ZABBIX_URL=""
ENV ZABBIX_TOKEN=""
ENV ZABBIX_USER=""
ENV ZABBIX_PASSWORD=""
ENV ZABBIX_VERIFY_SSL="true"

# Transport selection:
#   MCP_TRANSPORT=stdio  (default) — JSON-RPC 2.0 over stdin/stdout
#   MCP_TRANSPORT=sse             — HTTP/SSE on MCP_HOST:MCP_PORT
ENV MCP_TRANSPORT="stdio"
ENV MCP_HOST="0.0.0.0"
ENV MCP_PORT="8000"

# SSE mode exposes an HTTP port; the port is ignored in stdio mode.

ENTRYPOINT ["python", "server.py"]
