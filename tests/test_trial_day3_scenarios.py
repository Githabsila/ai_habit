"""
Вечер 3-го дня триала по сценарию серии (решение владельца 05.11): 3 дня подряд — «серия сохранится и пойдёт дальше»; пропуски — «сохрани
N дн. серии»; серии нет — «начни её». Везде цена первого месяца; о закрытом канале — ни слова (канал ещё не наполнен).
"""
import trial_messages as tm

BASE = {"name": "Аня", "ends_at_text": "6 ноября, 23:59", "intro_active": True, "intro_usd": "$1.99", "renewal_usd": "$5.99"}


def test_three_days_in_a_row_keeps_the_streak_going():
    text = tm._d3_end(dict(BASE, streak=3))
    assert "3 дня подряд" in text and "серия сохранится и пойдёт дальше" in text and "$1.99" in text


def test_with_gaps_it_offers_to_keep_the_days_earned():
    text = tm._d3_end(dict(BASE, streak=2))
    assert "серия — 2 дня" in text and "сохранить эти дни серии" in text and "$1.99" in text


def test_without_a_streak_it_offers_to_start_one():
    text = tm._d3_end(dict(BASE, streak=0))
    assert "Серия пока не началась" in text and "$1.99" in text


def test_the_channel_is_not_mentioned_yet():
    for streak in (0, 2, 3, 7):
        assert "канал" not in tm._d3_end(dict(BASE, streak=streak)).lower()
