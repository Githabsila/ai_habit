"""
Еженедельный разбор ИИ-аналитика (db/analyst.py): по понедельникам сам читает цифры за неделю, сохраняет разбор и память, и присылает
админам короткую выжимку со ссылкой на раздел /analytics. Сбой модели не ломает ничего: пишем в лог и ждём следующей недели.
Плюс блок «Аналитика заметила» для ежедневной сводки — только серьёзные датчики (ошибки, падения), без информационных.
"""
import html
import logging

from config import ADMIN_IDS

logger = logging.getLogger("analyst_scheduler")

IMPACT_ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}


def build_week_message(entry):
    """Короткий текст для админов из сохранённого разбора (HTML)."""
    report = (entry or {}).get("report")
    if not report:
        return None
    lines = ["🧠 <b>Еженедельный разбор аналитика</b>"]
    if report.get("headline"):
        lines.append(html.escape(report["headline"]))
    for finding in report.get("findings", [])[:3]:
        lines.append(f"{IMPACT_ICON.get(finding['impact'], '•')} <b>{html.escape(finding['title'])}</b> — {html.escape(finding['action'] or finding['evidence'])}")
    lines.append("Полный разбор, цифры и память аналитика — команда /analytics.")
    return "\n".join(lines)


async def run_weekly_analyst(bot):
    from db import analyst
    try:
        result = await analyst.run(7, trigger="weekly")
    except Exception:
        logger.exception("Еженедельный разбор аналитика не получился")
        return
    if not result.get("ok"):
        logger.warning("Еженедельный разбор аналитика пропущен: %s", result.get("error"))
        return
    text = build_week_message(result["report"])
    if not text:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            logger.warning(f"Не удалось отправить разбор аналитика админу {admin_id}")


def build_analytics_block():
    """Серьёзные датчики для ежедневной сводки; пусто, если всё спокойно (тогда в сводку ничего не добавляется)."""
    from db import stats
    items = [s for s in stats.signals()["items"] if s["level"] in ("bad", "warn")]
    if not items:
        return ""
    lines = ["\n\n📊 <b>Аналитика заметила:</b>"]
    for item in items[:4]:
        lines.append(f"• {html.escape(item['title'])} — {html.escape(item['text'])}")
    lines.append("Подробнее: /analytics")
    return "\n".join(lines)
