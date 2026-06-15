# syntax=docker/dockerfile:1

# ---- Builder stage -----------------------------------------------------------
# Resolve and install the Python dependencies into an isolated prefix so the
# build toolchain and pip caches never reach the runtime image. Most wheels here
# are prebuilt (psycopg2-binary, lxml, pdfplumber, cryptography, tiktoken), but
# build-essential keeps this stage self-contained for any sdist-only transitive
# dependency. None of it is copied forward.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --prefix=/install lays the packages out under /install/{bin,lib} so they drop
# straight onto /usr/local in the runtime stage.
RUN pip install --upgrade pip \
    && pip install --prefix=/install -r requirements.txt

# ---- Runtime stage -----------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg is required to strip the audio track out of MP4 uploads before
# transcription (see app/services/text_extraction.py:_extract_video and
# app/tasks/transcribe_call.py). The same image runs backend, worker, and beat,
# so it must live in the final stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Pull the dependencies installed in the builder onto the runtime interpreter.
# /install was populated with --prefix=/install, so its tree maps 1:1 onto
# /usr/local (site-packages + console scripts like alembic and uvicorn).
COPY --from=builder /install /usr/local

# Create a non-root user up front. uvicorn binds 8000 (>1024, no privilege
# needed) and Alembic only talks to the DB, so neither the migrate initContainer
# nor the server require root. Fixed uid/gid 10001 matches the Helm
# securityContext runAsUser/fsGroup so the K8s emptyDir for /tmp is writable.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home-dir /app --no-create-home appuser

COPY . .

# Hand the whole app dir to the runtime user after the source is in place.
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
