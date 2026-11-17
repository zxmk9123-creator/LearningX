# Northflank combined service and worker use the SAME reviewed container image.
# Build context must be /backend, Dockerfile location /backend/Dockerfile.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NIT_HOSTED_RUNTIME=1 \
    NIT_ENGINE_ROOT=/app/nit_engine \
    PORT=8080

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# No course uploads or signing material are included in this image.
COPY . /app
RUN test -f /app/nit_engine/scripts/ingest.py \
    && test -f /app/nit_engine/scripts/verified_coaching.py \
    && test -f /app/nit_engine/runtime/coaching.py \
    && python -m compileall -q /app/learningx /app/nit_engine \
    && groupadd --gid 10001 learningx \
    && useradd --uid 10001 --gid 10001 --no-create-home learningx

USER 10001:10001
EXPOSE 8080

# API service: override CMD on a second Northflank service for the DB job poller:
# python -m learningx.northflank_worker
CMD ["uvicorn", "learningx.api:app", "--host", "0.0.0.0", "--port", "8080"]
