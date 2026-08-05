from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    get_user,
    has_premium,
    was_premium_purchased,
    buy_shop_item,
    give_premium,
    get_shop_items,
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

    # Premium можно купить только один раз — навсегда, даже после того как
    # действие текущего premium закончится (иначе можно было бы просто
    # ждать неделю и покупать заново).
    if item_id == 1:
        if was_premium_purchased(callback.from_user.id):
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

    # Остальные товары (включая обычные награды)
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