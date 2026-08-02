"""
Небольшой веб-сервер, принимающий OAuth-редирект от Google.

Google не может достучаться до Telegram напрямую, поэтому нужен
публичный HTTP-адрес: пользователь жмёт кнопку в боте → экран согласия
Google → редирект сюда с кодом → мы меняем код на токены и пишем
пользователю в Telegram, что календарь подключён.

Запускается параллельно с polling бота в main.py.
"""

from aiohttp import web

from google_calendar import exchange_code, sync_habit_reminder

SUCCESS_HTML = """
<html><head><meta charset="utf-8"><title>Google Calendar подключён</title></head>
<body style="font-family: sans-serif; text-align:center; padding-top:80px;">
<h2>✅ Google Calendar подключён</h2>
<p>Можете вернуться в Telegram — бот уже прислал подтверждение.</p>
</body></html>
"""

ERROR_HTML = """
<html><head><meta charset="utf-8"><title>Ошибка</title></head>
<body style="font-family: sans-serif; text-align:center; padding-top:80px;">
<h2>❌ Не удалось подключить календарь</h2>
<p>Вернитесь в бота и попробуйте ещё раз.</p>
</body></html>
"""


def create_oauth_app(bot) -> web.Application:
    app = web.Application()

    async def oauth_callback(request: web.Request):
        code = request.query.get("code")
        state = request.query.get("state")  # telegram_id пользователя
        error = request.query.get("error")

        if error or not code or not state:
            return web.Response(text=ERROR_HTML, content_type="text/html", status=400)

        try:
            user_id = int(state)
        except ValueError:
            return web.Response(text=ERROR_HTML, content_type="text/html", status=400)

        try:
            exchange_code(code, user_id)
            sync_habit_reminder(user_id)

            try:
                await bot.send_message(
                    user_id,
                    "✅ <b>Google Calendar подключён!</b>\n\n"
                    "Каждый день в вашем календаре будет появляться событие "
                    "со списком привычек — во время, указанное в настройках "
                    "напоминаний бота.",
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"⚠️ Не удалось отправить подтверждение {user_id}: {e}")

            return web.Response(text=SUCCESS_HTML, content_type="text/html")

        except Exception as e:
            print(f"❌ Ошибка OAuth callback для {state}: {e}")
            return web.Response(text=ERROR_HTML, content_type="text/html", status=500)

    async def health(request: web.Request):
        return web.Response(text="ok")

    app.router.add_get("/oauth2callback", oauth_callback)
    app.router.add_get("/", health)

    return app


async def run_oauth_server(bot, port: int):
    app = create_oauth_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 OAuth-сервер запущен на порту {port}")
