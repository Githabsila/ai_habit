"""
Точки дня 10/12/17/22 (14.10): у человека с одной привычкой нет «Пока 0 из 1»; сообщения собираются из частей и не повторяют
то, что человек уже получал (раньше в 12:00, 17:00 и 22:00 приходило почти одно и то же — «как спам»).
"""
import random
import re
from datetime import datetime

from adam_messages import (
    CHECKPOINT_RECENT, compose_checkpoint_message,
)
from db import add_habit, add_user
from db.checkpoint_style import KEEP, checkpoints_sent_today, recent_checkpoint_picks, save_checkpoint_picks
from db.core import connect
from tests.test_adam_texts import FORMAL, GENDERED

HOURS = (10, 12, 17, 22)
ONE = [{"title": "Читать 20 минут"}]


def test_one_habit_has_no_score_at_any_hour():
    rng = random.Random(1)
    for hour in HOURS + (7, 15):
        for earlier in (0, 1, 3):
            for _ in range(120):
                text, _picks = compose_checkpoint_message(ONE, hour, completed=0, total=1, earlier_today=earlier, rng=rng)
                assert "из 1" not in text and "0 из" not in text and "ни одной" not in text.lower(), text
                assert "«Читать 20 минут»" in text and "одна привычка" in text.lower() or "одной привычке" in text.lower(), text


def test_several_habits_may_still_show_the_score_and_the_progress():
    rng = random.Random(2)
    zero = {compose_checkpoint_message([{"title": "А"}, {"title": "Б"}], 12, completed=0, total=3, rng=rng)[0] for _ in range(200)}
    assert any("0 из 3" in text or "из 3 пока ни одной" in text or "Ни одна из 3" in text for text in zero)
    assert all("из 1" not in text for text in zero)
    progress = {compose_checkpoint_message([{"title": "Б"}], 17, completed=2, total=3, rng=rng)[0] for _ in range(200)}
    assert any("2 из 3" in text for text in progress) and not any("Пока 0" in text for text in progress)


def test_the_parts_do_not_repeat_within_the_last_three_checkpoints():
    rng = random.Random(3)
    history = []
    for step in range(120):
        hour = HOURS[step % 4]
        text, picks = compose_checkpoint_message(ONE, hour, completed=0, total=1, recent_picks=history[:CHECKPOINT_RECENT],
                                                 earlier_today=step % 4, rng=rng)
        for previous in history[:CHECKPOINT_RECENT]:
            assert previous["o"] != picks["o"] and previous["f"] != picks["f"] and previous["c"] != picks["c"], (step, previous, picks)
        history.insert(0, picks)
    texts = []
    history = []
    for step in range(60):
        text, picks = compose_checkpoint_message(ONE, HOURS[step % 4], completed=0, total=1, recent_picks=history[:3], rng=rng)
        texts.append(text)
        history.insert(0, picks)
    assert all(a != b for a, b in zip(texts, texts[1:])) and len(set(texts)) > 40, "сообщения подряд разные, а не те же три шаблона"


def test_a_later_checkpoint_says_nothing_changed_only_when_it_is_true():
    rng = random.Random(4)
    again = ("с прошлой сверки", "картина прежняя", "напомню ещё раз", "возвращаюсь к плану", "всё ещё без отметок")
    seen = {compose_checkpoint_message(ONE, 17, completed=0, total=1, earlier_today=2, rng=rng)[0].lower() for _ in range(300)}
    assert any(any(mark in text for mark in again) for text in seen), "повторное напоминание должно звучать как повторное"
    for earlier, completed, total in ((0, 0, 1), (2, 1, 3), (3, 2, 4)):
        for _ in range(150):
            text = compose_checkpoint_message(ONE, 17, completed=completed, total=total, earlier_today=earlier, rng=rng)[0].lower()
            assert not any(mark in text for mark in again), (earlier, completed, text)


def test_every_hour_has_its_own_voice():
    rng = random.Random(5)
    by_hour = {hour: {compose_checkpoint_message(ONE, hour, completed=0, total=1, rng=rng)[0].split(".")[0] for _ in range(80)} for hour in HOURS}
    assert all(len(openers) >= 4 for openers in by_hour.values())
    assert by_hour[22].isdisjoint(by_hour[10]) and by_hour[17].isdisjoint(by_hour[22]), "вечернее начало не повторяет утреннее"


def test_titles_are_escaped_and_the_text_has_no_gender_or_formal_forms():
    rng = random.Random(6)
    text, _ = compose_checkpoint_message([{"title": "<b>Бег</b> & йога"}], 12, completed=0, total=1, timed_habits=[(23, 0, "<i>Сон</i>")], rng=rng)
    assert "<b>" not in text and "&lt;b&gt;Бег&lt;/b&gt; &amp; йога" in text and "&lt;i&gt;Сон&lt;/i&gt;" in text
    for hour in HOURS:
        for case in ((0, 1), (0, 4), (2, 4)):
            for _ in range(60):
                full = compose_checkpoint_message([{"title": "Чтение"}, {"title": "Зарядка"}][:1 if case[1] == 1 else 2], hour,
                                                  completed=case[0], total=case[1], rng=rng)[0]
                assert not GENDERED.search(full) and not FORMAL.search(full), full
                assert not re.search(r"\s[.,!?;:]|\{|\}|None", full), full


def test_the_memory_keeps_the_last_picks_newest_first_and_survives_a_missing_table(uid):
    for index in range(KEEP + 4):
        save_checkpoint_picks(uid, {"o": f"o{index}", "s": "", "f": f"f{index}", "c": f"c{index}"})
    recent = recent_checkpoint_picks(uid, 3)
    assert [p["o"] for p in recent] == [f"o{KEEP + 3}", f"o{KEEP + 2}", f"o{KEEP + 1}"]
    conn = connect()
    assert conn.execute("SELECT COUNT(*) AS n FROM checkpoint_picks WHERE user_id=?", (uid,)).fetchone()["n"] == KEEP
    conn.close()
    assert recent_checkpoint_picks(uid + 1) == []


async def test_the_scheduler_job_sends_different_checkpoints_and_never_a_score_for_one_habit(monkeypatch, uid):
    import coach
    from tests.test_habit_checkpoints import FakeBot, _freeze, _patch_user

    add_user(uid, "u", "Test")
    add_habit(uid, "Читать 20 минут")
    _patch_user(monkeypatch, uid)
    bot = FakeBot()
    for hour, job in ((12, coach.run_habit_checkpoint_12), (17, coach.run_habit_checkpoint_17), (22, coach.run_habit_checkpoint_22)):
        _freeze(monkeypatch, hour, 0)
        await job(bot)
    texts = [text for _chat, text in bot.sent]
    assert len(texts) == 3 and len(set(texts)) == 3
    assert all("из 1" not in text and "«Читать 20 минут»" in text for text in texts), texts
    assert len(recent_checkpoint_picks(uid, 5)) == 3, "после каждой отправки части запоминаются"
    day = datetime(2026, 1, 1).date().isoformat()
    assert checkpoints_sent_today(uid, day) == 3 and checkpoints_sent_today(uid + 1, day) == 0
    # сообщения, чьи части совпали бы с недавними, не отправляются дважды подряд одинаковыми кусками
    picks = recent_checkpoint_picks(uid, 3)
    assert picks[0]["o"] != picks[1]["o"] and picks[1]["o"] != picks[2]["o"]
