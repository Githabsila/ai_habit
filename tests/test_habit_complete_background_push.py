"""
Жалоба пользователя: отклик на выполнение привычки приходит с задержкой
около секунды. Одна из причин — запрос /complete ждал, пока бот отправит
поздравление в Telegram, хотя клиенту это сообщение для ответа не нужно.
Теперь отправка уходит в фон: ответ приходит сразу, сообщение — следом.
"""
import asyncio

from db import add_user

from tests.conftest import sign_init_data


class SlowBot:
    """send_message не завершится, пока тест сам не разрешит."""

    def __init__(self):
        self.sent = []
        self.release = asyncio.Event()

    async def send_message(self, chat_id, text, **kwargs):
        await self.release.wait()
        self.sent.append((chat_id, text))


async def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


async def test_complete_does_not_wait_for_telegram_push(client, uid):
    add_user(uid, "u", "Test")
    bot = SlowBot()
    client.app["bot"] = bot
    headers = await _headers(uid)
    r = await client.post("/api/habits", headers=headers, json={"title": "Пить воду"})
    habit_id = (await r.json())["habit"]["id"]

    # Если бы маршрут ждал отправки, этот запрос завис бы навсегда (release
    # ещё не выставлен) и тест упал бы по таймауту.
    r2 = await asyncio.wait_for(client.post(f"/api/habits/{habit_id}/complete", headers=headers), timeout=5)
    assert r2.status == 200
    assert (await r2.json())["habit"]["completed"] is True
    assert bot.sent == []  # ответ уже получен, а сообщение ещё "в пути"

    bot.release.set()
    for _ in range(20):
        if bot.sent:
            break
        await asyncio.sleep(0.01)

    # Сообщение всё равно доходит — просто не блокирует ответ.
    assert bot.sent, "поздравление первой победы должно уйти в фоне"
    assert bot.sent[0][0] == uid
