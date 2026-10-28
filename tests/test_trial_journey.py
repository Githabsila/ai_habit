"""
Путь целиком (ТЗ 28.10.2026 §29–§30): ссылка участника → вход → старт триала → полезные действия → сообщения по дням → окончание → окно 24 часа → оплата → продление →
начисление партнёру → выплата. Каждый шаг проверен тем же кодом, что работает в бою; время двигаем «машиной времени» админки.
"""
import itertools
from datetime import datetime, timezone

import pytest

import subscription_flow
from db import add_habit, add_user, partners, set_timezone, start_trial_mode, trial_flow
from db import trial as trial_db
from db.access import shift_trial_day
from db.access_state import reset_trial
from db.core import connect
from db.shop import begin_payment, finish_payment
from db.subscription import record_subscription_payment
from tests.conftest import sign_init_data
from tests.test_trial import _open_app, _tick

ADMIN = 777
_counter = itertools.count(867_100_000, 100)


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


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _user(uid_, name):
    add_user(uid_, f"j{uid_}", name)
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    set_timezone(uid_, "UTC")
    return uid_


async def _pay(bot, user_id, charge, amount, recurring=False):
    assert begin_payment(charge, user_id, f"subscription:{user_id}", amount, is_recurring=recurring) == "new"
    await subscription_flow.complete_subscription_payment(bot, user_id, charge_id=charge, recurring=recurring)
    finish_payment(charge, True)


async def test_the_whole_path_from_the_partner_link_to_the_payout(client, uid, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    partner, person = _user(uid, "Аня"), _user(uid + 1, "Боря")
    assert partners.add_partner(partner, ADMIN) == {"ok": True}
    record_subscription_payment(partner, months=1)

    # 1. ссылка участника → привязка
    touch = partners.register_touch(person, partners.ensure_personal_link(partner)["code"])
    assert touch["counted"] is True

    # 2. первое открытие приложения запускает триал: день 1, ни одного продающего сообщения
    start_trial_mode(person, ADMIN)
    reset_trial(person)                                                          # аккаунт в сценарии, но приложение ещё ни разу не открывалось
    assert trial_db.snapshot(person)["phase"] == "not_started"
    boot = await client.get("/api/bootstrap", headers=_headers(person))
    assert boot.status == 200
    snap = trial_db.snapshot(person)
    assert snap["phase"] == "day1" and snap["trial_ends_at"]
    for hour in (10, 14, 19):
        assert (await _tick(monkeypatch, [person], hour)).send_message.call_count == 0

    # 3. день 2: отметка привычки → одно спокойное предложение в приложении; отдельное сообщение той же продажи не уходит
    shift_trial_day(person, 2, ADMIN)
    habit = add_habit(person, "Зарядка")
    done = await client.post(f"/api/habits/{habit}/complete", headers=_headers(person))
    assert done.status == 200
    offer = (await (await client.post("/api/trial/offer", headers=_headers(person), json={"trigger": "habit"})).json())["offer"]
    assert offer and "«Зарядка»" in offer["text"]
    _open_app(person)
    assert (await _tick(monkeypatch, [person], 14)).send_message.call_count == 0

    # 4. день 3: приглашение к разговору и вопрос Адама, потом прямое сообщение о конце доступа
    shift_trial_day(person, 3, ADMIN)
    _sql("UPDATE trial_offers SET local_date=date('now', '-1 day') WHERE user_id=?", (person,))      # «машина времени» двигает день триала, а календарь в тесте один: вчерашние предложения — вчерашние
    talk = await _tick(monkeypatch, [person], 12)
    assert talk.send_message.call_count == 1 and "у меня есть вопрос по твоим дням" in talk.send_message.call_args_list[0].args[1]
    invite = trial_flow.open_invite(person)
    assert invite and invite["message"] and "Зарядка" in invite["message"], "вопрос Адама построен по тому, что человек реально делал"
    assert trial_flow.chat_offer(person, "Помогает утро и музыка, мешает усталость вечером")["text"] == "Показать короткую сводку?"
    end = await _tick(monkeypatch, [person], 19)
    assert end.send_message.call_count == 1 and "сегодня пробный доступ заканчивается" in end.send_message.call_args_list[0].args[1]

    # 5. окончание: окно 24 часа — одно сообщение, история видна, действия закрыты
    shift_trial_day(person, 4, ADMIN)
    assert trial_db.snapshot(person)["phase"] == "grace"
    notice = await _tick(monkeypatch, [person], 12)
    assert notice.send_message.call_count == 1 and "Пробный доступ завершён" in notice.send_message.call_args_list[0].args[1]
    assert (await client.get("/api/bootstrap", headers=_headers(person))).status == 200, "история видна"
    closed = await client.post("/api/habits", headers=_headers(person), json={"title": "Новая"})
    assert closed.status == 403 and (await closed.json())["error"] == "trial_expired"

    # 6. оплата в окне (вводная цена 153 ⭐): доступ открыт, партнёру начислено 40 %
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    await _pay(bot, person, f"j1-{person}", 153)
    assert trial_db.snapshot(person)["phase"] == "paid"
    first = _sql("SELECT * FROM partner_accruals WHERE charge_id=?", (f"j1-{person}",))
    assert (first["base_amount"], first["share_percent"], first["amount"], first["status"]) == (153, 40, 61, "accrued")
    assert (await client.post("/api/habits", headers=_headers(person), json={"title": "Новая"})).status != 403, "после оплаты действия снова открыты"

    # 7. после оплаты запланированные сообщения не приходят
    for hour in (12, 19):
        assert (await _tick(monkeypatch, [person], hour)).send_message.call_count == 0

    # 8. продление по полной цене (461 ⭐) — тоже начисление; повторное подтверждение ничего не добавляет
    await _pay(bot, person, f"j2-{person}", 461, recurring=True)
    second = _sql("SELECT * FROM partner_accruals WHERE charge_id=?", (f"j2-{person}",))
    assert second["amount"] == 184 and second["status"] == "accrued"
    assert partners.accrue_payment(f"j2-{person}") is None
    assert _sql("SELECT COUNT(*) AS n FROM partner_accruals WHERE partner_id=?", (partner,))["n"] == 2

    # 9. выплата: ждёт срока → к выплате → отмечена; сумма совпадает с начислениями
    assert partners.balance(partner)["accrued"] == 245 and partners.balance(partner)["payable"] == 0, "свежие покупки ждут срока возвратов"
    partners.set_setting("hold_days", "0", ADMIN)
    partners.set_setting("min_payout", "0", ADMIN)
    try:
        payout = partners.create_payout(partner, ADMIN, note="путь целиком", confirmation="перевод №1")
        assert payout["ok"] and payout["amount"] == 245 and payout["accruals"] == 2
        assert partners.balance(partner)["paid"] == 245 and partners.balance(partner)["payable"] == 0
    finally:
        partners.set_setting("hold_days", "14", ADMIN)
        partners.set_setting("min_payout", "100", ADMIN)
    stats = partners.participant_stats(partner)
    assert stats["invited"] == 1 and stats["buyers"] == 1 and stats["purchases"] == 2 and "Боря" not in str(stats)
