"""
Старый магазин в чате бота. Магазин живёт в Mini App (там цены в звёздах, лимиты, выдача рамок и ответов ADAM).
Кнопки «shop» и «buy_<id>» остались только в старых сообщениях: раньше они списывали Adam Coin по звёздной цене
и ничего не выдавали (прогон 09.11). Теперь ничего не списывают — вежливо ведут в приложение.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery

from keyboards import open_app_markup

router = Router()

SHOP_MOVED_TEXT = "🛍 Магазин теперь в приложении ADAM — там все товары, цены и твой баланс. Открой его кнопкой ниже 🙂"


async def _to_app(callback: CallbackQuery):
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(SHOP_MOVED_TEXT, reply_markup=open_app_markup())


@router.callback_query(F.data == "shop")
async def shop(callback: CallbackQuery):
    await _to_app(callback)


# Только buy_<число>: раньше startswith("buy_") ловил и buy_subscription / buy_premium — роутер магазина стоит раньше оплаты,
# падал на int("subscription"), и «Оплатить» в боте не работала (прогон 07.11, B2).
@router.callback_query(F.data.regexp(r"^buy_\d+$"))
async def buy_item(callback: CallbackQuery):
    await _to_app(callback)
