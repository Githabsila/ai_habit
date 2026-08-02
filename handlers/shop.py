from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    get_shop_items,
    get_user,
    give_premium,
    has_premium,
    buy_shop_item
)

from keyboards import shop_keyboard

router = Router()


# =====================================
# МАГАЗИН
# =====================================

@router.callback_query(F.data == "shop")
async def shop(callback: CallbackQuery):

    user = get_user(callback.from_user.id)
    items = get_shop_items()

    await callback.message.edit_text(
        f"""
🛒 <b>Магазин наград</b>

⭐ Ваш Adam Coin: <b>{user["xp"]}</b>

Выберите награду:
""",
        parse_mode="HTML",
        reply_markup=shop_keyboard(items)
    )

    await callback.answer()


# =====================================
# ПОКУПКА
# =====================================

@router.callback_query(F.data.startswith("buy_"))
async def buy_item(callback: CallbackQuery):

    item_id = int(callback.data.split("_")[1])

    # Premium можно купить только один раз
    if item_id == 1:

        if has_premium(callback.from_user.id):
            await callback.answer(
                "❌ Premium уже куплен.",
                show_alert=True
            )
            return

        success = buy_shop_item(
            callback.from_user.id,
            item_id
        )

        if not success:
            await callback.answer(
                "❌ Недостаточно Adam Coin.",
                show_alert=True
            )
            return

        give_premium(callback.from_user.id)

        await callback.answer(
            "👑 Premium успешно куплен!",
            show_alert=True
        )

        return

    # Остальные товары
    success = buy_shop_item(
        callback.from_user.id,
        item_id
    )

    if success:
        await callback.answer(
            "✅ Покупка совершена!",
            show_alert=True
        )
    else:
        await callback.answer(
            "❌ Недостаточно Adam Coin.",
            show_alert=True
        )