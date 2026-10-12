"""
Парное задание, доработка 12.10: механика как в Duolingo («Парное задание заканчивается завтра! 19/25», вклад каждого под именем),
итог при неудаче и факты о задании для Адама (чат и «Адам пишет первым»).
"""
import itertools
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import streak_scheduler as sched
from db import adam_checkin as ac
from db import get_pair_quest, pair_eve_reminder, pair_users_due_for_new_quest
from db.pair_quests import PAIR_GOAL, RULES_HABITS, compute_progress, context_lines, context_lines_from_state
from db.streak import local_today
from tests.test_pair_habits import DAYS, _counts, _event, _mark, _quest
from tests.test_pair_quests import _one, _sql

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")

_uid_counter = itertools.count(850_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture(autouse=True)
def _no_active_quests_left_behind():
    """БД общая на весь прогон, а settle_pair_quests() и планировщик смотрят ВСЕ идущие задания: набравшее цель задание отсюда
    попало бы в чужой тест («повторно не засчитывается»). Свои задания закрываем сами."""
    yield
    _sql("UPDATE pair_quests SET status='cancelled' WHERE status IN ('active','pending') AND inviter_id>=? AND inviter_id<?", (850_000_000, 860_000_000))


class _Evening(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz).replace(hour=18, minute=0, second=0, microsecond=0)


def _row(quest_id):
    return dict(_one("SELECT * FROM pair_quests WHERE id=?", (quest_id,)))


# ---------------------------------------------------------------------------
# ВКЛАД КАЖДОГО В ЗАЧЁТ (a_total + b_total == total)
# ---------------------------------------------------------------------------

def _shares(a, b, goal=PAIR_GOAL):
    calc = compute_progress(RULES_HABITS, goal, DAYS, _counts(a), _counts(b))
    return calc["total"], calc["a_total"], calc["b_total"]


@pytest.mark.parametrize("a,b", [
    ([2, 1, 3], [1, 3, 0]),                                   # цель далеко: считается всё
    ([3, 3, 2, 3], [3, 3, 3, 0]),                             # последняя привычка ждёт напарника
    ([3, 3, 2, 3], [3, 3, 3, 1]),                             # оба в день завершения
    ([3, 3, 3, 3, 3, 0, 1], [3, 3, 3, 0, 0, 1, 1]),           # удержано два дня подряд
    ([3, 3, 3, 1, 3, 3, 3], [3, 3, 3, 1, 3, 3, 3]),           # после цели ничего не копится
    ([0] * 7, [0] * 7),
])
def test_personal_shares_always_add_up_to_the_shared_progress(a, b):
    total, mine, theirs = _shares(a, b)
    assert mine + theirs == total


def test_a_held_habit_is_not_credited_to_anyone_and_the_day_of_finish_takes_exactly_what_was_needed():
    total, mine, theirs = _shares([3, 3, 2, 3], [3, 3, 3, 0])
    assert (total, mine, theirs) == (19, 10, 9), "три отметки A в последний день: одна удержана и никому не засчитана"
    total, mine, theirs = _shares([3, 3, 2, 3], [3, 3, 3, 1])
    assert total == PAIR_GOAL and mine + theirs == PAIR_GOAL and mine >= 8 and theirs >= 9


def test_view_exposes_shares_that_add_up_and_mirror_for_the_partner(uid):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1])
    _mark(b, quest_id, [1, 2, 0])
    mine, theirs = get_pair_quest(a)["quest"], get_pair_quest(b)["quest"]
    assert (mine["mine"], mine["partner_count"]) == (6, 3) and mine["mine"] + mine["partner_count"] == mine["progress"]
    assert (theirs["mine"], theirs["partner_count"]) == (3, 6)


def test_card_shows_each_persons_share_under_the_name_and_hides_zero():
    assert "function pairShareHtml(count)" in APP_JS
    assert "${pairShareHtml(q.mine)}" in APP_JS and "${pairShareHtml(q.partner_count)}" in APP_JS
    assert 'n > 0 ?' in APP_JS.split("function pairShareHtml(count)")[1].split("}")[0]
    assert ".pq-person .pq-share" in CSS


# ---------------------------------------------------------------------------
# «ЗАДАНИЕ ЗАКАНЧИВАЕТСЯ ЗАВТРА»
# ---------------------------------------------------------------------------

