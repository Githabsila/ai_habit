"""
Изменение привычки (п.8 владельца, 20.10): привычек может быть много, а поменять нужно одну — у каждой в раскрытой строке (✏️)
кнопка «Изменить», окно с названием, категорией, важностью и временем; сохраняется только эта привычка. Здесь: маршрут
PUT /api/habits/{id} (в том числе «Без категории» — раньше категорию снять было нельзя), разметка окна, скрипт, стили, симуляция.
"""
import json
import re
from pathlib import Path

from db import add_habit, add_user, get_habits
from db.habits import MAX_HABIT_TITLE_LENGTH
from tests.conftest import sign_init_data
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _habit(uid_, habit_id):
    return next(h for h in get_habits(uid_) if h["id"] == habit_id)


def _three(uid_):
    """id трёх привычек в порядке создания: Зарядка, Читать, Вода (get_habits отдаёт их в своём порядке — по приоритету и времени)."""
    add_user(uid_, "u", "Test")
    add_habit(uid_, "Зарядка", planned_time="07:30", category="health", priority=1)
    add_habit(uid_, "Читать", planned_time="21:00", category="study", priority=2)
    add_habit(uid_, "Вода", category="other", priority=1)
    by_title = {h["title"]: h["id"] for h in get_habits(uid_)}
    return [by_title["Зарядка"], by_title["Читать"], by_title["Вода"]]


async def _put(client, uid_, habit_id, body):
    return await client.put(f"/api/habits/{habit_id}", headers=_headers(uid_), data=json.dumps(body))


# ---------------------------------------------------------------------------------------------------------------- сервер

async def test_editing_one_habit_leaves_the_others_as_they_were(client, uid):
    ids = _three(uid)
    before = {h["id"]: dict(h) for h in get_habits(uid)}
    order_before = [h["id"] for h in get_habits(uid)]
    r = await _put(client, uid, ids[1], {"title": "Читать книгу", "category": "mind", "priority": 1, "planned_time": "22:15"})
    assert r.status == 200 and (await r.json()) == {"ok": True}
    changed = _habit(uid, ids[1])
    assert (changed["title"], changed["category"], changed["priority"], changed["planned_time"]) == ("Читать книгу", "mind", 1, "22:15")
    for other in (ids[0], ids[2]):
        assert dict(_habit(uid, other)) == before[other], "остальные привычки не тронуты"
    assert [h["id"] for h in get_habits(uid)] == order_before, "порядок в списке прежний"


async def test_the_category_can_be_cleared_and_an_absent_one_is_not_touched(client, uid):
    ids = _three(uid)
    r = await _put(client, uid, ids[0], {"title": "Зарядка", "category": ""})
    assert r.status == 200 and _habit(uid, ids[0])["category"] is None, "«Без категории» снимает категорию"
    r = await _put(client, uid, ids[1], {"title": "Читать"})
    assert r.status == 200 and _habit(uid, ids[1])["category"] == "study", "без ключа category категория остаётся"
    r = await _put(client, uid, ids[2], {"title": "Вода", "category": "work"})
    assert _habit(uid, ids[2])["category"] == "work"
    r = await _put(client, uid, ids[2], {"title": "Вода", "category": "несуществующая"})
    assert r.status == 200 and _habit(uid, ids[2])["category"] == "work", "неизвестная категория ничего не меняет"
    r = await _put(client, uid, ids[2], {"title": "Вода", "category": None})
    assert _habit(uid, ids[2])["category"] is None


async def test_priority_and_time_are_set_and_cleared(client, uid):
    ids = _three(uid)
    await _put(client, uid, ids[0], {"title": "Зарядка", "priority": 2})
    assert _habit(uid, ids[0])["priority"] == 2 and _habit(uid, ids[0])["planned_time"] == "07:30"
    await _put(client, uid, ids[0], {"title": "Зарядка", "priority": 1, "planned_time": ""})
    assert _habit(uid, ids[0])["priority"] == 1 and _habit(uid, ids[0])["planned_time"] is None
    await _put(client, uid, ids[0], {"title": "Зарядка"})
    assert _habit(uid, ids[0])["priority"] == 1, "без ключа priority важность остаётся"
    bad = await _put(client, uid, ids[0], {"title": "Другое имя", "planned_time": "25:99"})
    assert bad.status == 400 and (await bad.json())["error"] == "invalid_time"
    assert _habit(uid, ids[0])["title"] == "Зарядка", "при ошибке времени ничего не сохраняется"


async def test_a_short_title_is_refused_and_a_long_one_is_cut(client, uid):
    ids = _three(uid)
    short = await _put(client, uid, ids[0], {"title": " я "})
    assert short.status == 400 and (await short.json())["error"] == "title_too_short"
    assert _habit(uid, ids[0])["title"] == "Зарядка"
    await _put(client, uid, ids[0], {"title": "д" * 120})
    assert _habit(uid, ids[0])["title"] == "д" * MAX_HABIT_TITLE_LENGTH


