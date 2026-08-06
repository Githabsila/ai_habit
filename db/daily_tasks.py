from datetime import date

from .core import connect
from .users import add_xp
from .statistics import add_statistics


def create_daily_tasks(user_id):
    conn = connect()
    cursor = conn.cursor()

    today = str(date.today())

    cursor.execute(
        "DELETE FROM daily_tasks WHERE user_id=? AND task_date=?",
        (user_id, today)
    )

    tasks = [
        ("Выполнить привычку", 1, 20),
        ("Получить 20 Adam Coin", 20, 30),
        ("Задать вопрос AI", 1, 15)
    ]

    for task, goal, reward in tasks:
        cursor.execute("""
            INSERT INTO daily_tasks(user_id, task, progress, goal, reward, completed, task_date)
            VALUES (?, ?, 0, ?, ?, 0, ?)
        """, (user_id, task, goal, reward, today))

    conn.commit()
    conn.close()


def get_daily_tasks(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM daily_tasks WHERE user_id=? AND task_date=? ORDER BY id
    """, (user_id, str(date.today())))
    tasks = cursor.fetchall()
    conn.close()
    return tasks


def update_daily_task(user_id, task_name, amount=1):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM daily_tasks
        WHERE user_id=? AND task=? AND task_date=?
    """, (user_id, task_name, str(date.today())))

    task = cursor.fetchone()

    if not task:
        conn.close()
        return

    if task["completed"]:
        conn.close()
        return

    progress = task["progress"] + amount
    completed = 1 if progress >= task["goal"] else 0

    cursor.execute("""
        UPDATE daily_tasks SET progress=?, completed=? WHERE id=?
    """, (progress, completed, task["id"]))

    conn.commit()
    conn.close()

    if completed:
        xp = task["reward"]
        add_xp(user_id, xp)
        add_statistics(user_id, completed, xp)
