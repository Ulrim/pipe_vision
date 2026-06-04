# ===== AIVIS hmi frontend (React + Vite -> nginx static serving) =====
# Owned by devops. Application source (package.json, src) is owned by the
# dashboard-frontend agent and assumed filled in later.

# ---------- build stage ----------
FROM node:20-alpine AS build
WORKDIR /app

# Install deps first for layer caching. Supports npm/pnpm/yarn lockfiles if present.
COPY package*.json ./
RUN npm ci || npm install

# Build the static bundle (Vite -> /app/dist).
COPY . .
RUN npm run build

# ---------- runtime stage ----------
FROM nginx:1.27-alpine AS runtime

# SPA routing + gzip config.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static assets produced by the build stage.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -qO- http://localhost/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
