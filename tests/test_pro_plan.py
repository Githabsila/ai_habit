"""
Единая версия — Pro (решение владельца, 24.10.2026): бесплатной версии нет; первые 3 дня триал с открытыми функциями Pro, с 4-го дня — оплата
той же подписки Pro. Всё, что раньше давал отдельный Premium (еженедельный разбор цели, метка PRO в чате), теперь входит в Pro для каждого, у кого
есть доступ. Более дорогой тариф Premium — на будущее: пока config.PREMIUM_TIER_ENABLED=false, его нельзя купить (магазин, кнопки в боте).
"""
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import config
import handlers.payments as payments_mod
from db import add_user, get_user, give_premium_admin
from db.core import connect
from db.onboarding import get_surveys_due_for_feedback
from db.plans import PLAN_PREMIUM, PLAN_PRO, has_premium_features, has_pro_features, user_plan
from db.shop import get_shop_items
from tests.conftest import sign_init_data

_counter = itertools.count(861_500_000, 100)


@pytest.fixture
def person():
    return next(_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _lock_by_gate(monkeypatch, uid_):
    """Гейт включён, человек зарегистрирован давно: триал (3 дня) кончился, оплаты нет — доступа нет."""
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_CUTOVER", "2000-01-01")
    old = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE users SET created_at=? WHERE telegram_id=?", (old, uid_))
    conn.commit()
    conn.close()
    from db.access_state import set_trial_start
    set_trial_start(uid_, datetime.utcnow() - timedelta(days=30))          # триал начался при первом открытии приложения 30 дней назад


# ---------------------------------------------------------------------------------------------------------------- тариф

def test_everyone_with_access_has_all_pro_features_and_nobody_without_it(person, monkeypatch):
    add_user(person, f"p{person}", "Аня")
    assert has_pro_features(person) is True, "обычный человек (триал или доступ открыт) — это Pro"
    assert user_plan(person) == PLAN_PRO
    assert has_pro_features(person + 1) is False, "неизвестного человека нет"
    _lock_by_gate(monkeypatch, person)
    assert has_pro_features(person) is False and user_plan(person) is None, "после триала без оплаты Pro-функций нет — только оплата"


def test_paying_gives_pro_again_and_the_trial_has_the_same_features(person, monkeypatch):
    from db.subscription import record_subscription_payment
    add_user(person, f"p{person}", "Аня")
    _lock_by_gate(monkeypatch, person)
    assert has_pro_features(person) is False
    record_subscription_payment(person)
    assert has_pro_features(person) is True and user_plan(person) == PLAN_PRO


def test_the_old_premium_flag_adds_nothing_while_the_expensive_tier_is_off(person, monkeypatch):
    add_user(person, f"p{person}", "Аня")
    give_premium_admin(person)
    assert get_user(person)["premium"] == 1, "флаг у людей остаётся как есть — пригодится для будущего тарифа"
    assert has_premium_features(person) is False and user_plan(person) == PLAN_PRO
    monkeypatch.setattr(config, "PREMIUM_TIER_ENABLED", True)
    assert has_premium_features(person) is True and user_plan(person) == PLAN_PREMIUM
    add_user(person + 1, f"p{person + 1}", "Боря")
    assert has_premium_features(person + 1) is False and user_plan(person + 1) == PLAN_PRO


def test_an_admin_account_is_pro_even_when_its_test_trial_is_over(person, monkeypatch):
    """Админ пользуется приложением всегда (его не проверяет вход), поэтому метка в чате не должна показывать STANDARD на тестовом админском аккаунте."""
    add_user(person, f"p{person}", "Админ-тест")
    monkeypatch.setattr(config, "ADMIN_IDS", [person])
    _lock_by_gate(monkeypatch, person)
    assert has_pro_features(person) is True and user_plan(person) == PLAN_PRO
    add_user(person + 1, f"p{person + 1}", "Обычный")
    _lock_by_gate(monkeypatch, person + 1)
    assert has_pro_features(person + 1) is False, "обычного человека закрытый гейт по-прежнему не пускает"


def test_premium_is_off_by_default():
    assert config.PREMIUM_TIER_ENABLED is False


# ---------------------------------------------------------------------------------------------------------------- лимит ответов Адама

async def test_the_chat_limit_and_label_are_pro_for_everyone(client, person):
    add_user(person, f"p{person}", "Аня")
    r = await client.get("/api/ai/quota", headers={"X-Telegram-Init-Data": sign_init_data(person)})
    assert r.status == 200
    quota = await r.json()
    assert quota["pro"] is True and quota["limit"] >= 15


# ---------------------------------------------------------------------------------------------------------------- магазин

def test_the_premium_item_is_not_sold_while_the_tier_is_off(monkeypatch):
    kinds = [it["item_type"] for it in get_shop_items()]
    assert "premium" not in kinds
    monkeypatch.setattr(config, "PREMIUM_TIER_ENABLED", True)
    assert "premium" in [it["item_type"] for it in get_shop_items()], "товар остаётся в базе — вернётся с новым тарифом"


async def test_the_mini_app_shop_hides_premium_and_refuses_to_sell_it(client, person, monkeypatch):
    add_user(person, f"p{person}", "Аня")
    shop = await (await client.get("/api/shop", headers=_headers(person))).json()
    assert "premium" not in [it["item_type"] for it in shop["items"]]
    buy = await client.post("/api/buy/1", headers=_headers(person))
    assert buy.status == 403 and (await buy.json())["error"] == "not_for_sale"
    monkeypatch.setattr(config, "PREMIUM_TIER_ENABLED", True)
    assert "premium" in [it["item_type"] for it in (await (await client.get("/api/shop", headers=_headers(person))).json())["items"]]
    again = await client.post("/api/buy/1", headers=_headers(person))
    assert again.status != 403, "с включённым тарифом покупка идёт по прежним правилам (монет может не хватить, но не «не продаётся»)"


# ---------------------------------------------------------------------------------------------------------------- бот

class _Callback:
    def __init__(self):
        self.alerts, self.edits, self.invoices = [], [], []
        self.from_user = SimpleNamespace(id=1)
        outer = self

        class _Message:
            async def edit_text(self, *a, **k):
                outer.edits.append(a)

            async def answer_invoice(self, **k):
                outer.invoices.append(k)

            async def answer(self, *a, **k):
                outer.edits.append(a)

        self.message = _Message()
        self.data = ""

    async def answer(self, text=None, show_alert=False, **k):
        self.alerts.append((text, show_alert))


async def test_bot_premium_buttons_explain_it_is_inside_pro_and_sell_nothing():
    for handler in (payments_mod.premium_info, payments_mod.buy_premium):
        cb = _Callback()
        await handler(cb)
        assert cb.alerts and "входит в Pro" in cb.alerts[0][0] and cb.alerts[0][1] is True
        assert not cb.invoices and not cb.edits, "ни счёта, ни меню покупки"
    cb = _Callback()
    cb.data = "gift_premium_confirm_2"
    await payments_mod.gift_premium_confirm(cb)
    assert "входит в Pro" in cb.alerts[0][0] and not cb.invoices, "и подарок другу счёта не выставляет"


# ---------------------------------------------------------------------------------------------------------------- еженедельный разбор цели

def _survey(uid_):
    conn = connect()
    conn.execute("UPDATE users SET access_status='approved' WHERE telegram_id=?", (uid_,))
    conn.execute("INSERT OR REPLACE INTO user_survey(user_id, business, life_goal, bot_goal) VALUES (?, 'работа', 'жить лучше', 'бегать по утрам')", (uid_,))
    conn.commit()
    conn.close()


def test_the_weekly_goal_review_goes_to_everyone_with_access_not_only_premium(person, monkeypatch):
    add_user(person, f"p{person}", "Без Premium")
    add_user(person + 1, f"p{person + 1}", "Закрытый гейтом")
    _survey(person), _survey(person + 1)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_CUTOVER", "2000-01-01")
    old = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE users SET created_at=? WHERE telegram_id=?", (old, person + 1))
    conn.execute("UPDATE users SET created_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (person,))
    conn.commit()
    conn.close()
    from db.access_state import set_trial_start
    set_trial_start(person + 1, datetime.utcnow() - timedelta(days=30))
    set_trial_start(person, datetime.utcnow())
    due = {row["user_id"] for row in get_surveys_due_for_feedback(days=7)}
    assert person in due, "обычному человеку в триале разбор цели положен (входит в Pro)"
    assert person + 1 not in due, "закрытому гейтом после триала — нет"


# ---------------------------------------------------------------------------------------------------------------- тексты

def test_the_app_no_longer_advertises_a_separate_premium():
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "webapp" / "static"
    app = (static / "app.js").read_text(encoding="utf-8")
    index = (static / "index.html").read_text(encoding="utf-8")
    assert "Алмазы, Premium" not in app and "Premium и улучшения" not in index
    assert "Алмазы и улучшения для ADAM" in index and 'f.type === "paid" ? "Куплена"' in app
