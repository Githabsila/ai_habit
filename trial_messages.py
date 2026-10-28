"""
Сообщения триала ADAM — каталог по ТЗ владельца (30.08 п.13 и 12.10) и их показ. Пишет Адам от первого лица (правило владельца 21.10),
на «ты», без рода у человека (линтер тестов tests/test_adam_texts.py), без слов «бот» и «система».

Что в каталоге (фаза → время по местным часам человека):
  день 1       d1_goal      18–21   цель: два дня подряд → возможность попасть в закрытый канал
  день 2       d2_goal      12–15   про деньги ни слова; цель — два дня подряд (если сегодня ещё нет отметки)
               d2_offer     9–23    после первой отметки за день — особое предложение 1.99$ (153 ⭐)
  день 3       d3_midday    12–15   последний день триала, завтра доступ закроется, скидка
               d3_evening   19–22   то же вечером — «Или все твои мысли и первые действия…»
  день 4       d4_midday    12–15   триал закончился, СЕГОДНЯ последний день скидки, завтра полная цена
               d4_evening   19–22   последний вечер скидки
  день 5       d5_full      12–15   доступ закрыт, вернуться можно за полную цену (дальше — тишина, повторного триала нет)
  оплачено     paid_nudge   12–15   до закрытого канала осталось N дней подряд
               channel_link 8–23    серия набрана → ссылка в закрытый канал (нужен CLOSED_CHANNEL_ID)
Сообщение после оплаты (paid_welcome_text) шлёт обработчик платежа — оно зависит от дня и от отметок вчера/сегодня.

Показ: `due_messages(snap)` — что подходит человеку СЕЙЧАС (фаза, часы, условие); включены ли сообщения и не отправлено ли оно уже — решает
доставка (subscription_scheduler.py: тумблеры из админки, дневной лимит, тихие часы, claim_notification).
"""
import html
from dataclasses import dataclass
from typing import Callable

from adam_messages import day_word, streak_phrase


def _name(snap):
    return html.escape(snap.get("name") or "")


def _say(snap, sentence):
    """«Аня, сегодня второй день…» или, если имени нет, «Сегодня второй день…» — предложение дано со строчной буквы."""
    name = _name(snap)
    return f"{name}, {sentence}" if name else sentence[:1].upper() + sentence[1:]


def _price(snap):
    usd = snap["intro_usd"] if snap["intro_active"] else snap["renewal_usd"]
    return f"{snap['price_stars']} ⭐ (≈{usd})"


def _needed_phrase(snap):
    return streak_phrase(snap["needed"])


# ---------------------------------------------------------------------------------------------------------------- тексты

def _d1_goal(snap):
    if snap["marked_today"]:
        return _say(snap, "первый день закрыт — отличное начало 👏 Завтра отметь привычку ещё раз: два дня подряд дают возможность попасть в закрытый канал 🔑")
    return _say(snap, "первый день ещё не закончился — одной привычки хватит, чтобы серия пошла. Моя цель для тебя на ближайшие два дня: "
                      "ударный режим подряд, он открывает возможность попасть в закрытый канал 🔑")


def _d2_goal(snap):
    if snap["marked_yesterday"]:
        return _say(snap, "сегодня второй день. Отметь привычку — и два дня подряд будут у тебя в кармане: это даёт возможность попасть в закрытый канал 🔑")
    return _say(snap, "сегодня второй день. Твоя цель сейчас — два дня ударного режима подряд, они дают возможность попасть в закрытый канал 🔑 "
                      "Начни с одной привычки сегодня.")


def _channel_line_before_payment(snap):
    if snap["channel_eligible"]:
        return "Два дня подряд у тебя уже есть — после оплаты сразу пришлю ссылку в закрытый канал 🔑"
    if snap["marked_today"]:
        return "Закрытый канал откроется, когда наберётся " + _needed_phrase(snap) + " подряд — завтра достаточно отметить привычку 🔑"
    if snap["marked_yesterday"]:
        return "Отметь привычку сегодня — и после оплаты закрытый канал откроется сразу 🔑"
    return f"Закрытый канал открывается с {_needed_phrase(snap)} ударного режима подряд: отметь привычки сегодня и завтра 🔑"


def _d2_offer(snap):
    return (_say(snap, f"отличная отметка! Пока ты в ритме — особое предложение: Pro на первый месяц всего за {_price(snap)}, "
                       f"дальше {snap['renewal_usd']}.") + " " + _channel_line_before_payment(snap))


def _d3_midday(snap):
    return (_say(snap, f"сегодня последний день пробного периода — завтра доступ ко мне закроется. Особая цена только сейчас: "
                       f"{_price(snap)} за первый месяц, дальше {snap['renewal_usd']}.") + " " + _channel_line_before_payment(snap))


