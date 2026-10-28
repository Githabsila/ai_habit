"""
Решение владельца 28.10.2026: когда общий гейт оплаты будет включён, свежий триал получают ВСЕ — и уже зарегистрированные: три пробных дня с первого открытия приложения,
потом оплата. Включается переменными SUBSCRIPTION_GATE_ENABLED=true и SUBSCRIPTION_GATE_CUTOVER=all. Пока гейт выключен, обычные люди ничего не замечают.
Здесь же — контакты поддержки и имя оператора в правовых страницах.
"""
import itertools
from pathlib import Path

import pytest

import config
from db import add_user, get_user, set_timezone, trial as trial_db
from db.access import trial_scenario_for_user
from db.access_state import access_state
from db.core import connect
from db.subscription import bot_access_allowed, registered_after_cutover
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
_counter = itertools.count(868_100_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _old_user(uid_):
    add_user(uid_, f"o{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-400 days') WHERE telegram_id=?", (uid_,))
    set_timezone(uid_, "UTC")
    return uid_


def test_the_cutover_rule_understands_all_a_past_date_and_a_far_future_date():
    assert registered_after_cutover("2025-01-01 10:00:00", "all") is True and registered_after_cutover("2025-01-01 10:00:00", "ALL") is True
    assert registered_after_cutover("2025-01-01 10:00:00", "*") is True
    assert registered_after_cutover("2025-01-01 10:00:00", "2024-12-31") is True and registered_after_cutover("2025-01-01 10:00:00", "2025-01-02") is False
    assert registered_after_cutover("2025-01-01 10:00:00", "2100-01-01") is False and registered_after_cutover("2025-01-01 10:00:00", "boom") is False
    assert registered_after_cutover(None, "all") is True, "«для всех» — значит для всех, даже если дата регистрации не записана"
    assert registered_after_cutover(None, "2024-01-01") is False


def test_with_the_gate_off_an_old_user_notices_nothing(uid):
    person = _old_user(uid)
    assert config.SUBSCRIPTION_GATE_ENABLED is False
    assert trial_scenario_for_user(get_user(person)) is False and bot_access_allowed(person) is True
    assert access_state(person)["state"] == "open"


async def test_with_the_gate_on_for_all_an_old_user_gets_a_fresh_trial_from_the_first_open(client, uid, monkeypatch):
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_CUTOVER", "all")
    person = _old_user(uid)
    assert trial_scenario_for_user(get_user(person)) is True
    assert access_state(person)["state"] == "not_started" and bot_access_allowed(person) is True, "пока не открыл приложение — триал не идёт и не тратится"
    response = await client.get("/api/bootstrap", headers={"Authorization": f"tma {sign_init_data(person)}"})
    assert response.status == 200
    snap = trial_db.snapshot(person)
    assert snap["phase"] == "day1" and snap["day"] == 1 and snap["trial_ends_at"], "три свежих дня, как у новичка"
    assert get_user(person)["trial_started_at"] is not None


async def test_with_a_future_cutover_the_old_user_is_still_left_alone(client, uid, monkeypatch):
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_CUTOVER", "2100-01-01")
    person = _old_user(uid)
    assert trial_scenario_for_user(get_user(person)) is False
    assert (await client.get("/api/bootstrap", headers={"Authorization": f"tma {sign_init_data(person)}"})).status == 200
    assert get_user(person)["trial_started_at"] is None


def test_the_support_contacts_and_the_operator_name_are_filled_in():
    assert config.SUPPORT_CONTACT == "@floydddo, @AlxManagement", "контакты поддержки: оба аккаунта"
    terms, privacy = (STATIC / "terms.html").read_text(encoding="utf-8"), (STATIC / "privacy.html").read_text(encoding="utf-8")
    for page in (terms, privacy):
        assert "заполнить перед публикацией" not in page and "@floydddo" in page and "@AlxManagement" in page
    assert "Сервис предоставляется Егором" in terms and "Оператором данных является Егор" in privacy
