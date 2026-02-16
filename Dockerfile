FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./
COPY scans/ ./scans/

# Create data directory for EFS mount (trades.db will live here)
RUN mkdir -p /data

# Health check via dashboard endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/status')" || exit 1

ENTRYPOINT ["python", "scanner.py", "--continuous"]
