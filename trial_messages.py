"""
Сообщения триала ADAM — каталог по ТЗ владельца (28.10.2026 «Триал, подписка и приглашения друзей», §12–§18) и их показ. Пишет Адам от первого лица (правило владельца
21.10), на «ты», без рода у человека (линтер тестов tests/test_adam_texts.py), без слов «бот» и «система». Цены, сроки и условия в текстах не придумываются: их показывает
экран подписки из правил приложения (кнопка «Условия подписки»), а подробные личные детали остаются внутри приложения — на экран блокировки они не выходят.

Что в каталоге (фаза → время по местным часам человека):
  день 1       — ничего: знакомство с приложением, полезные действия и кнопка подписки (§12)
  день 2       d2_invite    12–17   одно внешнее приглашение, только если человек сегодня не заходил и предложения в приложении не было (§12)
  день 3       d3_talk      11–14   приглашение к разговору с Адамом: вопрос по данным человека (§13, §15)
               d3_end       18–21   прямо: сегодня пробный доступ заканчивается, история сохранится (§13)
  окно 24 ч    grace_notice 9–21    одно сообщение: что закрыто, что сохранено, до какого времени серия под защитой (§18)
  после окна   wb_question / wb_insight / wb_continue   11–19   редкие контекстные сообщения (db/trial_flow.py::winback_state): пауза растёт, при отсутствии реакции серия
                                      замедляется и останавливается, от неё можно отказаться; после оплаты не приходят (§18)
Сообщение после оплаты (paid_welcome_text) шлёт обработчик платежа — оно зависит от дня и от отметок вчера/сегодня.

Показ: `due_messages(snap, ctx)` — что подходит человеку СЕЙЧАС (фаза, часы, условие и ворота по журналу предложений); включены ли сообщения и не отправлено ли оно уже —
решает доставка (subscription_scheduler.py: тумблеры из админки, лимит на каждый вид, тихие часы, claim_notification).
"""
import html
from dataclasses import dataclass
from typing import Callable

from adam_messages import streak_phrase


def _name(snap):
    return html.escape(snap.get("name") or "")


def _say(snap, sentence):
    """«Аня, сегодня второй день…» или, если имени нет, «Сегодня второй день…» — предложение дано со строчной буквы."""
    name = _name(snap)
    return f"{name}, {sentence}" if name else sentence[:1].upper() + sentence[1:]


# ---------------------------------------------------------------------------------------------------------------- тексты

def _d2_invite(snap):
    if snap["marked_yesterday"]:
        return _say(snap, "вчера у тебя была отметка привычки — хороший старт. Загляни сегодня: продолжим с неё и посмотрим, что помогает держать ритм.")
    return _say(snap, "первый день позади. Загляни сегодня: выберем одно посильное дело и посмотрим, как оно пойдёт.")


def _d3_talk(snap):
    return _say(snap, "у меня есть вопрос по твоим дням в приложении: хочу понять, что помогает тебе держать ритм. Ответь, когда зайдёшь, — это пара минут.")


def _d3_end(snap):
    when = snap.get("ends_at_text") or "в полночь"
    return _say(snap, f"сегодня пробный доступ заканчивается: {when}. Твоя история сохранится. Если хочешь продолжить пользоваться привычками, планом и разговором со мной, "
                      "условия подписки собраны на одном экране.")


def _grace_notice(snap):
    return locked_text(snap)


def _wb(kind):
    def text(snap):
        from db import trial_flow
        return trial_flow.winback_text(snap["user_id"], trial_flow._facts(snap["user_id"]), (snap.get("wb") or {}).get("sent", 0), kind)
    return text


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


def renewal_line(snap, auto_renew):
    """Как продлевается подписка — честно, по тому, как она реально оформлена (ТЗ §2, §19): автопродление Stars или продление вручную."""
    if auto_renew:
        return (f"Продление автоматическое: каждые 30 дней спишется {snap.get('renewal_price_stars')} ⭐ (≈{snap.get('renewal_usd')}). "
                "Отключить можно в приложении (раздел «Подписка») или в настройках Telegram.")
    return "Продление вручную: когда срок подойдёт к концу, я напомню — само ничего не спишется."


