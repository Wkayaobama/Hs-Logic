# syntax=docker/dockerfile:1
# Single-container image for Cloud Run: FastAPI serves /api/* and the built SPA.
# Local dev keeps docker-compose.yml (backend/Dockerfile + frontend/Dockerfile).

FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
# settings.static_dir defaults to "static", resolved against /app.
COPY --from=web /web/dist ./static
EXPOSE 8080
# Cloud Run injects PORT; shell form so the variable expands. exec keeps
# uvicorn as PID 1 for clean SIGTERM handling.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
