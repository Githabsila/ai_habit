"""
coach.py
Проактивная часть AI-коуча — этап 3 "AI Coach":
  - проактивные сообщения по расписанию без дублей;
  - отдельные финальные напоминания о привычках только в 23:00 и 23:30;
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
    claim_notification, release_notification, notification_scope, in_time_window,
    get_habits, mark_habit_reminder_sent,
    reminder_category_enabled,
)
from multi_agent import generate_weekly_habit_feedback
from adam_messages import (
    format_day_progress_message,
    format_habit_checkpoint_message,
    format_plan_task_reminder_message,
    format_goal_reminder_message,
    format_week_start_message,
    format_week_end_message,
    format_month_start_message,
    format_month_end_message,
    format_planned_time_reminder_message,
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
    # Глобальный cron (один момент времени для всех, не по локальному часу
    # пользователя), поэтому ключ дедупликации — просто текущая неделя, а
    # не локальная дата пользователя.
    week_key = datetime.now().strftime("%G-W%V")
    scope = notification_scope(bot)

    for user in users:
        telegram_id = user["telegram_id"]

        # Раньше здесь не было проверки настройки напоминаний — недельный
        # отчёт уходил даже тем, кто отключил уведомления в настройках.
        # Категория "digests" — сводки и отчёты (см. db/settings.py).
        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "digests"):
            continue

        summary = get_weekly_summary(telegram_id)
        if summary["active_days"] == 0:
            # Неактивным за неделю не шлём — не будем добавлять спам
            # тем, кто и так, похоже, отошёл от бота.
            continue

        # Дедуп на случай повторного/двойного срабатывания cron-job'а —
        # раньше отчёт мог уйти дважды без единой защиты от дубля.
        if not claim_notification(telegram_id, week_key, "weekly_report", scope):
            continue

        try:
            await bot.send_message(
                telegram_id,
                format_weekly_report(summary),
                parse_mode="HTML"
            )
            sent += 1
        except Exception as e:
            release_notification(telegram_id, week_key, "weekly_report", scope)
            log_error("weekly_report", e, telegram_id)

    logger.info(f"Недельные отчёты отправлены {sent} пользователям")


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

REMINDER_AFTER_HOURS = 2  # только для задач плана; привычки больше не имеют 2-часовых пингов


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
        if not reminder_category_enabled(settings, "habits"):
            continue

        # Ночью и прямо в окне утреннего приветствия не отправляем
        # точечные пинги: в 06:00 пользователь должен получить только одно
        # утреннее сообщение ADAM, без конкурирующих уведомлений.
        now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
        if 0 <= now_local.hour < 6 or (now_local.hour == 6 and now_local.minute < 15):
            continue
        # Вечерняя сверка в 19:00 — единое сообщение по всему плану.
        # Не создаём рядом с ней отдельные пинги по одной задаче.
        if 18 <= now_local.hour < 20:
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
# СВОЁ ВРЕМЯ НАПОМИНАНИЯ У ПРИВЫЧКИ
# =====================================
#
# В отличие от отключённых выше 2-часовых пингов (спам по системному
# правилу) — это опционально: пользователь сам ставит время конкретной
# привычке (habits.planned_time, см. webapp/webapp_server.py), и только
# для тех, кто его поставил, приходит персональное напоминание строго
# в это время. Молчит для всех остальных привычек — планового времени
# по умолчанию нет ни у одной.

async def run_planned_time_reminders(bot):
    """Раз в минуту (main.py) — у кого из привычек planned_time совпало
    с текущей локальной минутой пользователя, тот ещё не выполнен и по
    нему сегодня ещё не напоминали."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]
        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "habits"):
            continue

        now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))

        for habit in get_habits(telegram_id):
            if habit["completed"] or habit["reminder_sent"]:
                continue
            planned_time = habit["planned_time"]
            if not planned_time:
                continue
            try:
                planned_hour, planned_minute = (int(x) for x in planned_time.split(":"))
            except (ValueError, AttributeError):
                continue
            # in_time_window вместо "== ровно эта минута": раньше пропущенный
            # ровно на этой минуте тик (нагрузка, передеплой) означал, что
            # planned_time уже "проехал" и напоминание по этой привычке
            # молчало весь день. От повторов внутри окна защищает флаг
            # reminder_sent (ставится сразу после первой попытки ниже).
            if not in_time_window(now_local, hour=planned_hour, minute=planned_minute):
                continue
            try:
                await bot.send_message(
                    telegram_id,
                    format_planned_time_reminder_message(habit["title"]),
                )
                sent += 1
            except Exception as e:
                log_error("planned_time_reminder", e, telegram_id)
            finally:
                # Помечаем отправленным даже при ошибке доставки — иначе
                # временный сбой Telegram превратится в повтор на каждый
                # следующий тик планировщика (раз в минуту) до конца дня.
                mark_habit_reminder_sent(habit["id"])

    if sent:
        logger.info(f"Напоминания по своему времени привычки: отправлено {sent}")


