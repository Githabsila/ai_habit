"""
Проверка удобства Mini App 09.11 (решения владельца «делай все»):
1) у новичка без привычек блок «Привычки» — первым на Главной, «Парное задание» скрыто;
2) в «Рейтинге» сам рейтинг сразу под шапкой;
3) большая карточка профиля — только на Главной и в Профиле, в Календаре и Рейтинге — тонкая строка (тап — Профиль);
4) число у месяца в Календаре подписано «сегодня» и ведёт к сегодняшнему дню;
5) «Мне нет 16» — с переспросом, а на стоп-экране — «Напиши нам» (ссылка на поддержку).
"""
from pathlib import Path

import config
from db import add_user
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp/static/style.css").read_text(encoding="utf-8")


def test_a_newcomer_sees_habits_first_and_no_empty_pair_card():
    assert "function applyNewcomerHomeLayout(isNewcomer)" in APP and "applyNewcomerHomeLayout(habits.length === 0);" in APP
    assert '.tab-panel[data-tab="home"].is-newcomer #pairQuestCard{display:none !important}' in CSS


def test_the_profile_card_is_compact_outside_home_and_profile():
    assert 'document.body.classList.toggle("header-compact", tab === "calendar" || tab === "rating");' in APP
    assert "body.header-compact header.player-card .xp-bar" in CSS and "body.header-compact header.player-card .player-follow" in CSS
    assert 'document.querySelector(\'.tab-bar__item[data-tab="profile"]\')?.click();' in APP, "тап по тонкой строке — Профиль"


def test_the_calendar_number_says_today_and_jumps_to_it():
    assert 'id="calendarTodayBadge"' in APP and "</b>сегодня</button>" in APP
    assert 'grid.querySelector(".cal-cell.is-today")?.scrollIntoView' in APP
    assert "насколько ты приблизился" not in APP


def test_under_16_asks_again_and_offers_support():
    assert "function showAgeConfirm()" in APP and "Тебе точно нет 16?" in APP and "Назад, я ошибся" in APP
    assert "showAgeConfirm();" in APP and "res?.support_url" in APP


async def test_the_age_stop_returns_a_support_link(client, uid, monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_CONTACT", "@floydddo, @AlxManagement")
    add_user(uid, "a", "Аня")
    r = await client.post("/api/start-quiz/seen", headers={"Authorization": f"tma {sign_init_data(uid)}"},
                          json={"age_range": "under16", "goal": None})
    body = await r.json()
    assert body.get("age_restricted") is True and body.get("support_url") == "https://t.me/floydddo"
