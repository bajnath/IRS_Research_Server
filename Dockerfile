# syntax=docker/dockerfile:1
# Container image for remote (streamable-HTTP) deployment.
#
# Used by:
#   - IBM Code Engine (deploy/ibm/)
#   - cloud.gov (docker image path) and any other container host
#
# The server serves MCP over streamable-HTTP at :8080/mcp with a health check
# at :8080/health. app.py auto-selects HTTP because PORT is set below.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV PORT=8080
# SQLite cache must be on a writable path even if /app is read-only
ENV DB_PATH=/tmp/irs_history.db

WORKDIR /app

# Install uv (fast, reproducible installs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

# The prebuilt catalog; config.py resolves it at /app/data/catalog/ via PYTHONPATH=/app/src.
COPY data/catalog/irs_catalog.json ./data/catalog/irs_catalog.json

EXPOSE 8080

# PORT=8080 (set above) makes app.py run HTTP on that port automatically.
CMD ["python", "-m", "irs_research_server.app"]
