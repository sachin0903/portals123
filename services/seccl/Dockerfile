# syntax=docker/dockerfile:1
# Seccl sync (interlace pipeline + webhook gateway; pick the process via the
# compose service's `command`). Build context is the REPO ROOT:
#   docker build -f services/seccl/Dockerfile -t seccl-sync .

# ---- builder: resolve deps into a venv with uv ----------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# uv workspace root (root pyproject + lock), this member, and EVERY workspace package it depends
# on — each is a `{ workspace = true }` source, so uv installs it from source and the tree has to
# be here. Keep this list in step with `services/seccl/pyproject.toml`'s `[tool.uv.sources]`:
# `uv sync --frozen` fails to resolve a member whose tree is absent, so a package added there and
# not here breaks the image build and nothing else. That is exactly how it broke — `pkgs/seccl`
# and `pkgs/book-sink` were added to the pyproject after this file was last touched, leaving a
# Dockerfile that could not build while every gate stayed green.
COPY pyproject.toml uv.lock ./
COPY pkgs/api-log pkgs/api-log
COPY pkgs/book-sink pkgs/book-sink
COPY pkgs/seccl pkgs/seccl
COPY services/seccl services/seccl

# --frozen: fail if the lock is out of date. --no-dev: runtime deps only.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --project services/seccl

# ---- runtime: same python base, so the venv's interpreter path is valid ----
FROM python:3.13-slim-bookworm AS runtime
# --create-home matters: `useradd --system` writes the passwd entry (home /home/app) WITHOUT
# creating the directory, and DuckDB needs one to install/cache its extensions -- interlace's
# connect_ducklake INSTALLs them when it opens the warehouse, failing with "IO Error: Can't
# find the home directory at '/home/app'" during the startup lifespan.
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app/services/seccl
COPY --from=builder --chown=app:app /app /app

# interlace's control plane (state.db + streams.db) lives here, and compose mounts the
# persistent `seccl-warehouse` volume over it. The directory MUST exist in the image and be
# owned by `app`: Docker initialises an empty named volume from the image's directory,
# ownership included, but creates the mount point ROOT-owned when the image has no such path
# -- and this process is non-root, so interlace's first sqlite3.connect dies with "unable to
# open database file" before serving a request. It used to exist by accident: `.interlace/` is
# git-ignored but was not dockerignored, so a developer's own state came in with the source
# and a clean clone (what CI and Coolify build) had none. Hence a deploy that failed where the
# laptop worked. Created explicitly, so it no longer depends on what is lying around.
RUN install -d -o app -g app /app/services/seccl/.interlace

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8100 8101

# Default: the pipeline daemon. Compose overrides this command for the gateway.
CMD ["python", "-m", "book_sink.startup_run", "--host", "0.0.0.0", "--port", "8100", "--allow-open", "--no-apply"]
