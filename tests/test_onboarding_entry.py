"""
Вход новичка (14.10): из бота — кнопка «Открыть ADAM» под сообщением о доступе и приветствием /start (раньше там была только
«Умные напоминания»); в приложении — приветствие в начале квиза, честные «первые шаги» вместо выдуманных отзывов,
окно «ударного режима» встаёт в цепочку (квиз → тур → ник → оно → подсказки), а не поверх квиза; человеку с готовой первой
привычкой подсказки сначала показывают, как её отметить.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import handlers.onboarding as ob
import handlers.start as start_handler
import keyboards
from db import add_user, set_access_status

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
URL = "https://example.test/adam"


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


@pytest.fixture
def web_url(monkeypatch):
    monkeypatch.setattr(keyboards, "WEBAPP_URL", URL)


def test_welcome_menu_puts_the_open_app_button_first_and_keeps_the_rest(web_url):
    markup = keyboards.welcome_menu()
    first = markup.inline_keyboard[0][0]
    assert first.web_app is not None and first.web_app.url == URL + "?from=bot_btn" and "Открыть ADAM" in first.text
    assert "reminders_menu" in {b.callback_data for b in _buttons(markup)}
    assert "admin" not in {b.callback_data for b in _buttons(markup)}
    assert "admin" in {b.callback_data for b in _buttons(keyboards.welcome_menu(is_admin=True))}


def test_welcome_menu_without_a_web_url_is_the_plain_menu(monkeypatch):
    monkeypatch.setattr(keyboards, "WEBAPP_URL", "")
    assert [b.callback_data for b in _buttons(keyboards.welcome_menu())] == [b.callback_data for b in _buttons(keyboards.main_menu())]


async def test_the_access_granted_message_leads_into_the_app(uid, web_url):
    add_user(uid, "u", "Аня")
    bot = SimpleNamespace(send_message=AsyncMock())
    await ob.grant_access(bot, uid)
    kwargs = bot.send_message.call_args.kwargs
    assert "Доступ открыт" in kwargs["text"] and "кнопкой ниже" in kwargs["text"]
    assert kwargs["reply_markup"].inline_keyboard[0][0].web_app.url == URL + "?from=bot_btn"


async def test_the_start_greeting_for_an_approved_person_has_the_open_app_button(uid, web_url):
    add_user(uid, "u", "Аня")
    set_access_status(uid, "approved")
    from db.consent import record_consent
    record_consent(uid)
    message = SimpleNamespace(
        text="/start", from_user=SimpleNamespace(id=uid, username="u", first_name="Аня", full_name="Аня"),
        answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()),
    )
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))
    await start_handler.start(message, state)
    kwargs = message.answer.call_args.kwargs
    assert kwargs["reply_markup"].inline_keyboard[0][0].web_app.url == URL + "?from=bot_btn"
    assert "кнопкой ниже" in message.answer.call_args.args[0]


# ---------------- Mini App: содержимое квиза ----------------

def test_the_quiz_opens_with_a_welcome_and_not_with_a_question_about_age():
    steps = APP[APP.index("const START_QUIZ_STEPS_ALL = ["):][:900]
    assert steps.index('type: "welcome"') < steps.index('key: "age_range"')
    assert "renderStartQuizWelcomeStep" in APP and "startQuizWelcomeTitle" in APP and '"Поехали"' in APP


def test_there_are_no_invented_reviews_left_in_the_onboarding():
    for name in ("Настя", "Игорь", "Марина", 'type: "testimonials"', "С ADAM уже не одни"):
        assert name not in APP, name
    steps = APP[APP.index("function renderStartQuizFirstStepsStep"):][:1200]
    assert "Отметь её сегодня" in steps and "Загляни к Адаму" in steps


def test_every_quiz_step_type_has_a_renderer():
    types = set(re.findall(r'\{ type: "(\w+)", title', APP[APP.index("const START_QUIZ_STEPS_ALL"):][:2600]))
    render = APP[APP.index("function renderStartQuizStep()"):][:2200]
    for step_type in types - {"choice"}:
        assert f'step.type === "{step_type}"' in render, step_type


def test_the_hints_start_with_how_to_check_the_habit_when_it_is_already_in_the_list():
    schedule = APP[APP.index("function scheduleProductOnboarding()"):][:2400]
    assert "reachedStage < 3" in schedule and schedule.index("showProductHint(2)") < schedule.index("showProductHint(11)")   # 11.11: дальше сразу к Адаму
    step2 = re.search(r"2: \{ target: '\.habits-panel', title: 'Твои привычки', text: '([^']+)'", APP).group(1)
    assert "появится" not in step2, "шаг 2 теперь показывают и тем, у кого привычка уже есть"


# ---------------- Mini App: окно «ударного режима» не лезет поверх квиза ----------------

NODE = shutil.which("node")


def _run_streak_window(tmp_path, scenario):
    start = APP.index("let afterStreakOnboarding = null;")
    end = APP.index("async function maybeShowWeeklyBonus()")
    script = tmp_path / "streak.js"
    script.write_text(r"""
