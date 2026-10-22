"""
«Окна и анимации» (22.10): админ открывает любое новое окно на демо-данных и сразу видит, что получилось, — без реальной награды.
Список сцен лежит в db/sim_lab.py, проигрывает их app.js (?sim=<ключ>), кнопки — в админке. Браузера в тестах нет: разметку и скрипт
держат проверки по тексту, маршрут — настоящий.
"""
import itertools
import re
from pathlib import Path

import pytest

from db import add_user
from db.sim_lab import SCENES, sim_scenes
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
ADMIN_HTML = (STATIC / "admin_panel.html").read_text(encoding="utf-8")

_uid_counter = itertools.count(854_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _scenes_block():
    start = APP_JS.index("const SIM_SCENES = {")
    return APP_JS[start:APP_JS.index("async function initSimMode()", start)]


# ---------------------------------------------------------------------------------------------------------------- список и скрипт

def test_scene_list_is_well_formed():
    keys = [s["key"] for s in sim_scenes()]
    assert keys == [row[0] for row in SCENES] and len(set(keys)) == len(keys)
    assert all(s["icon"] and s["title"] and s["note"] for s in sim_scenes())
    assert {"chest_month", "chest_final", "chest_pair"} <= set(keys), "все сундуки на месте"


def test_every_listed_scene_has_a_runner_and_every_runner_is_listed():
    runners = re.findall(r"^  (\w+): \(\) =>", _scenes_block(), flags=re.M)
    assert sorted(runners) == sorted(s["key"] for s in sim_scenes())


def test_scenes_never_talk_to_the_server():
    block = _scenes_block()
    for forbidden in ("api(", "fetch(", "applyActionPatch", "bootstrap", "claim"):
        assert forbidden not in block, f"демо ничего не начисляет и не сохраняет: {forbidden}"
    assert block.count("simReply(") >= 3, "сундуки получают «ответ сервера» из демо-данных"


def test_chest_scenes_use_the_real_window_with_each_kind_of_reward():
    block = _scenes_block()
    assert block.count("openChestReveal({") == 4, "месяц, главный, на двоих и сундук квеста дня"
    assert "final: true" in block and 'badge: "' in block, "главный сундук — фиолетовый, со значком"
    assert "month_points: 3" in block, "у сундука на двоих — очки месяца"


def test_app_opens_simulations_only_for_admins_after_the_lab():
    init = APP_JS[APP_JS.index("async function initSimMode()"):][:900]
    assert 'new URLSearchParams(location.search).get("sim")' in init and "!state?.user?.is_admin" in init
    assert '"/api/admin/lab/sims"' in init
    after = APP_JS[APP_JS.index("postBootstrapInitDone = true;"):][:160]
    assert after.index("initLabMode();") < after.index("initSimMode();")


def test_sim_bar_walks_replays_and_exits():
    bar = APP_JS[APP_JS.index("function renderSimBar()"):][:2600]
    for act in ("prev", "replay", "next", "exit"):
        assert f'data-sim="{act}"' in bar, act
    assert 'window.location.href = "/admin"' in bar
    assert 'bar.className = "lab-bar"' in bar, "панель в том же стиле, что у лаборатории парных заданий"


# ---------------------------------------------------------------------------------------------------------------- админка

def test_admin_panel_lists_the_simulations_under_the_lab():
    assert ADMIN_HTML.index("Тесты и нововведения") < ADMIN_HTML.index("Окна и анимации") < ADMIN_HTML.index("Ответы Адама в чате")
    assert 'id="simBox"' in ADMIN_HTML and "/api/admin/lab/sims" in ADMIN_HTML
    assert "href=\"/?sim=' + encodeURIComponent(s.key)" in ADMIN_HTML
    assert "loadSims();" in ADMIN_HTML


async def test_sims_route_is_for_admins_only(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    add_user(uid, "u", "Аня")
    assert (await client.get("/api/admin/lab/sims")).status == 401
    assert (await client.get("/api/admin/lab/sims", headers=_headers(uid))).status == 403
    admin_id = uid + 50
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin_id})
    add_user(admin_id, "adm", "Админ")
    r = await client.get("/api/admin/lab/sims", headers=_headers(admin_id))
    body = await r.json()
    assert r.status == 200 and [s["key"] for s in body["scenes"]] == [s["key"] for s in sim_scenes()]
