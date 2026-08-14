import asyncio
from webapp.server import run_webapp

async def main():
    print("Запускаем...")
    await run_webapp(8080)
    print("Сервер запущен.")
    await asyncio.Event().wait()

asyncio.run(main())