def test_eve_reminder_names_what_is_left_on_the_day_before_the_end(uid):
    a, b, quest_id = _quest(uid)
    from tests.test_pair_quests import _window
    _window(quest_id, start_offset=-5)                         # сегодня шестой день, завтра последний
    _mark(a, quest_id, [3, 3, 3, 0, 0, 0]); _mark(b, quest_id, [3, 2, 0, 0, 0, 0])
    info = pair_eve_reminder(_row(quest_id), a)
    assert info == {"left": 6, "progress": 14, "goal": PAIR_GOAL, "mine_today": 0, "partner_name": "Борис"}
    assert pair_eve_reminder(_row(quest_id), b)["partner_name"] == "Анна"
    _event(a, str(local_today(a)))
    assert pair_eve_reminder(_row(quest_id), a)["mine_today"] == 1


def test_eve_reminder_is_silent_when_nothing_to_say(uid):
    from tests.test_pair_quests import _window
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-5)
    _mark(a, quest_id, [1, 0, 0, 0, 0, 0]); _mark(b, quest_id, [1, 0, 0, 0, 0, 0])
    assert pair_eve_reminder(_row(quest_id), a) is None, "осталось 18, а до конца можно набрать не больше 12 — не пугаем"
    _mark(a, quest_id, [2, 3, 3, 3, 3, 3]); _mark(b, quest_id, [2, 3, 3, 3, 3, 3])
    assert pair_eve_reminder(_row(quest_id), a) is None, "цель уже набрана"
    _window(quest_id, start_offset=-2)
    assert pair_eve_reminder(_row(quest_id), a) is None, "ещё не предпоследний день"
    _window(quest_id, start_offset=-6)
    assert pair_eve_reminder(_row(quest_id), a) is None, "в последний день работает другой пуш"


def test_deadline_texts():
    eve = sched.pair_deadline_text("pairprev", {"left": 6, "progress": 14, "goal": 20, "mine_today": 0}, "Аня")
    assert "заканчивается завтра" in eve and "14/20" in eve and "не хватает 6 привычек" in eve and "<b>Аня</b>" in eve
    assert "Сегодня твои отметки очень нужны" in eve
    done_today = sched.pair_deadline_text("pairprev", {"left": 1, "progress": 19, "goal": 20, "mine_today": 2}, "Аня")
    assert "не хватает 1 привычки" in done_today and "очередь напарника" in done_today
    last = sched.pair_deadline_text("pairlast", {"left": 2, "progress": 18, "goal": 20, "mine_today": 0}, "Аня")
    assert "Последний день" in last and "Не подведи напарника" in last


async def test_scheduler_warns_the_day_before_the_end_once_and_never_on_other_days(uid, monkeypatch):
    from tests.test_pair_quests import _window
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-5)
    _mark(a, quest_id, [3, 3, 3, 0, 0, 0]); _mark(b, quest_id, [3, 2, 0, 0, 0, 0])
    monkeypatch.setattr(sched, "datetime", _Evening)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    await sched.run_pair_quest_notifications(bot)
    sent = {c.args[0]: c.args[1] for c in bot.send_message.call_args_list if c.args[0] in (a, b)}
    assert set(sent) == {a, b} and all("заканчивается завтра" in text for text in sent.values())
    assert "Борис" in sent[a] and "Анна" in sent[b]
    bot.send_message.reset_mock()
    await sched.run_pair_quest_notifications(bot)
    assert not [1 for c in bot.send_message.call_args_list if c.args[0] in (a, b)], "второй раз за день не шлём"

    _window(quest_id, start_offset=-3)                          # четвёртый день — рано
    _sql("DELETE FROM notification_log WHERE user_id IN (?, ?)", (a, b))
    await sched.run_pair_quest_notifications(bot)
    assert not [1 for c in bot.send_message.call_args_list if c.args[0] in (a, b)]


# ---------------------------------------------------------------------------
# НЕУДАЧА: ПУШ «НОВОЕ ЗАДАНИЕ» НАЧИНАЕТСЯ С ИТОГА
# ---------------------------------------------------------------------------

