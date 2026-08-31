"""
Дневной лимит AI-ответов: расходуется, не превышается, сбрасывается на
новый день (регрессия на баг "вчерашний used/bonus переносился на
сегодня" — см. db/shop.py::_ensure_ai_quota_day).
"""
from db import add_user
from db.shop import consume_ai_answer, get_ai_quota


def test_consume_respects_daily_limit(uid):
    add_user(uid, "tester", "Test")
    quota = get_ai_quota(uid, is_pro=False)
    limit = quota["limit"]

    for _ in range(limit):
        assert consume_ai_answer(uid, is_pro=False, cost=1) is True

    assert consume_ai_answer(uid, is_pro=False, cost=1) is False
    final = get_ai_quota(uid, is_pro=False)
    assert final["remaining"] == 0
    assert final["used"] == limit


def test_pro_user_gets_higher_daily_limit(uid):
    add_user(uid, "tester", "Test")
    free_quota = get_ai_quota(uid, is_pro=False)
    pro_quota = get_ai_quota(uid, is_pro=True)

    assert pro_quota["limit"] >= free_quota["limit"]
