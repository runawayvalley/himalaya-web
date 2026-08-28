FROM python:3.11-slim

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Download himalaya binary
ARG HIMALAYA_VERSION=v2.1.0
RUN curl -sSL "https://github.com/pimalaya/himalaya/releases/download/${HIMALAYA_VERSION}/himalaya.x86_64-linux.tgz" \
    | tar -xz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/himalaya

WORKDIR /app

# Ensure Python print() output (startup banner with token) is visible in docker logs
ENV PYTHONUNBUFFERED=1

COPY himalaya_web.py .

# Install gunicorn
RUN pip install --no-cache-dir gunicorn

# Create non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8877
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8877/health || exit 1

# --preload: load the app once in the master so config + token are initialized
# before forking workers (all workers share the same token file and config).
ENTRYPOINT ["gunicorn", "himalaya_web:app", "--bind", "0.0.0.0:8877", "--workers", "2", "--preload"]
