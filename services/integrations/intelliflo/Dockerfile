# syntax=docker/dockerfile:1
# Intelliflo integration engine. Build context is the REPO ROOT, same as services/sei and
# services/seccl:
#   docker build -f services/integrations/intelliflo/Dockerfile --target scheduler -t intelliflo-engine .
#
# Ported from sei_reports' src/integrations + Dockerfile_Integrations (branch
# feat/porting-integrations-from-platform) — three deployables (scheduler / tenant-sync / webhook)
# from one package, unlike sei_sync's single `interlace serve` process. No workspace-internal
# dependency (pkgs/*): everything this service needs is a plain PyPI package, so the builder stage
# only needs the root lock and this member's own tree.

# ---- builder: resolve deps into a venv with uv -----------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY services/integrations/intelliflo services/integrations/intelliflo

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --project services/integrations/intelliflo

# ---- runtime base: same python base, so the venv's interpreter path is valid ----
FROM python:3.13-slim-bookworm AS runtime
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app/services/integrations/intelliflo
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app

# ---- Scheduler ----
FROM runtime AS scheduler
CMD ["intelliflo-engine"]

# ---- Tenant sync ----
FROM runtime AS tenant-sync
CMD ["intelliflo-tenant-sync"]

# ---- Webhook ----
FROM runtime AS webhook
EXPOSE 4000
CMD ["intelliflo-engine-webhook"]
