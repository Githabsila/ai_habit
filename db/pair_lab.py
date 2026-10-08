"""
«Лаборатория» в админке: демо-состояния парного задания БЕЗ записи в базу — чтобы смотреть новый дизайн и крайние случаи
до того, как они дойдут до пользователей. Карточка рисуется настоящим кодом приложения (app.js) на данных, которые собирает
та же чистая функция, что и для живых заданий (db/pair_quests.py::_build_view), поэтому демо не расходится с реальностью.
"""
from datetime import date, timedelta

from . import pair_quests as pq

PARTNER = {
    "telegram_id": 0, "first_name": "Александр", "handle": None,
    "avatar_id": "default", "frame_id": "default", "streak": 12,
}

# (ключ, подпись) — в порядке показа
SCENARIOS = (
    ("none", "Нет задания — выбрать союзника"),
    ("invite_out", "Приглашение отправлено"),
    ("invite_in", "Тебя позвали в задание"),
    ("scheduled", "Старт завтра"),
    ("day1", "День 1 — первые привычки"),
    ("mid", "День 4 — идём в темпе"),
    ("tight", "День 6 — темп ниже нужного"),
    ("lost", "День 7 — уже не успеть"),
    ("hold_partner", "Последняя привычка ждёт друга"),
    ("hold_me", "Последняя привычка — моя"),
    ("done", "Выполнено — сундук ждёт"),
    ("claimed", "Сундук открыт"),
)


def lab_scenarios():
    return [{"key": key, "title": title} for key, title in SCENARIOS]


def _view(*, status="active", start_offset=-3, me=(), partner=(), claimed=False, partner_state=None):
    today = date.today()
    start = today + timedelta(days=start_offset)
    end = start + timedelta(days=pq.PAIR_WINDOW_DAYS - 1)
    days = pq._window_days(str(start), str(end))
    a_counts = {days[i]: n for i, n in enumerate(me)}
    b_counts = {days[i]: n for i, n in enumerate(partner)}
    calc = pq.compute_progress(pq.RULES_HABITS, pq.PAIR_GOAL, days, a_counts, b_counts)
    return pq._build_view(
        quest_id=0, status=status, goal=pq.PAIR_GOAL, rules=pq.RULES_HABITS,
        start_day=str(start), end_day=str(end), viewer_is_inviter=True, partner=dict(PARTNER),
        calc=calc, today=today, claimed=claimed, partner_state=partner_state,
    ), str(end)


def build_scenario(key):
    """payload формата get_pair_quest(): {"quest": ..., "outgoing": ..., ...} или None, если такого сценария нет."""
    if key not in dict(SCENARIOS):
        return None
    state = pq._state_template(False)
    state.update({"can_choose": False, "choose_from_day": None, "quest": None, "outgoing": None, "incoming": [],
                  "recent_fail": None, "completed_total": 2})
    if key == "none":
        state["can_choose"] = True
    elif key == "invite_out":
        state["outgoing"] = {"id": 0, "to": dict(PARTNER)}
    elif key == "invite_in":
        state["incoming"] = [{"id": 0, "from": dict(PARTNER)}]
    elif key == "scheduled":
        state["quest"], _ = _view(start_offset=1)
    elif key == "day1":
        state["quest"], _ = _view(start_offset=0, me=(2,), partner=(1,), partner_state="can_remind")
    elif key == "mid":
        state["quest"], _ = _view(start_offset=-3, me=(3, 2, 3, 1), partner=(2, 3, 1, 0), partner_state="can_remind")
    elif key == "tight":
        state["quest"], _ = _view(start_offset=-5, me=(2, 1, 2, 1, 1, 0), partner=(1, 1, 1, 1, 0, 0), partner_state="can_remind")
    elif key == "lost":
        state["quest"], _ = _view(start_offset=-5, me=(1, 0, 1, 0, 0, 0), partner=(0, 1, 0, 0, 0, 0), partner_state="can_remind")
    elif key == "hold_partner":
        state["quest"], _ = _view(start_offset=-3, me=(3, 3, 2, 3), partner=(3, 3, 3, 0), partner_state="can_remind")
    elif key == "hold_me":
        state["quest"], _ = _view(start_offset=-3, me=(3, 3, 3, 0), partner=(3, 3, 2, 3))
    elif key == "done":
        state["quest"], _ = _view(status="completed", start_offset=-3, me=(3, 3, 3, 1), partner=(3, 3, 3, 1))
    elif key == "claimed":
        state["quest"], end = _view(status="completed", start_offset=-3, me=(3, 3, 3, 1), partner=(3, 3, 3, 1), claimed=True)
        state["choose_from_day"] = str(date.fromisoformat(end) + timedelta(days=1))
    return state
