# syntax=docker/dockerfile:1.7
# One image for the API + review UI, the weekly scheduler and one-off CLI runs.
#   docker build -t paindisc:local .
#   docker run --rm -p 127.0.0.1:8000:8000 -e LLM_MOCK=true paindisc:local
# Secrets (ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL, ...) are passed at run time, never baked in.

ARG PYTHON_IMAGE=python:3.12-slim-trixie
ARG NODE_IMAGE=node:24-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.29

# ---------------------------------------------------------------- review UI (static build)
FROM ${NODE_IMAGE} AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- uv binary
FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- supercronic (cron for containers), pinned + checksummed
FROM ${PYTHON_IMAGE} AS supercronic
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA256_AMD64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_ARM64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94
RUN set -eu; \
    case "${TARGETARCH:-amd64}" in \
      amd64) sum="${SUPERCRONIC_SHA256_AMD64}" ;; \
      arm64) sum="${SUPERCRONIC_SHA256_ARM64}" ;; \
      *) echo "no pinned supercronic checksum for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/supercronic')" \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64}"; \
    echo "${sum}  /supercronic" | sha256sum -c -; \
    chmod 0755 /supercronic

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PAINDISC_HOME=/app \
    PAINDISC_FRONTEND_DIST=/app/frontend/dist \
    PAINDISC_API_PORT=8000 \
    PATH=/app/.venv/bin:$PATH

COPY --from=supercronic /supercronic /usr/local/bin/supercronic

RUN groupadd --system --gid 10001 paindisc \
 && useradd --system --uid 10001 --gid paindisc --home-dir /app --shell /usr/sbin/nologin paindisc

WORKDIR /app

# Dependencies first (cached until uv.lock changes), then the project itself. uv is mounted for the build
# only, so it isn't in the final image; scripts call `paindisc` from the venv on PATH.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

COPY config/ config/
COPY prompts/ prompts/
COPY scripts/ scripts/
# Synthetic fixtures so `paindisc demo` works inside the container (no real data).
COPY tests/fixtures/demo_docs.jsonl tests/fixtures/demo_docs.jsonl
COPY --from=frontend /build/frontend/dist frontend/dist

# data/ and reports/ are the only writable paths; mount volumes there.
RUN mkdir -p data reports \
 && chown paindisc:paindisc data reports \
 && chmod 0755 scripts/*.sh

USER paindisc:paindisc
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, sys, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PAINDISC_API_PORT', '8000'), timeout=4); sys.exit(0)" || exit 1

# 0.0.0.0 inside the container; publish it on 127.0.0.1 only (see docker-compose.yml, docs/deployment.md).
CMD ["sh", "-c", "exec paindisc serve --host 0.0.0.0 --port \"${PAINDISC_API_PORT:-8000}\""]
