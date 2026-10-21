"""
Оплата доступа (триал → Pro): что происходит, когда Stars дошли, и возврат.

complete_subscription_payment — общая точка для настоящего платежа (handlers/payments.py::successful_payment) и для проверки триала
из админки («оплатить тестово»): продлевает доступ на месяц, определяет по дню и отметкам, что человеку сказать (trial_messages.py:
paid_welcome_text — ТЗ владельца: оплаты мало, закрытый канал только с двумя днями ударного режима подряд) и, если серия уже набрана,
сразу создаёт ссылку в канал.
refund_payment — возврат Stars через Telegram (refundStarPayment): платёж помечается возвращённым, оплаченный срок снимается.
"""
from db import (
    get_payment, mark_payment_refunded, record_subscription_payment, revoke_subscription, try_grant_channel_access,
)
from db import trial as trial_db
import trial_messages


async def complete_subscription_payment(bot, user_id, months=1):
    """Продлевает подписку и собирает сообщение после оплаты. Возвращает (текст, состояние триала, ссылка в канал или None)."""
    before = trial_db.snapshot(user_id)                           # цена и день на момент оплаты — для учёта
    until = record_subscription_payment(user_id, months=months)
    snap = trial_db.snapshot(user_id)
    from db.tracking import track_server
    track_server(user_id, "subscription_paid", step=before["phase"] if before else None,
                 meta={"day": snap["day"], "price": before["price_stars"] if before else None, "streak": snap["streak"]})
    link = None
    if snap["channel_eligible"] and snap["channel_configured"]:
        try:
            link = await try_grant_channel_access(bot, user_id)
        except Exception:
            link = None                                    # оплата уже прошла: канал не должен её ломать, ссылку пришлёт ежедневный проход
    return trial_messages.paid_welcome_text(snap, until.strftime("%d.%m.%Y"), link=link), snap, link


async def refund_payment(bot, charge_id):
    """Возвращает Stars за платёж. {"ok": True, ...} или {"error": "not_found" | "already_refunded" | "telegram_failed", "detail": ...}."""
    row = get_payment(charge_id)
    if row is None:
        return {"error": "not_found"}
    if row["refunded_at"]:
        return {"error": "already_refunded"}
    try:
        await bot.refund_star_payment(user_id=row["user_id"], telegram_payment_charge_id=charge_id)
    except Exception as exc:
        return {"error": "telegram_failed", "detail": str(exc)[:200]}
    mark_payment_refunded(charge_id)
    if str(row["payload"] or "").startswith("subscription:"):
        revoke_subscription(row["user_id"])
    return {"ok": True, "user_id": row["user_id"], "amount": row["amount"], "payload": row["payload"]}
