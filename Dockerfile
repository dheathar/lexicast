# Plain CPU-only build — no CUDA base image needed. Kokoro runs fine on CPU;
# torch here is only its backend, not a GPU dependency.
#
# Backend-only image (2026-08-28): webapp.py's browser UI was stripped (the
# `lexicast` Claude Code skill is the only caller now, driving the HTTP API
# directly) and requirements trimmed to requirements-docker.txt -- the exact
# set the audiobook pipeline actually imports. This deliberately DROPS the
# ability to run session2notes.py/rag.py (transcription/diarization/RAG) via
# `docker exec` in this image -- those need the full requirements.txt and a
# separate build/venv if ever needed again; this image is audiobook-only.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    pandoc \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# CPU-only torch build explicitly, via PyTorch's own CPU wheel index -- a
# plain `pip install torch` (even on this GPU-less image) resolves the
# default CUDA build and drags in ~1GB+ of unused nvidia-*/cufft/cudnn
# wheels. This alone is most of the image-size difference between this and
# a naive requirements.txt install.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt \
    && rm -rf /root/.cache/pip

COPY . .

EXPOSE 5005

CMD ["python", "webapp.py"]