def test_restart_text_opens_with_the_result_when_the_goal_was_missed():
    friends = [{"first_name": "Боб", "active_days": 5}]
    close = sched.pair_restart_text("pairnew1", friends, {"progress": 15, "goal": 20})
    assert "не хватило совсем немного — 15 из 20" in close and "Боб" in close and "сундук" in close
    far = sched.pair_restart_text("pairnew1", friends, {"progress": 8, "goal": 20})
    assert "вышло 8 из 20" in far and "совсем немного" not in far
    assert "Новое парное задание!" in sched.pair_restart_text("pairnew1", friends), "после выполненного — прежний текст"
    assert sched.pair_restart_text("pairnew1", [], {"progress": 15, "goal": 20}) is None
    assert "ждёт напарника" in sched.pair_restart_text("pairnew2", friends, {"progress": 15, "goal": 20})


def test_due_list_carries_the_final_score_only_for_missed_quests(uid):
    from tests.test_pair_quests import _window
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-7)                         # окно кончилось вчера
    _mark(a, quest_id, [3, 3, 3, 0, 0, 0, 0]); _mark(b, quest_id, [3, 2, 0, 0, 0, 0, 0])
    _sql("UPDATE pair_quests SET status='expired', finished_at=CURRENT_TIMESTAMP WHERE id=?", (quest_id,))
    item = next(i for i in pair_users_due_for_new_quest() if i["user_id"] == a)
    assert item["status"] == "expired" and (item["progress"], item["goal"]) == (14, PAIR_GOAL)


async def test_scheduler_sends_the_missed_result_in_the_noon_push(uid, monkeypatch):
    from db import make_friends
    from tests.test_pair_quests import _window, _day, _user
    a, b, quest_id = _quest(uid)
    friend = _user(uid + 5, "Дима")
    assert make_friends(a, friend)
    for i in range(1, 6):
        _day(friend, -i)
    _window(quest_id, start_offset=-7)
    _mark(a, quest_id, [3, 3, 3, 0, 0, 0, 0]); _mark(b, quest_id, [3, 2, 0, 0, 0, 0, 0])
    _sql("UPDATE pair_quests SET status='expired', finished_at=CURRENT_TIMESTAMP WHERE id=?", (quest_id,))

    class _Noon(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)

    monkeypatch.setattr(sched, "datetime", _Noon)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    await sched.run_pair_quest_notifications(bot)
    to_a = [c.args[1] for c in bot.send_message.call_args_list if c.args[0] == a]
    assert len(to_a) == 1 and "14 из 20" in to_a[0] and "Дима" in to_a[0]


# ---------------------------------------------------------------------------
# ФАКТЫ О ЗАДАНИИ ДЛЯ АДАМА
# ---------------------------------------------------------------------------

def _view(**over):
    quest = {
        "phase": "active", "partner": {"first_name": "Борис"}, "progress": 14, "goal": 20, "days_left": 3, "need": 6,
        "today": {"me": 1, "partner": 0, "held": 0}, "cap": 3, "waiting_for": None, "pace": "ok", "starts_in": 0, "claimable": False,
    }
    quest.update(over)
    return {"quest": quest, "incoming": [], "outgoing": None, "recent_fail": None}


def test_context_lines_for_an_active_quest():
    lines = context_lines_from_state(_view(), include_idle=False)
    assert lines[0] == "Парное задание с напарником «Борис»: общий счёт 14 из 20, до конца 3 дн., нужно закрыть ещё 6"
    assert "у человека 1, у напарника 0" in lines[1] and "не больше 3 в день" in lines[1]
    assert len(lines) == 2, "темп в порядке и последней привычки нет — больше сказать нечего"


@pytest.mark.parametrize("over,expected", [
    ({"waiting_for": "me"}, "за самим человеком"),
    ({"waiting_for": "partner"}, "за напарником"),
    ({"waiting_for": "both"}, "отметятся оба"),
    ({"pace": "tight"}, "впритык"),
    ({"pace": "lost"}, "не обещай сундук"),
])
def test_context_lines_say_who_waits_for_whom_and_how_tight_it_is(over, expected):
    assert any(expected in line for line in context_lines_from_state(_view(**over), include_idle=False))


