# One image for the web UI, the worker and the scheduled tasks; docker-compose.yml picks the command.
# Python is pinned here, not taken from the server: OS or Python changes on the host cannot affect the app.
FROM python:3.14.5-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# Exact library versions first (cached layer), then the app itself without re-resolving dependencies
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml ./
COPY submissions ./submissions
# Editable install: code runs from /app, where migrate.py expects ../migrations next to the package
RUN pip install --no-deps -e .

# Read at runtime from /app: tenant mappings and SQL migrations
COPY clients ./clients
COPY migrations ./migrations

RUN useradd --system --uid 10001 app
USER app

EXPOSE 8000
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "submissions.web:create_app()"]
