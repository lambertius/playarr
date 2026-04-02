# ── Playarr Backend ──
FROM python:3.12-slim AS backend

WORKDIR /app

# Install ffmpeg and other system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 6969

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6969"]


# ── Playarr Frontend ──
FROM node:20-alpine AS frontend-build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build


# ── Nginx to serve frontend + proxy API ──
FROM nginx:alpine AS frontend

COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
