FROM python:3.12-slim

LABEL maintainer="alessandro.pioli@gmail.com"
LABEL description="Zabbix MCP Server — Model Context Protocol over stdio"

WORKDIR /app

# Install deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY server.py util.py ./

# .env is mounted at runtime — never bake credentials into the image
ENV ZABBIX_URL=""
ENV ZABBIX_TOKEN=""
ENV ZABBIX_USER=""
ENV ZABBIX_PASSWORD=""
ENV ZABBIX_VERIFY_SSL="true"

# MCP over stdio — no exposed ports
ENTRYPOINT ["python", "server.py"]
