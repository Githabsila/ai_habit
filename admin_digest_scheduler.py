"""
Ежедневная сводка для админов + сама функция построения текста, которую
переиспользует и handlers/admin.py::admin_stats (кнопка "Статистика"),
чтобы не разъезжались две копии одной и той же логики.

Раньше мониторинг ошибок/статистики был только "по запросу" (ручная
кнопка в /admin) — тихая поломка (как краш анкеты онбординга в этой
сессии) могла оставаться незамеченной сколько угодно, пока кто-то из
админов сам не откроет статистику. Эта рассылка — то же самое, но
проактивно, раз в день.
"""
import logging

from config import ADMIN_IDS, AI_GLOBAL_DAILY_UNIT_CEILING, AI_DAILY_TOKEN_CEILING
from db import (
    get_all_users_info,
    get_ai_feedback_stats,
    get_error_stats,
    get_access_status_counts,
    get_dau,
    get_subscription_conversion,
    get_ai_usage_today,
    get_habit_completion_rate,
    get_survey_funnel,
    get_first_ai_message_funnel,
    get_ai_tokens_today,
    get_ai_tokens_by_provider_today,
)

logger = logging.getLogger("admin_digest_scheduler")

# В какой час (UTC) слать ежедневную сводку.
DIGEST_HOUR_UTC = 8


def build_stats_report():
    """Общий текст статистики — используется и в /admin "Статистика",
    и в ежедневной автосводке. HTML-разметка, парсится parse_mode='HTML'."""
    users = get_all_users_info()
    total = len(users)
    premium = sum(1 for u in users if u["premium"])
    banned = sum(1 for u in users if u["banned"])
    total_xp = sum(u["xp"] for u in users)
    total_level = sum(u["level"] for u in users)
    avg_level = round(total_level / total, 2) if total else 0

    fb = get_ai_feedback_stats()
    fb_line = (
        f"👍 {fb['up']} / 👎 {fb['down']} (позитивных: {fb['positive_share']}%)"
        if fb["total"] else "оценок пока нет"
    )

    errors = get_error_stats(hours=24)
    err_line = (
        f"⚠️ {errors['total']} за 24ч (" + ", ".join(
            f"{row['scope']}: {row['cnt']}" for row in errors["by_scope"]
        ) + ")"
        if errors["total"] else "за 24ч ошибок не было ✅"
    )

    access_counts = get_access_status_counts()
    pending_n = access_counts.get("pending", 0)
    new_n = access_counts.get("new", 0)
    approved_n = access_counts.get("approved", 0)

    dau = get_dau(days=1)
    sub = get_subscription_conversion()

    # "Единицы квоты" — только ручной чат, для контроля злоупотреблений
    # одним пользователем (см. handlers/ai.py consume_ai_answer).
    ai_used = get_ai_usage_today()
    ai_ceiling_line = f"{ai_used} / {AI_GLOBAL_DAILY_UNIT_CEILING}"
    if ai_used >= AI_GLOBAL_DAILY_UNIT_CEILING:
        ai_ceiling_line += " 🚨 ПРЕВЫШЕН потолок"
    elif ai_used >= int(AI_GLOBAL_DAILY_UNIT_CEILING * 0.8):
        ai_ceiling_line += " ⚠️ близко к потолку"

    # Реальные токены API — покрывает ВСЁ (чат + автонапоминания на LLM:
    # совет дня, утреннее сообщение, еженедельный разбор, анализ анкеты).
    tokens_used = get_ai_tokens_today()
    tokens_line = f"{tokens_used:,} / {AI_DAILY_TOKEN_CEILING:,}".replace(",", " ")
    if tokens_used >= AI_DAILY_TOKEN_CEILING:
        tokens_line += " 🚨 ПРЕВЫШЕН потолок"
    elif tokens_used >= int(AI_DAILY_TOKEN_CEILING * 0.8):
        tokens_line += " ⚠️ близко к потолку"
    by_provider = get_ai_tokens_by_provider_today()
    if by_provider:
        tokens_line += " (" + ", ".join(f"{p}: {n:,}".replace(",", " ") for p, n in by_provider.items()) + ")"

    habits = get_habit_completion_rate()
    survey = get_survey_funnel()
    first_ai = get_first_ai_message_funnel()

    return f"""
📊 <b>Статистика бота</b>

👥 Пользователей: <b>{total}</b> · сегодня заходили: <b>{dau}</b>

💎 Premium: <b>{premium}</b> · 🚫 Заблокировано: <b>{banned}</b>

⭐ Всего Adam Coin: <b>{total_xp}</b> · Средний уровень: <b>{avg_level}</b>

🔐 Доступ: одобрено <b>{approved_n}</b> / на проверке <b>{pending_n}</b> / анкету не прошли <b>{new_n}</b>

📝 Воронка входа: анкету завершили <b>{survey['completed_survey']}</b>/{survey['total']} · доступ открыт <b>{survey['approved']}</b>

💬 Первое сообщение ADAM написали: <b>{first_ai['sent_first_message']}</b>/{first_ai['approved']} одобренных ({first_ai['rate_percent']}%)

💳 Подписка: платили хоть раз <b>{sub['paid']}</b>/{sub['eligible']} ({sub['rate_percent']}%)

✅ Привычки сегодня: выполнено <b>{habits['done']}</b>/{habits['total']} ({habits['rate_percent']}%)

🔥 Реальный расход токенов LLM сегодня (чат + все AI-напоминания): <b>{tokens_line}</b>

🤖 Расход AI-квоты чата сегодня: <b>{ai_ceiling_line}</b> · оценки ответов: {fb_line}

🩺 Мониторинг ошибок: <b>{err_line}</b>
""".strip()


async def run_admin_daily_digest(bot):
    """Раз в день (см. main.py: cron hour=DIGEST_HOUR_UTC) шлёт эту сводку
    всем админам — не дожидаясь, пока кто-то из них сам зайдёт в /admin."""
    try:
        text = build_stats_report()
    except Exception as e:
        logger.exception("Не удалось построить ежедневную сводку")
        text = f"🩺 Не удалось построить ежедневную сводку статистики: {e}"

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            logger.warning(f"Не удалось отправить ежедневную сводку админу {admin_id}")
