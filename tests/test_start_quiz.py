"""
Стартовый квиз "сколько лет / зачем пришёл" — 2-шаговый экран ПЕРЕД
app-tour (просьба пользователя, по образцу конкурентных фитнес-приложений).
См. db/users.py::should_show_start_quiz/mark_start_quiz_seen,
app.js::START_QUIZ_STEPS.
"""
from db import add_user, get_user
from db.users import should_show_start_quiz, mark_start_quiz_seen

from tests.conftest import sign_init_data


def test_should_show_start_quiz_for_new_user(uid):
    add_user(uid, "u", "Test")
    assert should_show_start_quiz(uid) is True


def test_mark_start_quiz_seen_stores_answers(uid):
    add_user(uid, "u", "Test")
    mark_start_quiz_seen(uid, age_range="18-24", goal="ai")

    assert should_show_start_quiz(uid) is False
    user = get_user(uid)
    assert user["onboarding_age_range"] == "18-24"
    assert user["onboarding_goal"] == "ai"


def test_mark_start_quiz_seen_without_answers_still_marks_seen(uid):
    add_user(uid, "u", "Test")
    mark_start_quiz_seen(uid)

    assert should_show_start_quiz(uid) is False
    user = get_user(uid)
    assert user["onboarding_age_range"] is None
    assert user["onboarding_goal"] is None


async def test_start_quiz_route_requires_auth(client):
    r = await client.post("/api/start-quiz/seen")
    assert r.status == 401


async def test_start_quiz_route_persists_answers(client, uid):
    import json

    add_user(uid, "u", "Test")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}

    r = await client.post(
        "/api/start-quiz/seen",
        headers=headers,
        data=json.dumps({"age_range": "25-34", "goal": "habit"}),
    )

    assert r.status == 200
    assert should_show_start_quiz(uid) is False
    user = get_user(uid)
    assert user["onboarding_age_range"] == "25-34"
    assert user["onboarding_goal"] == "habit"


async def test_bootstrap_exposes_show_start_quiz(client, uid):
    add_user(uid, "u", "Test")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}

    r = await client.get("/api/bootstrap", headers=headers)
    data = await r.json()

    assert data["show_start_quiz"] is True


async def test_bootstrap_exposes_referrals_count(client, uid):
    from db.users import add_referral

    add_user(uid, "u", "Test")
    add_referral(uid)
    add_referral(uid)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}

    r = await client.get("/api/bootstrap", headers=headers)
    data = await r.json()

    assert data["user"]["referrals"] == 2


# ---------------------------------------------------------------------------
# «Написать Адаму» с экрана результата теста
# ---------------------------------------------------------------------------

async def test_quiz_does_not_restart_after_the_chat_exit(client, uid):
    """Жалоба с телефона: прошёл тест, нажал «Написать Адаму», поговорил — и приложение
    снова начало тест с первого вопроса. Кнопка ведёт на /coach, а «seen» раньше
    ставился только кнопкой «Начать» на последнем шаге."""
    import json

    add_user(uid, "u", "Test")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    assert (await (await client.get("/api/bootstrap", headers=headers)).json())["show_start_quiz"] is True

    # то, что клиент теперь отправляет перед уходом в чат
    r = await client.post("/api/start-quiz/seen", headers=headers,
                          data=json.dumps({"age_range": "18-24", "goal": "ai"}))
    assert r.status == 200
    assert (await (await client.get("/api/bootstrap", headers=headers)).json())["show_start_quiz"] is False


def test_chat_button_marks_the_quiz_seen_before_leaving():
    from pathlib import Path
    app_js = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    start = app_js.index('document.getElementById("startQuizArchetypeChat")')
    handler = app_js[start:start + 2200]
    assert handler.index('"/api/start-quiz/seen"') < handler.index("window.location.href = url")
    assert "age_range: startQuizAnswers.age_range" in handler and "goal: startQuizAnswers.goal" in handler
    assert "setTimeout(resolve, 2500)" in handler, "сеть не должна держать пользователя дольше пары секунд"
    assert "chatBtn.disabled = true" in handler, "двойной тап не должен слать запрос дважды"
