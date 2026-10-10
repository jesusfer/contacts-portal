FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    SQLITE_PATH=/app/data/db.sqlite3 \
    MEDIA_ROOT=/app/media
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 portal \
    && useradd --uid 10001 --gid portal --no-create-home portal
COPY manage.py ./
COPY config/ config/
COPY crm/ crm/
COPY --from=frontend /build/frontend/dist/ frontend/dist/
COPY docker-entrypoint.sh ./
RUN mkdir -p data media staticfiles \
    && chown -R portal:portal /app \
    && chmod +x docker-entrypoint.sh
USER portal
RUN DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
    python manage.py collectstatic --noinput
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "--no-control-socket"]
