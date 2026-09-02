"""
Улучшение (фидбек #4): пол пользователя — для согласования "Ты"-обращения
в умных напоминаниях (сделал/сделала, был/была).
"""
from db import add_user, get_gender, set_gender, by_gender, guess_gender_from_name
from db.core import connect

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# guess_gender_from_name
# =====================================

def test_guess_female_name_by_ending():
    assert guess_gender_from_name("Мария") == "f"
    assert guess_gender_from_name("Анна") == "f"


def test_guess_male_name_by_ending():
    assert guess_gender_from_name("Александр") == "m"
    assert guess_gender_from_name("Дмитрий") == "m"


def test_guess_male_exception_names_ending_in_a():
    # Формально оканчиваются на "а"/"я", но это мужские имена.
    assert guess_gender_from_name("Никита") == "m"
    assert guess_gender_from_name("Илья") == "m"


def test_guess_returns_none_for_non_cyrillic_name():
    assert guess_gender_from_name("Alex") is None
    assert guess_gender_from_name("John") is None


def test_guess_returns_none_for_empty_name():
    assert guess_gender_from_name("") is None
    assert guess_gender_from_name(None) is None


# =====================================
# get_gender / set_gender
# =====================================

def test_get_gender_falls_back_to_name_guess(uid):
    add_user(uid, "u", "Мария")
    assert get_gender(uid) == "f"


def test_get_gender_none_for_unknown_user():
    assert get_gender(999999999999) is None


def test_get_gender_none_for_ambiguous_name(uid):
    add_user(uid, "u", "Alex")
    assert get_gender(uid) is None


def test_set_gender_overrides_name_guess(uid):
    # Имя "звучит" по-мужски, но пользователь явно выбрал "ж" в настройках —
    # явный выбор должен побеждать эвристику.
    add_user(uid, "u", "Александр")
    assert get_gender(uid) == "m"
    assert set_gender(uid, "f") is True
    assert get_gender(uid) == "f"


def test_set_gender_rejects_invalid_value(uid):
    add_user(uid, "u", "Test")
    assert set_gender(uid, "unknown") is False


def test_explicit_gender_persists_even_if_name_guess_would_differ(uid):
    add_user(uid, "u", "Мария")
    set_gender(uid, "m")
    conn = connect()
    row = conn.execute("SELECT gender, gender_explicit FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.close()
    assert row["gender"] == "m"
    assert row["gender_explicit"] == 1
    assert get_gender(uid) == "m"


# =====================================
# by_gender
# =====================================

def test_by_gender_male():
    assert by_gender("m", "сделал", "сделала") == "сделал"


def test_by_gender_female():
    assert by_gender("f", "сделал", "сделала") == "сделала"


def test_by_gender_unknown_falls_back_to_neutral_or_male():
    assert by_gender(None, "сделал", "сделала", neutral="сделал(а)") == "сделал(а)"
    assert by_gender(None, "сделал", "сделала") == "сделал"


# =====================================
# POST /api/settings/gender + бутстрап
# =====================================

async def test_gender_route_persists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/gender", headers=headers, json={"gender": "f"})
    assert r.status == 200
    assert get_gender(uid) == "f"


async def test_gender_route_rejects_invalid(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/gender", headers=headers, json={"gender": "x"})
    assert r.status == 400


async def test_bootstrap_reflects_explicit_gender(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/gender", headers=headers, json={"gender": "m"})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["gender"] == "m"


async def test_bootstrap_reflects_guessed_gender_without_explicit_choice(client, uid):
    add_user(uid, "u", "Мария")
    headers = await _headers(uid)
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["gender"] == "f"
