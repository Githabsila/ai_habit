"""
Оплата доступа (триал → Pro): что происходит, когда Stars дошли, сбой начисления и возврат.

complete_subscription_payment — общая точка для настоящего платежа (handlers/payments.py::successful_payment) и для проверки триала
из админки («оплатить тестово»): продлевает доступ на месяц (ранняя покупка добавляет оставшиеся бесплатные дни, оплата в окне после триала сохраняет серию),
собирает подтверждение со сроком и условиями продления (trial_messages.paid_welcome_text) и, если закрытый канал подключён и серия набрана, создаёт ссылку в него.
Начисление идемпотентно по платежу: повторная обработка того же charge_id (повтор после сбоя, повторное сообщение Telegram) месяц второй раз не добавляет.

Сбой (ТЗ 28.10 §19): если деньги получены, а начисление упало — report_payment_failure говорит владельцу (ник, id, платёж, ошибка) и человеку («платёж получен, повторно
платить не нужно», запасной контакт), а retry_failed_payments раз в несколько минут повторяет начисление и сообщает, когда получилось.
refund_payment — возврат Stars через Telegram (refundStarPayment): платёж помечается возвращённым, оплаченный срок снимается.
"""
import html
import logging
import uuid
from datetime import datetime, timedelta, timezone

from config import ADMIN_IDS, SUPPORT_CONTACT, SUPPORT_EMAIL
from db import (
    get_payment, get_user, mark_payment_refunded, record_subscription_payment, revoke_subscription, try_grant_channel_access,
)
from db import trial as trial_db
import trial_messages

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5                                                  # сколько раз повторяем упавшее начисление, потом — только вручную


def _naive_utc(moment):
    if moment is None:
        return None
    if getattr(moment, "tzinfo", None) is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def support_line():
    """Запасной способ связи для человека: контакт в Telegram, если задан, иначе почта."""
    return SUPPORT_CONTACT or SUPPORT_EMAIL


async def complete_subscription_payment(bot, user_id, months=1, paid_at=None, test=False, charge_id=None, recurring=False, expires_at=None,
                                        calendar_months=False):
    """Продлевает подписку и собирает сообщение после оплаты. Возвращает (текст, состояние триала, ссылка в канал или None)."""
    from db import shop
    paid_at = _naive_utc(paid_at)
    if test:                                                      # тестовая оплата из админки: в реальные показатели и выплаты не попадает (ТЗ §3, §26)
        charge_id = charge_id or f"test:{user_id}:{uuid.uuid4().hex[:10]}"
        shop.mark_payment_processed(charge_id, user_id, f"subscription:{user_id}", 0, purchased_at=paid_at or datetime.now(timezone.utc), is_test=True)
    before = trial_db.snapshot(user_id)                           # цена и день на момент оплаты — для учёта
    early = bool(before and before.get("state") == "trial")      # платит во время пробных дней: бесплатные дни добавляются к платному сроку
    if charge_id and shop.is_credited(charge_id):                 # этот платёж уже начислен (повтор после сбоя): срок второй раз не добавляем
        until = _paid_until(user_id)
    else:
        until = record_subscription_payment(user_id, months=months, paid_at=paid_at, expires_at=_naive_utc(expires_at), calendar_months=calendar_months)
        shop.mark_credited(charge_id)
    from db import trial_protection
    try:
        protection = trial_protection.apply_on_payment(user_id, paid_at)   # оплата в окне после триала возвращает серию, если день окна уже закрылся
    except Exception:
        logger.exception("Не удалось применить защиту серии после оплаты (%s)", user_id)
        protection = {"applied": False, "reason": "error"}
    if recurring:
        _remember_subscription(user_id, charge_id)
    try:
        snap = trial_db.snapshot(user_id)
        snap["protection"], snap["early"] = protection, early
        from db.tracking import track_server
        track_server(user_id, "subscription_renewed" if recurring and not (before and not before.get("ever_paid")) else "subscription_paid",
                     step=before["phase"] if before else None,
                     meta={"day": snap["day"], "price": before["price_stars"] if before else None, "streak": snap["streak"], "early": early,
                           "recurring": bool(recurring), "test": bool(test)})
    except Exception:
        logger.exception("Не удалось собрать состояние после оплаты (%s)", user_id)
        snap = {"early": early, "protection": protection, "phase": "paid", "day": 1, "streak": 0, "renewal_price_stars": 0, "renewal_usd": ""}
    link = None
    if snap.get("channel_eligible") and snap.get("channel_configured"):
        try:
            link = await try_grant_channel_access(bot, user_id)
        except Exception:
            link = None                                    # оплата уже прошла: канал не должен её ломать, ссылку пришлёт ежедневный проход
    return trial_messages.paid_welcome_text(snap, until, link=link, auto_renew=bool(recurring)), snap, link


