FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY agent ./agent
COPY config ./config
COPY engines ./engines
COPY financial_agent ./financial_agent
COPY knowledge_base ./knowledge_base
COPY mcp_servers ./mcp_servers
COPY skills ./skills
COPY storage ./storage
COPY workers ./workers

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
