FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FFMPEG_BIN=/usr/bin/ffmpeg
ENV FFPROBE_BIN=/usr/bin/ffprobe
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=600
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PADDLE_GPU_VERSION=3.3.1
ARG PADDLE_GPU_INDEX_URL=https://www.paddlepaddle.org.cn/packages/stable/cu130/
ARG ASR_CUBLAS_CU12_VERSION=12.9.2.10
ARG ASR_CUDNN_CU12_VERSION=9.24.0.43
ARG ASR_CUDA_RUNTIME_CU12_VERSION=12.9.79
ARG ASR_CUDA_NVRTC_CU12_VERSION=12.9.86
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:/usr/local/lib/python3.11/site-packages/nvidia/cuda_runtime/lib:/usr/local/lib/python3.11/site-packages/nvidia/cuda_nvrtc/lib:/usr/local/lib/python3.11/site-packages/nvidia/cu13/lib:${LD_LIBRARY_PATH}

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    export DEBIAN_FRONTEND=noninteractive; \
    success=0; \
    for attempt in 1 2 3 4 5; do \
        if apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
            && apt-get install -y --fix-missing --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-chi-sim; then \
            success=1; \
            break; \
        fi; \
        echo "APT install failed on attempt ${attempt}, retrying..." >&2; \
        rm -rf /var/lib/apt/lists/*; \
        sleep $((attempt * 5)); \
    done; \
    test "$success" = "1"; \
    command -v ffmpeg; \
    command -v ffprobe; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY agent ./agent
COPY config ./config
COPY engines ./engines
COPY financial_agent ./financial_agent
COPY knowledge_base ./knowledge_base
COPY mcp_servers ./mcp_servers
COPY scripts ./scripts
COPY skills ./skills
COPY storage ./storage
COPY workers ./workers

RUN pip install --no-cache-dir --timeout 600 --retries 5 -i "${PYPI_INDEX_URL}" -e . \
    && pip install --no-cache-dir --timeout 600 --retries 5 -i "${PYPI_INDEX_URL}" \
        "nvidia-cublas-cu12==${ASR_CUBLAS_CU12_VERSION}" \
        "nvidia-cudnn-cu12==${ASR_CUDNN_CU12_VERSION}" \
        "nvidia-cuda-runtime-cu12==${ASR_CUDA_RUNTIME_CU12_VERSION}" \
        "nvidia-cuda-nvrtc-cu12==${ASR_CUDA_NVRTC_CU12_VERSION}" \
    && pip install --no-cache-dir --timeout 600 --retries 5 --extra-index-url "${PYPI_INDEX_URL}" -i "${PADDLE_GPU_INDEX_URL}" "paddlepaddle-gpu==${PADDLE_GPU_VERSION}"

EXPOSE 8000

CMD ["sh", "-lc", "python scripts/ensure_video_runtime.py && uvicorn app.api:app --host 0.0.0.0 --port 8000"]
