FROM --platform=linux/amd64 python:3.12-slim

WORKDIR /app

# System deps kept intentionally minimal — slim image is fine for httpx
# and uvicorn. If you swap to gunicorn/gevent you'll need build-essential.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install deps first so layer caching doesn't bust on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code + bundled data.
COPY app ./app
COPY json ./json

EXPOSE 8000

# uvicorn directly — for production you'd put gunicorn in front with
# multiple uvicorn workers. Single-worker is fine for dev/staging.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]