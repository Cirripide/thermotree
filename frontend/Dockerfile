# syntax=docker/dockerfile:1.7

############################
# Stage 1 — Angular build
############################
ARG NODE_VER=22.14.0
FROM node:${NODE_VER}-alpine AS build

WORKDIR /app

# Deterministic install from lockfile. --no-audit/--no-fund silence checks
# that don't belong in image builds.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

# Production build. defaultConfiguration is already "production" in
# angular.json, but pinning it explicitly here prevents a silent flip
# if that default ever changes.
RUN npx ng build --configuration production

############################
# Stage 2 — nginx runtime
############################
# nginxinc/nginx-unprivileged ships nginx pre-configured to run as a
# non-root user (UID 101) on port 8080. Less to get wrong than chowning
# the cache, run, and log directories on plain nginx:alpine.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# The image's /docker-entrypoint.d/20-envsubst-on-templates.sh processes
# /etc/nginx/templates/*.template through envsubst at container start and
# writes the result to /etc/nginx/conf.d/. ${UPSTREAM_BACKEND} is injected
# this way — compose uses the default below; ACA overrides via env var.
USER root
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Default that keeps `docker compose up` working with the existing
# docker-compose service name. Overridden in ACA by the container app env.
ENV UPSTREAM_BACKEND=backend:8000

# Angular's @angular-devkit/build-angular:application builder emits to
# dist/<project>/browser/ (and a server/ sibling if SSR is enabled). The
# /browser suffix is load-bearing — copying dist/thermotree/ directly
# would copy the wrong files and break the SPA.
COPY --from=build /app/dist/thermotree/browser /usr/share/nginx/html

# Build stage runs as root, so files are root-owned. The unprivileged
# nginx user is UID 101.
RUN chown -R 101:101 /usr/share/nginx/html

USER 101

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --spider --quiet http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
