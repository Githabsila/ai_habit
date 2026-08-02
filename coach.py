"""
coach.py
Проактивная часть AI-коуча — этап 3 "AI Coach":
  - прогноз срыва привычек (rule-based, без обращений к Groq — дёшево и
    можно гонять для всех пользователей разом);
  - проактивные сообщения (вечерний пинг тем, у кого серия под угрозой);
  - недельные отчёты (шаблонные, без LLM — экономим запросы к Groq,
    см. этап 4 "уменьшение количества запросов к Groq").

Оба джоба регистрируются в main.py через scheduler (apscheduler), как и
существующие new_day/send_reminders.
"""

import logging

from db import get_all_users, get_settings, get_progress, get_weekly_summary, log_error

logger = logging.getLogger("coach")


# =====================================
# ПРОГНОЗ СРЫВА ПРИВЫЧЕК
# =====================================

def is_at_risk(progress: dict) -> bool:
    """Пользователь 'под риском', если у него есть привычки, они не все
    выполнены сегодня, и есть накопленная серия (streak >= 1) — то есть
    реально есть что терять. Без серии (streak == 0) пинговать нет смысла —
    терять нечего, это просто обычное напоминание, которое и так уходит
    через reminders.py."""
    if not progress or progress["total"] == 0:
        return False
    if progress["completed"] >= progress["total"]:
        return False
    if progress["streak"] < 1:
        return False
    return True


def format_risk_message(progress: dict) -> str:
    left = progress["total"] - progress["completed"]
    return (
        f"🔥 <b>Твоя серия — {progress['streak']} дн.</b> сегодня под угрозой!\n\n"
        f"Осталось невыполненных привычек: <b>{left}</b>. Ещё есть время сегодня "
        f"их закрыть 💪"
    )


async def run_streak_risk_check(bot):
    """Раз в день (вечером) — тем, у кого включены напоминания и есть что
    терять, уходит короткий пинг. Не LLM-звонок, чисто по данным из БД —
    дёшево и можно запускать для всех пользователей сразу."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        progress = get_progress(telegram_id)
        if not is_at_risk(progress):
            continue

        try:
            await bot.send_message(
                telegram_id,
                format_risk_message(progress),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("streak_risk", e, telegram_id)

    logger.info(f"Проверка риска срыва серии: отправлено {sent} уведомлений")


# =====================================
# НЕДЕЛЬНЫЕ ОТЧЁТЫ
# =====================================

def format_weekly_report(summary: dict) -> str:
    active_days = summary["active_days"]

    if active_days >= 6:
        comment = "Отличная неделя — почти без пропусков! Так держать 🔥"
    elif active_days >= 3:
        comment = "Неплохая неделя, но есть куда расти 💪"
    elif active_days >= 1:
        comment = "Неделя выдалась тяжёлой — бывает. Главное не бросать 🌱"
    else:
        comment = "На этой неделе активности не было — начнём заново? 🙂"

    return (
        "📅 <b>Твоя неделя в Project ADAM</b>\n\n"
        f"✅ Выполнено привычек: <b>{summary['completed']}</b>\n"
        f"⭐ Получено Adam Coin: <b>{summary['xp']}</b>\n"
        f"📆 Активных дней: <b>{active_days}/7</b>\n\n"
        f"{comment}"
    )


async def run_weekly_report(bot):
    """Раз в неделю — короткий шаблонный отчёт по каждому пользователю.
    Специально без вызова Groq (см. этап 4): данных для персонализации
    текста тут достаточно и без LLM, а недельная рассылка по всем
    пользователям — не место экономить на количестве, а место экономить
    на токенах."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        summary = get_weekly_summary(telegram_id)
        if summary["active_days"] == 0:
            # Неактивным за неделю не шлём — не будем добавлять спам
            # тем, кто и так, похоже, отошёл от бота.
            continue

        try:
            await bot.send_message(
                telegram_id,
                format_weekly_report(summary),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("weekly_report", e, telegram_id)

    logger.info(f"Недельные отчёты отправлены {sent} пользователям")
