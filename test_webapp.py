import asyncio
from db import create_tables
from webapp.webapp_server import run_webapp

async def main():
    print("Запускаем...")
    # Локальный запуск без main.py: таблицы базы иначе не создаются, и любой запрос Mini App падает с «no such table: users».
    create_tables()
    await run_webapp(8080)
    print("Сервер запущен.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
