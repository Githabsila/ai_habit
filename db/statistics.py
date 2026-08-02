from datetime import date

from .core import connect


def add_statistics(user_id, completed, xp):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO statistics(user_id, completed, gained_xp, stat_date)
        VALUES (?, ?, ?, ?)
    """, (user_id, completed, xp, str(date.today())))

    conn.commit()
    conn.close()


def get_statistics(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM statistics WHERE user_id=? ORDER BY id DESC",
        (user_id,)
    )
    stats = cursor.fetchall()
    conn.close()
    return stats
