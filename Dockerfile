FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY core ./core
COPY db ./db
COPY telegram_bot ./telegram_bot

# Каталог под файл SQLite создаётся заранее с нужным владельцем:
# docker-том при первом подключении унаследует эти права.
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data
USER bot

# Миграции применяются при каждом старте — это идемпотентно.
CMD ["sh", "-c", "alembic upgrade head && exec python -m telegram_bot"]
