"""
Денежная партнёрская программа (ТЗ 28.10.2026, §20–§24): личные и подписанные ссылки, история переходов и правило привязки, начисление 40 % от полной суммы покупки приглашённого
в первые 30 дней, защита от самоприглашения, дублей и подмены, сторно при возврате, «на проверке» с причиной, индивидуальные условия, ручные выплаты и журнал исправлений.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from db import add_user, partners
from db.core import connect
from db.subscription import record_subscription_payment

ADMIN = 777
_counter = itertools.count(865_100_000, 100)
UTC = timezone.utc


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


def _user(uid_, name="Аня"):
    add_user(uid_, f"u{uid_}", name)
    _sql("UPDATE users SET access_status='approved' WHERE telegram_id=?", (uid_,))
    return uid_


def _partner(uid_, **kw):
    _user(uid_)
    result = partners.add_partner(uid_, ADMIN, **kw)
    assert result == {"ok": True}, result
    record_subscription_payment(uid_, months=1)
    return uid_


def _pay(user_id, charge, amount=153, days_ago=0, test=False, status="done"):
    when = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO stars_payments(charge_id, user_id, payload, amount, created_at, purchased_at, is_test, status) VALUES (?, ?, 'x', ?, ?, ?, ?, ?)",
         (charge, user_id, amount, when, when, 1 if test else 0, status))
    return charge


def _invitee(uid_, partner_id, code=None):
    _user(uid_, "Боря")
    code = code or partners.ensure_personal_link(partner_id)["code"]
    return partners.register_touch(uid_, code)


def _accrual(charge):
    return _sql("SELECT * FROM partner_accruals WHERE charge_id=?", (charge,))


# ---------------------------------------------------------------------------------------------------------------- участники и ссылки

def test_a_partner_gets_one_personal_link_and_labeled_links_for_comparing_places(uid):
    owner = _partner(uid)
    personal = partners.ensure_personal_link(owner)
    assert personal["is_personal"] == 1 and partners.ensure_personal_link(owner)["code"] == personal["code"], "личная ссылка одна навсегда"
    assert partners.parse_start_code(partners.start_payload(personal["code"])) == personal["code"]
    assert partners.parse_start_code("ref_123") is None and partners.parse_start_code("p_") is None
    made = partners.create_link(owner, "  Канал   про привычки ")
    assert made["label"] == "Канал про привычки" and made["code"] != personal["code"]
    assert partners.create_link(owner, "  ") == {"error": "empty_label"}
    assert partners.create_link(uid + 50, "Кому-то") == {"error": "not_partner"}
    for index in range(partners.MAX_LINKS):
        partners.create_link(owner, f"площадка {index}")
    assert partners.create_link(owner, "лишняя") == {"error": "too_many_links"}


def test_only_the_pilot_list_can_become_a_partner_and_the_conditions_are_checked(uid):
    assert partners.add_partner(uid, ADMIN) == {"error": "user_not_found"}
    _user(uid)
    assert partners.add_partner(uid, ADMIN, share_percent=120) == {"error": "invalid_share"}
    assert partners.add_partner(uid, ADMIN, window_days=0) == {"error": "invalid_window"}
    assert partners.add_partner(uid, ADMIN) == {"ok": True} and partners.add_partner(uid, ADMIN) == {"error": "already_partner"}
    assert partners.get_partner(uid)["share_percent"] is None, "своих условий нет — действуют значения по умолчанию"


# ---------------------------------------------------------------------------------------------------------------- переходы и привязка

def test_self_invites_unknown_codes_and_repeat_taps_are_not_counted(uid):
    owner = _partner(uid)
    code = partners.ensure_personal_link(owner)["code"]
    assert partners.register_touch(owner, code)["reason"] == "self"
    assert partners.register_touch(uid + 1, "nonexist")["reason"] == "not_found"
    friend = _user(uid + 2)
    assert partners.register_touch(friend, code)["counted"] is True
    again = partners.register_touch(friend, code)
    assert again["counted"] is False, "та же ссылка второй раз ничего не меняет"
    touches = _sql("SELECT COUNT(*) AS n FROM partner_touches WHERE invitee_id=?", (friend,))["n"]
    assert touches == 2, "но история переходов хранит каждый приход"


def test_the_first_link_wins_by_default_the_last_one_by_setting(uid):
    first_owner, second_owner = _partner(uid), _partner(uid + 1)
    friend = _user(uid + 2)
    partners.register_touch(friend, partners.ensure_personal_link(first_owner)["code"])
    outcome = partners.register_touch(friend, partners.ensure_personal_link(second_owner)["code"])
    assert outcome["counted"] is False and outcome["reason"] == "kept_first"
    assert _sql("SELECT partner_id FROM partner_attributions WHERE invitee_id=?", (friend,))["partner_id"] == first_owner
    assert partners.set_setting("attribution_rule", "last", ADMIN) == {"ok": True}
    assert partners.register_touch(friend, partners.ensure_personal_link(second_owner)["code"])["counted"] is True
    assert _sql("SELECT partner_id FROM partner_attributions WHERE invitee_id=?", (friend,))["partner_id"] == second_owner
    assert _sql("SELECT COUNT(*) AS n FROM partner_touches WHERE invitee_id=?", (friend,))["n"] == 3, "история хранит все переходы, а засчитан один"
    partners.set_setting("attribution_rule", "first", ADMIN)


def test_after_the_first_purchase_the_link_is_locked_and_an_earlier_payer_is_not_new(uid):
    owner, other = _partner(uid), _partner(uid + 1)
    friend = uid + 2
    _invitee(friend, owner)
    _pay(friend, f"c{friend}")
    assert partners.accrue_payment(f"c{friend}") is not None
    partners.set_setting("attribution_rule", "last", ADMIN)
    try:
        assert partners.register_touch(friend, partners.ensure_personal_link(other)["code"])["reason"] == "locked"
    finally:
        partners.set_setting("attribution_rule", "first", ADMIN)
    payer = _user(uid + 3)
    _pay(payer, f"c{payer}")
    assert partners.register_touch(payer, partners.ensure_personal_link(owner)["code"])["reason"] == "stale", "заплатил раньше — это не новый приглашённый"


def test_the_inviter_card_shows_name_and_a_copyable_nick_without_subscribing_anyone(uid):
    owner = _partner(uid)
    _sql("UPDATE users SET handle=? WHERE telegram_id=?", (f"anya{owner}", owner))
    friend = uid + 1
    assert partners.inviter_card(friend) is None
    _invitee(friend, owner)
    card = partners.inviter_card(friend)
    assert card["name"] == "Аня" and card["handle"] == f"@anya{owner}" and card["user_id"] == owner
    assert _sql("SELECT COUNT(*) AS n FROM follows WHERE follower_id=? OR followee_id=?", (friend, friend))["n"] == 0, "ничего не делается за человека"


# ---------------------------------------------------------------------------------------------------------------- начисления

def test_forty_percent_of_the_full_amount_for_every_confirmed_purchase_in_the_window(uid):
    owner = _partner(uid)
    friend = uid + 1
    _invitee(friend, owner)
    _pay(friend, "sub1", amount=153)
    _pay(friend, "micro1", amount=7)
    big, small = partners.accrue_payment("sub1"), partners.accrue_payment("micro1")
    assert (big["base_amount"], big["share_percent"], big["amount"], big["status"]) == (153, 40, 61, "accrued"), "40 % от полной суммы, округление вниз"
    assert small["amount"] == 2 and small["status"] == "accrued", "мелкие покупки тоже считаются"
    assert partners.accrue_payment("sub1") is None, "повторное подтверждение той же покупки ничего не добавляет"
    assert _sql("SELECT COUNT(*) AS n FROM partner_accruals WHERE partner_id=?", (owner,))["n"] == 2


def test_test_payments_refunds_other_buyers_and_outside_the_window_give_nothing(uid):
    owner = _partner(uid)
    friend, stranger, late = uid + 1, uid + 2, uid + 3
    _invitee(friend, owner)
    _user(stranger)
    _pay(friend, "t1", test=True)
    _pay(friend, "pending1", status="received")
    _pay(stranger, "s1")
    assert partners.accrue_payment("t1") is None and partners.accrue_payment("pending1") is None and partners.accrue_payment("s1") is None
    _invitee(late, owner)
    _sql("UPDATE partner_attributions SET attributed_at=? WHERE invitee_id=?", ((datetime.now(UTC) - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S"), late))
    _pay(late, "l1")
    assert partners.accrue_payment("l1") is None, "на 40-й день начислений уже нет"
    assert _accrual("t1") is None and _accrual("l1") is None and _accrual("s1") is None


def test_someone_outside_the_pilot_or_without_a_subscription_never_gets_money_silently(uid):
    outsider = _user(uid)
    code = "outsider1"
    _sql("INSERT INTO partner_links(partner_id, code, label, is_personal, created_at) VALUES (?, ?, 'x', 1, datetime('now'))", (outsider, code))
    friend = _user(uid + 1)
    partners.register_touch(friend, code)
    _pay(friend, "o1")
    assert partners.accrue_payment("o1") is None, "не из пилота — начислений нет"
    owner = _partner(uid + 2)
    _sql("UPDATE users SET subscription_paid_until=NULL WHERE telegram_id=?", (owner,))
    buyer = uid + 3
    _invitee(buyer, owner)
    _pay(buyer, "n1")
    held = partners.accrue_payment("n1")
    assert held["status"] == "hold" and "нет действующей платной подписки" in held["reason"], "покупка сама допуск не даёт — начисление ждёт проверки"
    assert partners.balance(owner)["hold"] == held["amount"] and partners.balance(owner)["payable"] == 0


def test_a_burst_of_arrivals_and_expired_conditions_go_to_review_with_a_reason(uid):
    owner = _partner(uid)
    code = partners.ensure_personal_link(owner)["code"]
    people = [_user(uid + 10 + i) for i in range(partners.BURST_COUNT)]
    for person in people:
        partners.register_touch(person, code)
    _pay(people[-1], "b1")
    flagged = partners.accrue_payment("b1")
    assert flagged["status"] == "hold" and "подозрительная регистрация" in flagged["reason"]
    expired_owner = _partner(uid + 1, valid_until="2020-01-01 00:00:00")
    buyer = uid + 2
    _invitee(buyer, expired_owner)
    _pay(buyer, "e1")
    assert partners.accrue_payment("e1")["status"] == "hold"


def test_a_refund_reverses_the_accrual_and_a_paid_one_is_marked_for_clawback(uid):
    owner = _partner(uid)
    friend = uid + 1
    _invitee(friend, owner)
    _pay(friend, "r1")
    _pay(friend, "r2")
    partners.accrue_payment("r1")
    partners.accrue_payment("r2")
    partners.set_setting("hold_days", "0", ADMIN)
    partners.set_setting("min_payout", "0", ADMIN)
    try:
        partners.create_payout(owner, ADMIN, note="октябрь")
        _sql("UPDATE stars_payments SET refunded_at=datetime('now') WHERE charge_id='r1'")
        assert partners.sync_accruals()["reversed"] == 1
        reversed_row = _accrual("r1")
        assert reversed_row["status"] == "reversed" and reversed_row["payout_id"] is not None
        assert partners.balance(owner)["clawback"] == reversed_row["amount"], "уже выплаченное при возврате — к удержанию"
        assert partners.reverse_payment("r1") is None, "второй раз ничего не происходит"
    finally:
        partners.set_setting("hold_days", "14", ADMIN)
        partners.set_setting("min_payout", "100", ADMIN)


def test_the_sync_catches_purchases_the_hook_missed_and_is_safe_to_repeat(uid):
    owner = _partner(uid)
    friend = uid + 1
    _invitee(friend, owner)
    _pay(friend, "m1")
    assert partners.sync_accruals()["accrued"] >= 1
    assert partners.sync_accruals()["accrued"] == 0
    assert _accrual("m1")["amount"] == 61


def test_personal_conditions_do_not_touch_the_past_or_other_partners(uid):
    first, second = _partner(uid), _partner(uid + 1)
    a, b = uid + 2, uid + 3
    _invitee(a, first)
    _invitee(b, second)
    _pay(a, "k1")
    partners.accrue_payment("k1")
    assert partners.set_conditions(first, ADMIN, share_percent=50, window_days=90, valid_until="2099-01-01 00:00:00") == {"ok": True}
    _pay(a, "k2")
    _pay(b, "k3")
    partners.accrue_payment("k2")
    partners.accrue_payment("k3")
    assert (_accrual("k1")["share_percent"], _accrual("k2")["share_percent"], _accrual("k3")["share_percent"]) == (40, 50, 40)
    assert partners.set_conditions(uid + 99, ADMIN, share_percent=10) == {"error": "not_partner"}
    assert partners.set_conditions(first, ADMIN, status="boom") == {"error": "invalid_status"}
    assert partners.set_conditions(first, ADMIN, status="paused") == {"ok": True}
    assert partners.is_valid_now(partners.get_partner(first)) is False


# ---------------------------------------------------------------------------------------------------------------- выплаты и журнал

def test_manual_payout_pays_everything_ready_marks_it_and_writes_the_journal(uid):
    owner = _partner(uid)
    friend = uid + 1
    _invitee(friend, owner)
    _sql("UPDATE partner_attributions SET attributed_at=? WHERE invitee_id=?", ((datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"), friend))
    _sql("UPDATE partners SET valid_from=? WHERE user_id=?", ((datetime.now(UTC) - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S"), owner))
    _pay(friend, "p1")
    _pay(friend, "p2", days_ago=20)
    partners.accrue_payment("p1")
    partners.accrue_payment("p2")
    balance = partners.balance(owner)
    assert balance["waiting"] == 61 and balance["payable"] == 61, "свежая покупка ждёт срока (возвраты), 20-дневная уже к выплате"
    assert partners.create_payout(owner, ADMIN) == {"error": "below_minimum", "payable": 61, "min_payout": 100}
    partners.set_setting("min_payout", "50", ADMIN)
    try:
        payout = partners.create_payout(owner, ADMIN, note="за октябрь", confirmation="перевод #1")
        assert payout["ok"] and payout["amount"] == 61 and payout["accruals"] == 1
        assert _accrual("p2")["status"] == "paid" and _accrual("p1")["status"] == "accrued"
        assert partners.create_payout(owner, ADMIN)["error"] == "below_minimum", "то, что выплачено, второй раз не выплачивается"
    finally:
        partners.set_setting("min_payout", "100", ADMIN)
    actions = [row["action"] for row in partners.audit_log(owner)]
    assert "payout" in actions and "add_partner" in actions


def test_review_corrections_need_a_reason_and_are_journaled(uid):
    owner = _partner(uid)
    friend = uid + 1
    _invitee(friend, owner)
    _pay(friend, "x1")
    accrual = partners.accrue_payment("x1")
    assert partners.correct_accrual(accrual["id"], ADMIN, "hold", "") == {"error": "invalid_request"}
    assert partners.correct_accrual(accrual["id"], ADMIN, "boom", "причина") == {"error": "invalid_request"}
    assert partners.correct_accrual(9999, ADMIN, "hold", "причина") == {"error": "not_found"}
    assert partners.correct_accrual(accrual["id"], ADMIN, "hold", "похоже на повторный аккаунт") == {"ok": True}
    assert _accrual("x1")["status"] == "hold" and _accrual("x1")["reason"] == "похоже на повторный аккаунт"
    assert partners.correct_accrual(accrual["id"], ADMIN, "release", "проверено") == {"ok": True} and _accrual("x1")["status"] == "accrued"
    assert partners.correct_accrual(accrual["id"], ADMIN, "reverse", "это возврат вне Telegram") == {"ok": True} and _accrual("x1")["status"] == "reversed"
    assert [r["action"] for r in partners.audit_log(owner)][:3] == ["accrual_reverse", "accrual_release", "accrual_hold"]


def test_the_participant_sees_totals_and_history_without_names_of_the_invited(uid):
    owner = _partner(uid)
    first, second = uid + 1, uid + 2
    _invitee(first, owner)
    _invitee(second, owner)
    _pay(second, "h1")
    partners.accrue_payment("h1")
    stats = partners.participant_stats(owner)
    assert stats["invited"] == 2 and stats["buyers"] == 1 and stats["purchases"] == 1 and stats["share_percent"] == 40 and stats["window_days"] == 30
    assert stats["history"][0]["invitee"] == "Приглашённый №2" and stats["history"][0]["amount"] == 61
    assert "Боря" not in str(stats) and str(second) not in str(stats), "личные данные приглашённых не показываем"
    assert stats["balance"]["accrued"] == 61 and stats["links"][0]["invitees"] == 2 and stats["links"][0]["buyers"] == 1
    assert partners.participant_stats(uid + 77) is None


def test_each_new_accrual_gives_one_signal_only(uid):
    owner = _partner(uid)
    friend = uid + 1
    _invitee(friend, owner)
    _pay(friend, "n9")
    accrual = partners.accrue_payment("n9")
    assert accrual["id"] in [r["id"] for r in partners.unnotified_accruals()]
    assert partners.mark_accrual_notified(accrual["id"]) is True and partners.mark_accrual_notified(accrual["id"]) is False
    assert accrual["id"] not in [r["id"] for r in partners.unnotified_accruals()]


def test_settings_accept_only_sensible_values(uid):
    assert partners.set_setting("boom", "1") == {"error": "unknown_setting"}
    for key, bad in (("attribution_rule", "middle"), ("default_share", "101"), ("default_window_days", "0"), ("min_payout", "-1"), ("currency", "USD"), ("rounding", "ceil")):
        assert partners.set_setting(key, bad) == {"error": "invalid_value"}, key
    assert partners.all_settings()["attribution_rule"] == "first" and partners.all_settings()["default_share"] == "40"
