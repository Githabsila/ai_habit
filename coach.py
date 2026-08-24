"""
coach.py
Проактивная часть AI-коуча — этап 3 "AI Coach":
  - прогноз срыва привычек (rule-based, без обращений к OpenAI — дёшево и
    можно гонять для всех пользователей разом);
  - проактивные сообщения (вечерний пинг тем, у кого серия под угрозой);
  - жёсткий дедлайн в 22:00 — контрольное напоминание ВСЕМ, у кого остались
    невыполненные привычки, независимо от серии;
  - недельные отчёты (шаблонные, без LLM — экономим запросы к OpenAI,
    см. этап 4 "уменьшение количества запросов к OpenAI");
  - еженедельный AI-разбор по КАЖДОЙ привычке отдельно (habit_logs) —
    персонализированная обратная связь с конкретным советом.

Джобы регистрируются в main.py через scheduler (apscheduler), как и
существующие new_day/send_reminders.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from db import (
    get_all_users,
    get_settings,
    get_progress,
    get_weekly_summary,
    log_error,
    get_incomplete_habits,
    get_weekly_habit_breakdown,
    get_ai_style,
    get_habits_needing_reminder,
    mark_habit_reminder_sent,
    get_plan_tasks_needing_reminder,
    mark_plan_task_reminder_sent,
    get_plans_needing_goal_reminder,
    mark_goal_reminder_sent,
    get_daily_plan,
    get_timezone,
    claim_notification,
)
from multi_agent import generate_weekly_habit_feedback
from adam_messages import (
    format_day_progress_message,
    format_evening_progress_message,
    format_habit_reminder_message,
    format_habit_final_motivation_message,
    format_habit_noon_message,
    format_plan_task_reminder_message,
    format_goal_reminder_message,
    format_week_start_message,
    format_week_end_message,
    format_month_start_message,
    format_month_end_message,
)

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

    if active_days >= 7:
        comment = "БЕЗ ПРОПУСКОВ 🔥"
    else:
        comment = "НАДО ПОДНАЖАТЬ И ПОСТАРАТЬСЯ — НА СЛЕДУЮЩЕЙ НЕДЕЛЕ ЛУЧШЕ СПРАВИТЬСЯ 💪"

    return (
        "📅 <b>Твоя неделя в Project ADAM</b>\n\n"
        f"✅ Выполнено привычек: <b>{summary['completed']}</b>\n"
        f"⭐ Получено Adam Coin: <b>{summary['xp']}</b>\n"
        f"📆 Активных дней: <b>{active_days}/7</b>\n\n"
        f"{comment}"
    )


async def run_weekly_report(bot):
    """Раз в неделю — короткий шаблонный отчёт по каждому пользователю.
    Специально без вызова OpenAI (см. этап 4): данных для персонализации
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
# КОНТРОЛЬНАЯ ТОЧКА (22:00)
# =====================================

def format_hard_deadline_message(incomplete_habits) -> str:
    titles = [h["title"] for h in incomplete_habits]

    lines = "\n".join(f"• {t}" for t in titles)

    return (
        "⏰ <b>Контрольная точка — 22:00</b>\n\n"
        "До конца дня осталось немного, а эти привычки ещё не отмечены "
        "выполненными:\n\n"
        f"{lines}\n\n"
        "Ещё есть время всё закрыть 💪"
    )


