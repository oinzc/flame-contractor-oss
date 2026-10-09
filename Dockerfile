FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright FLAME_SERVE_FRONTEND=true
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN printf 'Acquire::Retries "3";\n' > /etc/apt/apt.conf.d/80-retries \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin flame \
    && mkdir -p /app/data/profiles /app/artifacts \
    && chown -R flame:flame /app/data /app/artifacts
ENV PYTHONPATH=/app/backend
COPY backend/constraints-docker.txt ./
# Apply the tested runtime baseline without invalidating the browser installation cache.
RUN pip install --no-cache-dir -c constraints-docker.txt -r requirements.txt && pip check
COPY backend/app/ ./app/
COPY backend/alembic/ ./alembic/
COPY backend/alembic.ini ./
COPY scripts/docker/entrypoint.py /app/entrypoint.py
COPY --from=frontend /build/dist /app/frontend/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import json,urllib.request; h=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)); assert h['status']=='ok' and h['database']=='ready'"
ENTRYPOINT ["python", "/app/entrypoint.py"]
