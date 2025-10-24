# # # syntax=docker/dockerfile:1

# # FROM python:3.11-slim

# # ENV PYTHONDONTWRITEBYTECODE=1 \
# #     PYTHONUNBUFFERED=1 \
# #     PIP_NO_CACHE_DIR=1 \
# #     PIP_DISABLE_PIP_VERSION_CHECK=1 \
# #     TZ=Asia/Baku \
# #     PYTHONPATH=/app/src          

# # WORKDIR /app

# # RUN apt-get update && apt-get install -y --no-install-recommends \
# #     ca-certificates tzdata \
# #     && rm -rf /var/lib/apt/lists/*

# # # зависимости — кэшируются по requirements.txt
# # COPY requirements.txt .
# # RUN pip install --no-cache-dir -r requirements.txt

# # # код
# # COPY . .

# # # gunicorn + uvicorn worker
# # CMD ["gunicorn", "web.server:app", "-k", "uvicorn.workers.UvicornWorker", "-c", "gunicorn.conf.py"]

# # syntax=docker/dockerfile:1.6

# FROM python:3.11-slim AS base
# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1 \
#     PIP_NO_CACHE_DIR=1 \
#     PYTHONPATH=/app/src \
#     DEBIAN_FRONTEND=noninteractive

# ENV TZ=Asia/Baku

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     curl tini build-essential tzdata \
#     && rm -rf /var/lib/apt/lists/*

# WORKDIR /app

# FROM base AS deps
# COPY requirements.txt .
# RUN pip install --upgrade pip && \
#     pip wheel --wheel-dir=/wheels -r requirements.txt

# FROM base AS runtime
# ENV PATH="/home/appuser/.local/bin:${PATH}"
# RUN useradd -m -u 10001 appuser
# USER appuser

# COPY --from=deps /wheels /wheels
# RUN pip install --user /wheels/*

# COPY . /app

# ENTRYPOINT ["/usr/bin/tini","--"]
# CMD ["gunicorn","-c","gunicorn.conf.py","web.server:app"]
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    DEBIAN_FRONTEND=noninteractive

ENV TZ=Asia/Baku

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tini build-essential tzdata ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS deps
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir=/wheels -r requirements.txt

FROM base AS runtime
ENV PATH="/home/appuser/.local/bin:${PATH}"
RUN useradd -m -u 10001 appuser
USER appuser

COPY --from=deps /wheels /wheels
RUN pip install --user /wheels/*

COPY . /app

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["gunicorn","-c","gunicorn.conf.py","web.server:app"]