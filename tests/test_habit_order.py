"""
Ручной порядок привычек (п.8 владельца, 21.10: «у пользователя их много, все в одном порядке, а он хочет сдвинуть одну»): по умолчанию новые
сверху, как раньше; «Выше / Ниже / В начало» в окне «Изменить привычку» двигают ТОЛЬКО эту привычку, остальные сохраняют порядок;
новые привычки после этого снова встают сверху; порядок у каждого человека свой.
"""
import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from db import add_habit, add_user, delete_habit, get_habits, move_habit
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
_counter = itertools.count(859_950_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _habits(uid_, *titles):
    add_user(uid_, f"u{uid_}", "Аня")
    for title in titles:
        add_habit(uid_, title)
    return {h["title"]: h["id"] for h in get_habits(uid_)}


def _titles(uid_):
    return [h["title"] for h in get_habits(uid_)]


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------------------------------------------- база

def test_the_default_order_is_still_newest_first(uid):
    _habits(uid, "А", "Б", "В", "Г")
    assert _titles(uid) == ["Г", "В", "Б", "А"]


def test_one_habit_moves_and_the_others_keep_their_order(uid):
    ids = _habits(uid, "А", "Б", "В", "Г")                    # список: Г, В, Б, А
    assert move_habit(uid, ids["А"], "up") == [ids["Г"], ids["В"], ids["А"], ids["Б"]]
    assert _titles(uid) == ["Г", "В", "А", "Б"]
    move_habit(uid, ids["А"], "top")
    assert _titles(uid) == ["А", "Г", "В", "Б"]
    move_habit(uid, ids["А"], "down")
    assert _titles(uid) == ["Г", "А", "В", "Б"]
    move_habit(uid, ids["Г"], "bottom")
    assert _titles(uid) == ["А", "В", "Б", "Г"]


def test_moving_past_the_edge_changes_nothing(uid):
    ids = _habits(uid, "А", "Б", "В")                          # В, Б, А
    before = _titles(uid)
    assert move_habit(uid, ids["В"], "up") == [ids["В"], ids["Б"], ids["А"]] and _titles(uid) == before
    assert move_habit(uid, ids["В"], "top") and _titles(uid) == before
    move_habit(uid, ids["А"], "down")
    assert _titles(uid) == before
    single = uid + 1
    only = _habits(single, "Одна")
    assert move_habit(single, only["Одна"], "down") == [only["Одна"]]


def test_a_new_habit_goes_on_top_and_the_chosen_order_stays_below(uid):
    ids = _habits(uid, "А", "Б", "В")                          # В, Б, А
    move_habit(uid, ids["В"], "bottom")                        # Б, А, В
    assert _titles(uid) == ["Б", "А", "В"]
    add_habit(uid, "Новая")
    assert _titles(uid) == ["Новая", "Б", "А", "В"], "новые — сверху, как всегда, расставленные вручную — под ними"
    move_habit(uid, get_habits(uid)[0]["id"], "bottom")
    assert _titles(uid) == ["Б", "А", "В", "Новая"]


def test_deleting_keeps_the_rest_in_place(uid):
    ids = _habits(uid, "А", "Б", "В", "Г")                     # Г, В, Б, А
    move_habit(uid, ids["А"], "top")                           # А, Г, В, Б
    delete_habit(ids["Г"])
    assert _titles(uid) == ["А", "В", "Б"]
    move_habit(uid, ids["Б"], "up")
    assert _titles(uid) == ["А", "Б", "В"]


def test_order_is_personal_and_foreign_or_unknown_moves_are_refused(uid):
    mine = _habits(uid, "А", "Б")
    other = _habits(uid + 1, "Х", "У")
    assert move_habit(uid, other["Х"], "top") is None, "чужую привычку двигать нельзя"
    assert _titles(uid + 1) == ["У", "Х"]
    assert move_habit(uid, 999_999_999, "top") is None
    with pytest.raises(ValueError):
        move_habit(uid, mine["А"], "sideways")
    move_habit(uid, mine["А"], "top")
    assert _titles(uid) == ["А", "Б"] and _titles(uid + 1) == ["У", "Х"]


# ---------------------------------------------------------------------------------------------------------------- API

async def test_the_move_route_answers_with_the_new_order_and_the_app_reads_it(client, uid):
    ids = _habits(uid, "А", "Б", "В")                          # В, Б, А
    resp = await client.post(f"/api/habits/{ids['А']}/move", headers=_headers(uid), data=json.dumps({"to": "top"}))
    body = await resp.json()
    assert resp.status == 200 and body == {"ok": True, "order": [ids["А"], ids["В"], ids["Б"]], "place": 1}
    boot = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert [h["title"] for h in boot["habits"]] == ["А", "В", "Б"], "Главная получает порядок, который выбрал человек"
    down = await (await client.post(f"/api/habits/{ids['А']}/move", headers=_headers(uid), data=json.dumps({"to": "down"}))).json()
    assert down["place"] == 2 and down["order"][1] == ids["А"]


async def test_the_move_route_refuses_bad_requests(client, uid):
    mine = _habits(uid, "А", "Б")
    other = _habits(uid + 1, "Х")
    assert (await client.post(f"/api/habits/{other['Х']}/move", headers=_headers(uid), data=json.dumps({"to": "top"}))).status == 404
    bad = await client.post(f"/api/habits/{mine['А']}/move", headers=_headers(uid), data=json.dumps({"to": "sideways"}))
    assert bad.status == 400 and (await bad.json())["error"] == "invalid_move"
    assert (await client.post(f"/api/habits/{mine['А']}/move", headers=_headers(uid), data="not json")).status == 400
    assert (await client.post(f"/api/habits/{mine['А']}/move", data=json.dumps({"to": "top"}), headers={"Content-Type": "application/json"})).status in (401, 403)
    assert _titles(uid) == ["Б", "А"], "ничего не изменилось"


# ---------------------------------------------------------------------------------------------------------------- окно и скрипт

def test_the_edit_window_has_the_order_row():
    block = INDEX[INDEX.index('id="habitEditSheet"'):][:3600]
    for needle in ('id="habitEditOrderLabel">Место в списке<', 'role="group" aria-labelledby="habitEditOrderLabel"', 'id="habitEditUp">Выше<',
                   'id="habitEditDown">Ниже<', 'id="habitEditTop">В начало<', 'id="habitEditOrderHint"'):
        assert needle in block, needle
    assert block.index('id="habitEditPriority"') < block.index('id="habitEditOrderLabel"') < block.index('id="habitEditTime"')
    css = CSS[CSS.index("Изменение привычки (app.js::openHabitEdit)"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    assert ".habit-edit-order__btn{" in css and ".habit-edit-order__btn:disabled" in css
    for forbidden in ("filter", "infinite", "animation"):
        assert forbidden not in css


def test_the_script_moves_only_this_habit_and_asks_the_server():
    move = APP_JS[APP_JS.index("async function moveHabitEdit(to) {"):][:1300]
    assert 'api(`/api/habits/${s.id}/move`, { method: "POST", body: JSON.stringify({ to }) })' in move
    assert move.index("if (s.sim)") < move.index("api(`/api/habits"), "в симуляции сервер не трогаем"
    assert "applyHabitOrder(res.order);" in move and 'trk("habit_moved", to, null);' in move
    for needle in ('"habitEditUp")?.addEventListener("click", () => moveHabitEdit("up"))', '"habitEditDown")?.addEventListener("click", () => moveHabitEdit("down"))',
                   '"habitEditTop")?.addEventListener("click", () => moveHabitEdit("top"))', "const HABIT_EDIT_SIM = {", "place: 3, total: 5 };"):
        assert needle in APP_JS, needle
    assert "app.js?v=20261021_QUESTS_V87" in INDEX and "style.css?v=20261021_QUESTS_V76" in INDEX


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_list_follows_the_order_from_the_server_and_the_hint_follows_the_place():
    text = APP_JS.replace("\r\n", "\n")

    def _function(header):
        start = text.index(header)
        return text[start:text.index("\n}\n", start) + 3]

    script = r"""
const els = {};
const el = (id) => (els[id] = els[id] || { textContent: "", disabled: false });
const document = { getElementById: el };
let habitEdit = null; let rendered = 0;
const state = { habits: [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }] };
function renderHabits() { rendered += 1; }
%s
%s
const out = {};
applyHabitOrder([3, 1, 2]);
out.order = state.habits.map((h) => h.id); out.rendered = rendered;
habitEdit = { place: 3, total: 4 }; renderHabitEditOrder();
out.middle = { hint: els.habitEditOrderHint.textContent, up: els.habitEditUp.disabled, top: els.habitEditTop.disabled, down: els.habitEditDown.disabled };
habitEdit = { place: 1, total: 4 }; renderHabitEditOrder();
out.first = { up: els.habitEditUp.disabled, top: els.habitEditTop.disabled, down: els.habitEditDown.disabled };
habitEdit = { place: 4, total: 4 }; renderHabitEditOrder();
out.last = { up: els.habitEditUp.disabled, down: els.habitEditDown.disabled };
habitEdit = { place: 1, total: 1 }; renderHabitEditOrder();
out.alone = { hint: els.habitEditOrderHint.textContent, up: els.habitEditUp.disabled, down: els.habitEditDown.disabled };
process.stdout.write(JSON.stringify(out));
""" % (_function("function applyHabitOrder(order) {"), _function("function renderHabitEditOrder() {"))
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["order"] == [3, 1, 2, 4], "привычки из ответа — по порядку, неизвестные в конце"
    assert out["rendered"] == 1
    assert out["middle"] == {"hint": "Сейчас 3 из 4", "up": False, "top": False, "down": False}
    assert out["first"] == {"up": True, "top": True, "down": False} and out["last"] == {"up": False, "down": True}
    assert out["alone"] == {"hint": "Привычка одна — двигать нечего", "up": True, "down": True}


def test_the_window_words_do_not_repeat_with_the_new_row():
    import re
    from tests.test_window_copy import shared
    start = INDEX.index('id="habitEditHeading"')
    block = INDEX[start:start + 3400]
    heading = re.search(r'id="habitEditHeading">([^<]+)<', block).group(1)
    labels = re.findall(r'class="habit-edit-label"[^>]*>([^<]+)<', block)
    order_buttons = [re.search(rf'id="{i}">([^<]+)<', block).group(1) for i in ("habitEditUp", "habitEditDown", "habitEditTop")]
    assert labels == ["Название", "Категория", "Место в списке", "Напоминание"], labels
    assert not shared(heading, *labels, *order_buttons, "Сейчас 3 из 5", "Отмена", "Сохранить")
