"""
Самообслуживание аккаунта (см. webapp/webapp_server.py /api/account/*) —
раньше единственный способ выгрузить свои данные целиком или удалить
аккаунт был написать на email из privacy.html и ждать, пока это вручную
сделает админ. Теперь оба действия доступны прямо в настройках.
"""
from pathlib import Path

from .core import connect, DATA_DIR

# Что остаётся после удаления аккаунта: сама строка в users (обезличенная и заблокированная — иначе удалившийся мог бы снова получить пробный период и награды),
# платежи и расчёты (хранить их требуют учёт и закон), журналы защиты от злоупотреблений, жалоб и блокировок. Всё остальное, что привязано к человеку, стирается целиком
# (проверка на нарушения 30.10: раньше стирались 8 таблиц из 79, а приложение обещало, что пропадут все привычки и покупки).
_KEEP_TABLES = {
    "users", "stars_payments", "pay_orders", "pay_events", "partners", "partner_accruals", "partner_attributions", "partner_touches", "partner_payouts", "partner_audit",
    "referral_invites", "abuse_flags", "access_log", "error_log", "user_reports", "user_blocks",
}
# Столбцы, по которым строка принадлежит человеку (у «двусторонних» таблиц — любая из сторон: подписки, подарки, друзья, парные задания).
_PERSON_COLUMNS = ("user_id", "from_user_id", "to_user_id", "follower_id", "followee_id", "friend_id", "inviter_id", "invitee_id", "helper_id", "owner_id")
# В выгрузку «всё, что о тебе хранится» не попадают служебные записи против злоупотреблений и внутренние журналы.
_EXPORT_SKIP = {"users", "abuse_flags", "access_log", "error_log", "partner_audit", "partner_payouts", "user_reports", "user_blocks"}
_EXPORT_ROW_LIMIT = 5000


def _person_tables(cursor, skip):
    """[(таблица, [столбцы-владельцы])] для всех таблиц, где есть привязка к человеку; имена берутся из самой базы, а не из ввода."""
    found = []
    for (table,) in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        if table in skip:
            continue
        columns = {row[1] for row in cursor.execute(f'PRAGMA table_info("{table}")')}
        owned = [column for column in _PERSON_COLUMNS if column in columns]
        if owned:
            found.append((table, owned))
    return found


def _jsonable(value):
    return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else value


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
    # Всё остальное, что хранится о человеке (задачи, план дня, заметки, друзья, подарки, покупки и т.д.), — как есть, по таблицам.
    other_records = {}
    for table, owned in _person_tables(cursor, _EXPORT_SKIP):
        where = " OR ".join(f'"{column}"=?' for column in owned)
        rows = cursor.execute(f'SELECT * FROM "{table}" WHERE {where} LIMIT {_EXPORT_ROW_LIMIT}', (telegram_id,) * len(owned)).fetchall()
        if rows:
            other_records[table] = [{key: _jsonable(row[key]) for key in row.keys()} for row in rows]
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
        "other_records": other_records,
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
    # Учёт пути (db/tracking.py): события несут названия привычек и задач, сессии — что человек нажимал, сводки — пересказ его данных.
    cursor.execute("DELETE FROM user_events WHERE user_id=?", (telegram_id,))
    cursor.execute("DELETE FROM app_sessions WHERE user_id=?", (telegram_id,))
    cursor.execute("DELETE FROM user_summaries WHERE user_id=?", (telegram_id,))
    for table, owned in _person_tables(cursor, _KEEP_TABLES):
        cursor.execute(f'DELETE FROM "{table}" WHERE ' + " OR ".join(f'"{column}"=?' for column in owned), (telegram_id,) * len(owned))

    cursor.execute(
        """UPDATE users SET
            username=NULL,
            first_name='Удалённый пользователь',
            avatar_id='default',
            frame_id='default',
            public_profile_enabled=0,
            long_term_goals=NULL,
            handle=NULL,
            gender=NULL,
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