def paid_welcome_text(snap, until, link=None, auto_renew=False):
    """Подтверждение сразу после оплаты (ТЗ §19): точный срок доступа, что стало с бесплатными днями и серией, как продлевается подписка.
    until — когда подписка закончится (datetime или готовая строка)."""
    until_str = until.strftime("%d.%m.%Y") if hasattr(until, "strftime") else str(until)
    lines = [f"✅ Подписка активна до {until_str}. Спасибо, что продолжаешь со мной!"]
    if snap.get("early"):
        lines.append("Оставшиеся бесплатные дни пробного периода добавлены к сроку: ничего не потеряно.")
    protection = snap.get("protection") or {}
    if protection.get("applied") and protection.get("restored"):
        lines.append(f"Серия сохранена: {streak_phrase(protection['restored'])} подряд снова с тобой.")
    lines.append(renewal_line(snap, auto_renew))
    if link:
        lines.append(channel_link_text(snap, link))
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------- каталог

@dataclass(frozen=True)
class TrialMessage:
    key: str
    label: str                                        # название в админке
    when: str                                         # «День 3 · 18–21» — когда подходит
    phase: str                                        # day2 | day3 | grace | expired
    hours: tuple                                      # окно местных часов (с, до)
    text: Callable = None                             # snap -> текст
    condition: Callable = lambda snap: True           # условие по снимку человека
    gate: Callable = None                             # (snap, ctx) -> bool: ворота по журналу предложений, заходам и паузам (db/trial_flow.py::context)
    buttons: tuple = ()                               # "sub" — условия подписки, "talk" — разговор с Адамом, "open" — открыть приложение, "optout" — отказаться от серии, "buy" — оплатить
    service: bool = False                             # сообщение о настоящем изменении доступа: приходит и тому, кто выключил напоминания (но не в тихие часы)
    series: bool = False                              # редкая серия: частота и паузы считает trial_flow.winback_state, а не «один раз»
    action: str = ""                                  # зарезервировано: текст строит доставка


CATALOG = (
    TrialMessage("d2_invite", "День 2 · приглашение, если сегодня не заходил", "День 2 · 12–17, один раз; не уходит, если человек уже заходил или получил предложение в приложении",
                 "day2", (12, 17), _d2_invite, gate=lambda s, c: not c["opened_today"] and not c["sale_shown"] and not c["dismissed"], buttons=("open", "sub")),
    TrialMessage("d3_talk", "День 3 · приглашение к разговору с Адамом", "День 3 · 11–14, один раз; без продажи",
                 "day3", (11, 14), _d3_talk, gate=lambda s, c: c["chats_today"] < 2, buttons=("talk",)),
    TrialMessage("d3_end", "День 3 · прямо про конец пробного доступа", "День 3 · 18–21, один раз; после оплаты не приходит",
                 "day3", (18, 21), _d3_end, buttons=("sub",), service=True),
    TrialMessage("grace_notice", "Окно 24 часа · что закрыто и что сохранено", "Окно после триала · 9–21, один раз",
                 "grace", (9, 21), _grace_notice, buttons=("sub",), service=True),
    TrialMessage("wb_question", "После триала · полезный вопрос", "После окна · 11–19, редко (паузы растут, без реакции — замедляется)",
                 "expired", (11, 19), _wb("question"), gate=lambda s, c: c["winback"]["allowed"] and c.get("wb_kind") == "question", buttons=("sub", "optout"), series=True),
    TrialMessage("wb_insight", "После триала · вывод по прошлым действиям", "После окна · 11–19, редко",
                 "expired", (11, 19), _wb("insight"), gate=lambda s, c: c["winback"]["allowed"] and c.get("wb_kind") == "insight", buttons=("sub", "optout"), series=True),
    TrialMessage("wb_continue", "После триала · продолжить начатое", "После окна · 11–19, редко",
                 "expired", (11, 19), _wb("continue"), gate=lambda s, c: c["winback"]["allowed"] and c.get("wb_kind") == "continue", buttons=("sub", "optout"), series=True),
)
BY_KEY = {m.key: m for m in CATALOG}


def due_messages(snap, ctx=None):
    """Сообщения, подходящие человеку сейчас: сценарий триала идёт, фаза и час совпали, условие и ворота выполнены."""
    if not snap or not snap.get("scenario"):
        return []
    if ctx is None:
        from db import trial_flow
        ctx = trial_flow.context(snap["user_id"], snap)
    snap = dict(snap, wb=ctx.get("winback") or {})
    hour = snap["local_hour"]
    return [m for m in CATALOG if m.phase == snap["phase"] and m.hours[0] <= hour < m.hours[1] and m.condition(snap) and (m.gate is None or m.gate(snap, ctx))]


def render(key, snap, link=None):
    """Текст сообщения по ключу для данного состояния человека (админка: «отправить мне»)."""
    return BY_KEY[key].text(snap)
