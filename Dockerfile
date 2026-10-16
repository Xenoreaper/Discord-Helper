FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config_store.py emoji_utils.py config.json ./
COPY cogs ./cogs
COPY webapp ./webapp

# Create the runtime user with a UID/GID that matches the host owner of the
# bind-mounted config.json, so the bot can write it. Override at build time:
#   docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
ARG UID=1000
ARG GID=1000
RUN if ! getent group "${GID}" >/dev/null; then groupadd --gid "${GID}" appuser; fi \
 && useradd --create-home --uid "${UID}" --gid "${GID}" appuser \
 && chown -R appuser:appuser /app
USER appuser

CMD ["python", "bot.py"]