async def run_hard_deadline_check(bot):
    """Жёсткий дедлайн: если к 22:00 у пользователя остались привычки, не
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

        # Контрольная точка должна приходить именно в 22:00 по локальному
        # времени пользователя, а не по timezone сервера/Railway.
        try:
            now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            if now_local.hour != 22:
                continue

            day = now_local.date().isoformat()
            if not claim_notification(telegram_id, day, "hard_deadline_22"):
                continue

            await bot.send_message(
                telegram_id,
                format_hard_deadline_message(incomplete),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("hard_deadline", e, telegram_id)

    logger.info(f"Контрольная точка 22:00: отправлено {sent} напоминаний")


# =====================================
# ИНДИВИДУАЛЬНЫЕ НАПОМИНАНИЯ ПО ЗАДАЧАМ (через N часов бездействия)
# =====================================
#
# В отличие от reminders.py (общая рассылка всем раз в 2 часа с одним и тем
# же текстом) и run_hard_deadline_check (один раз в 22:00, списком) — это
# точечная проверка: у КАЖДОГО пользователя отдельно смотрим, по какой
# конкретно привычке/задаче плана дня прошло >= REMINDER_AFTER_HOURS часов
# без выполнения, и шлём напоминание с её названием. По каждой задаче
# напоминание уходит один раз в день (флаг reminder_sent, сбрасывается
# вместе с самой задачей — см. db/habits.py и db/daily_plan.py).

REMINDER_AFTER_HOURS = 2


async def run_task_reminder_check(bot):
    """Запускается по расписанию каждые ~15-30 минут (main.py). За один
    прогон пользователю может уйти несколько сообщений — по одному на
    каждую просроченную привычку/задачу плюс, при необходимости, одно
    про общую цель дня."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        # После полуночи и до 06:00 никаких напоминаний пользователю не отправляем.
        # Это также защищает от старых/просроченных индивидуальных пингов.
        now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
        if 0 <= now_local.hour < 6:
            continue

        # -- привычки --
        # Индивидуальные 2-часовые пинги по привычкам отключены.
        # Теперь для привычек есть одна общая контрольная точка в 12:00,
        # чтобы несколько отдельных сообщений не превращались в спам.

        # -- привычки с учётом времени выполнения --
        now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
        current_minutes = now_local.hour * 60 + now_local.minute
        for habit in get_habits_needing_reminder(telegram_id, hours=REMINDER_AFTER_HOURS):
            planned = habit["planned_time"] if "planned_time" in habit.keys() else None
            if planned:
                try:
                    hh, mm = map(int, planned[:5].split(":"))
                    planned_minutes = hh * 60 + mm
                    if current_minutes < planned_minutes:
                        continue
                except Exception:
                    pass
            try:
                await bot.send_message(telegram_id, format_habit_reminder_message(habit["title"]), parse_mode="HTML")
                sent += 1
            except Exception as e:
                log_error("task_reminder_habit", e, telegram_id)
            finally:
                mark_habit_reminder_sent(habit["id"])

        # -- задачи из плана дня (Mini App) --
        for task in get_plan_tasks_needing_reminder(telegram_id, hours=REMINDER_AFTER_HOURS):
            try:
                await bot.send_message(
                    telegram_id,
                    format_plan_task_reminder_message(task["text"]),
                    parse_mode="HTML"
                )
                sent += 1
            except Exception as e:
                log_error("task_reminder_plan_task", e, telegram_id)
            finally:
                mark_plan_task_reminder_sent(task["id"])

        # -- общая цель дня --
        for plan in get_plans_needing_goal_reminder(telegram_id, hours=REMINDER_AFTER_HOURS):
            try:
                await bot.send_message(
                    telegram_id,
                    format_goal_reminder_message(plan["main_goal"]),
                    parse_mode="HTML"
                )
                sent += 1
            except Exception as e:
                log_error("task_reminder_goal", e, telegram_id)
            finally:
                mark_goal_reminder_sent(plan["id"])

    logger.info(f"Индивидуальные напоминания по задачам: отправлено {sent} сообщений")


# =====================================
# УТРЕННИЕ НАПОМИНАНИЯ ПО ПРИВЫЧКАМ (06:00)
# =====================================
#
# В отличие от run_task_reminder_check (точечно, через REMINDER_AFTER_HOURS
# часов бездействия), это фиксированная утренняя проверка (промт п.5):
# в 06:00 — по одному сообщению на каждую ещё не отмеченную привычку, и
# только если такие привычки вообще есть (п.4/11 — не слать, если всё уже
# выполнено), плюс одно финальное мотивационное сообщение в конце.

