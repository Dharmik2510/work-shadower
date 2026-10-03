# Work Shadower API + worker (same image, two entrypoints).
#   API:    docker run ... image                       (default CMD)
#   Worker: docker run ... image python -m app.worker
# To serve the web SPA from the same container, copy its build into /app/web and set WEB_DIST_DIR=/app/web.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations

RUN useradd --system --uid 10001 --home /app appuser \
 && mkdir -p /app/data/assets && chown -R appuser /app/data
USER appuser

ENV LOCAL_STORAGE_DIR=/app/data/assets PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz',timeout=4)" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
