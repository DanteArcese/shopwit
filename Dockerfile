FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_NO_DEV=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY . .
EXPOSE 8000
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]