# =====================================
# КОНТРОЛЬНАЯ ТОЧКА ПО ПРИВЫЧКАМ (10:00)
# =====================================
#
# В отличие от run_task_reminder_check (точечно, через REMINDER_AFTER_HOURS
# часов бездействия), это отдельная контрольная точка в 10:00.
# В 12:00 отдельный job уже рассылает индивидуальные пинги по оставшимся
# привычкам.

async def _run_habit_checkpoint(bot, target_hour: int, kind: str, label: str):
    """Отправляет одну контрольную точку по привычкам в локальный час пользователя.

    Важно: запись о доставленном уведомлении создаётся ТОЛЬКО после успешной
    отправки Telegram-сообщения. Раньше claim_notification() выполнялся до
    send_message(); если Telegram временно отвечал ошибкой, уведомление
    помечалось как уже отправленное и пользователь больше его не получал.
    """
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]
        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "habits"):
            continue

        try:
            now = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            # Окно допуска, не "== ровно эта минута" — от повторов защищает
            # claim_notification ниже (атомарный, один раз в день на kind),
            # а не точность попадания в тик планировщика.
            if not in_time_window(now, hour=target_hour, minute=0):
                continue

            incomplete = get_incomplete_habits(telegram_id)
            if not incomplete:
                continue

            day = now.date().isoformat()
            scope = notification_scope(bot)

            # Резервируем уведомление до отправки, чтобы два параллельных
            # экземпляра приложения не отправили дубль. Если Telegram
            # отклонит сообщение, резерв ниже освобождается и следующий тик
            # сможет повторить попытку.
            if not claim_notification(telegram_id, day, kind, scope):
                continue

            try:
                await bot.send_message(
                    telegram_id,
                    format_habit_checkpoint_message(incomplete, target_hour),
                    parse_mode="HTML"
                )
            except Exception:
                release_notification(telegram_id, day, kind, scope)
                raise

            sent += 1
            logger.info("%s: отправлено %s", label, telegram_id)
        except Exception as e:
            log_error(kind, e, telegram_id)

    logger.info(f"{label}: отправлено {sent} сообщений")


async def run_habit_checkpoint_10(bot):
    """10:00 — контрольная точка по привычкам, только если есть невыполненные."""
    await _run_habit_checkpoint(bot, 10, "habit_checkpoint_10", "Контрольная точка привычек 10:00")


async def run_habit_checkpoint_12(bot):
    """12:00 — дневная контрольная точка по привычкам, только если есть невыполненные."""
    await _run_habit_checkpoint(bot, 12, "habit_checkpoint_12", "Дневное напоминание привычек 12:00")


# =====================================
# ВЕЧЕРНЯЯ СВЕРКА ПЛАНА ДНЯ (19:00)
# =====================================
#
# В 19:00 считаются ВСЕ пункты сегодняшнего плана:
# 1) главная задача;
# 2) все второстепенные задачи.
# Поэтому пользователь всегда видит одну честную цифру по всему плану.

