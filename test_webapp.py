import asyncio
from webapp.webapp_server import run_webapp

async def main():
    print("Запускаем...")
    await run_webapp(8080)
    print("Сервер запущен.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
