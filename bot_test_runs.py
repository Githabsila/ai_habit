"""
Прогон сценария (просьба владельца 11.11: «вручную весь путь пользователя в триале в первые 3 дня при разных сценариях перепроверить»).

Админ выбирает сценарий — «Триал, день 1: ничего не сделал», «День 3: последний», «Пропустил вчера»… — и бот присылает ЕМУ
весь день такого человека по порядку: каждое сообщение с пометкой «🧪 12:00 · Точка дня», тем же текстом и с теми же кнопками, что
и в настоящей рассылке (тексты собирают bot_catalog и trial_messages — тот же код, что у рассылок). Сообщения триала собираются на
снимке самого админа (имя, серия, цены), в котором подменены только день и фаза триала.

Безопасно для прода: в базу ничего не пишется, никому кроме самого админа ничего не уходит, настоящие рассылки не трогаются.
Ждать 3 дня не нужно — день целиком приходит за минуту.
"""
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PAUSE_SECONDS = 0.6           # тесты подменяют на 0


@dataclass(frozen=True)
class Step:
    at: str              # «12:00» — местное время человека
    kind: str            # "bot" (bot_catalog) | "trial" (trial_messages)
    key: str
    trial: tuple = ()    # для "trial": (фаза, день)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    about: str
    steps: tuple


SCENARIOS = (
    Scenario("d1_partial", "Триал, день 1: отмечает не всё", "Новичок завёл привычки, часть закрыл днём, часть забыл.", (
        Step("06:00", "bot", "morning"), Step("12:00", "bot", "checkpoint_12"), Step("17:00", "bot", "checkpoint_17"),
        Step("19:00", "bot", "evening_plan"), Step("22:00", "bot", "checkpoint_22"))),
    Scenario("d1_nothing", "Триал, день 1: ничего не отметил", "Завёл привычки и пропал до вечера. Серия под угрозой.", (
        Step("06:00", "bot", "morning"), Step("12:00", "bot", "checkpoint_12"), Step("17:00", "bot", "checkpoint_17"),
        Step("19:00", "bot", "evening_plan"), Step("23:00", "bot", "risk23"), Step("23:30", "bot", "timer"),
        Step("00:00", "bot", "timer_expired"))),
    Scenario("d2_absent", "Триал, день 2: не заходил", "Второй день, в приложение ещё не заходил.", (
        Step("06:00", "bot", "morning"), Step("12:00", "bot", "checkpoint_12"), Step("12–17", "trial", "d2_invite", ("day2", 2)),
        Step("17:00", "bot", "checkpoint_17"), Step("22:00", "bot", "checkpoint_22"))),
    Scenario("d3_last", "Триал, день 3: последний день", "Последний день пробного доступа, ещё не оплатил.", (
        Step("06:00", "bot", "morning"), Step("11–14", "trial", "d3_talk", ("day3", 3)), Step("12:00", "bot", "checkpoint_12"),
        Step("17:00", "bot", "checkpoint_17"), Step("18–21", "trial", "d3_end", ("day3", 3)), Step("22:00", "bot", "checkpoint_22"))),
    Scenario("grace", "После триала: окно 24 часа", "Пробный доступ кончился, не оплатил: сутки, чтобы решить.", (
        Step("9–21", "trial", "grace_notice", ("grace", 4)),)),
    Scenario("winback", "После триала: не вернулся", "Окно прошло. Редкие сообщения, паузы между ними растут.", (
        Step("11–19", "trial", "wb_question", ("expired", 5)), Step("через N дней", "trial", "wb_insight", ("expired", 8)),
        Step("ещё позже", "trial", "wb_continue", ("expired", 12)))),
    Scenario("missed", "Пропустил вчера", "Вчера ни одной отметки — зовём вернуться в серию.", (
        Step("10:00", "bot", "reengage_10"), Step("16:00", "bot", "reengage_16"))),
    Scenario("long_gone", "Не заходил больше недели", "Долгий перерыв.", (Step("18:00", "bot", "reengage_long"),)),
    Scenario("renewal", "Подписка кончается", "Оплаченный месяц подходит к концу.", (
        Step("за 7 дней, 12:00", "bot", "renew_7"), Step("за 1 день, 12:00", "bot", "renew_1"), Step("в последний день, 12:00", "bot", "renew_0"),
        Step("с автопродлением, за 3 дня", "bot", "renew_auto"), Step("автосписание не прошло", "bot", "renew_failed"))),
    Scenario("channel", "Вылет из закрытого канала", "Серия погасла или кончилась подписка.", (
        Step("сразу", "bot", "channel_streak"), Step("сразу", "bot", "channel_subscription"))),
)
BY_KEY = {s.key: s for s in SCENARIOS}


def _title(step):
    if step.kind == "trial":
        import trial_messages
        return trial_messages.BY_KEY[step.key].label
    import bot_catalog
    return bot_catalog.BY_KEY[step.key].title


def render_step(step, user_id):
    """(текст, клавиатура) шага — тем же кодом, что и настоящая рассылка."""
    if step.kind == "trial":
        from db import trial as trial_db
        from subscription_scheduler import build_message
        snap = trial_db.snapshot(user_id)
        phase, day = step.trial
        snap = dict(snap, phase=phase, day=day, paid=False, wb={"sent": 0, "allowed": True})
        return build_message(step.key, snap)
    import bot_catalog
    return bot_catalog.render(step.key, user_id)


def describe():
    return [{"key": s.key, "title": s.title, "about": s.about, "steps": [{"at": st.at, "title": _title(st)} for st in s.steps]} for s in SCENARIOS]


async def run(bot, admin_id, key):
    """Присылает админу весь сценарий. Возвращает {"sent", "failed": [ключи с ошибкой]}."""
    scenario = BY_KEY[key]
    await bot.send_message(admin_id, f"🧪 <b>Прогон: {scenario.title}</b>\n{scenario.about}\n\nДальше — сообщения дня по порядку. Видишь только ты.",
                           parse_mode="HTML")
    sent, failed = 0, []
    for step in scenario.steps:
        await asyncio.sleep(PAUSE_SECONDS)
        try:
            text, markup = render_step(step, admin_id)
            await bot.send_message(admin_id, f"🧪 <b>{step.at}</b> · {_title(step)}\n\n{text}", parse_mode="HTML", reply_markup=markup)
            sent += 1
        except Exception as exc:
            logger.exception("Прогон %s: шаг %s не собрался", key, step.key)
            failed.append(step.key)
            try:
                await bot.send_message(admin_id, f"⚠️ {step.at} · {step.key}: не собралось ({type(exc).__name__})", parse_mode=None)
            except Exception:
                pass
    return {"sent": sent, "failed": failed}
