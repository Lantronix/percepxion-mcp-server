FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as an unprivileged user. Firmware files for update_firmware_by_smart_group
# go in PERCEPXION_FIRMWARE_DIR (mounted read-only, readable by uid 10001);
# without it, firmware upload is disabled on the remote transports.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp
USER 10001

# Remote transports by default: Streamable HTTP at /mcp and legacy SSE at /sse.
# Listening on 0.0.0.0 requires MCP_AUTH_TOKEN (or MCP_AUTH_TOKEN_FILE) and
# MCP_ALLOWED_HOSTS; the server refuses to start without them.
ENV MCP_TRANSPORT=both MCP_HOST=0.0.0.0 MCP_PORT=8765
EXPOSE 8765
CMD ["python", "percepxion_mcp.py"]
