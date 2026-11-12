"""
Карта всех сообщений, которые бот шлёт САМ (просьба владельца 11.11: «увидеть все отработанные сообщения в боте — когда, почему и при
каких условиях приходят»). Одна запись — одна рассылка: группа, название, когда (по МЕСТНОМУ времени человека, если не сказано иное),
кому и при каких условиях, чем отключается, и какие функции её отправляют (sources).

По sources журнал отправок (db/outbox.py) находит, какая рассылка отправила сообщение, — админка показывает её название рядом с каждой
строкой журнала. preview — ключ готового образца: ("bot", ключ bot_catalog) или ("trial", ключ trial_messages) — его можно прислать
себе («Прислать мне») и он участвует в прогоне сценария (bot_test_runs.py).

Общие правила почти для всех рассылок (не повторяем в каждой строке): не пишем тем, кто заблокировал бота или забанен, кто не прошёл
анкету; уважаем общий тумблер «Напоминания», его категорию и тихие часы; одно и то же сообщение — не больше раза за свой период
(claim_notification). Новая рассылка добавляется сюда — тест следит, чтобы каждая функция-рассылка из main.py была на карте.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BotMessage:
    key: str
    group: str
    title: str
    when: str
    conditions: str
    off: str
    sources: tuple
    preview: tuple = ()


G_DAY = "1. День человека: привычки и план"
G_STREAK = "2. Серия (ударный режим)"
G_BACK = "3. Возврат и удержание"
G_TRIAL = "4. Пробный период"
G_PAY = "5. Подписка и оплата"
G_SOCIAL = "6. Друзья, рейтинг, приглашения"
G_DIGEST = "7. Сводки недели и месяца"
G_ADAM = "8. Адам пишет сам"
G_ACCESS = "9. Доступ и анкета"
G_ADMIN = "10. Только админам"

MAP = (
    # ------------------------------------------------------------------ день человека
    BotMessage("ai_welcome", G_DAY, "Приглашение написать Адаму", "Через 4–30 минут после регистрации, один раз в жизни",
               "Человек ещё ни разу не писал Адаму", "Напоминания", ("coach.run_ai_welcome_nudge",)),
    BotMessage("morning", G_DAY, "Доброе утро", "06:00", "Есть привычки; текст пишет ИИ (запасной — если модель недоступна)",
               "Напоминания → «Утро»", ("morning_ping.run_morning_ping",), ("bot", "morning")),
    BotMessage("checkpoint_12", G_DAY, "Точка дня 12:00", "12:00", "Остались незакрытые привычки (причины пропуска — в карточке человека, «Напоминания»)",
               "Напоминания → «Привычки»", ("coach.run_habit_checkpoint_12", "coach._run_habit_checkpoint"), ("bot", "checkpoint_12")),
    BotMessage("checkpoint_17", G_DAY, "Точка дня 17:00", "17:00", "Остались незакрытые привычки",
               "Напоминания → «Привычки»", ("coach.run_habit_checkpoint_17",), ("bot", "checkpoint_17")),
    BotMessage("checkpoint_22", G_DAY, "Точка дня 22:00", "22:00", "За день что-то уже отмечено, но не всё (пустой день ведёт «Последний час» в 23:00)",
               "Напоминания → «Привычки»", ("coach.run_habit_checkpoint_22",), ("bot", "checkpoint_22")),
    BotMessage("checkpoint_10", G_DAY, "Точка дня 10:00", "10:00 — сейчас не рассылается (не больше 5 напоминаний в день)", "—",
               "—", ("coach.run_habit_checkpoint_10",)),
    BotMessage("planned_time", G_DAY, "Привычка по таймеру", "В минуту, на которую у привычки стоит время", "Привычка ещё не отмечена и сегодня по ней не напоминали",
               "Напоминания → «Привычки»; убрать время у привычки", ("coach.run_planned_time_reminders",)),
    BotMessage("task_reminder", G_DAY, "Задача плана висит 2 часа", "Каждые 15 минут проверка", "Задача или главное дело плана не закрыто 2+ часа",
               "Напоминания → «План»", ("coach.run_task_reminder_check",)),
    BotMessage("evening_plan", G_DAY, "Вечерняя сверка плана", "19:00", "В плане дня есть незакрытое", "Напоминания → «План»",
               ("coach.run_day_progress_check",), ("bot", "evening_plan")),
    # ------------------------------------------------------------------ серия
    BotMessage("risk23", G_STREAK, "Последний час", "23:00", "Есть серия, а за день ничего не отмечено", "Напоминания → «Серия»",
               ("streak_scheduler.run_streak_risk_notifications",), ("bot", "risk23")),
    BotMessage("timer", G_STREAK, "Финишный таймер", "23:30, бот правит сообщение до полуночи", "То же: серия и пустой день",
               "Напоминания → «Серия»", ("streak_scheduler.run_streak_risk_notifications", "streak_scheduler._run_countdown"), ("bot", "timer")),
    BotMessage("freeze_upsell", G_STREAK, "Заморозка спасёт серию", "23:00, не чаще раза в неделю", "Серия под угрозой, заморозки нет, хватает монет",
               "Напоминания → «Серия»", ("streak_scheduler._maybe_send_freeze_upsell",)),
    BotMessage("weekly_bonus", G_STREAK, "Неделя без пропусков — награда", "Воскресенье 10:00", "7 дней подряд без пропусков, награду ещё не забирал",
               "Напоминания → «Серия»", ("streak_scheduler.run_weekly_streak_bonus",)),
    BotMessage("personal_record", G_STREAK, "Ещё день — и рекорд", "09:00", "Серия на 1 день короче личного рекорда", "Напоминания → «Серия»",
               ("streak_scheduler.run_personal_record_notifications",)),
    BotMessage("channel_out", G_STREAK, "Вылет из закрытого канала", "Сразу при проверке (раз в 15 минут)", "Серия погасла или кончилась подписка (канал подключён)",
               "Нельзя — это сообщение о доступе", ("channel_sync.run_channel_sync",), ("bot", "channel_streak")),
    # ------------------------------------------------------------------ возврат
    BotMessage("reengage_short", G_BACK, "Вернись в серию (1–3 дня)", "10:00 и 16:00 (у части людей ещё 21:00 — эксперимент)",
               "1–3 полных дня без отметок; после первой отметки за день — тишина", "Напоминания → «Серия»",
               ("streak_scheduler.run_streak_reengagement_notifications",), ("bot", "reengage_10")),
    BotMessage("reengage_long", G_BACK, "Вернись в серию (долгий перерыв)", "4–7 дней: 12:00 и 20:00; 8–30 дней: 18:00 через день; 31+: 18:00 раз в 3 дня",
               "Давно без отметок", "Напоминания → «Серия»", ("streak_scheduler.run_streak_reengagement_notifications",), ("bot", "reengage_long")),
    # ------------------------------------------------------------------ триал
    BotMessage("d2_invite", G_TRIAL, "День 2 · зовём зайти", "День 2, 12–17, один раз", "Сегодня не заходил и не видел предложения в приложении",
               "Админка → «Триал» (выключатель сообщения)", ("subscription_scheduler.run_trial_reminders",), ("trial", "d2_invite")),
    BotMessage("d3_talk", G_TRIAL, "День 3 · поговорить с Адамом", "День 3, 11–14, один раз, без продажи", "Сегодня меньше 2 сообщений Адаму",
               "Админка → «Триал»", ("subscription_scheduler.run_trial_reminders",), ("trial", "d3_talk")),
    BotMessage("d3_end", G_TRIAL, "День 3 · пробный доступ кончается", "День 3, 18–21, один раз", "Ещё не оплатил; приходит и при выключенных напоминаниях",
               "Админка → «Триал»", ("subscription_scheduler.run_trial_reminders",), ("trial", "d3_end")),
    BotMessage("grace_notice", G_TRIAL, "Окно 24 часа после триала", "9–21, один раз", "Триал кончился, не оплатил", "Админка → «Триал»",
               ("subscription_scheduler.run_trial_reminders",), ("trial", "grace_notice")),
    BotMessage("winback", G_TRIAL, "После триала · редкие возвраты", "11–19, паузы растут; без реакции — реже", "Не оплатил после окна",
               "Кнопка «Не присылать» под сообщением; админка → «Триал»", ("subscription_scheduler.run_trial_reminders",), ("trial", "wb_question")),
    # ------------------------------------------------------------------ оплата
    BotMessage("renew_manual", G_PAY, "Подписка кончается (без автопродления)", "12:00 за 7, 3, 1 день и в день окончания", "Оплачен срок, автопродления нет",
               "Нельзя — сообщение о деньгах и доступе", ("subscription_renewal.run_renewal_reminders",), ("bot", "renew_1")),
    BotMessage("renew_auto", G_PAY, "Скоро автосписание", "12:00 за 3 дня", "Включено автопродление", "Нельзя",
               ("subscription_renewal.run_renewal_reminders",), ("bot", "renew_auto")),
    BotMessage("renew_failed", G_PAY, "Автопродление не прошло", "Через 3 часа после конца срока", "Списания не было; доступ держится ещё сутки",
               "Нельзя", ("subscription_renewal.run_renewal_reminders",), ("bot", "renew_failed")),
    BotMessage("pay_done", G_PAY, "Оплата прошла", "Сразу после оплаты (звёзды, карта, крипта)", "—", "Нельзя",
               ("payments_ext.fulfil", "handlers.payments.successful_payment", "subscription_flow.complete_subscription_payment")),
    BotMessage("pay_retry", G_PAY, "Покупка всё-таки начислена", "Раз в 3 минуты повтор", "Деньги пришли, а начислить сразу не вышло", "Нельзя",
               ("subscription_flow.retry_failed_payments",)),
    # ------------------------------------------------------------------ друзья
    BotMessage("rank_overtaken", G_SOCIAL, "Тебя обогнали в рейтинге", "Раз в сутки (08:00 UTC), только днём по местному времени", "Был в топ-100 и кто-то обошёл",
               "Напоминания → «Серия»", ("streak_scheduler.run_rank_overtaken_notifications",)),
    BotMessage("pair_quest", G_SOCIAL, "Парное задание", "Сундук ждёт — сразу; последний день — 18:00; задание кончилось — позвать снова",
               "Есть парное задание с другом", "Напоминания", ("streak_scheduler.run_pair_quest_notifications", "streak_scheduler._send_pair_push", "streak_scheduler._run_pair_restart_nudges")),
    BotMessage("rescue_friend", G_SOCIAL, "Спаси серию друга", "Раз в 5 минут, только днём по местному времени", "Серия друга недавно погасла",
               "Напоминания", ("streak_scheduler.run_rescue_notifications", "streak_scheduler._send_rescue_push")),
    BotMessage("referral_mailing", G_SOCIAL, "Позови друга", "2-й, 4-й, 6-й день после регистрации", "Остались награды за приглашения",
               "Флаг referral_mailing; Напоминания", ("referral_mailing.run_referral_mailing",)),
    BotMessage("referral_notice", G_SOCIAL, "Награда за друга", "После первой отметки приглашённого", "—", "—", ("referral_mailing.run_referral_notices",)),
    BotMessage("partner_accrual", G_SOCIAL, "Партнёрская программа: новое начисление", "Раз в 5 минут", "Приглашённый что-то купил", "—",
               ("partner_scheduler.run_partner_jobs",)),
    # ------------------------------------------------------------------ сводки
    BotMessage("week_start", G_DIGEST, "Начало недели", "Понедельник 07:00", "—", "Напоминания → «Сводки»", ("coach.run_week_start_ping", "coach._broadcast")),
    BotMessage("week_end", G_DIGEST, "Конец недели", "Воскресенье 18:00", "—", "Напоминания → «Сводки»", ("coach.run_week_end_ping",)),
    BotMessage("weekly_report", G_DIGEST, "Отчёт недели", "Воскресенье 19:00", "—", "Напоминания → «Сводки»", ("coach.run_weekly_report",)),
    BotMessage("weekly_analysis", G_DIGEST, "Разбор проседающей привычки (ИИ)", "Воскресенье 19:15", "Есть история привычек", "Напоминания → «Сводки»",
               ("coach.run_weekly_habit_analysis",)),
    BotMessage("month_start", G_DIGEST, "Начало месяца", "1-го числа 07:30", "—", "Напоминания → «Сводки»", ("coach.run_month_start_ping",)),
    BotMessage("month_end", G_DIGEST, "Конец месяца", "Последний день 18:30", "—", "Напоминания → «Сводки»", ("coach.run_month_end_ping",)),
    BotMessage("monthly_analysis", G_DIGEST, "Разбор месяца (ИИ)", "1-го числа 08:00", "Есть история привычек", "Напоминания → «Сводки»",
               ("coach.run_monthly_habit_analysis",)),
    BotMessage("goal_feedback", G_DIGEST, "Разбор недели по цели (ИИ)", "Понедельник 10:00", "Заполнена цель в анкете, есть доступ", "Тихие часы",
               ("goal_feedback.run_goal_feedback",)),
    # ------------------------------------------------------------------ Адам
    BotMessage("adam_checkin", G_ADAM, "Адам пишет первым", "Окна 12:30–16:30 и 18:30–21:30, рядом с привычным временем человека",
               "Есть повод (ИИ решает писать или промолчать); не раньше чем через час после другого пуша", "Флаг adam_checkin; Напоминания",
               ("adam_proactive.run_adam_checkins",)),
    # ------------------------------------------------------------------ доступ
    BotMessage("auto_approve", G_ACCESS, "Доступ открыт (автоодобрение)", "Через 3 часа после анкеты", "Заявка ещё на проверке, не забанен", "—",
               ("onboarding_auto.run_auto_approve", "handlers.onboarding.notify_approved")),
    BotMessage("inactive_purge", G_ACCESS, "Аккаунт удалён за неактивность", "04:40 UTC", "Не заходил INACTIVE_PURGE_MONTHS месяцев (0 — выключено)", "INACTIVE_PURGE_MONTHS=0",
               ("db.inactive_purge.run_inactive_purge",)),
    # ------------------------------------------------------------------ админам
    BotMessage("admin_digest", G_ADMIN, "Сводка дня", "Раз в день", "—", "—", ("admin_digest_scheduler.run_admin_daily_digest",)),
    BotMessage("admin_analyst", G_ADMIN, "Разбор аналитика", "Понедельник 06:30 UTC", "—", "—", ("analyst_scheduler.run_weekly_analyst",)),
    BotMessage("admin_errors", G_ADMIN, "Всплеск ошибок", "Проверка раз в 10 минут", "Ошибок больше порога", "ERROR_SPIKE_THRESHOLD", ("error_alert_scheduler.run_error_spike_check",)),
    BotMessage("admin_abuse", G_ADMIN, "Подозрительная активность", "Проверка раз в 3 минуты", "—", "—", ("abuse_alerts.run_abuse_alerts",)),
    BotMessage("admin_backup", G_ADMIN, "Копия базы и кода", "05:00 и 05:20 UTC", "—", "—", ("backups.backup.send_offsite_backup", "backups.backup.send_code_archive")),
)

# Задания планировщика, которые получают бота, но людям ничего не пишут (сверка ников через getChat).
SILENT_JOBS = ("username_sync.run_username_sync",)

BY_KEY = {m.key: m for m in MAP}
_BY_SOURCE = {}
for _m in MAP:
    for _s in _m.sources:
        _BY_SOURCE.setdefault(_s, _m)


def find(chain):
    """Рассылка по цепочке «модуль.функция < …» из журнала отправок; None — ответ на действие человека или не на карте."""
    for name in (chain or "").split(" < "):
        hit = _BY_SOURCE.get(name.strip())
        if hit is not None:
            return hit
    return None


def find_row(row):
    """Рассылка для строки журнала: сначала по ключу, который рассылка назвала сама (outbox.kind), потом по стеку вызова."""
    key = row.get("kind") if hasattr(row, "get") else None
    return BY_KEY.get(key) or find(row.get("chain") if hasattr(row, "get") else None)


def as_rows(stats=None):
    stats = stats or {}
    rows = []
    for m in MAP:
        # Ключ, который рассылка назвала сама, плюс «модуль.функция» — только если функция отправляет ОДНО это сообщение
        # (общие функции вроде _run_habit_checkpoint или run_trial_reminders иначе приписывали бы чужие отправки, прогон 11.11).
        own = [s for s in m.sources if sum(1 for other in MAP if s in other.sources) == 1]
        sent = [stats[k] for k in [m.key, *own] if k in stats]
        rows.append({"key": m.key, "group": m.group, "title": m.title, "when": m.when, "conditions": m.conditions, "off": m.off,
                     "preview": list(m.preview), "week_count": sum(s["count"] for s in sent), "week_failed": sum(s["failed"] for s in sent),
                     "last_at": max((s["last_at"] for s in sent if s["last_at"]), default=None)})
    return rows
