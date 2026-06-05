# ===== AIVIS hmi frontend (React + Vite -> nginx static serving) =====
# Owned by devops. Built from the MONOREPO ROOT context (npm workspaces) so the
# build can resolve the @aivis/shared-types workspace package
# (packages/shared-types/ts). See docker-compose.yml: build.context: .
#
# Application source (package.json, src) is owned by the hmi-frontend agent.

# ---------- build stage ----------
FROM node:20-alpine AS build
WORKDIR /repo

# Workspace manifests first for layer caching (lockfile-aware install).
COPY package.json package-lock.json ./
COPY apps/hmi/package.json apps/hmi/
COPY apps/dashboard/package.json apps/dashboard/
COPY packages/shared-types/ts/package.json packages/shared-types/ts/

# Install the whole workspace deterministically (hoisted node_modules).
RUN npm ci

# Sources needed to build the hmi bundle: the app + the shared-types package.
COPY packages/shared-types/ts/ packages/shared-types/ts/
COPY apps/hmi/ apps/hmi/

# Build the static bundle (Vite -> apps/hmi/dist).
RUN npm run build --workspace @aivis/hmi

# ---------- runtime stage ----------
FROM nginx:1.27-alpine AS runtime

# SPA routing + gzip + /healthz config.
COPY apps/hmi/nginx.conf /etc/nginx/conf.d/default.conf

# Static assets produced by the build stage.
COPY --from=build /repo/apps/hmi/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://localhost/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
