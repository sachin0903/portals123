# syntax=docker/dockerfile:1
# Platform portal — tenant staff (advisors, compliance, paraplanners). SvelteKit,
# adapter-node. Build context is the REPO ROOT (pnpm workspace):
#   docker build -f apps/platform/Dockerfile -t graphene-platform .

# ---- builder: install the workspace and build the app ---------------------
FROM node:24-slim AS builder
ENV PNPM_HOME=/pnpm PATH="/pnpm:$PATH" LEFTHOOK=0
RUN corepack enable
WORKDIR /app

# Manifests first so `pnpm install` caches unless a package.json changes.
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

# Source for just the platform app and the workspace packages it depends on — NOT `COPY . .`.
# The build context is the repo root, so a blanket copy makes this layer's cache hash depend on
# every file in the monorepo: an api/ or services/ change would bust it and force a full
# platform rebuild despite nothing here changing. Scoping the copy to platform's actual
# dependency set means this layer (and the build below) only invalidates when one of THESE
# paths does.
COPY pkgs/config pkgs/config
COPY pkgs/auth pkgs/auth
COPY pkgs/rum pkgs/rum
COPY pkgs/ui pkgs/ui
COPY pkgs/api-client pkgs/api-client
COPY apps/platform apps/platform
RUN pnpm --filter @graphene/platform build

# Produce a self-contained, production-only deployment (build/ + prod node_modules
# with workspace deps copied in, not symlinked). `--legacy` is required because
# pnpm v10+ otherwise refuses to deploy non-injected workspaces.
RUN pnpm --filter @graphene/platform deploy --prod --legacy /out

# ---- runtime: just Node + the built server --------------------------------
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

# adapter-node honours PORT/HOST. Set ORIGIN at runtime to the public URL or
# SvelteKit will reject form POSTs (CSRF origin check). See infrastructure/README.md.
CMD ["node", "build"]