def _d3_evening(snap):
    return _say(snap, "последний вечер пробного периода — завтра доступ закроется. Или все твои мысли и первые действия о дисциплине и привычках "
                      f"были лишь слова? Особая цена ещё со мной: {_price(snap)} за первый месяц.")


def _streak_line_after_trial(snap):
    if snap["channel_eligible"]:
        return f"Твой ударный режим — {streak_phrase(snap['streak'])} подряд — сохранился: не дай ему пропасть."
    return "Ударный режим пока не набран — он начнётся в день, когда ты вернёшься."


def _d4_midday(snap):
    return (f"Пробный период закончился, доступ закрыт. {_streak_line_after_trial(snap)} Сегодня последний день, когда месяц стоит {_price(snap)}; "
            f"завтра — {snap['renewal_price_stars']} ⭐ ({snap['renewal_usd']}).")


def _d4_evening(snap):
    return (f"Последний вечер скидки: {snap['price_stars']} ⭐ вместо {snap['renewal_price_stars']} ⭐. "
            "Завтра цена станет полной, а повторного триала не будет.")


def _d5_full(snap):
    return (f"Доступ пока закрыт. Вернуться ко мне можно за {snap['renewal_price_stars']} ⭐ в месяц (≈{snap['renewal_usd']}): "
            "твои привычки и серия ждут тебя.")


def _paid_nudge(snap):
    if snap["marked_today"]:
        return "Подписка активна 👍 Сегодня уже отмечено — завтра отметь привычку ещё раз, и ссылка в закрытый канал придёт сама 🔑"
    left = max(1, snap["needed"] - snap["streak"])
    return (f"Подписка активна 👍 До закрытого канала осталось {left} {day_word(left)} ударного режима подряд — "
            "отметь привычку сегодня, и ссылка придёт сама 🔑")


def _price_line(snap):
    """Цена для окна оплаты: первая покупка — по вводной цене в любой день (ТЗ §6), дальше полная; без «только сегодня»."""
    if snap["intro_active"]:
        return (f"Первый месяц — <b>{snap['price_stars']} ⭐</b> (≈{snap['intro_usd']}), дальше {snap['renewal_price_stars']} ⭐ (≈{snap['renewal_usd']}).")
    return f"Месяц — <b>{snap['renewal_price_stars']} ⭐</b> (≈{snap['renewal_usd']})."


def locked_text(snap):
    """Что человек видит, когда основные действия закрыты (бот при любом действии; то же говорит экран в приложении, ТЗ §7 и §18).
    Дополнительное окно (grace) — конкретный срок защиты серии; после него (expired) — просто как вернуться. История и данные сохранены в обоих случаях."""
    if snap.get("phase") == "grace":
        until = snap.get("ends_at_text") or ""
        streak = (f"Заработанная серия — {streak_phrase(snap['streak'])} подряд — сохранится, если продолжить до {until}."
                  if snap["streak"] > 0 else f"Продолжить можно до {until}: серия начнётся в день, когда ты вернёшься.")
        return (f"⏳ <b>Пробный доступ завершён.</b>\n\nТвоя история сохранена. {streak} "
                f"Подписка снова откроет привычки, планирование и разговор со мной.\n\n{_price_line(snap)}")
    return ("🔒 <b>Доступ пока закрыт.</b>\n\nТвоя история сохранена. Подписка снова откроет привычки, планирование и разговор со мной.\n\n"
            f"{_price_line(snap)}")


def channel_link_text(snap, link):
    return f"🔑 {streak_phrase(snap['streak'])} ударного режима подряд — вот ссылка в закрытый канал: {html.escape(link)}"


