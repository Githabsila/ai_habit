"""
Статусы серии (db/streak_status.py): у каждой ступени своё уникальное название, цвет и значок; плитка в профиле называет ступень вместо
путающих «30 дн.» рядом с серией 41; пороги совпадают с вехами рамок; окно «Статусы серии» по тапу.
"""
import itertools
import re
from pathlib import Path

import pytest

from db import add_user
from db import streak
from db.core import connect
from db.evolution import FAMILIES
from db.streak_status import LADDER, NAME_BY_MILESTONE, status_for_days

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")
_ids = itertools.count(900_400_000)


def test_every_status_has_its_own_name_color_and_glyph():
    names = [row["name"] for row in LADDER]
    accents = [row["accent"] for row in LADDER]
    keys = [row["key"] for row in LADDER]
    assert len(set(names)) == len(names) and len(set(accents)) == len(accents) and len(set(keys)) == len(keys)
    assert [row["days"] for row in LADDER] == sorted(row["days"] for row in LADDER) and LADDER[0]["days"] == 0
    for key in keys:                                                      # у каждой ступени в приложении нарисован свой значок
        assert re.search(rf"\b{key}:\s*'<", APP), key
    for accent in accents:
        assert re.fullmatch(r"#[0-9a-f]{6}", accent)


def test_status_names_do_not_repeat_other_names_the_player_sees():
    other = {f["title"].lower() for f in FAMILIES.values()} | {f["ru"].lower() for f in FAMILIES.values()}
    other |= {frame.lower() for _title, frame, _code in streak.MILESTONES.values()}
    other |= {"месяц в ударе", "сотня в огне", "стальной стержень", "несгораемый", "год в огне", "две недели в огне", "легенда"}
    assert not [row["name"] for row in LADDER if row["name"].lower() in other]
    assert all(len(row["name"]) <= 7 for row in LADDER), "названия должны помещаться в плитку на узком экране"


def test_thresholds_match_the_frame_milestones_and_titles_come_from_the_ladder():
    assert set(NAME_BY_MILESTONE) == set(streak.MILESTONES)
    for days, (title, _frame, _code) in streak.MILESTONES.items():
        assert title == NAME_BY_MILESTONE[days]


@pytest.mark.parametrize("days,name,next_name,left", [
    (0, "Старт", "Закалка", 14), (13, "Старт", "Закалка", 1), (14, "Закалка", "Кремень", 16), (41, "Кремень", "Булат", 19),
    (100, "Монолит", "Титан", 100), (364, "Зенит", "Абсолют", 1), (365, "Абсолют", None, None), (999, "Абсолют", None, None),
])
def test_the_status_follows_the_current_streak(days, name, next_name, left):
    status = status_for_days(days)
    assert status["name"] == name
    assert (status["next"] or {}).get("name") == next_name and (status["next"] or {}).get("left") == left


def test_the_ladder_marks_what_is_reached_and_how_far_the_rest_is():
    ladder = status_for_days(41)["ladder"]
    assert [row["reached"] for row in ladder] == [True, True, True, False, False, False, False, False]
    assert [row["left"] for row in ladder][3:] == [19, 59, 159, 259, 324]
    assert status_for_days(None)["name"] == "Старт" and status_for_days("-5")["tier"] == 0


def test_the_streak_payload_carries_the_status_and_old_reward_titles_get_the_new_names():
    uid = next(_ids)
    add_user(uid, "status_user", "Аня")
    conn = connect()
    conn.execute("UPDATE users SET streak=41 WHERE telegram_id=?", (uid,))
    conn.execute("INSERT INTO streak_rewards(user_id, milestone, status, frame, permanent) VALUES (?,?,?,?,1)", (uid, 30, "Месяц в ударе", "Золотой характер"))
    conn.commit()
    conn.close()
    payload = streak.get_streak_status(uid)
    assert payload["status"]["name"] == "Кремень" and payload["status"]["next"]["left"] == 19
    assert {r["milestone"]: r["status"] for r in payload["rewards"]}[30] == "Кремень"        # название из базы заменено на новое
    assert "Золотой характер" in {r["frame"] for r in payload["rewards"]}                       # рамки не тронуты


def test_the_profile_tile_names_the_status_and_opens_the_ladder():
    assert 'id="profileMetricStatus"' in INDEX and 'id="profileMetricNext"' in INDEX and 'id="statusLadderOverlay"' in INDEX
    assert "metricReward.textContent" not in APP and "reward.milestone} дн." not in APP, "плитка больше не показывает дни вехи вместо статуса"
    assert "renderStreakStatus(streak.status" in APP and "openStatusLadder" in APP and '"statusLadderOverlay"' in APP
    assert ".streak-metric--status" in STYLE and ".status-ladder__row" in STYLE
