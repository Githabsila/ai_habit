"""
Карта функций (webapp/feature_map.py) — страж аналитики. Тест падает, если в коде появилось то, чего нет в карте: новое окно,
маршрут API, команда или кнопка бота, событие. Так новые функции не пропадают из статистики, а владелец видит их эффективность с первого
дня. Текст ошибки говорит, что именно и куда дописать.
"""
import re
from datetime import date
from pathlib import Path

from webapp import feature_map as fm
from db.tracking import EVENT_LABELS

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
HELP = "Добавь это в webapp/feature_map.py (см. инструкцию в начале файла) — иначе новая функция не появится в аналитике."


def _api_keys():
    keys = set()
    for name in ("webapp_server.py", "routes_ai_miniapp.py"):
        text = (ROOT / "webapp" / name).read_text(encoding="utf-8")
        for method, path in re.findall(r'@routes\.(get|post|put|patch|delete)\("([^"]+)"', text):
            keys.add(f"{method.upper()} {path}")
    return keys


def _known_api(key):
    return fm.feature_for_api(key) is not None or fm.is_ignored_api(key)


def test_every_api_route_is_in_the_feature_map_or_marked_passive():
    missing = sorted(key for key in _api_keys() if not _known_api(key))
    assert not missing, f"Маршруты без места в карте функций: {missing}. {HELP} Осознанное действие человека — в api функции, фоновая загрузка — в PASSIVE_API."


def test_every_window_and_tab_belongs_to_a_feature():
    screens = {name[:-len("Overlay")] for name in re.findall(r'id="(\w+Overlay)"', INDEX)}
    screens |= set(re.findall(r'data-tab="(\w+)"', INDEX))
    missing = sorted(s for s in screens if not fm.feature_for_screen(s) and not fm.is_ignored_screen(s))
    assert not missing, f"Окна и вкладки без функции: {missing}. {HELP} Имя — id окна без «Overlay»."


def _bot_labels():
    labels = set()
    for path in sorted((ROOT / "handlers").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for value in re.findall(r'F\.data\s*==\s*"([^"]+)"', text):
            labels.add("cb:" + re.sub(r"\d+", "#", value))
        for value in re.findall(r'F\.data\.startswith\("([^"]+)"', text):
            labels.add("cb:" + re.sub(r"\d+", "#", value) + "#")
        for args in re.findall(r"\bCommand\(([^)]*)\)", text):
            for command in re.findall(r'"(\w+)"', args):
                labels.add(f"cmd:/{command}")
        if re.search(r"\bCommandStart\(", text):
            labels.add("cmd:/start")
    return labels


def test_every_bot_command_and_button_belongs_to_a_feature():
    missing = sorted(l for l in _bot_labels() if not fm.feature_for_bot(l) and not fm.is_ignored_bot(l))
    assert not missing, f"Команды и кнопки бота без функции: {missing}. {HELP} Формат: cmd:/команда или cb:данные (цифры → #)."


def _event_names():
    names = set(EVENT_LABELS)
    for path in ROOT.glob("**/*.py"):
        if any(part in ("tests", "aiwork", "shop_final", ".claude", "venv", ".venv") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        names |= set(re.findall(r'track_server\(\s*[^,()]+,\s*"([a-z][a-z0-9_.-]+)"', text))
    names |= set(re.findall(r'\btrk\(\s*"([a-z][a-z0-9_.-]+)"', APP))
    return names


def test_every_event_is_mapped_to_a_feature_or_ignored():
    missing = sorted(n for n in _event_names() if not fm.feature_for_event(n) and n not in fm.IGNORED_EVENTS)
    assert not missing, f"События без функции: {missing}. {HELP} Событие — в events функции (и подпись в db/tracking.py::EVENT_LABELS)."


def test_features_only_list_events_that_exist():
    known = _event_names()
    wrong = sorted((f.id, e) for f in fm.FEATURES for e in f.events if e not in known)
    assert not wrong, f"В карте указаны события, которых нет в коде (опечатка?): {wrong}"


def test_each_thing_belongs_to_exactly_one_feature():
    seen = {}
    for f in fm.FEATURES:
        for kind, items in (("screen", f.screens), ("event", f.events), ("api", f.api)):
            for item in items:
                other = seen.setdefault((kind, item), f.id)
                assert other == f.id, f"{kind} {item!r} описан и в {other}, и в {f.id}"


def test_feature_records_are_valid():
    ids = [f.id for f in fm.FEATURES]
    assert len(ids) == len(set(ids))
    for f in fm.FEATURES:
        assert f.group in fm.GROUPS, f.id
        date.fromisoformat(f.since)
        assert f.name.strip()


def test_new_buttons_inside_a_known_window_land_in_that_feature_without_any_edit():
    assert fm.feature_for_click("brand-new-button=go", "shop") == "shop"
    assert fm.feature_for_click("brand-new-button=go", "userProfile") == "friends"
    # на главной неопознанная кнопка не приписывается никому — она попадает в список «не привязано»
    assert fm.feature_for_click("brand-new-button=go", "home") is None


def test_classify_separates_unmapped_from_ignored():
    assert fm.classify("api", "GET /api/bootstrap") == (None, True)
    assert fm.classify("api", "POST /api/brand-new") == (None, False)
    assert fm.classify("api", "POST /api/habits")[0] == "habits"
    assert fm.classify("screen", "shop")[0] == "shop"
    assert fm.classify("click", "cb:shop")[0] == "shop"
    assert fm.classify("click", "cmd:/admin") == (None, True)
