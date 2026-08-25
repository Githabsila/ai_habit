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
    format_habit_reminder_messages,
    format_habit_final_motivation_message,
    format_habit_noon_message,
    format_habit_checkpoint_10_message,
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
    left = len(titles)
    habit_word = "привычка" if left == 1 else "привычки" if left < 5 else "привычек"
    verb = "неотмечена" if left == 1 else "неотмечены"
    lines = "\n".join(f"• {t}" for t in titles)

    return (
        "⏰ <b>Контрольная точка — 22:00</b>\n\n"
        f"<b>{verb} {habit_word}: {left}</b>\n"
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

        # Ночью и прямо в окне утреннего приветствия не отправляем
        # точечные пинги: в 06:00 пользователь должен получить только одно
        # утреннее сообщение ADAM, без конкурирующих уведомлений.
        now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
        if 0 <= now_local.hour < 6 or (now_local.hour == 6 and now_local.minute < 15):
            continue
        # После 20:00 не отправляем точечные сообщения по задачам плана:
        # вечерняя сверка в 22:30 должна собрать ВСЕ открытые пункты в одно
        # сообщение, а не присылать их по одному с интервалом.
        if now_local.hour >= 20:
            continue

        # -- привычки --
        # Индивидуальные 2-часовые пинги по привычкам отключены.
        # Теперь для привычек есть одна общая контрольная точка в 12:00,
        # чтобы несколько отдельных сообщений не превращались в спам.

        # -- привычки --
        # Индивидуальные пинги по привычкам полностью отключены.
        # Все открытые привычки собираются в одну актуальную контрольную
        # точку, чтобы не создавать серию однотипных уведомлений.

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
# КОНТРОЛЬНАЯ ТОЧКА ПО ПРИВЫЧКАМ (10:00)
# =====================================
#
# В отличие от run_task_reminder_check (точечно, через REMINDER_AFTER_HOURS
# часов бездействия), это отдельная контрольная точка в 10:00.
# В 12:00 отдельный job уже рассылает индивидуальные пинги по оставшимся
# привычкам.

async def run_habit_checkpoint_10(bot):
    """Единая контрольная точка по привычкам в 10:00 локального времени.
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
            if now.hour != 10:
                continue

            incomplete = get_incomplete_habits(telegram_id)
            if not incomplete:
                continue

            day = now.date().isoformat()
            if not claim_notification(telegram_id, day, "habit_checkpoint_10"):
                continue

            await bot.send_message(
                telegram_id,
                format_habit_checkpoint_10_message(incomplete),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("habit_checkpoint_10", e, telegram_id)

    logger.info(f"Контрольная точка привычек 10:00: отправлено {sent} сообщений")


# =====================================
# ОТДЕЛЬНЫЕ НАПОМИНАНИЯ ПО ПРИВЫЧКАМ (12:00)
# =====================================

async def run_habit_reminders_12(bot):
    """В 12:00 отправляет по одному сообщению только по тем привычкам,
    которые ещё не выполнены. Формулировки внутри одной рассылки не
    повторяются."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]
        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        try:
            now = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            if now.hour != 12:
                continue

            incomplete = get_incomplete_habits(telegram_id)
            if not incomplete:
                continue

            day = now.date().isoformat()
            if not claim_notification(telegram_id, day, "habit_reminders_12"):
                continue

            messages = format_habit_reminder_messages([h["title"] for h in incomplete])
            for text in messages:
                try:
                    await bot.send_message(telegram_id, text, parse_mode="HTML")
                    sent += 1
                except Exception as e:
                    log_error("habit_reminder_12_item", e, telegram_id)

            if len(messages) > 1:
                try:
                    await bot.send_message(
                        telegram_id,
                        format_habit_final_motivation_message(),
                        parse_mode="HTML"
                    )
                    sent += 1
                except Exception as e:
                    log_error("habit_reminder_12_final", e, telegram_id)
        except Exception as e:
            log_error("habit_reminders_12", e, telegram_id)

    logger.info(f"Отдельные напоминания по привычкам 12:00: отправлено {sent} сообщений")


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
    """22:30 — финальная сверка плана, если осталась хотя бы 1
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
        main_open = bool(plan.get("main_goal") and not plan.get("main_goal_completed"))
        open_tasks = [t for t in tasks if not t["completed"]]
        total_items = len(tasks) + (1 if plan.get("main_goal") else 0)
        done_items = sum(1 for t in tasks if t["completed"]) + (1 if plan.get("main_goal") and plan.get("main_goal_completed") else 0)

        # Даже если обычных задач нет, одна открытая главная задача должна
        # попасть в вечернюю сверку.
        if total_items == 0 or done_items >= total_items:
            continue

        try:
            now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            if now_local.hour != 22 or now_local.minute != 30:
                continue
            day = now_local.date().isoformat()
            if not claim_notification(telegram_id, day, "evening_progress_2230"):
                continue
            await bot.send_message(
                telegram_id,
                format_evening_progress_message(
                    done_items,
                    total_items,
                    [t["text"] for t in open_tasks],
                    main_goal=(plan["main_goal"] if main_open else None),
                ),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            log_error("evening_progress_check", e, telegram_id)

    logger.info(f"Вечерняя сверка по плану дня (22:30): отправлено {sent} сообщений")


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
