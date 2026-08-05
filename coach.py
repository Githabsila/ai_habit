"""
coach.py
Проактивная часть AI-коуча — этап 3 "AI Coach":
  - прогноз срыва привычек (rule-based, без обращений к Groq — дёшево и
    можно гонять для всех пользователей разом);
  - проактивные сообщения (вечерний пинг тем, у кого серия под угрозой);
  - жёсткий дедлайн в 21:00 — контрольное напоминание ВСЕМ, у кого остались
    невыполненные привычки, независимо от серии;
  - недельные отчёты (шаблонные, без LLM — экономим запросы к Groq,
    см. этап 4 "уменьшение количества запросов к Groq");
  - еженедельный AI-разбор по КАЖДОЙ привычке отдельно (habit_logs) —
    персонализированная обратная связь с конкретным советом.

Джобы регистрируются в main.py через scheduler (apscheduler), как и
существующие new_day/send_reminders.
"""

import logging

from db import (
    get_all_users,
    get_settings,
    get_progress,
    get_weekly_summary,
    log_error,
    get_incomplete_habits,
    get_weekly_habit_breakdown,
    get_ai_style,
)
from multi_agent import generate_weekly_habit_feedback

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


# =====================================
# ЖЁСТКИЙ ДЕДЛАЙН (21:00)
# =====================================

def format_hard_deadline_message(incomplete_habits) -> str:
    titles = [h["title"] for h in incomplete_habits]

    lines = "\n".join(f"• {t}" for t in titles)

    return (
        "⏰ <b>Контрольная точка — 21:00</b>\n\n"
        "До конца дня осталось немного, а эти привычки ещё не отмечены "
        "выполненными:\n\n"
        f"{lines}\n\n"
        "Ещё есть время всё закрыть 💪"
    )


async def run_hard_deadline_check(bot):
    """Жёсткий дедлайн: если к 21:00 у пользователя остались привычки, не
    отмеченные выполненными сегодня, — отправляем контрольное напоминание.
    В отличие от run_streak_risk_check (20:00, только для тех, у кого есть
    серия — то есть есть что терять), эта проверка идёт ВСЕМ, у кого
    включены напоминания и есть незакрытые привычки, независимо от серии."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        incomplete = get_incomplete_habits(telegram_id)
        if not incomplete:
            continue

        try:
            await bot.send_message(
                telegram_id,
                format_hard_deadline_message(incomplete),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("hard_deadline", e, telegram_id)

    logger.info(f"Жёсткий дедлайн 21:00: отправлено {sent} напоминаний")


# =====================================
# ЕЖЕНЕДЕЛЬНЫЙ AI-РАЗБОР ПО ПРИВЫЧКАМ
# =====================================

def _format_breakdown_text(breakdown) -> str:
    lines = []
    for row in breakdown:
        lines.append(
            f"{row['habit_title']}: выполнено {row['done']}/{row['total']}, "
            f"пропущено {row['missed']}"
        )
    return "\n".join(lines)


def _fallback_habit_feedback(breakdown) -> str:
    """Шаблонный запасной вариант, если Groq недоступен — берём привычку
    с наибольшим числом пропусков и даём общий, но конкретный совет."""
    worst = max(breakdown, key=lambda r: r["missed"])

    if worst["missed"] == 0:
        return (
            "📊 <b>AI-разбор недели по привычкам</b>\n\n"
            "На этой неделе все привычки выполнялись без пропусков — "
            "отличный ритм, продолжай в том же духе 🔥"
        )

    return (
        "📊 <b>AI-разбор недели по привычкам</b>\n\n"
        f"Ты пропустил {worst['missed']} дн. привычки «{worst['habit_title']}». "
        "Попробуй на первое время сделать её полегче или покороче — "
        "чтобы было проще вернуться в ритм, чем совсем бросить 🌱"
    )


async def run_weekly_habit_analysis(bot):
    """Раз в неделю — смотрим не на общий агрегат, а на КАЖДУЮ привычку
    отдельно (habit_logs, см. db/habits.py) и просим AI дать конкретную
    персонализированную обратную связь по самой проседающей привычке —
    например предложить сократить время, чтобы вернуться в ритм."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        breakdown = get_weekly_habit_breakdown(telegram_id)
        if not breakdown:
            # За неделю не было ни одной записи в журнале (нет привычек
            # или бот только что запущен) — разбирать нечего.
            continue

        breakdown_text = _format_breakdown_text(breakdown)
        style = get_ai_style(telegram_id) or "neutral"

        ai_text = await generate_weekly_habit_feedback(breakdown_text, style)

        if ai_text:
            text = "📊 <b>AI-разбор недели по привычкам</b>\n\n" + ai_text
        else:
            text = _fallback_habit_feedback(breakdown)

        try:
            await bot.send_message(telegram_id, text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            log_error("weekly_habit_analysis", e, telegram_id)

    logger.info(f"AI-разбор недели по привычкам: отправлено {sent} пользователям")
