"""
План дня в боте (план, волна 1): перенос незакрытого на завтра из вечерней сверки 19:00 (coach.run_day_progress_check).

Пока у админов и по флагу plan_carry_over (админка → флаги). Сам перенос — db.daily_plan.carry_over_unfinished: копии незакрытого в
завтрашний план, сегодняшний не меняется, повтор не дублирует.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_IDS

router = Router()

CARRY_FLAG = "plan_carry_over"
CARRY_BUTTON = "➡️ Перенести незакрытое на завтра"


def carry_enabled(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        from db.feature_flags import is_feature_enabled
        return is_feature_enabled(CARRY_FLAG, user_id)
    except Exception:
        return False


def carry_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=CARRY_BUTTON, callback_data="plan_carry")]])


def carry_result_text(result):
    if not result["moved"]:
        return "Переносить нечего: незакрытое уже в плане на завтра (или всё закрыто) 👌"
    lines = [f"➡️ Перенёс на завтра: {result['moved']}."]
    if result["main"]:
        lines.append("Незакрытая главная задача стала главной на завтра.")
    if result["skipped"]:
        lines.append(f"Не влезло (в плане максимум 9 задач): {result['skipped']}.")
    lines.append("Утром начни с них — без раскачки.")
    return "\n".join(lines)


@router.callback_query(F.data == "plan_carry")
async def plan_carry(callback: CallbackQuery):
    from db.daily_plan import carry_over_unfinished
    from keyboards import open_plan_markup
    result = carry_over_unfinished(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(carry_result_text(result), reply_markup=open_plan_markup())
    await callback.answer()