const src = %s;
const calls = []; const timers = [];
const overlay = { hidden: true, classList: { _s: new Set(), add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); }, contains(c){ return this._s.has(c); } } };
const coach = { textContent: '' };
const holder = {
  state: JSON.parse(%s),
  document: { getElementById: (id) => id === 'streakOnboardingOverlay' ? overlay : id === 'streakOnboardingCoach' ? coach : null },
  setTimeout: (f) => { timers.push(f); },
  trk: (n) => calls.push('trk:' + n),
  api: () => { calls.push('api'); return { catch(){ } }; },
  hideProductHint: () => calls.push('hideHint'),
};
// Функции окна читают state/document/api через замыкание — подставляем их через with.
const api2 = new Function('holder', 'with (holder) {' + src + ' return { maybeShowStreakOnboarding, closeStreakOnboarding }; }')(holder);
const out = {};
%s
console.log(JSON.stringify(out));
""" % (json.dumps(APP[start:end]), json.dumps(json.dumps(scenario["state"])), scenario["js"]), encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_streak_window_waits_for_the_quiz_tour_and_nick(tmp_path):
    due = {"streak_onboarding": {"show": True, "message": "Привет"}}
    out = _run_streak_window(tmp_path, {
        "state": {**due, "show_start_quiz": True},
        "js": "out.during_quiz = api2.maybeShowStreakOnboarding(); out.shown_during_quiz = !overlay.hidden;",
    })
    assert out["during_quiz"] is False and out["shown_during_quiz"] is False

    out = _run_streak_window(tmp_path, {
        "state": {**due, "show_app_tour": True},
        "js": "out.during_tour = api2.maybeShowStreakOnboarding(); out.chain = api2.maybeShowStreakOnboarding({ chain: true }); out.shown = !overlay.hidden;",
    })
    assert out["during_tour"] is False and out["chain"] is True and out["shown"] is True, "сама цепочка открывает окно в конце"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_streak_window_runs_what_comes_next_after_it_closes_and_is_not_shown_twice(tmp_path):
    out = _run_streak_window(tmp_path, {
        "state": {"streak_onboarding": {"show": True, "message": "Привет"}},
        "js": """
let next = 0;
out.opened = api2.maybeShowStreakOnboarding({ chain: true, then: () => { next += 1; } });
out.again = api2.maybeShowStreakOnboarding();            // перерисовка экрана не открывает его второй раз
out.tracked = calls.filter(c => c === 'trk:onb_streak_shown').length;
api2.closeStreakOnboarding();
timers.forEach(f => f());
out.next = next; out.hidden = overlay.hidden; out.stateShow = holder.state.streak_onboarding.show;
out.afterClose = api2.maybeShowStreakOnboarding();        // после «Я в игре» окно больше не появляется
out.coach = coach.textContent;
""",
    })
    assert out["opened"] is True and out["again"] is True and out["tracked"] == 1
    assert out["next"] == 1 and out["hidden"] is True and out["stateShow"] is False and out["afterClose"] is False
    assert "Привет" in out["coach"]


async def test_back_to_main_menu_keeps_the_open_app_button(web_url):
    """Найдено в живом чате @aihabbit_bot: «⬅️ Главное меню» показывало одну кнопку «Умные напоминания» — из меню бота было не
    попасть в приложение (тупик: только обратно в напоминания). Теперь там то же меню, что после /start."""
    import handlers.menu as menu_handler
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(message=message, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    await menu_handler.back_menu(callback)
    markup = message.edit_text.await_args.kwargs["reply_markup"]
    first = markup.inline_keyboard[0][0]
    assert first.web_app is not None and "Открыть ADAM" in first.text
    assert "reminders_menu" in {b.callback_data for b in _buttons(markup)}


async def test_review_without_the_app_quiz_the_bot_politely_invites_into_the_app(uid, web_url):
    """Решение владельца 09.11: пользоваться ADAM можно только в приложении (там 16+ и согласие на обработку данных). Кто их не прошёл,
    на /start и на любое сообщение получает вежливое приглашение открыть Mini App — анкета в боте не запускается."""
    from middlewares.access_control import AccessControlMiddleware
    add_user(uid, "u", "Аня")
    message = SimpleNamespace(
        text="/start", from_user=SimpleNamespace(id=uid, username="u", first_name="Аня", full_name="Аня"),
        answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()),
    )
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))
    await start_handler.start(message, state)
    assert "живёт в приложении" in message.answer.call_args.args[0]
    assert message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].web_app.url == URL + "?from=bot_only"
    assert await state.get_state() is None, "анкета в боте не началась"
    from aiogram.types import Message as _M

    class _Msg(_M):
        async def answer(self, text, **kwargs):
            shown.append(text)

    shown = []
    event = _Msg.model_construct(message_id=1, from_user=SimpleNamespace(id=uid, username="u"), text="привет", successful_payment=None)
    handler = AsyncMock()
    await AccessControlMiddleware()(handler, event, {})
    assert handler.await_count == 0 and shown and "живёт в приложении" in shown[0]
