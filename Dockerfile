FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_NO_DEV=1 UV_NO_SYNC=1 UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
LABEL org.opencontainers.image.source=https://github.com/DanteArcese/unitpricer
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY . .
RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput
RUN useradd --system --uid 10001 --no-create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "-c", "deploy/gunicorn.conf.py"]