def _paid_until(user_id):
    user = get_user(user_id)
    raw = user["subscription_paid_until"] if user and "subscription_paid_until" in user.keys() else None
    try:
        return datetime.fromisoformat(str(raw)) if raw else datetime.now(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _remember_subscription(user_id, charge_id):
    """Подписка с автопродлением: запоминаем платёж, по которому её можно отключить (editUserStarSubscription)."""
    from db.core import connect
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_auto_renew=1, subscription_charge_id=? WHERE telegram_id=?", (charge_id, user_id))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- счёт

SUBSCRIPTION_PERIOD_SECONDS = 2592000                              # единственный период подписки Stars, который принимает Telegram: 30 дней


def invoice_spec(user_id):
    """Что выставить человеку (ТЗ §2, §6): название, описание, сумма в Stars и — если это подписка с автопродлением — период. None — оплата не нужна.
    Схема: первая покупка — разовый счёт на 30 дней по вводной цене (Stars не умеют другую цену первого периода внутри подписки); все следующие — подписка
    с автопродлением по полной цене, если включён флаг SUBSCRIPTION_AUTORENEW_ENABLED, иначе тоже разовый счёт («продление вручную»)."""
    from config import SUBSCRIPTION_AUTORENEW_ENABLED
    snap = trial_db.snapshot(user_id)
    if snap is None:
        return None
    if snap["paid"]:                                       # платить может любой, кто захотел (решение владельца 28.10), не только тот, у кого идёт сценарий триала
        return _renewal_spec(user_id, snap, SUBSCRIPTION_AUTORENEW_ENABLED)
    price = int(snap["price_stars"])
    renewal = snap["renewal_price_stars"]
    recurring = bool(SUBSCRIPTION_AUTORENEW_ENABLED and not snap["intro_active"])
    if snap["intro_active"]:
        description = (f"Первый месяц по вводной цене: привычки, план дня и разговор со мной на 30 дней. Дальше — {renewal} ⭐ в месяц. "
                       "Продление вручную: само ничего не спишется.")
    elif recurring:
        description = f"Подписка: привычки, план дня и разговор со мной. Каждые 30 дней спишется {price} ⭐, отключить можно в любой момент."
    else:
        description = "Привычки, план дня и разговор со мной на 30 дней. Продление вручную: само ничего не спишется."
    return {"title": "ADAM Pro — 30 дней", "description": description[:255], "price": price, "recurring": recurring,
            "period": SUBSCRIPTION_PERIOD_SECONDS if recurring else None, "snapshot": snap, "payload": f"subscription:{user_id}"}


def _term_over(user):
    """Оплаченный срок уже позади (идут только сутки запаса на подтверждение автосписания)."""
    raw = user["subscription_paid_until"] if user is not None and "subscription_paid_until" in user.keys() else None
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(str(raw).replace("Z", "").split("+")[0])
    except ValueError:
        return False
    return until <= datetime.utcnow()


def _renewal_spec(user_id, snap, autorenew_enabled):
    """Подписка уже идёт (решение 05.11: «автопродление очень важно, на удержании всё строим»). Без автопродления — можно включить его заранее
    или продлить вручную; новый срок добавляется к концу текущего (db/subscription.py::record_subscription_payment), дни не пропадают.
    С включённым автопродлением платить нечего — None."""
    from db.subscription import auto_renew_active, subscription_overview
    from db.users import get_user
    user = get_user(user_id)
    if auto_renew_active(user) and not _term_over(user):
        return None                                                # срок идёт, Telegram продлит сам
    # Прогон 09.11: срок кончился, а списание не пришло (сутки запаса) — бот просит «пополни звёзды и продли», значит, оплата вручную доступна.
    price = int(snap["renewal_price_stars"])
    until = (subscription_overview(user_id) or {}).get("paid_until_text") or ""
    after = f" Новый срок начнётся после текущего{(' (до ' + until + ')') if until else ''} — оплаченные дни не пропадут."
    if autorenew_enabled:
        description = f"Автопродление: каждые 30 дней спишется {price} ⭐, отключить можно в любой момент.{after}"
        return {"title": "ADAM Pro — автопродление", "description": description[:255], "price": price, "recurring": True,
                "period": SUBSCRIPTION_PERIOD_SECONDS, "snapshot": snap, "payload": f"subscription:{user_id}"}
    description = f"Ещё 30 дней привычек, плана дня и разговора со мной.{after} Продление вручную: само ничего не спишется."
    return {"title": "ADAM Pro — ещё 30 дней", "description": description[:255], "price": price, "recurring": False,
            "period": None, "snapshot": snap, "payload": f"subscription:{user_id}"}


async def create_invoice_link(bot, user_id):
    """Ссылка на оплату по правилам invoice_spec. (ссылка, спецификация) или (None, None), если платить нечего."""
    from aiogram.types import LabeledPrice
    spec = invoice_spec(user_id)
    if spec is None:
        return None, None
    kwargs = dict(title=spec["title"], description=spec["description"], payload=spec["payload"], provider_token="", currency="XTR",
                  prices=[LabeledPrice(label="Доступ на 30 дней" if not spec["recurring"] else "Подписка на 30 дней", amount=spec["price"])])
    if spec["period"]:
        kwargs["subscription_period"] = spec["period"]
    link = await bot.create_invoice_link(**kwargs)
    from db.tracking import track_server
    track_server(user_id, "subscription_invoice", step=spec["snapshot"]["phase"],
                 meta={"price": spec["price"], "day": spec["snapshot"]["day"], "recurring": spec["recurring"]})
    return link, spec


# ---------------------------------------------------------------------------------------------------------------- сбой начисления

def _person(user):
    nick = f"@{user.username}" if getattr(user, "username", None) else "без @ника"
    name = html.escape(getattr(user, "full_name", None) or getattr(user, "first_name", None) or "")
    return f"{name} {nick}".strip()


async def report_payment_failure(bot, user, charge_id, payload, amount, error):
    """Деньги получены, а начисление упало: сообщаем владельцу всё, что нужно для поиска, и человеку — что делать (не платить повторно)."""
    from db import log_error
    from db.tracking import track_server
    log_error("payment_failed", f"charge_id={charge_id} payload={payload} error={error}", getattr(user, "id", None))
    track_server(getattr(user, "id", None), "subscription_payment_failed", meta={"charge_id": str(charge_id)[:60], "error": str(error)[:120]})
    admin_text = (
        "⚠️ <b>Оплата получена, а доступ не открылся</b>\n\n"
        f"Покупатель: {_person(user)} · id <code>{user.id}</code>\n"
        f"Платёж: <code>{html.escape(str(charge_id))}</code> · {amount} ⭐ · <code>{html.escape(str(payload))}</code>\n"
        f"Ошибка: <code>{html.escape(str(error)[:300])}</code>\n\n"
        f"Начисление повторится само (до {MAX_ATTEMPTS} раз, раз в несколько минут). Если не получится — открой доступ вручную в админке и напиши человеку."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML")
        except Exception:
            logger.warning("Не удалось отправить сбой оплаты админу %s", admin_id)
    try:
        await bot.send_message(
            user.id,
            "Платёж получен, спасибо! Доступ откроется чуть позже: я проверяю оплату. Повторно платить не нужно. "
            f"Если за несколько минут ничего не изменится, напиши: {support_line()}.",
        )
    except Exception:
        logger.warning("Не удалось сообщить о сбое оплаты человеку %s", getattr(user, "id", None))


def _external_charge(charge_id):
    """Платёж внешнего способа (payments_ext.fulfil: «<provider>:<order_id>» — Tribute, Heleket, Lava…), а не звёзды Telegram."""
    from pay_providers import PROVIDERS
    return str(charge_id or "").split(":", 1)[0] in PROVIDERS and ":" in str(charge_id or "")


async def retry_failed_payments(bot):
    """Раз в несколько минут повторяет начисление по платежам, где деньги получены, а доступ не открыт (ТЗ §19). Успех — человеку и владельцу."""
    from db import shop
    rows = shop.failed_payments(limit=20, max_attempts=MAX_ATTEMPTS)
    done = 0
    for row in rows:
        charge_id, user_id, payload = row["charge_id"], row["user_id"], str(row["payload"] or "")
        if not payload.startswith("subscription:"):
            continue                                              # остальные покупки (рамки, пакеты) разбирает владелец вручную по сообщению о сбое
        if shop.begin_payment(charge_id, user_id, payload, row["amount"]) == "duplicate":
            continue
        try:
            text, snap, link = await complete_subscription_payment(
                bot, user_id, paid_at=_parse(row["purchased_at"]), charge_id=charge_id, recurring=bool(row["is_recurring"]), expires_at=_parse(row["expires_at"]),
                calendar_months=_external_charge(charge_id))   # карта/крипта — календарные месяцы, как при первой попытке (payments_ext.py)
            shop.finish_payment(charge_id, True)
        except Exception as exc:
            logger.exception("Повтор начисления не удался: %s", charge_id)
            shop.finish_payment(charge_id, False, error=f"{type(exc).__name__}: {exc}")
            if int(row["attempts"] or 0) + 1 >= MAX_ATTEMPTS:
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, f"🛑 Начисление по платежу <code>{html.escape(str(charge_id))}</code> (id {user_id}) не удалось {MAX_ATTEMPTS} раз — нужна ручная выдача доступа.",
                                               parse_mode="HTML")
                    except Exception:
                        pass
            continue
        done += 1
        try:
            await bot.send_message(user_id, text)
        except Exception:
            logger.warning("Не удалось сообщить человеку %s, что доступ открыт", user_id)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"✅ Начисление по платежу <code>{html.escape(str(charge_id))}</code> (id {user_id}) повторено — доступ открыт.", parse_mode="HTML")
            except Exception:
                pass
    return done


