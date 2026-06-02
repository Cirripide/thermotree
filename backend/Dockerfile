# syntax=docker/dockerfile:1.7

############################
# Stage 1 — builder
############################
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-time deps: headers + binutils are needed for any wheel that compiles
# against GDAL/PROJ (rasterio, fiona via geopandas, pyproj). NOT installed
# in the runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        binutils \
        libgdal-dev \
        libproj-dev \
        gdal-bin \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv so we can copy the whole tree to runtime with a single
# COPY --from=builder and not pollute the system Python.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

############################
# Stage 2 — runtime
############################
FROM python:3.11-slim-bookworm AS runtime

# Runtime-only system libs for Debian bookworm (GDAL 3.6 / PROJ 9):
#   libgdal32  : GDAL runtime shared library (no -dev headers).
#   libproj25  : PROJ runtime shared library (no -dev).
#   proj-data  : PROJ grid shifts; reprojection fails without it.
#   gdal-bin   : ogr2ogr / gdalinfo CLIs (kept; useful for ops debugging).
#   tini       : PID 1, forwards SIGTERM to uvicorn workers and reaps zombies.
#   ca-certificates : required for HTTPS to Photon / Nominatim / STAC.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgdal32 \
        libproj25 \
        proj-data \
        gdal-bin \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Non-root user. UID/GID 10001 keeps us well clear of host-mapped UIDs.
RUN groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

# --- GDAL / PROJ runtime config (tuned for remote COG reads) ---
# The /api/zones path is ~95% windowed COG range-reads from Planetary Computer.
# These knobs cut measured cold-read time ~25-30%:
#   DISABLE_READDIR_ON_OPEN=EMPTY_DIR - don't list the blob "directory" on open
#   HTTP_VERSION=2 / MULTIPLEX        - HTTP/2 keep-alive across range reads
#   MERGE_CONSECUTIVE_RANGES          - coalesce adjacent ranges into one GET
#   INGESTED_BYTES_AT_OPEN=32768      - prefetch the COG header (no 2nd RTT on open)
#   VSI_CACHE / GDAL_CACHEMAX         - in-process block cache, fewer re-fetches
#   HTTP_MAX_RETRY / RETRY_DELAY      - ride out transient 5xx/timeouts (was 0 retries)
#
# PROJ_LIB / GDAL_DATA are deliberately NOT set. The pip rasterio/pyproj wheels
# bundle their own (newer) proj.db; pointing them at the system PROJ data
# (the former PROJ_LIB=/usr/share/proj) triggered a version-mismatch error on
# EVERY per-scene reproject ("proj.db ... comes from another PROJ installation")
# and cost ~37% of read time. Letting the wheels self-locate removes both the
# error spam and the cost. Do NOT re-add these two vars for the Python runtime.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    GDAL_HTTP_VERSION=2 \
    GDAL_HTTP_MULTIPLEX=YES \
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES \
    GDAL_INGESTED_BYTES_AT_OPEN=32768 \
    GDAL_HTTP_MAX_RETRY=3 \
    GDAL_HTTP_RETRY_DELAY=1 \
    VSI_CACHE=TRUE \
    VSI_CACHE_SIZE=67108864 \
    GDAL_CACHEMAX=512 \
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif,.tiff,.TIF,.TIFF \
    UVICORN_WORKERS=1 \
    PORT=8000

WORKDIR /app

# Copy only the application package. .dockerignore keeps tests, venv, caches
# out of the build context entirely.
COPY --chown=app:app ./app ./app

USER app

EXPOSE 8000

# Container-internal health check. python:3.11-slim has no curl/wget by
# default; stdlib urllib keeps the image lean. Endpoint is GET / on the
# bound port (backend/app/controllers/health.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; url=f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/'; sys.exit(0 if urllib.request.urlopen(url, timeout=3).status == 200 else 1)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]

# Shell form for ${PORT}/${UVICORN_WORKERS} expansion. UVICORN_WORKERS=1
# preserves the per-process MAX_CONCURRENT_ZONE_BUILDS invariant in
# backend/app/core/config.py and keeps ZonesRunner coalescing effective.
# Scale horizontally with replicas, not by bumping workers.
# --proxy-headers + --forwarded-allow-ips='*' so request.client.host honors
# X-Forwarded-For from the nginx sidecar on the private compose network.
CMD uvicorn app.main:app \
        --host 0.0.0.0 \
        --port ${PORT} \
        --workers ${UVICORN_WORKERS} \
        --proxy-headers \
        --forwarded-allow-ips='*'
