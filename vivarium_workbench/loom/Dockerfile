# bigraph-loom is a read-only static React viewer (Vite build → bigraph_loom/_dist).
# This image builds that bundle and serves it with nginx. Render's Docker runtime
# injects $PORT at runtime; the nginx config template binds to it via envsubst.

# ── Stage 1: build the static viewer bundle ──────────────────────────────────
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build          # tsc -b && vite build → bigraph_loom/_dist

# ── Stage 2: serve the static bundle ─────────────────────────────────────────
FROM nginx:alpine
# Render overrides PORT; this default lets the image run locally too.
ENV PORT=8080
# Only substitute ${PORT} so nginx's own $uri etc. are left untouched.
ENV NGINX_ENVSUBST_FILTER=PORT
# The stock nginx entrypoint runs envsubst on /etc/nginx/templates/*.template
# before launching, then execs the default CMD — so we don't override ENTRYPOINT.
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/bigraph_loom/_dist /usr/share/nginx/html
EXPOSE 8080