async def run_noon_habit_reminders(bot):
    """Единая контрольная точка по привычкам в 12:00 локального времени.
    Один пользователь получает максимум одно сообщение в сутки и только
    если хотя бы одна привычка ещё не выполнена."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]
        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        try:
            tz_name = get_timezone(telegram_id)
            now = datetime.now(ZoneInfo(tz_name))
            if now.hour != 12:
                continue

            incomplete = get_incomplete_habits(telegram_id)
            if not incomplete:
                continue

            day = now.date().isoformat()
            if not claim_notification(telegram_id, day, "habit_noon"):
                continue

            await bot.send_message(
                telegram_id,
                format_habit_noon_message(incomplete),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("noon_habit_reminders", e, telegram_id)

    logger.info(f"Контрольная точка привычек 12:00: отправлено {sent} сообщений")


# =====================================
# ДНЕВНОЕ / ВЕЧЕРНЕЕ НАПОМИНАНИЕ ПО ПЛАНУ ДНЯ (15:00 / 20:00)
# =====================================
#
# Промт п.2: если к 15:00 из плана дня выполнено <= половины задач — придёт
# мотивационное сообщение; если к 20:00 осталась хотя бы 1 невыполненная
# задача — тоже. Если план дня пуст (нет ни одной задачи) — проверять
# нечего, писать не будем (п.4/11).

async def run_day_progress_check(bot):
    """15:00 — мотивационное сообщение, если выполнено <= половины задач
    сегодняшнего плана дня."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        plan = get_daily_plan(telegram_id)
        tasks = plan["tasks"]
        if not tasks:
            continue

        done = sum(1 for t in tasks if t["completed"])
        total = len(tasks)

        if done > total / 2:
            continue

        try:
            await bot.send_message(
                telegram_id,
                format_day_progress_message(done, total),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("day_progress_check", e, telegram_id)

    logger.info(f"Дневное напоминание по плану дня (15:00): отправлено {sent} сообщений")


async def run_evening_progress_check(bot):
    """20:00 — мотивационное сообщение, если осталась хотя бы 1
    невыполненная задача сегодняшнего плана дня."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        plan = get_daily_plan(telegram_id)
        tasks = plan["tasks"]
        if not tasks:
            continue

        done = sum(1 for t in tasks if t["completed"])
        total = len(tasks)

        if done >= total:
            continue

        try:
            await bot.send_message(
                telegram_id,
                format_evening_progress_message(done, total, [t["text"] for t in tasks if not t["completed"]]),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("evening_progress_check", e, telegram_id)

    logger.info(f"Вечернее напоминание по плану дня (20:00): отправлено {sent} сообщений")


# =====================================
# ПЕРИОДИЧЕСКИЕ МОТИВАЦИОННЫЕ СООБЩЕНИЯ
# (начало/конец недели, начало/конец месяца) — промт п.8
# =====================================

async def _broadcast(bot, text_fn, job_name):
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        try:
            await bot.send_message(telegram_id, text_fn(), parse_mode="HTML")
            sent += 1
        except Exception as e:
            log_error(job_name, e, telegram_id)

    logger.info(f"{job_name}: отправлено {sent} сообщений")


async def run_week_start_ping(bot):
    await _broadcast(bot, format_week_start_message, "week_start_ping")


async def run_week_end_ping(bot):
    await _broadcast(bot, format_week_end_message, "week_end_ping")


async def run_month_start_ping(bot):
    await _broadcast(bot, format_month_start_message, "month_start_ping")


async def run_month_end_ping(bot):
    await _broadcast(bot, format_month_end_message, "month_end_ping")


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
    """Шаблонный запасной вариант, если OpenAI недоступен — берём привычку
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
