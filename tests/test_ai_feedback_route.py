"""
/api/ai/feedback (webapp/routes_ai_miniapp.py) — бэкенд существовал давно,
но ни одна кнопка в чате Mini App (webapp/static/ai_coach.js) его не
вызывала: 👍/👎 нигде не отображались. См. фикс в ai_coach.js — кнопки
оценки добавлены в message-mini-actions.

Плюс security-фикс: message_id теперь проверяется на принадлежность
вызывающему пользователю (см. get_ai_message_text) — без неё любой
авторизованный пользователь мог оценить чужое сообщение по угаданному id.
"""
from db import add_user, add_ai_message, get_ai_feedback_stats

from tests.conftest import sign_init_data


async def _headers(uid):
    return {"Authorization": f"tma {sign_init_data(uid)}"}


async def test_feedback_requires_valid_rating(client, uid):
    add_user(uid, "tester", "Test")
    message_id = add_ai_message(uid, "assistant", "Тестовый ответ ADAM")

    r = await client.post(
        "/api/ai/feedback",
        json={"init_data": sign_init_data(uid), "message_id": message_id, "rating": "sideways"},
    )
    assert r.status == 400


async def test_feedback_up_is_saved(client, uid):
    add_user(uid, "tester", "Test")
    message_id = add_ai_message(uid, "assistant", "Тестовый ответ ADAM")
    before = get_ai_feedback_stats()["up"]

    r = await client.post(
        "/api/ai/feedback",
        json={"init_data": sign_init_data(uid), "message_id": message_id, "rating": "up"},
    )

    assert r.status == 200
    assert get_ai_feedback_stats()["up"] == before + 1


async def test_feedback_can_be_changed_not_duplicated(client, uid):
    add_user(uid, "tester", "Test")
    message_id = add_ai_message(uid, "assistant", "Тестовый ответ ADAM")
    init_data = sign_init_data(uid)

    await client.post("/api/ai/feedback", json={"init_data": init_data, "message_id": message_id, "rating": "up"})
    stats_after_up = get_ai_feedback_stats()

    await client.post("/api/ai/feedback", json={"init_data": init_data, "message_id": message_id, "rating": "down"})
    stats_after_down = get_ai_feedback_stats()

    # Повторная оценка того же message_id тем же пользователем меняет
    # рейтинг (UPSERT), а не добавляет вторую запись.
    assert stats_after_down["total"] == stats_after_up["total"]
    assert stats_after_down["down"] == stats_after_up["down"] + 1


async def test_feedback_rejects_message_id_belonging_to_another_user(client, uid):
    """IDOR-фикс: нельзя оценить сообщение, которое не входит в ai_messages
    этого пользователя — ни угаданный, ни чужой реальный id."""
    add_user(uid, "tester", "Test")
    other_user = uid + 1
    add_user(other_user, "other", "Other")
    other_message_id = add_ai_message(other_user, "assistant", "Ответ ADAM другому пользователю")

    r = await client.post(
        "/api/ai/feedback",
        json={"init_data": sign_init_data(uid), "message_id": other_message_id, "rating": "up"},
    )

    assert r.status == 404


async def test_feedback_rejects_nonexistent_message_id(client, uid):
    add_user(uid, "tester", "Test")

    r = await client.post(
        "/api/ai/feedback",
        json={"init_data": sign_init_data(uid), "message_id": 999999999, "rating": "up"},
    )

    assert r.status == 404
