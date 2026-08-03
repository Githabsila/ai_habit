from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import get_survey, get_milestones, toggle_milestone
from keyboards import milestones_keyboard, back_menu_keyboard

router = Router()


@router.callback_query(F.data == "milestones")
async def show_milestones(callback: CallbackQuery):

    user_id = callback.from_user.id
    survey = get_survey(user_id)
    milestones = get_milestones(user_id)

    if not survey or not milestones:
        await callback.message.edit_text(
            "🧭 Вехи появятся здесь после того, как вы пройдёте анкету и "
            "получите доступ — они подбираются автоматически под вашу цель.",
            reply_markup=back_menu_keyboard()
        )
        await callback.answer()
        return

    done_count = sum(1 for m in milestones if m["done"])

    text = (
        f"🧭 <b>Ваша цель</b>\n{survey['bot_goal']}\n\n"
        f"<b>Вехи</b> ({done_count}/{len(milestones)}):\n"
        "Нажмите на веху, чтобы отметить выполненной."
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=milestones_keyboard(milestones)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("milestone_toggle_"))
async def toggle(callback: CallbackQuery):

    milestone_id = int(callback.data.removeprefix("milestone_toggle_"))
    toggle_milestone(milestone_id, callback.from_user.id)

    survey = get_survey(callback.from_user.id)
    milestones = get_milestones(callback.from_user.id)
    done_count = sum(1 for m in milestones if m["done"])

    text = (
        f"🧭 <b>Ваша цель</b>\n{survey['bot_goal']}\n\n"
        f"<b>Вехи</b> ({done_count}/{len(milestones)}):\n"
        "Нажмите на веху, чтобы отметить выполненной."
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=milestones_keyboard(milestones)
    )

    if done_count == len(milestones):
        await callback.answer("🎉 Все вехи по цели пройдены!", show_alert=True)
    else:
        await callback.answer()