def test_idle_states_are_for_the_chat_only():
    scheduled = _view(phase="scheduled", starts_in=1)
    assert context_lines_from_state(scheduled) == ["Парное задание с напарником «Борис» стартует завтра"]
    assert context_lines_from_state(scheduled, include_idle=False) == []
    chest = _view(phase="completed", claimable=True)
    assert "сундук ещё не открыт" in context_lines_from_state(chest)[0]
    invite = {"quest": None, "incoming": [{"id": 1, "from": {"first_name": "Аня"}}], "outgoing": None, "recent_fail": None}
    assert "«Аня» зовёт" in context_lines_from_state(invite)[0] and context_lines_from_state(invite, include_idle=False) == []
    waiting = {"quest": None, "incoming": [], "outgoing": {"id": 2, "to": {"first_name": "Вика"}}, "recent_fail": None}
    assert "для «Вика» отправлено" in context_lines_from_state(waiting)[0]
    failed = {"quest": None, "incoming": [], "outgoing": None, "recent_fail": {"partner": {"first_name": "Гриша"}}}
    assert "без упрёков" in context_lines_from_state(failed)[0]
    assert context_lines_from_state({"quest": None, "incoming": [], "outgoing": None, "recent_fail": None}) == []


def test_context_lines_from_the_database(uid):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1]); _mark(b, quest_id, [1, 2, 0])
    lines = context_lines(a)
    assert lines[0].startswith("Парное задание с напарником «Борис»: общий счёт 9 из 20")
    assert context_lines(uid + 777) == [], "у человека без заданий фактов нет"


def test_adam_checkin_state_and_prompt_carry_the_active_quest(uid):
    from tests.test_adam_checkin import _user as checkin_user
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1]); _mark(b, quest_id, [1, 2, 0])
    state = ac.build_checkin_state(a)
    assert state["pair_lines"] and "общий счёт 9 из 20" in state["pair_lines"][0]
    prompt = ac.build_checkin_prompt(state, "behind", "no_greeting")
    assert "Парное задание с напарником «Борис»" in prompt
    assert "напарником не упрекай" in prompt and "без склонения" in prompt
    checkin_user(uid + 50)
    lonely = ac.build_checkin_state(uid + 50)
    assert lonely["pair_lines"] == []
    assert "Парное задание" not in ac.build_checkin_prompt(lonely, "free_chat", "no_greeting")


# ---------------------------------------------------------------------------
# ОТМЕТКА МИМО MINI APP: ЧАТ С АДАМОМ И КНОПКИ БОТА
# ---------------------------------------------------------------------------

def _eligible(monkeypatch, value=True):
    import db.friends as friends_mod
    monkeypatch.setattr(friends_mod, "_can_receive_nudge", lambda row: value)


def test_partner_is_told_when_the_mark_was_made_outside_the_app(uid, monkeypatch):
    from db import pair_pending_partner_events
    a, b, quest_id = _quest(uid)
    _eligible(monkeypatch)
    assert [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id] == [], "отметок нет — сообщать нечего"
    _event(a, str(local_today(a)))                                   # A закрыл привычку в чате с Адамом
    events = [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id]
    assert len(events) == 1
    assert (events[0]["type"], events[0]["to"], events[0]["from"], events[0]["progress"], events[0]["final"]) == ("partner_done", b, a, 1, False)
    assert [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id] == [], "тот же журнал, что у приложения: второй раз за день не шлём"


def test_no_event_when_the_partner_is_already_in_action_or_cannot_be_nudged_now(uid, monkeypatch):
    from db import pair_pending_partner_events
    a, b, quest_id = _quest(uid)
    _event(a, str(local_today(a))); _event(b, str(local_today(b)))
    _eligible(monkeypatch)
    assert [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id] == [], "оба уже в деле"

    a2, b2, quest2 = _quest(uid + 10)
    _event(a2, str(local_today(a2)))
    _eligible(monkeypatch, False)                                    # ночь, тихие часы или дел не осталось
    assert [e for e in pair_pending_partner_events() if e["quest_id"] == quest2] == []
    _eligible(monkeypatch, True)
    later = [e for e in pair_pending_partner_events() if e["quest_id"] == quest2]
    assert len(later) == 1 and later[0]["to"] == b2, "когда подтолкнуть можно — событие появляется, а не пропадает"


def test_an_app_push_and_a_scheduler_push_never_double_up(uid, monkeypatch):
    from db import pair_on_day_completed, pair_pending_partner_events
    a, b, quest_id = _quest(uid)
    _eligible(monkeypatch)
    _event(a, str(local_today(a)))
    assert [e["type"] for e in pair_on_day_completed(a)] == ["partner_done"], "приложение отправило пуш первым"
    assert [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id] == []


