"""
Каталог сообщений бота для админки (просьба владельца 05.11: «каждое окно и сообщение можно открыть и проверить с любого телефона»).

Здесь — сообщения, которые бот шлёт САМ (напоминания, таймер, возврат в серию, канал, ответы по умолчанию). Каждое собирается тем же
кодом, что и настоящая рассылка, на данных того, кто смотрит (его привычки, род для «сделал/сделала»); привычек нет — подставляем примерные.
В админке: текст прямо на странице и «Прислать мне» — сообщение приходит в Telegram только самому админу, в базу ничего не пишется.
Сообщения триала — в своём каталоге (админка → «Триал»), окна приложения — в «Окна и анимации».

Новое сообщение бота добавляется сюда (тест следит, что каждое собирается без ошибок).
"""
import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

DEMO_HABITS = [{"title": "Пить воду"}, {"title": "Читать 20 минут"}]


@dataclass(frozen=True)
class CatalogItem:
    key: str
    group: str
    title: str
    when: str
    render: Callable  # (user_id) -> (text, reply_markup или None)


def _habits(user_id):
    try:
        from db import get_incomplete_habits
        rows = get_incomplete_habits(user_id)
        return [{"title": r["title"]} for r in rows] or DEMO_HABITS
    except Exception:
        return DEMO_HABITS


def _morning(user_id):
    from keyboards import adam_chat_markup
    from morning_ping import FALLBACK_TEXT
    return FALLBACK_TEXT, adam_chat_markup()


def _checkpoint(hour, completed=0):
    """completed — сколько уже закрыто: в 22:00 точка приходит только если что-то отмечено (иначе её заменяют 23:00 и 23:30)."""
    def render(user_id):
        from adam_messages import compose_checkpoint_message
        habits = _habits(user_id)
        text, _picks = compose_checkpoint_message(habits, hour, completed=completed, total=len(habits) + completed)
        return text, None
    return render


def _checkpoint_simple(user_id):
    from adam_messages import format_simple_habit_checkpoint_message
    return format_simple_habit_checkpoint_message(_habits(user_id)), None


def _risk23(user_id):
    from streak_scheduler import RISK_23_FIRST, _countdown_keyboard
    return RISK_23_FIRST[0], _countdown_keyboard()


def _timer(minutes, seconds):
    def render(user_id):
        from streak_scheduler import _countdown_keyboard, countdown_text
        return countdown_text(minutes, seconds), _countdown_keyboard()
    return render


def _timer_saved(user_id):
    from streak_scheduler import COUNTDOWN_SAVED_TEXT
    return COUNTDOWN_SAVED_TEXT, None


def _timer_expired(user_id):
    from streak_scheduler import COUNTDOWN_EXPIRED_TEXT
    return COUNTDOWN_EXPIRED_TEXT, None


def _reengage(slot, days):
    def render(user_id):
        from streak_scheduler import _choose_reengagement_message
        _key, text = _choose_reengagement_message(user_id, slot, days)
        return text, None
    return render


def _channel_removed(reason):
    def render(user_id):
        from channel_sync import REMOVED_TEXT
        from config import CHANNEL_REENTRY_STREAK_DAYS
        return REMOVED_TEXT[reason].format(days=CHANNEL_REENTRY_STREAK_DAYS), None
    return render


def _fallback(user_id):
    from handlers.fallback import FALLBACK_TEXT
    from keyboards import adam_chat_markup
    return FALLBACK_TEXT, adam_chat_markup()


def _evening_plan(user_id):
    from adam_messages import format_day_progress_message
    from handlers.plan_bot import carry_keyboard
    text = format_day_progress_message(1, 3, ["главная: «Сдать отчёт»", "второстепенная: «Позвонить маме»"])
    return text, carry_keyboard()


def _carry_result(user_id):
    from handlers.plan_bot import carry_result_text
    return carry_result_text({"moved": 2, "skipped": 0, "main": True}), None


def _renew_manual(days):
    def render(user_id):
        import subscription_renewal as sr
        from config import SUBSCRIPTION_AUTORENEW_ENABLED, SUBSCRIPTION_RENEWAL_PRICE_STARS
        return (sr.manual_text(days, "5 декабря, 14:00", SUBSCRIPTION_AUTORENEW_ENABLED),
                sr.renewal_keyboard(SUBSCRIPTION_AUTORENEW_ENABLED, SUBSCRIPTION_RENEWAL_PRICE_STARS))
    return render


def _renew_auto(user_id):
    import subscription_renewal as sr
    from config import SUBSCRIPTION_RENEWAL_PRICE_STARS
    return sr.auto_notice_text("5 декабря, 14:00", SUBSCRIPTION_RENEWAL_PRICE_STARS), None


def _renew_failed(user_id):
    import subscription_renewal as sr
    from config import SUBSCRIPTION_RENEWAL_PRICE_STARS
    return sr.failed_text(SUBSCRIPTION_RENEWAL_PRICE_STARS), sr.renewal_keyboard(False, SUBSCRIPTION_RENEWAL_PRICE_STARS)


