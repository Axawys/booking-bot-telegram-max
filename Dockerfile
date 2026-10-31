FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY core ./core
COPY telegram_bot ./telegram_bot

RUN useradd --create-home --uid 1000 bot
USER bot

CMD ["python", "-m", "telegram_bot"]