def paid_welcome_text(snap, until_str, link=None):
    """Сообщение сразу после оплаты. snap — состояние на момент оплаты (отметки вчера/сегодня, серия, день триала)."""
    head = f"✅ Доступ продлён до {until_str}. Спасибо, что остаёшься со мной!"
    if snap["channel_eligible"]:
        if link:
            return head + f"\n\n{channel_link_text(snap, link)}"
        if snap["channel_configured"]:
            return head + f"\n\n🔑 У тебя {streak_phrase(snap['streak'])} подряд — условие для закрытого канала выполнено. Ссылку пришлю чуть позже."
        return head + (f"\n\n🔑 У тебя {streak_phrase(snap['streak'])} подряд — условие для закрытого канала выполнено. "
                       "Как только канал откроется, пришлю ссылку.")
    needed = _needed_phrase(snap)
    if snap["marked_today"]:
        tail = f"Сегодня уже отмечено — завтра отметь привычку ещё раз, и ссылка в закрытый канал придёт сама 🔑 Канал открывается за {needed} подряд: оплаты мало, нужен ритм."
    elif snap["marked_yesterday"]:
        tail = f"Сначала отметь привычку сегодня — когда наберётся {needed} подряд, ссылка в закрытый канал придёт сама 🔑 Не теряй ритм!"
    elif snap["day"] == 2:
        tail = "Оплаты мало, чтобы попасть в закрытый канал, — нужен ритм. Осталось два дня: отметь хотя бы одну привычку сегодня и завтра 🔑"
    elif snap["day"] <= 1:
        tail = f"Закрытый канал открывается за {needed} ударного режима подряд: отметь привычку сегодня и завтра — и ссылка придёт сама 🔑"
    else:
        tail = f"Чтобы получить доступ в закрытый канал, нужно {needed} ударного режима подряд: отметь привычки сегодня и завтра 🔑"
    return f"{head}\n\n{tail}"


# ---------------------------------------------------------------------------------------------------------------- каталог

@dataclass(frozen=True)
class TrialMessage:
    key: str
    label: str                                        # название в админке
    when: str                                         # «День 3 · 19–22» — когда подходит
    phase: str                                        # day1 | day2 | day3 | expired_intro | expired_full | paid
    hours: tuple                                      # окно местных часов (с, до)
    text: Callable = None                             # snap -> текст
    condition: Callable = lambda snap: True           # дополнительное условие к фазе
    buttons: tuple = ()                               # "buy" — оплатить Stars, "open" — открыть приложение
    action: str = ""                                  # "channel_link" — текст строит доставка (нужна ссылка из Telegram)


CATALOG = (
    TrialMessage("d1_goal", "День 1 · цель на два дня подряд", "День 1 · 18–21", "day1", (18, 21), _d1_goal, buttons=("open",)),
    TrialMessage("d2_goal", "День 2 · цель, про деньги ни слова", "День 2 · 12–15, если сегодня ещё нет отметки", "day2", (12, 15), _d2_goal,
                 condition=lambda s: not s["marked_today"], buttons=("open",)),
    TrialMessage("d2_offer", "День 2 · предложение после отметки", "День 2 · 9–23, после первой отметки за день", "day2", (9, 23), _d2_offer,
                 condition=lambda s: s["marked_today"], buttons=("buy",)),
    TrialMessage("d3_midday", "День 3 · последний день триала", "День 3 · 12–15", "day3", (12, 15), _d3_midday, buttons=("buy",)),
    TrialMessage("d3_evening", "День 3 · последний вечер", "День 3 · 19–22", "day3", (19, 22), _d3_evening, buttons=("buy",)),
    TrialMessage("d4_midday", "День 4 · триал закончился, последний день скидки", "День 4 · 12–15", "grace", (12, 15), _d4_midday, buttons=("buy",)),
    TrialMessage("d4_evening", "День 4 · последний вечер скидки", "День 4 · 19–22", "grace", (19, 22), _d4_evening, buttons=("buy",)),
    TrialMessage("d5_full", "День 5 · доступ закрыт, полная цена", "День 5 · 12–15, один раз", "expired", (12, 15), _d5_full,
                 condition=lambda s: s["day"] == s["days_total"] + 2, buttons=("buy",)),
    TrialMessage("paid_nudge", "Оплачено · сколько осталось до канала", "Каждый день 12–15, пока нет ссылки", "paid", (12, 15), _paid_nudge,
                 condition=lambda s: s["channel_configured"] and not s["channel_granted"] and not s["channel_eligible"], buttons=("open",)),
    TrialMessage("channel_link", "Оплачено · ссылка в закрытый канал", "Когда набрано 2 дня подряд, 8–23", "paid", (8, 23), None,
                 condition=lambda s: s["channel_configured"] and not s["channel_granted"] and s["channel_eligible"], action="channel_link"),
)
BY_KEY = {m.key: m for m in CATALOG}


def due_messages(snap):
    """Сообщения, подходящие человеку сейчас: сценарий триала идёт, фаза и час совпали, условие выполнено."""
    if not snap or not snap.get("scenario"):
        return []
    hour = snap["local_hour"]
    return [m for m in CATALOG if m.phase == snap["phase"] and m.hours[0] <= hour < m.hours[1] and m.condition(snap)]


def render(key, snap, link=None):
    """Текст сообщения по ключу для данного состояния человека (админка: «отправить мне»)."""
    message = BY_KEY[key]
    if message.action == "channel_link":
        return channel_link_text(snap, link or "https://t.me/+ссылка-в-канал")
    return message.text(snap)