def _capture(user_id):
    from handlers.fallback import capture_keyboard, capture_question
    return capture_question("Купить подарок маме"), capture_keyboard()


def _age(user_id):
    from db.age_gate import AGE_RESTRICTED_TEXT
    return AGE_RESTRICTED_TEXT, None


CATALOG = (
    CatalogItem("morning", "Напоминания по привычкам", "Доброе утро", "06:00 · в жизни текст пишет ИИ, здесь — запасной", _morning),
    CatalogItem("checkpoint_12", "Напоминания по привычкам", "Точка дня 12:00", "12:00 · если остались незакрытые привычки", _checkpoint(12)),
    CatalogItem("checkpoint_17", "Напоминания по привычкам", "Точка дня 17:00", "17:00 · если остались незакрытые привычки", _checkpoint(17)),
    CatalogItem("checkpoint_22", "Напоминания по привычкам", "Точка дня 22:00", "22:00 · только если за день что-то уже отмечено", _checkpoint(22, completed=1)),
    CatalogItem("checkpoint_simple", "Напоминания по привычкам", "Точка дня, упрощённая", "Вместо полной — если в настройках «Упрощённая»", _checkpoint_simple),
    CatalogItem("risk23", "Ударный режим", "Последний час", "23:00 · если за день ничего не отмечено", _risk23),
    CatalogItem("timer", "Ударный режим", "Финишный таймер", "23:30 · одно сообщение, бот правит его до полуночи", _timer(29, 30)),
    CatalogItem("timer_saved", "Ударный режим", "Таймер: серия спасена", "Если привычку отметили до полуночи", _timer_saved),
    CatalogItem("timer_expired", "Ударный режим", "Таймер: время вышло", "В полночь, если так и не отметили", _timer_expired),
    CatalogItem("reengage_10", "Возврат в серию", "Вернись в серию, 10:00", "1–3 дня без отметок", _reengage("10", 2)),
    CatalogItem("reengage_16", "Возврат в серию", "Вернись в серию, 16:00", "1–3 дня без отметок", _reengage("16", 2)),
    CatalogItem("reengage_long", "Возврат в серию", "Вернись в серию, долгий перерыв", "4+ дней без отметок", _reengage("long", 8)),
    CatalogItem("renew_7", "Подписка", "Конец срока: за 7 дней", "12:00 · без автопродления", _renew_manual(7)),
    CatalogItem("renew_1", "Подписка", "Конец срока: завтра", "12:00 · без автопродления (также за 3 дня)", _renew_manual(1)),
    CatalogItem("renew_0", "Подписка", "Конец срока: сегодня", "12:00 · без автопродления", _renew_manual(0)),
    CatalogItem("renew_auto", "Подписка", "Автопродление: предупреждение", "12:00 · за 3 дня до списания", _renew_auto),
    CatalogItem("renew_failed", "Подписка", "Автопродление не прошло", "Через 3 часа после конца срока, если списания нет", _renew_failed),
    CatalogItem("channel_streak", "Закрытый канал", "Вылет: серия погасла", "Когда канал подключён", _channel_removed("streak")),
    CatalogItem("channel_subscription", "Закрытый канал", "Вылет: подписка закончилась", "Когда канал подключён", _channel_removed("subscription")),
    CatalogItem("fallback", "Ответы бота", "Ответ на обычный текст", "Человек написал боту что-то вне режимов", _fallback),
    CatalogItem("evening_plan", "План дня", "Вечерняя сверка плана", "19:00 · если в плане есть незакрытое; кнопка переноса — у админов и по флагу plan_carry_over", _evening_plan),
    CatalogItem("carry_result", "План дня", "Итог переноса на завтра", "После кнопки «Перенести незакрытое на завтра»", _carry_result),
    CatalogItem("capture", "Ответы бота", "Задача из чата: «Добавить в план?»", "Человек написал боту текст (админы и флаг bot_quick_capture)", _capture),
    CatalogItem("age", "Ответы бота", "Сервис для 16+", "Ответил в анкете «Мне нет 16»", _age),
)
BY_KEY = {item.key: item for item in CATALOG}


def render(key, user_id):
    """(текст, клавиатура) или исключение — админка покажет ошибку прямо в карточке сообщения."""
    return BY_KEY[key].render(user_id)


def catalog_for(user_id):
    out = []
    for item in CATALOG:
        try:
            text, markup = item.render(user_id)
            buttons = [b.text for row in (markup.inline_keyboard if markup else []) for b in row]
            out.append({"key": item.key, "group": item.group, "title": item.title, "when": item.when, "text": text, "buttons": buttons})
        except Exception as exc:
            logger.exception("Каталог сообщений: не собралось %s", item.key)
            out.append({"key": item.key, "group": item.group, "title": item.title, "when": item.when, "error": f"{type(exc).__name__}: {exc}"})
    return out
