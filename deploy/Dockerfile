# Запасной способ запустить ADAM НЕ на Railway: свой сервер (VPS), Fly.io, Render или компьютер с Docker.
#
# Railway этот файл не использует: он лежит в deploy/, а не в корне проекта, поэтому сборка на Railway
# остаётся прежней (Railpack + Procfile). Не переносите его в корень, иначе Railway переключится на Docker.
#
#   docker build -f deploy/Dockerfile -t adam .
#   docker run -d --name adam --restart unless-stopped -p 8080:8080 -v adam-data:/data --env-file .env adam
#
# .env с BOT_TOKEN, OPENAI_API_KEY, GROQ_API_KEY, ADMIN_ID, WEBAPP_URL (адрес, по которому приложение открывается
# из Telegram, обязательно https) в образ НЕ попадает: он исключён в deploy/Dockerfile.dockerignore и передаётся
# при запуске. База лежит в томе /data (RAILWAY_VOLUME_MOUNT_PATH): образ можно пересобирать, база остаётся.
# Как поднять базу из копии на новом сервере — RESTORE.md. Бот работает на long polling: два экземпляра с одним
# BOT_TOKEN одновременно запускать нельзя — старый нужно остановить.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    RAILWAY_VOLUME_MOUNT_PATH=/data \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=40s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=8)"

CMD ["python", "main.py"]
