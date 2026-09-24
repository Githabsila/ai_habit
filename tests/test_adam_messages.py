"""
Два фидбека пользователя про формулировки контрольных точек:

1. "Контрольная точка дня: уже закрыто 0 из 6" / "Уже есть результат: 0 из 6
   привычек выполнено" — звучит как противоречие, когда за день ещё вообще
   ничего не отмечено (completed == 0).
2. Сообщение конца недели ("Финиш недели близко... нужен ещё один хороший
   шаг") не учитывало, что сегодняшние привычки уже все закрыты — правильнее
   похвалить за результат, а не подгонять сделать что-то ещё.

Каждый пул шаблонов — не вероятностный аргумент (текст может выпасть в
любом из N вариантов), а точное множество: проверяем принадлежность
результата этому множеству, перебрав все комбинации шаблон×эмодзи.
"""
from adam_messages import (
    format_habit_checkpoint_message,
    format_simple_habit_checkpoint_message,
    format_week_end_message,
    HABIT_CHECKPOINT_TEMPLATES,
    HABIT_CHECKPOINT_ZERO_TEMPLATES,
    SIMPLE_HABIT_CHECKPOINT_TEMPLATES,
    WEEK_END_TEMPLATES,
    WEEK_END_ALL_DONE_TEMPLATES,
    MOTIVATION_EMOJIS,
    SOFT_EMOJIS,
)


def _candidates(templates, pool, **kwargs):
    return {t.format(emoji=e, **kwargs) for t in templates for e in pool}


def test_habit_checkpoint_zero_completed_avoids_already_done_wording():
    kwargs = dict(time=12, completed=0, total=6, left=1, habits="«Разминка»", left_phrase="одна привычка", timed_note="")
    msg = format_habit_checkpoint_message([{"title": "Разминка"}], hour=12, completed=0, total=6)

    assert msg in _candidates(HABIT_CHECKPOINT_ZERO_TEMPLATES, SOFT_EMOJIS, **kwargs)
    assert msg not in _candidates(HABIT_CHECKPOINT_TEMPLATES, SOFT_EMOJIS, **kwargs)


def test_habit_checkpoint_nonzero_completed_uses_progress_wording():
    kwargs = dict(time=12, completed=2, total=6, left=1, habits="«Разминка»", left_phrase="одна привычка", timed_note="")
    msg = format_habit_checkpoint_message([{"title": "Разминка"}], hour=12, completed=2, total=6)

    assert msg in _candidates(HABIT_CHECKPOINT_TEMPLATES, SOFT_EMOJIS, **kwargs)
    assert msg not in _candidates(HABIT_CHECKPOINT_ZERO_TEMPLATES, SOFT_EMOJIS, **kwargs)


def test_habit_checkpoint_appends_timed_note_before_motivational_close():
    """Привычки со своим ещё не наступившим временем не входят в основной
    список {habits}, а добавляются отдельной фразой "Не забудь в HH:MM —
    ..." в конце сообщения, перед мотивационной концовкой (и эмодзи)."""
    msg = format_habit_checkpoint_message(
        [{"title": "Разминка"}], hour=12, completed=4, total=6,
        timed_habits=[(18, 0, "Спорт"), (23, 0, "Растяжка")],
    )

    note = " Не забудь в 18:00 — «Спорт» и в 23:00 — «Растяжка»."
    assert note in msg
    # note должна идти после списка обычных привычек и перед мотивационной
    # концовкой/эмодзи — то есть не в самом конце строки.
    assert not msg.rstrip().endswith(note.strip())


def test_simple_habit_checkpoint_has_no_progress_stats_or_timer_wording():
    """Упрощённый стиль (habit_checkpoint_style == 'simple') — без рамки
    "X из Y выполнено" и без единого упоминания времени/таймера, только
    прямой список того, что осталось без таймера."""
    kwargs = dict(habits="«Растяжка»")
    msg = format_simple_habit_checkpoint_message([{"title": "Растяжка"}])

    assert msg in _candidates(SIMPLE_HABIT_CHECKPOINT_TEMPLATES, SOFT_EMOJIS, **kwargs)
    assert "из" not in msg
    assert "Не забудь" not in msg


def test_week_end_message_all_done_uses_praise_wording():
    msg = format_week_end_message(all_done_today=True)

    assert msg in _candidates(WEEK_END_ALL_DONE_TEMPLATES, MOTIVATION_EMOJIS)
    assert msg not in _candidates(WEEK_END_TEMPLATES, MOTIVATION_EMOJIS)


def test_week_end_message_not_all_done_uses_default_wording():
    msg = format_week_end_message(all_done_today=False)

    assert msg in _candidates(WEEK_END_TEMPLATES, MOTIVATION_EMOJIS)
    assert msg not in _candidates(WEEK_END_ALL_DONE_TEMPLATES, MOTIVATION_EMOJIS)
