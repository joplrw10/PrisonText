FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUMPKINSYNC_DATA=/app/data \
    PUMPKINSYNC_HOST=0.0.0.0 \
    PUMPKINSYNC_PORT=8765

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE USER_MEDIA_NOTICE.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)" || exit 1

CMD ["python", "-m", "pumpkinsync", "--host", "0.0.0.0", "--no-browser"]
