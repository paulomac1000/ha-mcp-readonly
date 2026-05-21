# Home Assistant MCP Readonly Server
# Multi-stage build for minimal production image

FROM python:3.14-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /root/.local /root/.local

COPY server.py .
COPY version.py .
COPY context_generator/ ./context_generator/
COPY tools/ ./tools/
COPY start.sh .
RUN chmod +x start.sh

ENV PATH=/root/.local/bin:$PATH

# Explicit USER: container runs as root (required for docker.sock, journal, dbus access)
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root
USER root

# Ports: 9091 (health), 9092 (MCP SSE), 9093 (REST API)
EXPOSE 9091 9092 9093

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:9091/health || exit 1

ENTRYPOINT ["./start.sh"]
