"""
Карта сообщений, прогон сценария и журнал отправок (11.11, просьба владельца «видеть все сообщения бота — когда, почему, при каких
условиях — и перепроверить путь триала, не ожидая 3 дня»).
"""
import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot_messages_map
import bot_test_runs
from db import add_user, outbox
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(bot_test_runs, "PAUSE_SECONDS", 0)


def _job_sources():
    """«модуль.функция» каждой рассылки, которую main.py ставит в планировщик и которой передаётся бот."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    imports = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module
    jobs = re.findall(r"add_job\(\s*(\w+)[^)]*args=\[bot\]", src)
    return {f"{imports[j]}.{j}" for j in jobs if j in imports}


def test_every_bot_job_is_on_the_map():
    on_map = {s for m in bot_messages_map.MAP for s in m.sources}
    missing = sorted(_job_sources() - on_map - set(bot_messages_map.SILENT_JOBS))
    assert not missing, f"Рассылка не на карте сообщений (bot_messages_map.py): {missing}"


def test_map_previews_point_to_real_messages():
    import bot_catalog
    import trial_messages
    for m in bot_messages_map.MAP:
        if m.preview:
            kind, key = m.preview
            assert key in (bot_catalog.BY_KEY if kind == "bot" else trial_messages.BY_KEY), m.key


@pytest.mark.parametrize("scenario", [s.key for s in bot_test_runs.SCENARIOS])
async def test_every_scenario_step_renders_with_real_code(scenario, uid):
    add_user(uid, f"a{uid}", "Аня")
    bot = SimpleNamespace(send_message=AsyncMock())
    result = await bot_test_runs.run(bot, uid, scenario)
    assert result["failed"] == [], result
    assert result["sent"] == len(bot_test_runs.BY_KEY[scenario].steps)
    assert all(call.args[0] == uid for call in bot.send_message.await_args_list), "прогон уходит только самому админу"


async def test_outbox_records_who_sent_what():
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from bot_middlewares import log_outbox

    class Session(BaseSession):
        async def make_request(self, bot, method, timeout=None):
            return True

        async def stream_content(self, *a, **k):
            pass

        async def close(self):
            pass

    bot = Bot("123:abc", session=Session())
    bot.session.middleware(log_outbox)

    async def run_habit_checkpoint_12():
        await bot.send_message(870_555_001, "Точка дня")
    await run_habit_checkpoint_12()
    await bot.send_message(-100123, "в группу — не пишем")
    rows = outbox.for_user(870_555_001)
    assert rows and rows[0]["text"] == "Точка дня" and rows[0]["ok"] == 1
    assert rows[0]["source"].endswith("run_habit_checkpoint_12")
    assert outbox.for_user(-100123) == []


def test_journal_finds_the_mailing_by_its_function():
    hit = bot_messages_map.find("coach._run_habit_checkpoint < coach.run_habit_checkpoint_12")
    assert hit is not None and hit.key == "checkpoint_12"
    assert bot_messages_map.find("handlers.habits.done") is None


async def test_admin_routes(client, uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid])
    add_user(uid, f"a{uid}", "Аня")
    headers = {"Authorization": f"tma {sign_init_data(uid)}", "Content-Type": "application/json"}
    data = await (await client.get("/api/admin/bot-map", headers=headers)).json()
    assert len(data["messages"]) == len(bot_messages_map.MAP) and data["scenarios"]
    outbox.record(uid, "sendMessage", "coach.run_habit_checkpoint_12", "coach.run_habit_checkpoint_12", "Точка дня")
    items = (await (await client.get(f"/api/admin/user/{uid}/outbox", headers=headers)).json())["items"]
    assert items[0]["title"] == "Точка дня 12:00"
    fake = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = fake
    r = await client.post("/api/admin/bot-map/run", headers=headers, json={"scenario": "d3_last"})
    assert r.status == 200 and (await r.json())["failed"] == []
    regular = {"Authorization": f"tma {sign_init_data(uid + 1)}"}
    add_user(uid + 1, "u", "U")
    assert (await client.get("/api/admin/bot-map", headers=regular)).status == 403
