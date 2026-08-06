from datetime import date

from .core import connect


def update_calendar(user_id):
    conn = connect()
    cursor = conn.cursor()

    today = str(date.today())

    cursor.execute(
        "SELECT id FROM calendar WHERE user_id=? AND day=?",
        (user_id, today)
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE calendar SET completed = completed + 1 WHERE id=?",
            (row["id"],)
        )
    else:
        cursor.execute(
            "INSERT INTO calendar(user_id, day, completed) VALUES (?, ?, 1)",
            (user_id, today)
        )

    conn.commit()
    conn.close()


def get_calendar(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM calendar WHERE user_id=? ORDER BY day DESC",
        (user_id,)
    )
    data = cursor.fetchall()
    conn.close()
    return data
