from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# =====================================
# АДМИНКА
# =====================================

def admin_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin_users"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📢 Рассылка",
                    callback_data="admin_broadcast"
                )
            ],

            [
                InlineKeyboardButton(
                    text="💎 Выдать Premium",
                    callback_data="admin_premium"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⭐ Выдать Adam Coin",
                    callback_data="admin_xp"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать",
                    callback_data="admin_ban"
                )
            ],

            [
                InlineKeyboardButton(
                    text="✅ Разбанить",
                    callback_data="admin_unban"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="admin_stats"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# ГЛАВНОЕ МЕНЮ
# =====================================

def main_menu():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="profile"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎯 Мои привычки",
                    callback_data="habits"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📅 Ежедневные задания",
                    callback_data="daily"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📊 Прогресс",
                    callback_data="progress"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📅 Календарь",
                    callback_data="calendar"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🤖 AI-наставник",
                    callback_data="ai"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🏆 Рейтинг",
                    callback_data="rating"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🏅 Достижения",
                    callback_data="achievements"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🛒 Магазин",
                    callback_data="shop"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🎁 Ежедневный бонус",
                    callback_data="daily_bonus"
                )
            ],

            [
                InlineKeyboardButton(
                    text="👥 Сообщество",
                    callback_data="community"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="settings"
                )
            ]

        ]
    )


# =====================================
# МЕНЮ ПРИВЫЧЕК
# =====================================

def habits_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="➕ Добавить привычку",
                    callback_data="add_habit"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📋 Мои привычки",
                    callback_data="my_habits"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# КНОПКИ ПРИВЫЧКИ
# =====================================

def complete_keyboard(habit_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="✅ Выполнить",
                    callback_data=f"complete_{habit_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"edit_{habit_id}"
                ),

                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"delete_{habit_id}"
                )
            ]

        ]
    )


# =====================================
# AI
# =====================================

def ai_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


def ai_feedback_keyboard(message_id: int):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="👍",
                    callback_data=f"ai_fb_up_{message_id}"
                ),
                InlineKeyboardButton(
                    text="👎",
                    callback_data=f"ai_fb_down_{message_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# НАЗАД
# =====================================

def back_menu_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# НАСТРОЙКИ
# =====================================

def settings_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔔 Напоминания",
                    callback_data="toggle_reminders"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🕘 Изменить время",
                    callback_data="change_time"
                )
            ],

            [
                InlineKeyboardButton(
                    text="📅 Google Calendar",
                    callback_data="google_calendar_menu"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🗑 Сбросить прогресс",
                    callback_data="reset_progress"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# GOOGLE CALENDAR
# ==============================# ПРОГРЕСС
# =====================================

def progress_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# DAILY TASKS
# =====================================

def daily_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data="refresh_daily"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# ДОСТИЖЕНИЯ
# =====================================

def achievements_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# РЕЙТИНГ
# =====================================

def rating_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# СООБЩЕСТВО
# =====================================

def community_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🌍 Общий чат",
                    callback_data="community_chat"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🤝 Пригласить друга",
                    callback_data="invite_friend"
                )
            ],

            [
                InlineKeyboardButton(
                    text="👥 Мои приглашения",
                    callback_data="my_referrals"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )


# =====================================
# МАГАЗИН
# =====================================

def shop_keyboard(items):

    keyboard = []

    for item in items:

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{item['name']} ({item['price']} Adam Coin)",
                    callback_data=f"buy_{item['id']}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data="back_menu"
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=keyboard
    )