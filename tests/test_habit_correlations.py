"""Roadmap #27 — статистические корреляции между привычками."""
from db import add_user, get_habit_correlations
from db.core import connect


def _log(uid, habit_id, title, day_offset, completed):
    conn = connect()
    conn.execute(
        "INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) "
        "VALUES (?,?,?,date('now', ?),?,0)",
        (uid, habit_id, title, f"-{day_offset} days", 1 if completed else 0),
    )
    conn.commit()
    conn.close()


def test_no_correlations_without_data(uid):
    add_user(uid, "u", "Test")
    assert get_habit_correlations(uid) == []


def test_strong_correlation_detected(uid):
    add_user(uid, "u", "Test")
    # 10 дней: когда "Зарядка" сделана, "Растяжка" тоже сделана каждый раз.
    # "Растяжка" НИКОГДА не делается без "Зарядки" — сильная связь.
    for day in range(10):
        _log(uid, 1, "Зарядка", day, True)
        _log(uid, 2, "Растяжка", day, True)
    # И ещё 10 дней, когда обе не сделаны (не создают шума в rate/baseline).
    for day in range(10, 20):
        _log(uid, 1, "Зарядка", day, False)
        _log(uid, 2, "Растяжка", day, False)

    correlations = get_habit_correlations(uid)
    assert len(correlations) >= 1
    top = correlations[0]
    assert top["a"] == "Зарядка"
    assert top["b"] == "Растяжка"
    assert top["rate"] == 100


def test_no_correlation_when_independent(uid):
    add_user(uid, "u", "Test")
    # Полностью независимые — попеременно то одна, то другая, без связи.
    for day in range(20):
        _log(uid, 1, "Чтение", day, day % 2 == 0)
        _log(uid, 2, "Готовка", day, day % 3 == 0)
    correlations = get_habit_correlations(uid)
    # Не гарантируем точный 0 (может случайно совпасть), но точно не
    # должно быть "сильной" (>=80%) связи между полностью независимыми.
    assert all(c["rate"] < 80 for c in correlations)


def test_insufficient_samples_not_flagged(uid):
    add_user(uid, "u", "Test")
    # Всего 2 дня — меньше CORRELATION_MIN_SAMPLES.
    for day in range(2):
        _log(uid, 1, "Йога", day, True)
        _log(uid, 2, "Дыхание", day, True)
    assert get_habit_correlations(uid) == []


def test_correlations_limited(uid):
    add_user(uid, "u", "Test")
    for day in range(10):
        for i in range(1, 6):
            _log(uid, i, f"Привычка{i}", day, True)
    correlations = get_habit_correlations(uid, limit=2)
    assert len(correlations) <= 2
