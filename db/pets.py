"""
Roadmap #11 — виртуальный питомец. Кормится завершёнными привычками
(каждое complete_habit() — забота +1), растёт через стадии. Никакой
внешней арт-графики (рисовать не умею) — стадии показываны эмодзи +
CSS-анимацией на фронте, не картинками.
"""
from .core import connect

PET_STAGES = [
    (0, "🥚", "Яйцо"),
    (10, "🐣", "Птенец"),
    (30, "🐥", "Подросток"),
    (70, "🦅", "Взрослый"),
    (150, "🔥🦅", "Легенда"),
]


def _stage_for(care_points):
    stage = PET_STAGES[0]
    for threshold, emoji, name in PET_STAGES:
        if care_points >= threshold:
            stage = (threshold, emoji, name)
        else:
            break
    return stage


def get_pet(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM virtual_pets WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("INSERT INTO virtual_pets(user_id, care_points) VALUES (?, 0)", (user_id,))
        conn.commit()
        care_points, last_fed_day = 0, None
    else:
        care_points, last_fed_day = row["care_points"], row["last_fed_day"]
    conn.close()

    threshold, emoji, name = _stage_for(care_points)
    next_stage = next((s for s in PET_STAGES if s[0] > threshold), None)
    return {
        "care_points": care_points,
        "emoji": emoji,
        "stage_name": name,
        "last_fed_day": last_fed_day,
        "next_stage_points": next_stage[0] if next_stage else None,
        "next_stage_emoji": next_stage[1] if next_stage else None,
        "is_max_stage": next_stage is None,
    }


def feed_pet(user_id, day):
    """Вызывается из complete_habit() — забота +1 за КАЖДУЮ отметку
    привычки, без ограничения "раз в день" (несколько привычек в день —
    питомец растёт быстрее, стимулирует закрывать больше привычек, не
    просто одну ради галочки)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT care_points FROM virtual_pets WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO virtual_pets(user_id, care_points, last_fed_day) VALUES (?, 1, ?)",
            (user_id, day),
        )
        new_points = 1
        old_points = 0
    else:
        old_points = row["care_points"]
        new_points = old_points + 1
        cursor.execute(
            "UPDATE virtual_pets SET care_points=?, last_fed_day=? WHERE user_id=?",
            (new_points, day, user_id),
        )
    conn.commit()
    conn.close()

    old_stage = _stage_for(old_points)
    new_stage = _stage_for(new_points)
    evolved = new_stage[0] != old_stage[0]
    return {"care_points": new_points, "evolved": evolved, "stage_name": new_stage[2], "emoji": new_stage[1]}
