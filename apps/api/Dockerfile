# syntax=docker/dockerfile:1
# FastAPI backend. Build context is the REPO ROOT (uv workspace lives there):
#   docker build -f apps/api/Dockerfile -t graphene-api .

# ---- builder: resolve deps into a venv with uv ----------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# uv workspace root (root pyproject + lock), the api member, and the four workspace
# packages it depends on. Those are `{ workspace = true }` sources, so uv installs them
# FROM SOURCE (editable) and needs the trees present -- with only the manifests here the
# sync dies at "Distribution not found at: file:///app/pkgs/api-log", exit 2. They were
# implicit members of the shared venv until apps/api declared them properly, which is why
# this file got away with copying just the api member for so long.
COPY pyproject.toml uv.lock ./
COPY pkgs/api-log pkgs/api-log
COPY pkgs/baton pkgs/baton
COPY pkgs/intelliflo pkgs/intelliflo
COPY pkgs/morgan-lloyd pkgs/morgan-lloyd
COPY pkgs/sei pkgs/sei
COPY pkgs/seccl pkgs/seccl
# Includes external/static/scalar-api-reference.js (Scalar 1.71.0). That path is
# served at /docs; do not move the file under dist/ (.dockerignore drops **/dist).
COPY apps/api apps/api

# --frozen: fail if the lock is out of date. --no-dev: runtime deps only.
# (Split this into a manifest-only "uv sync --no-install-project" step first
#  if you want dependency-layer caching across source changes.)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --project apps/api

# Ship the small, anonymised Seccl lake fixture so lake.py's default path
# (/app/docs/seccl.duckdb) resolves in the container. Copied after `uv sync` so
# refreshing the fixture doesn't bust the dependency layer. Real data is served
# from a volume via LAKE_SECCL_PATH instead, never baked into an image.
COPY docs/seccl.duckdb docs/seccl.duckdb

# ---- runtime: same python base, so the venv's interpreter path is valid ----
FROM python:3.13-slim-bookworm AS runtime
# --create-home matters: `useradd --system` writes the passwd entry (home /home/app) WITHOUT
# creating the directory, and DuckDB refuses to work without one -- app/lake/attach.py runs
# `INSTALL ducklake/postgres/httpfs` on every tenant attach, and with no home it raises
# "IO Error: Can't find the home directory at '/home/app'". So every in-process lake read
# (account holdings, cash, valuations) failed in the container while passing on a dev box,
# where the developer has a home. It is also where DuckDB caches the extensions it downloads.
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

# WeasyPrint's native stack (Pango / Cairo / GLib / gdk-pixbuf) + fonts — the same set as
# Dockerfile.worker. The API renders the true-to-generation PDF preview inline
# (POST /templates/{uid}/preview.pdf via app.render.renderers), so it now needs the render engine's
# native libs, not just the Python package.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi8 \
    fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# The firm API is this same image. docker-compose runs it as the `external` service with
# `python -m external` (working directory apps/api) on 8010. This default command stays the
# portal API.
EXPOSE 8010

# Migrations are NOT run here — the deploy pipeline runs them (with the elevated
# role) before this image is released. See infrastructure/README.md.
# The same SSE connections that hang a local reload hang a deploy: SIGTERM waits forever
# for them to close unless this timeout is set. Two seconds is the local figure; five leaves
# an in-flight request room before the streams are cancelled.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "apps/api", "--timeout-graceful-shutdown", "5"]
