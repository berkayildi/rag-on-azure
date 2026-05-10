# syntax=docker/dockerfile:1.7
#
# Two-stage build for the rag_on_azure FastAPI service.
#
# Build context is the repo root; the Dockerfile lives at app/Dockerfile
# (per docs/design/rag-on-azure.md §1) and references files via app/...
# paths so .dockerignore can prune everything outside app/.
#
# Stage rationale:
#   builder  — pip-installs the package + deps to /install. BuildKit
#              cache-mount on /root/.cache/pip avoids re-downloading
#              wheels across builds without baking the cache into the
#              final image.
#   runtime  — python:3.12-slim-bookworm + /install copied over. No pip,
#              no caches, no source tree (the package is in site-packages
#              after install). Runs as a non-root user.
#
# Image size target ~450MB uncompressed; measured and reported in the
# Phase E close commit.

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Both packages installed: app for the FastAPI surface, ingest because
# Phase 5's POST /ingest route imports the corpus pipeline directly and
# schedules it as a background task. .dockerignore admits ingest/src and
# ingest/pyproject.toml; ingest/tests and ingest/.cache stay excluded.
COPY app/pyproject.toml ./app/pyproject.toml
COPY app/src ./app/src
COPY ingest/pyproject.toml ./ingest/pyproject.toml
COPY ingest/src ./ingest/src

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install ./app ./ingest


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INGEST_MANIFEST_PATH=/opt/rag-on-azure/corpus_manifest.yaml

# Non-root user. uid 1001 is conventional for app containers and avoids
# colliding with system uids on host-shared filesystems if anyone ever
# bind-mounts in. Container Apps doesn't enforce non-root but it's a
# zero-cost hardening.
RUN groupadd -r app && useradd -r -g app -u 1001 -m -d /home/app app

COPY --from=builder /install /usr/local

# Bake the corpus manifest at a stable runtime path so POST /ingest can
# locate it without any operator-side configuration. INGEST_MANIFEST_PATH
# (set above) feeds rag_on_azure.settings.Settings.ingest_manifest_path.
COPY ingest/corpus_manifest.yaml /opt/rag-on-azure/corpus_manifest.yaml

USER app
WORKDIR /home/app

EXPOSE 8000

# --factory matches the local invocation we used in Phase D — no drift
# between developer-machine boot and container boot.
CMD ["uvicorn", "rag_on_azure.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
