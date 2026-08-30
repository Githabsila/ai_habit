from datetime import datetime, timedelta

from .core import connect


# =====================================
# ПЛАН ДНЯ (Telegram Mini App — webapp/server.py: /api/plan/*)
# =====================================
#
# ВАЖНО: раньше эти три функции были заглушками прямо в db/__init__.py
# (всегда возвращали пустой план и ничего не сохраняли — план дня из
# мини-аппы никогда не сохранялся и не мог показать прогресс). Здесь —
# настоящая реализация поверх таблиц daily_plans / daily_plan_tasks
# (см. миграцию в db/core.py).

# Пром 14: задачи плана дня — в отличие от привычек (обнуляются строго в
# 00:00) — считаются "сегодняшними" до 3:00 ночи. Многие работают по ночам
# и не успевают закрыть задачи ровно до полуночи; привычки при этом всё
# равно обнуляются в 00:00 (см. db/streak.py rollover_user/reset_habits_for_user
# — их этот сдвиг не касается).
PLAN_DAY_ROLLOVER_HOUR = 3


def _effective_plan_date():
    now = datetime.now()
    effective = now if now.hour >= PLAN_DAY_ROLLOVER_HOUR else now - timedelta(days=1)
    return str(effective.date())


def get_daily_plan(user_id, plan_date=None):
    plan_date = plan_date or _effective_plan_date()

    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM daily_plans WHERE user_id=? AND plan_date=?",
        (user_id, plan_date)
    )
    plan = cursor.fetchone()

    if not plan:
        cursor.execute(
            "INSERT INTO daily_plans(user_id, plan_date) VALUES (?, ?)",
            (user_id, plan_date)
        )
        conn.commit()

        cursor.execute(
            "SELECT * FROM daily_plans WHERE user_id=? AND plan_date=?",
            (user_id, plan_date)
        )
        plan = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM daily_plan_tasks WHERE plan_id=? ORDER BY id",
        (plan["id"],)
    )
    tasks = cursor.fetchall()

    conn.close()

    return {
        "id": plan["id"],
        "main_goal": plan["main_goal"] or "",
        "main_goal_completed": bool(plan["main_goal_completed"]),
        "tasks": [
            {"id": t["id"], "text": t["text"], "completed": bool(t["completed"])}
            for t in tasks
        ]
    }


def set_daily_main_goal(user_id, text):
    """Создаёт/обновляет главную задачу дня без пересоздания списка обычных задач."""
    plan = get_daily_plan(user_id)
    text = (text or "").strip()
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE daily_plans
        SET main_goal=?, main_goal_completed=0, goal_reminder_sent=0
        WHERE id=?
    """, (text, plan["id"]))
    conn.commit()
    conn.close()


def delete_daily_main_goal(user_id):
    plan = get_daily_plan(user_id)
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE daily_plans
        SET main_goal='', main_goal_completed=0, goal_reminder_sent=0
        WHERE id=?
    """, (plan["id"],))
    conn.commit()
    conn.close()


def toggle_daily_main_goal(user_id):
    plan = get_daily_plan(user_id)
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE daily_plans
        SET main_goal_completed = CASE main_goal_completed WHEN 1 THEN 0 ELSE 1 END
        WHERE id=? AND TRIM(main_goal) != ''
    """, (plan["id"],))
    conn.commit()
    conn.close()


def add_daily_task(user_id, text, max_tasks=5):
    text = (text or "").strip()
    if not text:
        return None
    plan = get_daily_plan(user_id)
    if len(plan["tasks"]) >= max_tasks:
        raise ValueError("task_limit")

    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO daily_plan_tasks(plan_id, text, created_at, reminder_sent)
        VALUES (?, ?, CURRENT_TIMESTAMP, 0)
    """, (plan["id"], text))
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id


def update_daily_plan_task(user_id, task_id, text):
    text = (text or "").strip()
    plan = get_daily_plan(user_id)
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE daily_plan_tasks
        SET text=?, reminder_sent=0
        WHERE id=? AND plan_id=?
    """, (text, int(task_id), plan["id"]))
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return bool(updated)


def delete_daily_task(user_id, task_id):
    plan = get_daily_plan(user_id)
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_plan_tasks WHERE id=? AND plan_id=?", (int(task_id), plan["id"]))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return bool(deleted)


def save_daily_plan(user_id, main_goal, tasks):
    plan = get_daily_plan(user_id)
    plan_id = plan["id"]

    conn = connect()
    cursor = conn.cursor()

    # Новый план на день (или отредактированная цель/список задач) —
    # 2-часовой отсчёт для напоминаний (см. get_plan_tasks_needing_reminder /
    # get_plans_needing_goal_reminder) начинается заново.
    cursor.execute("""
        UPDATE daily_plans SET main_goal=?, main_goal_completed=0, goal_reminder_sent=0
        WHERE id=?
    """, ((main_goal or "").strip(), plan_id))

    cursor.execute("DELETE FROM daily_plan_tasks WHERE plan_id=?", (plan_id,))

    for task_text in (tasks or [])[:5]:
        task_text = (task_text or "").strip()
        if task_text:
            cursor.execute("""
                INSERT INTO daily_plan_tasks(plan_id, text, created_at, reminder_sent)
                VALUES (?, ?, CURRENT_TIMESTAMP, 0)
            """, (plan_id, task_text))

    conn.commit()
    conn.close()


def toggle_daily_task(task_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE daily_plan_tasks
        SET completed = CASE completed WHEN 1 THEN 0 ELSE 1 END
        WHERE id=?
    """, (task_id,))
    conn.commit()
    conn.close()


# =====================================
# НАПОМИНАНИЯ ПО КОНКРЕТНОЙ ЗАДАЧЕ ИЗ ПЛАНА ДНЯ
# =====================================

def get_plan_tasks_needing_reminder(user_id, hours=2):
    """Задачи из сегодняшнего плана дня, которые не отмечены выполненными
    уже >= hours часов и по которым ещё не слали напоминание сегодня.
    Используется coach.run_task_reminder_check для точечного пинга по
    названию конкретной задачи (например «Прочитать книгу»)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.* FROM daily_plan_tasks t
        JOIN daily_plans p ON p.id = t.plan_id
        WHERE p.user_id=? AND p.plan_date=?
          AND t.completed=0
          AND t.reminder_sent=0
          AND t.created_at <= datetime('now', ?)
    """, (user_id, _effective_plan_date(), f"-{hours} hours"))
    tasks = cursor.fetchall()
    conn.close()
    return tasks


def mark_plan_task_reminder_sent(task_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE daily_plan_tasks SET reminder_sent=1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()


def get_plans_needing_goal_reminder(user_id, hours=2):
    """Сегодняшний план, если в нём задана общая цель (main_goal), по ней
    ещё не напоминали, прошло >= hours часов с момента её постановки и
    ни одна задача плана пока не отмечена выполненной — то есть по цели
    дня совсем нет прогресса."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.* FROM daily_plans p
        WHERE p.user_id=? AND p.plan_date=?
          AND p.main_goal IS NOT NULL AND TRIM(p.main_goal) != ''
          AND p.goal_reminder_sent=0
          AND p.created_at <= datetime('now', ?)
          AND NOT EXISTS (
              SELECT 1 FROM daily_plan_tasks t
              WHERE t.plan_id = p.id AND t.completed = 1
          )
    """, (user_id, _effective_plan_date(), f"-{hours} hours"))
    plans = cursor.fetchall()
    conn.close()
    return plans


def mark_goal_reminder_sent(plan_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE daily_plans SET goal_reminder_sent=1 WHERE id=?", (plan_id,))
    conn.commit()
    conn.close()
