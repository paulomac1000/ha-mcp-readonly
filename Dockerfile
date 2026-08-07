ARG PYTHON_IMAGE=python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md LICENSE version.py server.py ./
COPY tools/ ./tools/
COPY context_generator/ ./context_generator/
COPY ha_graph/ ./ha_graph/
RUN python -m pip install --no-cache-dir build==1.5.0 setuptools==83.0.0 wheel==0.47.0 \
    && python -m build --wheel --no-isolation

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_TRANSPORT=stdio \
    MCP_BIND_HOST=127.0.0.1 \
    HEALTH_SERVER_ENABLED=1 \
    REST_API_ENABLED=0

RUN groupadd --gid 10001 haobserver \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin haobserver \
    && mkdir -p /app/output /config \
    && chown -R haobserver:haobserver /app /config
WORKDIR /app
COPY --from=builder /build/dist/*.whl /tmp/app.whl
RUN python -m pip install --no-cache-dir /tmp/app.whl \
    && rm /tmp/app.whl
USER 10001:10001
EXPOSE 9091 9092
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9091/ready', timeout=3)" || exit 1
ENTRYPOINT ["ha-mcp-readonly"]
