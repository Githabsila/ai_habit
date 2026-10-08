"""
db.get_users_needing_ai_welcome_nudge (coach.py::run_ai_welcome_nudge) —
просьба пользователя: если человек за первые минуты после регистрации
так и не написал ADAM ни разу, через 4-5 минут прислать в бота
приглашение начать диалог. Окно 4-30 минут после created_at, и ТОЛЬКО
если ai_intro_shown ещё не выставлен.

Верхняя граница (30 минут) не даёт при деплое фичи задеть всех
исторических пользователей с ai_intro_shown=0 — только реально свежие
регистрации.
"""
from datetime import datetime, timedelta, timezone

from db import add_user as _add_user, claim_ai_first_message, ban_user, set_access_status
from db.core import connect
from db.ai import get_users_needing_ai_welcome_nudge

from tests.conftest import sign_init_data



def add_user(uid, username, first_name):
    """Рассылки получают только прошедшие анкету (db/push_access.py) — в этих тестах пользователь «свой»."""
    _add_user(uid, username, first_name)
    set_access_status(uid, "approved")

def _backdate(telegram_id, minutes_ago):
    conn = connect()
    created = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)).isoformat(sep=" ", timespec="seconds")
    conn.execute("UPDATE users SET created_at=? WHERE telegram_id=?", (created, telegram_id))
    conn.commit()
    conn.close()


def test_excludes_users_registered_too_recently(uid):
    add_user(uid, "u", "Test")
    _backdate(uid, minutes_ago=2)
    assert uid not in get_users_needing_ai_welcome_nudge()


def test_includes_users_in_the_target_window(uid):
    add_user(uid, "u", "Test")
    _backdate(uid, minutes_ago=5)
    assert uid in get_users_needing_ai_welcome_nudge()


def test_excludes_users_registered_too_long_ago(uid):
    """Защита от разовой массовой рассылки всем историческим пользователям
    при первом деплое этой фичи."""
    add_user(uid, "u", "Test")
    _backdate(uid, minutes_ago=60)
    assert uid not in get_users_needing_ai_welcome_nudge()


def test_excludes_users_who_already_wrote_to_ai(uid):
    add_user(uid, "u", "Test")
    _backdate(uid, minutes_ago=5)
    claim_ai_first_message(uid)
    assert uid not in get_users_needing_ai_welcome_nudge()


def test_excludes_banned_users(uid):
    add_user(uid, "u", "Test")
    _backdate(uid, minutes_ago=5)
    ban_user(uid)
    assert uid not in get_users_needing_ai_welcome_nudge()


# ------------------- Бейдж непрочитанного на кнопке ИИ (app.js) -------------------
# Красная точка на кнопке ИИ в Mini App показывается по user.ai_intro_shown
# из /api/bootstrap (см. webapp_server.py::_shape_user, app.js::renderPlayerCard).

async def test_bootstrap_exposes_ai_intro_shown(client, uid):
    add_user(uid, "u", "Test")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}

    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["user"]["ai_intro_shown"] is False

    claim_ai_first_message(uid)

    r2 = await client.get("/api/bootstrap", headers=headers)
    body2 = await r2.json()
    assert body2["user"]["ai_intro_shown"] is True
