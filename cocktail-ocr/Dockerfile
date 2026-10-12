# Self-contained local deployment: docker build -t cocktail-finder . && \
#   docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/data:/data" cocktail-finder
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
ENV CF_DATA_DIR=/data CF_FRONTEND_DIR=/app/frontend/dist CF_WORKERS=2
RUN useradd -m app && mkdir -p /data && chown app /data
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