def test_finished_goal_is_left_to_the_settle_job(uid, monkeypatch):
    from db import pair_pending_partner_events
    a, b, quest_id = _quest(uid)
    from tests.test_pair_quests import _window
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 3, 1]); _mark(b, quest_id, [3, 3, 3, 0])      # 19, цель ждёт напарника
    _eligible(monkeypatch)
    events = [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id]
    assert len(events) == 1 and events[0]["final"] is True and events[0]["to"] == b
    _mark(b, quest_id, [0, 0, 0, 1])                                        # цель набрана
    assert [e for e in pair_pending_partner_events() if e["quest_id"] == quest_id] == []


async def test_scheduler_tells_the_partner_about_a_mark_made_in_the_chat(uid, monkeypatch):
    a, b, quest_id = _quest(uid)
    _eligible(monkeypatch)
    _event(a, str(local_today(a)))
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    await sched.run_pair_quest_notifications(bot)
    to_b = [c.args[1] for c in bot.send_message.call_args_list if c.args[0] == b]
    assert len(to_b) == 1 and "Анна" in to_b[0] and "Твой ход" in to_b[0] and "1/20" in to_b[0]
    await sched.run_pair_quest_notifications(bot)
    assert len([c for c in bot.send_message.call_args_list if c.args[0] == b]) == 1


def test_partner_done_text_is_shared_between_the_app_and_the_scheduler():
    from db import pair_partner_done_text
    plain = pair_partner_done_text("Аня", {"progress": 7, "goal": 20, "final": False})
    assert plain == "🤝 <b>Аня</b> уже в деле — на двоих 7/20 привычек. Твой ход! 🔥"
    assert "до сундука осталась одна привычка" in pair_partner_done_text("Аня", {"progress": 19, "goal": 20, "final": True})
    server = (ROOT / "webapp" / "webapp_server.py").read_text(encoding="utf-8")
    assert "pair_partner_done_text(name, event)" in server


# ---------------------------------------------------------------------------
# ПОСЛЕ ОТМЕТКИ В ЧАТЕ: что осталось и как идёт парное задание
# ---------------------------------------------------------------------------

def test_chat_line_for_every_state_of_the_active_quest(uid):
    from db.pair_quests import chat_line
    from tests.test_pair_quests import _window
    a, b, quest_id = _quest(uid)
    assert chat_line(uid + 500) == ""
    _mark(a, quest_id, [2, 1, 1])
    assert chat_line(a) == "🤝 Парное задание: 4/20 · напарник сегодня ещё не отметился"
    _mark(b, quest_id, [0, 0, 1])
    assert chat_line(a) == "🤝 Парное задание: 5/20 · вы оба сегодня в деле 🔥"
    a2, b2, quest2 = _quest(uid + 20)
    _window(quest2, start_offset=-3)
    _mark(a2, quest2, [3, 3, 3, 3]); _mark(b2, quest2, [3, 3, 2, 0])         # 17 + 3 → осталась одна, она за напарником
    assert chat_line(a2).endswith("последняя привычка за напарником")
    assert chat_line(b2).endswith("последняя привычка за тобой")


def test_marking_a_habit_in_the_chat_shows_what_is_left_and_the_pair_progress(uid):
    from db import add_habit
    from habit_intents import try_handle_habit_intent
    a, b, quest_id = _quest(uid)
    add_habit(a, "Чтение")
    reply = try_handle_habit_intent(a, "выполни привычку Бег")
    assert "отмечена выполненной" in reply
    assert "Осталось: «Чтение»." in reply
    assert "🤝 Парное задание: 1/20 · напарник сегодня ещё не отметился" in reply
    last = try_handle_habit_intent(a, "выполни привычку Чтение")
    assert "🎉 Все привычки на сегодня выполнены!" in last and "Осталось" not in last
    assert "🤝 Парное задание: 2/20" in last


def test_without_a_quest_the_chat_reply_has_no_pair_line(uid):
    from habit_intents import try_handle_habit_intent
    from tests.test_pair_quests import _user as pair_user
    pair_user(uid, "Одиночка")
    reply = try_handle_habit_intent(uid, "выполни привычку Бег")
    assert "🎉 Все привычки на сегодня выполнены!" in reply and "Парное" not in reply
