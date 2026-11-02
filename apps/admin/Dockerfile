# syntax=docker/dockerfile:1
# Internal tenant-admin portal (SvelteKit, adapter-node). Build context is the
# REPO ROOT (pnpm workspace):  docker build -f apps/admin/Dockerfile -t graphene-admin .
# Currently a bare skeleton (no auth) — deploy it network-locked (see infrastructure/README.md).

# ---- builder: install the workspace and build the app ---------------------
FROM node:24-slim AS builder
ENV PNPM_HOME=/pnpm PATH="/pnpm:$PATH" LEFTHOOK=0
RUN corepack enable
WORKDIR /app

COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY pkgs/config/package.json pkgs/config/
COPY pkgs/auth/package.json pkgs/auth/
COPY pkgs/rum/package.json pkgs/rum/
COPY pkgs/ui/package.json pkgs/ui/
COPY pkgs/api-client/package.json pkgs/api-client/
COPY apps/client/package.json apps/client/
COPY apps/platform/package.json apps/platform/
COPY apps/admin/package.json apps/admin/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

# Source for the admin app and the workspace packages it depends on — NOT `COPY . .`. The build
# context is the repo root, so a blanket copy makes this layer's cache hash depend on every file
# in the monorepo: an api/ or services/ change would bust it and force a full admin rebuild
# despite nothing here changing. Scoping the copy to admin's actual dependency set means this
# layer (and the build below) only invalidates when one of THESE paths does.
#
# The operate sidebar shares the platform IA through `@graphene/ui/nav` (already in `pkgs/ui`),
# not by importing `apps/platform` by relative path.
COPY pkgs/config pkgs/config
COPY pkgs/auth pkgs/auth
COPY pkgs/rum pkgs/rum
COPY pkgs/ui pkgs/ui
COPY pkgs/api-client pkgs/api-client
COPY apps/admin apps/admin
RUN pnpm --filter @graphene/admin build
# --legacy: pnpm v10+ refuses to deploy non-injected workspaces without it.
RUN pnpm --filter @graphene/admin deploy --prod --legacy /out

# ---- runtime --------------------------------------------------------------
FROM node:24-slim AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app app
COPY --from=builder --chown=app:app /out/build ./build
COPY --from=builder --chown=app:app /out/node_modules ./node_modules
COPY --from=builder --chown=app:app /out/package.json ./package.json
USER app
EXPOSE 3000
CMD ["node", "build"]