async def run_day_progress_check(bot):
    """19:00 — единая сверка главной и второстепенных задач."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "habits"):
            continue

        try:
            now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            if not in_time_window(now_local, hour=19, minute=0):
                continue

            plan = get_daily_plan(telegram_id)
            tasks = plan.get("tasks") or []
            main_goal = (plan.get("main_goal") or "").strip()

            items = []
            if main_goal:
                items.append({
                    "text": main_goal,
                    "completed": bool(plan.get("main_goal_completed")),
                    "kind": "главная",
                })
            items.extend({
                "text": t["text"],
                "completed": bool(t["completed"]),
                "kind": "второстепенная",
            } for t in tasks)

            if not items:
                continue

            done = sum(1 for item in items if item["completed"])
            total = len(items)
            left = total - done

            # В 19:00 уведомляем только если есть незакрытые пункты.
            if left <= 0:
                continue

            day = now_local.date().isoformat()
            scope = notification_scope(bot)
            if not claim_notification(telegram_id, day, "day_progress_19", scope):
                continue

            open_items = [
                f"{item['kind']}: «{item['text']}»"
                for item in items if not item["completed"]
            ]

            try:
                await bot.send_message(
                    telegram_id,
                    format_day_progress_message(
                        done,
                        total,
                        open_items,
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                # Резерв освобождаем, только если реально не отправили — иначе
                # временный сбой Telegram/сети "сжигал" вечернюю сверку на весь
                # день без единой попытки повтора.
                release_notification(telegram_id, day, "day_progress_19", scope)
                raise
            sent += 1

        except Exception as e:
            log_error("day_progress_check", e, telegram_id)

    logger.info(
        f"Вечерняя сверка плана дня (19:00): отправлено {sent} сообщений"
    )


# Старую отдельную вечернюю сверку 22:30 намеренно не запускаем:
# в 19:00 пользователь получает единую картину по главной + второстепенным задачам.

# =====================================
# ПЕРИОДИЧЕСКИЕ МОТИВАЦИОННЫЕ СООБЩЕНИЯ
# (начало/конец недели, начало/конец месяца) — промт п.8
# =====================================

async def _broadcast(bot, text_fn, job_name, day_key):
    """day_key — ключ дедупликации claim_notification (неделя или месяц,
    в зависимости от job'а). Раньше здесь не было вообще никакой защиты от
    дубля: любое повторное/наложившееся срабатывание cron-job'а (напр. на
    границе misfire_grace_time) рассылало это сообщение ВСЕМ пользователям
    ещё раз."""
    users = get_all_users()
    sent = 0
    scope = notification_scope(bot)

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "digests"):
            continue

        if not claim_notification(telegram_id, day_key, job_name, scope):
            continue

        try:
            await bot.send_message(telegram_id, text_fn(), parse_mode="HTML")
            sent += 1
        except Exception as e:
            release_notification(telegram_id, day_key, job_name, scope)
            log_error(job_name, e, telegram_id)

    logger.info(f"{job_name}: отправлено {sent} сообщений")


async def run_week_start_ping(bot):
    week_key = datetime.now().strftime("%G-W%V")
    await _broadcast(bot, format_week_start_message, "week_start_ping", week_key)


async def run_week_end_ping(bot):
    week_key = datetime.now().strftime("%G-W%V")
    await _broadcast(bot, format_week_end_message, "week_end_ping", week_key)


async def run_month_start_ping(bot):
    month_key = datetime.now().strftime("%Y-%m")
    await _broadcast(bot, format_month_start_message, "month_start_ping", month_key)


async def run_month_end_ping(bot):
    month_key = datetime.now().strftime("%Y-%m")
    await _broadcast(bot, format_month_end_message, "month_end_ping", month_key)


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
    week_key = datetime.now().strftime("%G-W%V")
    scope = notification_scope(bot)

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "digests"):
            continue

        breakdown = get_weekly_habit_breakdown(telegram_id)
        if not breakdown:
            # За неделю не было ни одной записи в журнале (нет привычек
            # или бот только что запущен) — разбирать нечего.
            continue

        # Резервируем ДО платного вызова AI — раньше здесь не было защиты
        # от дубля вообще: повторное срабатывание job'а не только слало
        # разбор дважды, но и оплачивало вызов OpenAI дважды на пустом месте.
        if not claim_notification(telegram_id, week_key, "weekly_habit_analysis", scope):
            continue

        breakdown_text = _format_breakdown_text(breakdown)
        style = get_ai_style(telegram_id) or "neutral"

        try:
            ai_text = await generate_weekly_habit_feedback(breakdown_text, style)

            if ai_text:
                text = "📊 <b>AI-разбор недели по привычкам</b>\n\n" + ai_text
            else:
                text = _fallback_habit_feedback(breakdown)

            await bot.send_message(telegram_id, text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            release_notification(telegram_id, week_key, "weekly_habit_analysis", scope)
            log_error("weekly_habit_analysis", e, telegram_id)

    logger.info(f"AI-разбор недели по привычкам: отправлено {sent} пользователям")
