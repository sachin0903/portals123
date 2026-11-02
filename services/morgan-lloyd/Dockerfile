# syntax=docker/dockerfile:1
# Morgan Lloyd sync (interlace pipeline). Build context is the REPO ROOT:
#   docker build -f services/morgan-lloyd/Dockerfile -t morgan-lloyd-sync .
#
# One process: ML is poll-only (Dataverse OData + custom actions), so there is no gateway and no
# drop to fetch. The process is `interlace serve --no-apply`, then one full `POST /runs`.

# ---- builder: resolve deps into a venv with uv ----------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

# uv workspace root (root pyproject + lock), this member, and EVERY workspace package in its
# dependency closure — each is a `{ workspace = true }` source, so uv installs it from source and
# the tree has to be here. `morgan-lloyd` and `book-sink` both pull `api-log`. Keep in step with
# the `[tool.uv.sources]` of each: `uv sync --frozen` fails to resolve a member whose tree is
# absent, which breaks the image build and nothing else.
COPY pyproject.toml uv.lock ./
COPY pkgs/api-log pkgs/api-log
COPY pkgs/book-sink pkgs/book-sink
COPY pkgs/morgan-lloyd pkgs/morgan-lloyd
COPY services/morgan-lloyd services/morgan-lloyd

# --frozen: fail if the lock is out of date. --no-dev: runtime deps only — which is why
# `book-sink` must be a runtime dependency of this service and not a dev one: `models/tenants.py`
# imports it at module load, and interlace discovers models by importing them.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --project services/morgan-lloyd

# ---- runtime: same python base, so the venv's interpreter path is valid ----
FROM python:3.13-slim-bookworm AS runtime
# --create-home matters: `useradd --system` writes the passwd entry (home /home/app) WITHOUT
# creating the directory, and DuckDB needs one to install/cache its extensions -- interlace's
# connect_ducklake INSTALLs them when it opens the warehouse, failing with "IO Error: Can't
# find the home directory at '/home/app'" during the startup lifespan.
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app/services/morgan-lloyd
COPY --from=builder --chown=app:app /app /app

# interlace's control plane (state.db + streams.db) lives here, and compose mounts a persistent
# volume over it. The directory MUST exist in the image and be owned by `app`: Docker initialises
# an empty named volume from the image's directory, ownership included, but creates the mount
# point ROOT-owned when the image has no such path -- and this process is non-root, so interlace's
# first sqlite3.connect dies with "unable to open database file" before serving a request.
RUN install -d -o app -g app /app/services/morgan-lloyd/.interlace

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8102

CMD ["python", "-m", "book_sink.startup_run", "--host", "0.0.0.0", "--port", "8102", "--allow-open", "--no-apply"]
