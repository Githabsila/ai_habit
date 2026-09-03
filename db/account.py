"""
Самообслуживание аккаунта (см. webapp/webapp_server.py /api/account/*) —
раньше единственный способ выгрузить свои данные целиком или удалить
аккаунт был написать на email из privacy.html и ждать, пока это вручную
сделает админ. Теперь оба действия доступны прямо в настройках.
"""
from pathlib import Path

from .core import connect, DATA_DIR


def export_full_account_data(telegram_id):
    """Право на переносимость данных: всё, что реально принадлежит
    пользователю, одним JSON — не только CSV по привычкам (уже был
    /api/export/habits.csv), но и профиль, история переписки с ADAM,
    анкета, вехи, достижения."""
    from . import get_user, get_habits, get_achievements, get_survey, get_milestones, get_ai_history

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT day, habit_title, completed FROM habit_logs WHERE user_id=? ORDER BY day",
        (telegram_id,),
    )
    habit_logs = [dict(row) for row in cursor.fetchall()]
    conn.close()

    user = get_user(telegram_id)
    survey = get_survey(telegram_id)

    return {
        "profile": {
            "telegram_id": telegram_id,
            "username": user["username"] if user else None,
            "first_name": user["first_name"] if user else None,
            "xp": user["xp"] if user else 0,
            "level": user["level"] if user else 1,
            "streak": user["streak"] if user else 0,
            "created_at": user["created_at"] if user else None,
            "long_term_goals": user["long_term_goals"] if user else None,
        },
        "survey": dict(survey) if survey else None,
        "habits": [dict(h) for h in get_habits(telegram_id)],
        "habit_history": habit_logs,
        "achievements": [dict(a) for a in get_achievements(telegram_id)],
        "milestones": [dict(m) for m in get_milestones(telegram_id)],
        "ai_chat_history": get_ai_history(telegram_id, limit=100000),
    }


def request_account_deletion(telegram_id):
    """Немедленно стирает персонально идентифицирующие данные и блокирует
    дальнейшее использование аккаунта (banned=1). Агрегированные
    неидентифицирующие цифры (xp/streak/уровень/история привычек без
    личных заметок) намеренно оставлены — право на удаление касается
    персональных данных, а не абстрактной статистики; каскадное удаление
    по полусотне таблиц в проекте такого размера — риск забыть одну и
    оставить осиротевшие строки, которые потом придётся чистить вручную.
    """
    conn = connect()
    cursor = conn.cursor()

    # Текстовый контент, который пользователь писал сам о себе или ADAM —
    # реальный персональный контент, в отличие от чисел XP/streak.
    cursor.execute("DELETE FROM ai_messages WHERE user_id=?", (telegram_id,))
    cursor.execute("DELETE FROM user_ai_profile WHERE user_id=?", (telegram_id,))
    cursor.execute("DELETE FROM user_survey WHERE user_id=?", (telegram_id,))
    cursor.execute("DELETE FROM habit_notes WHERE user_id=?", (telegram_id,))
    cursor.execute("DELETE FROM client_errors WHERE user_id=?", (telegram_id,))

    cursor.execute(
        """UPDATE users SET
            username=NULL,
            first_name='Удалённый пользователь',
            avatar_id='default',
            frame_id='default',
            public_profile_enabled=0,
            long_term_goals=NULL,
            banned=1
           WHERE telegram_id=?""",
        (telegram_id,),
    )

    conn.commit()
    conn.close()

    avatar_path = Path(DATA_DIR) / "avatars" / f"{telegram_id}.jpg"
    try:
        if avatar_path.exists():
            avatar_path.unlink()
    except OSError:
        pass
