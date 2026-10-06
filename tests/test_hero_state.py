"""
Аватар-наставник ADAM вместо питомца-птенца: картинка героя зависит от
состояния серии (db/hero.py::get_hero_state), а не от очков заботы.
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from db import add_user, get_hero_state, HERO_KEYS
from db.core import connect
from db.hero import AT_RISK_HOUR, HERO_BANDS
from db.streak import day_key, ensure_tables

from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
HERO_ASSETS = ROOT / "webapp" / "static" / "assets" / "hero"

MORNING = datetime(2026, 3, 10, 9, 0)
EVENING = datetime(2026, 3, 10, 18, 0)


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def _seed(uid_, streak=0, days=None, last_broken_streak=0, now=MORNING):
    """days: {сколько дней назад: статус}; 0 — сегодня, 1 — вчера."""
    add_user(uid_, "u", "Test")
    ensure_tables()
    conn = connect()
    conn.execute("UPDATE users SET streak=? WHERE telegram_id=?", (streak, uid_))
    conn.execute("INSERT OR IGNORE INTO streak_meta(user_id) VALUES(?)", (uid_,))
    conn.execute(
        "UPDATE streak_meta SET last_broken_streak=? WHERE user_id=?",
        (last_broken_streak, uid_),
    )
    for ago, status in (days or {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?,?,?,?)",
            (uid_, day_key(now.date() - timedelta(days=ago)), status, 0),
        )
    conn.commit()
    conn.close()


# =====================================
# «Обычные» состояния по серии
# =====================================

def test_new_user_is_start(uid):
    _seed(uid)
    hero = get_hero_state(uid, now=MORNING)
    assert hero["key"] == "start"
    assert hero["band"] == 0
    assert hero["streak"] == 0
    assert hero["counted_today"] is False


@pytest.mark.parametrize("streak,key", [
    (1, "start"),
    (2, "early"),
    (13, "early"),
    (14, "growth"),
    (30, "growth"),
    (31, "peak"),
    (365, "peak"),
])
def test_band_by_streak_when_today_counted(uid, streak, key):
    _seed(uid, streak=streak, days={0: "completed", 1: "completed"})
    # Вечером, но сегодня уже засчитано — тревоги нет.
    assert get_hero_state(uid, now=EVENING)["key"] == key


def test_bands_are_increasing_and_start_at_zero():
    minimums = [m for m, _ in HERO_BANDS]
    assert minimums[0] == 0
    assert minimums == sorted(minimums)
    assert len(set(minimums)) == len(minimums)


def test_progress_to_next_band(uid):
    _seed(uid, streak=5, days={0: "completed", 1: "completed"})
    progress = get_hero_state(uid, now=MORNING)["progress"]
    assert progress["from"] == 2
    assert progress["to"] == 14
    assert progress["days_left"] == 9
    assert progress["next_title"] == "Стабильный рост"
    assert progress["percent"] == 25  # (5-2)/(14-2)


def test_peak_has_no_next_band(uid):
    _seed(uid, streak=40, days={0: "completed", 1: "completed"})
    progress = get_hero_state(uid, now=MORNING)["progress"]
    assert progress["to"] is None
    assert progress["next_title"] is None
    assert progress["percent"] == 100


# =====================================
# Серия под угрозой / пропущен день
# =====================================

def test_not_at_risk_in_the_morning(uid):
    _seed(uid, streak=5, days={1: "completed"})
    hero = get_hero_state(uid, now=MORNING)
    assert hero["key"] == "early"
    assert hero["counted_today"] is False


def test_at_risk_after_threshold_hour_when_today_not_counted(uid):
    _seed(uid, streak=5, days={1: "completed"})
    now = MORNING.replace(hour=AT_RISK_HOUR)
    assert get_hero_state(uid, now=now)["key"] == "at_risk"


def test_not_at_risk_once_today_is_counted(uid):
    _seed(uid, streak=5, days={0: "completed", 1: "completed"})
    assert get_hero_state(uid, now=EVENING)["key"] == "early"


def test_at_risk_all_day_when_yesterday_was_saved_by_freeze(uid):
    _seed(uid, streak=9, days={1: "freeze"})
    hero = get_hero_state(uid, now=MORNING)
    assert hero["key"] == "at_risk"
    assert "заморозка" in hero["caption"]


# =====================================
# Серия оборвалась / долгий перерыв / возвращение
# =====================================

def test_ended_right_after_break_mentions_lost_streak(uid):
    _seed(uid, streak=0, days={1: "missed"}, last_broken_streak=7)
    hero = get_hero_state(uid, now=MORNING)
    assert hero["key"] == "ended"
    assert "7 дн." in hero["caption"]


def test_ended_lasts_two_days_then_long_break(uid):
    _seed(uid, streak=0, days={2: "missed"}, last_broken_streak=4)
    assert get_hero_state(uid, now=MORNING)["key"] == "ended"

    # Тот же срыв, но уже 3 дня назад — «долгий перерыв».
    other = uid + 500_000
    _seed(other, streak=0, days={3: "missed"}, last_broken_streak=4)
    assert get_hero_state(other, now=MORNING)["key"] == "long_break"


def test_long_break_for_user_with_old_history(uid):
    _seed(uid, streak=0, days={30: "completed"})
    assert get_hero_state(uid, now=MORNING)["key"] == "long_break"


def test_return_on_first_day_after_break(uid):
    _seed(uid, streak=1, days={0: "completed", 3: "missed"}, last_broken_streak=6)
    hero = get_hero_state(uid, now=EVENING)
    assert hero["key"] == "return"
    assert hero["counted_today"] is True


def test_after_return_day_the_hero_goes_back_to_start_then_early(uid):
    # Следующий день после возвращения: серия всё ещё 1, привычка ещё не
    # закрыта (утро) — картинка «старт», как и просил автор.
    _seed(uid, streak=1, days={1: "completed", 4: "missed"}, last_broken_streak=6)
    assert get_hero_state(uid, now=MORNING)["key"] == "start"

    # А закрыл вторую — серия 2, «второй день серии».
    other = uid + 500_000
    _seed(other, streak=2, days={0: "completed", 1: "completed", 5: "missed"}, last_broken_streak=6)
    assert get_hero_state(other, now=MORNING)["key"] == "early"


def test_first_ever_day_is_start_not_return(uid):
    _seed(uid, streak=1, days={0: "completed"})
    assert get_hero_state(uid, now=EVENING)["key"] == "start"


# =====================================
# Ассеты и контракт
# =====================================

@pytest.mark.parametrize("key", HERO_KEYS)
def test_every_state_has_an_image_file(key):
    assert (HERO_ASSETS / f"{key}.webp").is_file(), f"нет картинки для состояния {key}"


def test_hero_payload_shape(uid):
    _seed(uid)
    hero = get_hero_state(uid, now=MORNING)
    assert set(hero) == {
        "key", "title", "caption", "tone", "streak", "band",
        "counted_today", "image", "video", "progress", "evolution",
    }
    assert hero["image"].startswith(f"/static/assets/hero/{hero['key']}.webp?v=")
    assert set(hero["progress"]) == {"from", "to", "days_left", "next_title", "percent"}


# =====================================
# Видео-петля (необязательная) и раздача файлов героя
# =====================================

def test_video_is_none_when_no_clip_exists(uid, monkeypatch, tmp_path):
    monkeypatch.setattr("db.hero.HERO_ASSETS_DIR", tmp_path)
    (tmp_path / "start.webp").write_bytes(b"abc")
    _seed(uid)
    hero = get_hero_state(uid, now=MORNING)
    assert hero["video"] is None
    # В версию входит размер файла: замена картинки сама сбрасывает кэш.
    assert hero["image"] == "/static/assets/hero/start.webp?v=1-3"


def test_video_url_appears_when_clip_exists(uid, monkeypatch, tmp_path):
    monkeypatch.setattr("db.hero.HERO_ASSETS_DIR", tmp_path)
    (tmp_path / "start.webp").write_bytes(b"abc")
    (tmp_path / "start.mp4").write_bytes(b"0123456789")
    _seed(uid)
    hero = get_hero_state(uid, now=MORNING)
    assert hero["video"] == "/static/assets/hero/start.mp4?v=1-10"


def test_video_is_per_state(uid, monkeypatch, tmp_path):
    """Клип есть только у «peak» — у остальных состояний video = None."""
    monkeypatch.setattr("db.hero.HERO_ASSETS_DIR", tmp_path)
    (tmp_path / "peak.mp4").write_bytes(b"x" * 7)
    _seed(uid, streak=40, days={0: "completed", 1: "completed"})
    assert get_hero_state(uid, now=MORNING)["video"] == "/static/assets/hero/peak.mp4?v=1-7"
    other = uid + 500_000
    _seed(other, streak=5, days={0: "completed", 1: "completed"})
    assert get_hero_state(other, now=MORNING)["video"] is None


async def test_hero_assets_are_cached_long_but_other_static_is_not(client):
    r = await client.get("/static/assets/hero/start.webp?v=1-1")
    assert r.status == 200
    assert "immutable" in r.headers["Cache-Control"]
    # Прочая статика по-прежнему без кэша (иначе WebView держал бы старый код).
    r2 = await client.get("/static/assets/logo.svg")
    assert "no-store" in r2.headers["Cache-Control"]


async def test_hero_assets_support_range_requests(client):
    """Видео iOS/Android берут кусками (Range) — раздача обязана отвечать 206."""
    r = await client.get("/static/assets/hero/start.webp", headers={"Range": "bytes=0-9"})
    assert r.status == 206
    assert r.headers["Content-Range"].startswith("bytes 0-9/")
    assert len(await r.read()) == 10


def _load_converter():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "convert_hero_videos", ROOT / "tools" / "convert_hero_videos.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_converter_keys_match_hero_states():
    """tools/convert_hero_videos.py ищет клипы по ключам состояний — они
    должны совпадать с db.hero.HERO_KEYS, иначе часть клипов молча
    пропустится."""
    assert set(_load_converter().KEYS) == set(HERO_KEYS)


def test_converter_pingpong_filter_appends_reverse_concat():
    converter = _load_converter()
    plain = converter.build_filter(480, 720, 24, pingpong=False)
    looped = converter.build_filter(480, 720, 24, pingpong=True)
    assert "scale=480:720" in plain and "reverse" not in plain
    assert looped.startswith(plain)
    assert "reverse" in looped and "concat=n=2" in looped


def test_converter_crop_bias_moves_vertical_crop():
    """Клип 9:16 обрезается до 2:3 по вертикали: crop_y задаёт, откуда срезать
    (0.5 — поровну; меньше — больше снизу, чтобы не резать голову)."""
    converter = _load_converter()
    assert "(ih-out_h)*0.5" in converter.build_filter(480, 720, 24, pingpong=False)
    assert "(ih-out_h)*0.25" in converter.build_filter(480, 720, 24, pingpong=False, crop_y=0.25)
    # Подложка (--poster) режется тем же кропом, что и видео.
    assert converter.crop_scale(600, 900, 0.25) in converter.build_filter(600, 900, 24, False, 0.25)


async def test_bootstrap_and_complete_both_return_hero(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/habits", headers=headers, json={"title": "Пить воду"})
    habit_id = (await r.json())["habit"]["id"]

    r_boot = await client.get("/api/bootstrap", headers=headers)
    boot_hero = (await r_boot.json())["hero"]
    assert boot_hero["key"] == "start"
    assert boot_hero["counted_today"] is False

    r2 = await client.post(f"/api/habits/{habit_id}/complete", headers=headers)
    body = await r2.json()
    assert body["hero"]["counted_today"] is True
    assert body["hero"]["streak"] == 1
    # Тот же набор полей, что и в bootstrap: app.js::applyActionPatch просто
    # подменяет state.hero.
    assert set(boot_hero) == set(body["hero"])


def test_index_html_has_hero_markup_used_by_app_js():
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    for element_id in (
        "heroWidget", "heroWidgetImg", "heroWidgetTitle", "heroWidgetCaption",
        "heroWidgetBarFill", "heroWidgetHint", "heroWidgetPortrait",
        "heroLightbox", "heroLightboxFrame", "heroLightboxImg",
        "heroLightboxTitle", "heroLightboxText",
    ):
        assert f'id="{element_id}"' in html, element_id
        assert f'"{element_id}"' in js, element_id
    # Питомца-птенца в разметке больше нет.
    assert 'id="petWidget"' not in html
