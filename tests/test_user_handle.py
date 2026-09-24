"""
Уникальный игровой @ник (в духе Duolingo) — создаётся автоматически при
регистрации (db.users.add_user), можно сменить самому в настройках
(db.handles.update_handle), виден в рейтинге/команде/ленте друзей/
публичном профиле (см. соответствующие SELECT'ы в db/*.py).
"""
from db import (
    add_user, get_user, get_rating, clear_rating_cache, add_habit,
    get_handle, is_handle_taken, update_handle, generate_unique_handle,
    normalize_handle, HANDLE_RE,
)
from db.core import connect

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# ГЕНЕРАЦИЯ ПРИ РЕГИСТРАЦИИ
# =====================================

def test_add_user_assigns_a_handle(uid):
    add_user(uid, "somebody", "Александр")
    handle = get_handle(uid)
    assert handle
    assert HANDLE_RE.match(handle)
    # Транслитерация кириллического имени — латиница в начале ника.
    assert handle.startswith("aleksandr")


def test_two_new_users_get_different_handles(uid):
    other = uid + 1
    add_user(uid, "a", "Игорь")
    add_user(other, "b", "Игорь")
    assert get_handle(uid) != get_handle(other)


def test_add_user_is_idempotent_and_keeps_existing_handle(uid):
    add_user(uid, "a", "Игорь")
    first = get_handle(uid)
    add_user(uid, "a", "Игорь")  # повторный вызов (как при каждом /start)
    assert get_handle(uid) == first


def test_add_user_self_heals_missing_handle(uid):
    """Защита от гонки миграция/первый логин: если у уже существующей
    строки почему-то нет ника (handle=NULL), повторный add_user() должен
    сам его назначить, а не оставлять пустым навсегда."""
    add_user(uid, "a", "Игорь")
    conn = connect()
    conn.execute("UPDATE users SET handle=NULL WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    assert get_handle(uid) is None

    add_user(uid, "a", "Игорь")
    assert get_handle(uid) is not None


def test_handle_base_falls_back_for_short_or_empty_name(uid):
    add_user(uid, "a", "")
    handle = get_handle(uid)
    assert handle
    assert handle.startswith("user")


# =====================================
# db.handles — генерация/валидация напрямую
# =====================================

def test_generate_unique_handle_avoids_collision(uid):
    add_user(uid, "a", "Мария")
    taken = get_handle(uid)
    conn = connect()
    cursor = conn.cursor()
    # Достаточно попыток, чтобы почти наверняка столкнуться с "taken" на
    # каком-то из первых кандидатов, если бы проверка не работала — но
    # раз функция вообще не может вернуть "taken", просто проверяем, что
    # результат отличается и валиден.
    new_handle = generate_unique_handle(cursor, "Мария")
    conn.close()
    assert new_handle != taken
    assert HANDLE_RE.match(new_handle)


def test_normalize_handle_strips_at_and_lowercases():
    assert normalize_handle("  @Alex_007  ") == "alex_007"


# =====================================
# db.handles.update_handle
# =====================================

def test_update_handle_success(uid):
    add_user(uid, "a", "Тест")
    assert update_handle(uid, "cool_nick1") is None
    assert get_handle(uid) == "cool_nick1"


def test_update_handle_strips_at_and_lowercases(uid):
    add_user(uid, "a", "Тест")
    assert update_handle(uid, "@MyNick") is None
    assert get_handle(uid) == "mynick"


def test_update_handle_rejects_too_short(uid):
    add_user(uid, "a", "Тест")
    original = get_handle(uid)
    assert update_handle(uid, "ab") == "invalid_format"
    assert get_handle(uid) == original


def test_update_handle_rejects_invalid_characters(uid):
    add_user(uid, "a", "Тест")
    assert update_handle(uid, "bad nick!") == "invalid_format"


def test_update_handle_rejects_taken(uid):
    other = uid + 1
    add_user(uid, "a", "Тест")
    add_user(other, "b", "Тест2")
    update_handle(other, "reserved_nick")
    assert update_handle(uid, "reserved_nick") == "taken"


def test_update_handle_allows_keeping_own_handle(uid):
    add_user(uid, "a", "Тест")
    current = get_handle(uid)
    assert update_handle(uid, current) is None


def test_is_handle_taken(uid):
    add_user(uid, "a", "Тест")
    handle = get_handle(uid)
    assert is_handle_taken(handle) is True
    assert is_handle_taken(handle, exclude_user_id=uid) is False
    assert is_handle_taken("definitely_free_xyz") is False


# =====================================
# РОУТ /api/settings/handle
# =====================================

async def test_handle_route_persists(client, uid):
    add_user(uid, "a", "Тест")
    headers = await _headers(uid)
    r = await client.post("/api/settings/handle", headers=headers, json={"handle": "new_handle_1"})
    assert r.status == 200
    body = await r.json()
    assert body["handle"] == "new_handle_1"
    assert get_handle(uid) == "new_handle_1"


async def test_handle_route_rejects_invalid_format(client, uid):
    add_user(uid, "a", "Тест")
    headers = await _headers(uid)
    r = await client.post("/api/settings/handle", headers=headers, json={"handle": "x"})
    assert r.status == 400
    body = await r.json()
    assert body["error"] == "invalid_format"


async def test_handle_route_rejects_taken(client, uid):
    other = uid + 1
    add_user(uid, "a", "Тест")
    add_user(other, "b", "Тест2")
    headers_other = await _headers(other)
    await client.post("/api/settings/handle", headers=headers_other, json={"handle": "taken_nick_x"})

    headers = await _headers(uid)
    r = await client.post("/api/settings/handle", headers=headers, json={"handle": "taken_nick_x"})
    assert r.status == 400
    body = await r.json()
    assert body["error"] == "taken"


async def test_bootstrap_reflects_handle(client, uid):
    add_user(uid, "a", "Тест")
    headers = await _headers(uid)
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["user"]["handle"] == get_handle(uid)


# =====================================
# ИНТЕГРАЦИЯ: рейтинг / публичный профиль
# =====================================

def test_rating_includes_handle(uid):
    add_user(uid, "a", "Тест")
    # get_rating() теперь персональный (см. db/leagues.py) — зритель со
    # streak=0 в своём же списке не появится (ещё не набрал streak>=2 для
    # входа в лигу рейтинга), поэтому явно задаём streak, чтобы попасть в
    # свою же лигу и проверить, что handle долетает до неё.
    conn = connect()
    conn.execute("UPDATE users SET streak=5 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    clear_rating_cache()
    # limit нарочно огромный: внутри лиги может быть много пользователей
    # из других тестов с тем же диапазоном streak — маленький limit не
    # гарантировал бы, что именно этот попадёт в срез.
    _league, rows = get_rating(uid, limit=100000)
    row = next(r for r in rows if r["telegram_id"] == uid)
    assert row["handle"] == get_handle(uid)


async def test_public_profile_includes_handle(client, uid):
    add_user(uid, "a", "Тест")
    headers = await _headers(uid)
    await client.post("/api/settings/public-profile", headers=headers, json={"enabled": True})
    r = await client.get(f"/api/public/profile/{uid}")
    assert r.status == 200
    body = await r.json()
    assert body["handle"] == get_handle(uid)
