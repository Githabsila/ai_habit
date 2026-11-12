from .core import connect
from .users import add_xp
from .statistics import add_statistics


def _today(user_id):
    """Местная дата человека. Раньше — дата сервера: смена дня (местная полночь) пересоздавала уже закрытые задания «сегодня по серверу»,
    и награду за них можно было получить дважды в сутки (прогон 11.11)."""
    from .streak import local_today
    return local_today(user_id).isoformat()


def create_daily_tasks(user_id):
    conn = connect()
    cursor = conn.cursor()

    today = _today(user_id)

    # Уже созданные на этот день не трогаем (раньше DELETE + INSERT обнулял выполненные).
    if cursor.execute("SELECT 1 FROM daily_tasks WHERE user_id=? AND task_date=? LIMIT 1", (user_id, today)).fetchone():
        conn.close()
        return

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
    """, (user_id, _today(user_id)))
    tasks = cursor.fetchall()
    conn.close()
    return tasks


def update_daily_task(user_id, task_name, amount=1):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM daily_tasks
        WHERE user_id=? AND task=? AND task_date=?
    """, (user_id, task_name, _today(user_id)))

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
