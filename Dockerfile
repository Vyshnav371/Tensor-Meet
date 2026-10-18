# Tensor on Cloud Run: Angular build + FastAPI backend in one container (one URL for the app, API and WebSocket).
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TENSOR_STATIC_DIR=/app/static \
    TENSOR_KNOWLEDGE_DIR=/app/knowledge \
    TENSOR_COOKIE_SECURE=1
WORKDIR /app/backend
COPY backend/requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY backend/app ./app
COPY knowledge /app/knowledge
COPY --from=web /web/dist/tensor-ui/browser /app/static
RUN useradd --system --uid 10001 tensor
USER tensor
# --proxy-headers: Cloud Run terminates HTTPS; uvicorn then sees the original scheme and client address.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
