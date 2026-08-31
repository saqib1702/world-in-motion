# syntax=docker/dockerfile:1

# --- Stage 1: build the frontend -------------------------------------------
# Built inside Linux rather than copying a local dist/, because @rollup/* and
# @esbuild/* ship platform-specific native binaries — a tree installed on
# Windows cannot build here. This is also why node_modules is gitignored.
FROM node:20-slim AS frontend

WORKDIR /build

# package.json + lockfile first, so the dependency layer is cached and only
# re-resolved when the manifests actually change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Same-origin in production: gunicorn serves the built assets, so no proxy and
# no CORS. The empty base makes api.js issue relative requests.
ENV VITE_API_BASE=""
# Optional: bakes a write token into the bundle so the deployed UI can trigger
# ticks. Vite inlines this at build time, which means anyone who loads the page
# can read it — only pass it for a personal or demo deployment:
#   docker build --build-arg VITE_API_TOKEN=$API_TOKEN -t world-in-motion .
# Left empty, the UI reads /meta, sees writes are gated, and renders read-only.
ARG VITE_API_TOKEN=""
ENV VITE_API_TOKEN=$VITE_API_TOKEN
RUN npm run build


# --- Stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim AS runtime

# PYTHONUNBUFFERED so container logs stream to CloudWatch instead of sitting in
# a buffer until the process exits.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY agents/    ./agents/
COPY api/       ./api/
COPY db/        ./db/
COPY engine/    ./engine/
COPY ingestion/ ./ingestion/
COPY llm/       ./llm/
COPY scripts/   ./scripts/
COPY app.py config.py ./

COPY --from=frontend /build/dist ./frontend/dist

# Non-root, because ECS tasks should not run as uid 0.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# /health returns 503 (not 200) when Mongo is unreachable, so an ALB target
# group or ECS container health check can act on it directly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status==200 else 1)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "api:create_app()"]
