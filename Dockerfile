# ============================================================
# PropSentry Engine — Docker Image (Linux)
# ============================================================
# This image runs ONLY the engine, agent, copilot, and storage.
# MetaTrader5 is NOT installed here (impossible on Linux).
# The engine communicates with MT5 via the HTTP bridge on the Windows host.
# ============================================================

FROM python:3.11-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# ── Stage: dependencies ──────────────────────────────────────────────────
FROM base AS deps

# Copy dependency manifest
COPY pyproject.toml ./

# Install all dependencies EXCEPT MetaTrader5 (Windows-only)
# We generate a filtered requirements file at build time
RUN pip install --upgrade pip && \
    pip install \
        pydantic>=2.7.0 \
        pydantic-settings>=2.2.0 \
        pytz>=2024.1 \
        pandas>=2.2.0 \
        numpy>=1.26.0 \
        redis>=5.0.0 \
        "python-telegram-bot>=21.0" \
        sqlalchemy>=2.0.0 \
        httpx>=0.27.0 \
        loguru>=0.7.0 \
        pyyaml>=6.0.1 \
        fastapi>=0.111.0 \
        uvicorn>=0.30.0

# ── Stage: final image ────────────────────────────────────────────────────
FROM base AS final

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy project source
COPY src/       ./src/
COPY config/    ./config/
COPY scripts/   ./scripts/

# Create data directories
RUN mkdir -p data/historical data/backups data/cache logs

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Health check — verifies the bridge is reachable
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://${MT5_BRIDGE_HOST:-host.docker.internal}:${MT5_BRIDGE_PORT:-8765}/health || exit 1

# Default command — live copilot mode
CMD ["python", "scripts/run_live_copilot.py"]
