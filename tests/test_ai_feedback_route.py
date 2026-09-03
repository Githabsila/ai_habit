"""
/api/ai/feedback (webapp/routes_ai_miniapp.py) — бэкенд существовал давно,
но ни одна кнопка в чате Mini App (webapp/static/ai_coach.js) его не
вызывала: 👍/👎 нигде не отображались. См. фикс в ai_coach.js — кнопки
оценки добавлены в message-mini-actions.
"""
from db import add_user, get_ai_feedback_stats

from tests.conftest import sign_init_data


async def _headers(uid):
    return {"Authorization": f"tma {sign_init_data(uid)}"}


async def test_feedback_requires_valid_rating(client, uid):
    add_user(uid, "tester", "Test")
    r = await client.post(
        "/api/ai/feedback",
        json={"init_data": sign_init_data(uid), "message_id": 1, "rating": "sideways"},
    )
    assert r.status == 400


async def test_feedback_up_is_saved(client, uid):
    add_user(uid, "tester", "Test")
    before = get_ai_feedback_stats()["up"]

    r = await client.post(
        "/api/ai/feedback",
        json={"init_data": sign_init_data(uid), "message_id": 1, "rating": "up"},
    )

    assert r.status == 200
    assert get_ai_feedback_stats()["up"] == before + 1


async def test_feedback_can_be_changed_not_duplicated(client, uid):
    add_user(uid, "tester", "Test")
    init_data = sign_init_data(uid)

    await client.post("/api/ai/feedback", json={"init_data": init_data, "message_id": 42, "rating": "up"})
    stats_after_up = get_ai_feedback_stats()

    await client.post("/api/ai/feedback", json={"init_data": init_data, "message_id": 42, "rating": "down"})
    stats_after_down = get_ai_feedback_stats()

    # Повторная оценка того же message_id тем же пользователем меняет
    # рейтинг (UPSERT), а не добавляет вторую запись.
    assert stats_after_down["total"] == stats_after_up["total"]
    assert stats_after_down["down"] == stats_after_up["down"] + 1