def _parse(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return _naive_utc(moment)


# ---------------------------------------------------------------------------------------------------------------- возврат

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
    from db.shop import is_credited
    credited = is_credited(charge_id) or row["status"] == "done"   # старые платежи (до учёта credited_at) — по статусу
    mark_payment_refunded(charge_id)
    # Прогон 09.11: платёж, начисление по которому так и не прошло, срок не давал — его возврат срок не снимает (иначе пропадёт чужой оплаченный месяц).
    if str(row["payload"] or "").startswith("subscription:") and credited:
        from db.shop import list_user_payments
        from db.subscription import set_auto_renew, take_back_one_period
        others = [p for p in list_user_payments(row["user_id"], limit=200)
                  if p["charge_id"] != charge_id and not p["refunded_at"] and str(p["payload"] or "").startswith("subscription:") and p["status"] == "done"]
        if others:
            take_back_one_period(row["user_id"])                   # прогон 07.11 (P3): есть другие оплаченные месяцы — снимаем один период, не весь срок
        else:
            revoke_subscription(row["user_id"])                    # единственный платёж — снимаем срок целиком, как раньше
        user = get_user(row["user_id"])
        if user is not None and user["subscription_charge_id"] == charge_id:
            set_auto_renew(row["user_id"], False)                  # возврат подписки Stars — дальше не списываем
    return {"ok": True, "user_id": row["user_id"], "amount": row["amount"], "payload": row["payload"]}
