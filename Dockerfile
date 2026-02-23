FROM python:3.12-slim

WORKDIR /app

# ---------------------------------------------------------------------------
# System dependencies — libgomp1 is required by ONNX Runtime (fastembed).
# ---------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python dependencies (cached unless requirements.txt changes).
# ---------------------------------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Pre-download the fastembed model so it's baked into the image.
# Avoids a ~90 MB download on first scan (cold-start penalty in Fargate).
# ---------------------------------------------------------------------------
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

# ---------------------------------------------------------------------------
# Application code (changes most often — last layer for cache efficiency).
# ---------------------------------------------------------------------------
COPY *.py ./
COPY scans/ ./scans/

# Create data directory for EFS mount (trades.db will live here)
RUN mkdir -p /data

# Health check via dashboard endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/status')" || exit 1

ENTRYPOINT ["python", "scanner.py", "--continuous"]