async def test_nobody_edits_a_foreign_habit(client, uid):
    ids = _three(uid)
    stranger = uid + 1
    add_user(stranger, "s", "Stranger")
    r = await _put(client, stranger, ids[0], {"title": "Чужое имя"})
    assert r.status in (403, 404)
    assert _habit(uid, ids[0])["title"] == "Зарядка"


async def test_editing_a_done_habit_keeps_it_done(client, uid):
    ids = _three(uid)
    done = await client.post(f"/api/habits/{ids[0]}/complete", headers=_headers(uid))
    assert done.status == 200
    await _put(client, uid, ids[0], {"title": "Зарядка 10 минут"})
    habit = _habit(uid, ids[0])
    assert habit["title"] == "Зарядка 10 минут" and habit["completed"] == 1


# ---------------------------------------------------------------------------------------------------------------- окно и скрипт

def test_the_window_markup_has_every_field_and_both_buttons():
    start = INDEX.index('id="habitEditSheet"')
    block = INDEX[start - 80:start + 2800]
    block = block[:block.index("</section>")]
    for needle in ('class="feedback-sheet habit-edit-sheet"', 'role="dialog"', 'aria-labelledby="habitEditHeading"', 'id="habitEditBackdrop"',
                   'id="habitEditTitle"', 'maxlength="50"', 'id="habitEditCats"', 'role="radiogroup"', 'id="habitEditPriority"',
                   'id="habitEditStar"', 'id="habitEditTime"', 'type="time"', 'id="habitEditTimeClear"', 'id="habitEditCancel"', 'id="habitEditSave"'):
        assert needle in block, needle
    assert block.index('id="habitEditTitle"') < block.index('id="habitEditCats"') < block.index('id="habitEditPriority"') < block.index('id="habitEditTime"')
    assert "hidden" in INDEX[start - 80:start + 120]


def test_each_habit_row_gets_an_edit_button_that_opens_only_that_habit():
    row = APP_JS[APP_JS.index("function habitItemHtml(h)"):][:4200]
    assert 'data-action="edit" aria-label="Изменить привычку">${ICON_EDIT}' in row
    assert row.index('data-action="edit"') < row.index('data-action="edit-time"') < row.index("${noteBtn}")
    handler = APP_JS[APP_JS.index('if (action === "edit") {'):][:120]
    assert "openHabitEdit(habitId)" in handler and "return;" in handler
    assert "initHabitEdit();" in APP_JS and "habitEditSheet" in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:900]


def test_saving_sends_one_habit_and_only_when_something_changed():
    save = APP_JS[APP_JS.index("async function saveHabitEdit()"):][:2400]
    assert 'api(`/api/habits/${s.id}`, {' in save and 'method: "PUT"' in save
    assert "title, category: s.category, priority: s.priority, planned_time: time" in save
    assert "if (!changed.length) { closeHabitEdit(); return; }" in save
    assert "if (s.sim)" in save and save.index("if (s.sim)") < save.index("api(`/api/habits"), "симуляция ничего не сохраняет"
    assert "title.length < 2" in save and "await loadBootstrap()" in save
    assert 'trk("habit_edit_save"' in save and 'trk("habit_edit_open"' in APP_JS


def test_the_simulation_opens_the_real_window_on_a_demo_habit():
    assert "habit_edit: () => openHabitEdit(0, { habit: HABIT_EDIT_SIM, sim: true })," in APP_JS
    assert re.search(r'const HABIT_EDIT_SIM = \{ id: 0, title: "[^"]+", category: "study", priority: 2, planned_time: "21:30", place: 3, total: 5 \};', APP_JS)


def test_the_window_text_does_not_repeat_itself():
    start = INDEX.index('id="habitEditHeading"')
    block = INDEX[start:start + 2200]
    heading = re.search(r'id="habitEditHeading">([^<]+)<', block).group(1)
    labels = re.findall(r'class="habit-edit-label"[^>]*>([^<]+)<', block)
    priority = re.sub(r"<[^>]+>", " ", block[block.index('id="habitEditPriority"'):][:400].split("</button>")[0])
    buttons = [re.search(rf'id="{i}">([^<]+)<', block).group(1) for i in ("habitEditCancel", "habitEditSave")]
    assert heading == "Изменить привычку" and labels[:1] == ["Название"], labels
    assert not shared(heading, *labels, priority, *buttons, "Нет", "Убрать"), "заголовок, подписи и кнопки не повторяют слова"


def test_the_window_styles_are_light_for_weak_webviews():
    css = CSS[CSS.index("Изменение привычки (app.js::openHabitEdit)"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    assert ".habit-edit-chip.is-active" in css and ".habit-item__edit" in css and ".habit-edit-priority.is-on" in css
    for forbidden in ("filter", "backdrop-filter", "infinite"):
        assert forbidden not in css, forbidden
    assert "Окно «Сундук»" not in css.split("*/", 1)[1], "блок лежит до сундука, как требуют срезы стилей в тестах"


def test_the_event_and_the_simulation_are_registered():
    from db.sim_lab import SCENES
    from db.tracking import EVENT_LABELS
    assert "habit_edit" in [row[0] for row in SCENES]
    assert "habit_edit_open" in EVENT_LABELS and "habit_edit_save" in EVENT_LABELS
