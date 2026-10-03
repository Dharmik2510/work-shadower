# One image for the whole backend: API + web SPA, and the worker (different command).
# Build from the repo root:  docker build -f infra/Dockerfile -t workshadower .
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY server/requirements.txt .
RUN pip install -r requirements.txt
COPY server/app ./app
COPY server/migrations ./migrations
COPY --from=web /web/dist ./web
RUN useradd --system --uid 10001 --home /app appuser \
 && mkdir -p /app/data/assets && chown -R appuser /app/data
USER appuser
ENV WEB_DIST_DIR=/app/web LOCAL_STORAGE_DIR=/app/data/assets PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz',timeout=4)" || exit 1
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
