from .core import connect


def get_settings(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (user_id,))
    settings = cursor.fetchone()
    conn.close()
    return settings


def update_reminder_time(user_id, hour, minute):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE settings SET reminder_hour=?, reminder_minute=?
        WHERE user_id=?
    """, (hour, minute, user_id))
    conn.commit()
    conn.close()
