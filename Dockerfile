# Multi-stage lightweight Docker build (< 30 sec build time)
FROM python:3.11-slim as base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Create persistent data directory for SQLite
RUN mkdir -p /app/data

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ app/
COPY max_bot_sdk/ max_bot_sdk/
COPY docs/ docs/
COPY scripts/ scripts/
COPY tests/ tests/
COPY openapi.yaml .
COPY openapi.json .
COPY DATA-API.yaml .
COPY .env.example .
COPY conftest.py .

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
