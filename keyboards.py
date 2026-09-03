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
                    text="🕓 Заявки на доступ",
                    callback_data="admin_pending"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔎 Карточка пользователя",
                    callback_data="admin_user_card"
                )
            ],

            [
                InlineKeyboardButton(
                    text="🏷 Поиск по тегу",
                    callback_data="admin_tag_search"
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


def broadcast_target_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем", callback_data="broadcast_all")],
            [InlineKeyboardButton(text="🏷 По тегу", callback_data="broadcast_tag")],
        ]
    )


def tag_search_results_keyboard(users):
    rows = []
    for user in users:
        label = f"🔎 {user['telegram_id']}"
        if user["username"]:
            label += f" (@{user['username']})"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"admin_card_{user['telegram_id']}")
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =====================================
# ЗАЯВКИ НА ДОСТУП (pending)
# =====================================

def pending_keyboard(users):
    """users — список sqlite3.Row с telegram_id/username, статус 'pending'."""

    rows = []

    for user in users:
        label = f"✅ {user['telegram_id']}"
        if user["username"]:
            label += f" (@{user['username']})"
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"admin_approve_{user['telegram_id']}"
            )
        ])

    rows.append([
        InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="admin")
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# =====================================
# ГЛАВНОЕ МЕНЮ
# =====================================

def main_menu(is_admin=False):
    """Панель бота теперь отвечает только за то, что не переехало в
    Mini App: сама регистрация (анкета, см. handlers/start.py и
    handlers/onboarding.py — вне этой клавиатуры) и «Основа: умные
    напоминания». Все остальные разделы (профиль, привычки, задания,
    прогресс, календарь, AI, рейтинг, достижения, магазин, сообщество,
    вехи, Premium, стиль AI, сброс прогресса) живут в Mini App —
    он открывается кнопкой меню Telegram (см. main.py: set_chat_menu_button).

    is_admin=True добавляет кнопку "Админ-панель" — раньше она приходила
    отдельным вторым сообщением только на /start (см. handlers/start.py) и
    пропадала при возврате в главное меню откуда-то ещё; теперь она прямо
    в этом меню и видна только тем, чей telegram_id есть в config.ADMIN_IDS."""

    rows = [
        [
            InlineKeyboardButton(
                text="🔔 Умные напоминания",
                callback_data="reminders_menu"
            )
        ]
    ]

    if is_admin:
        rows.append([
            InlineKeyboardButton(
                text="🛠 Админ-панель",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


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
                    text="💡 Совет дня",
                    callback_data="ai_tip"
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


def ai_feedback_keyboard(message_id: int, suggested_habit: str | None = None):
    """suggested_habit: если AI явно посоветовал конкретную привычку, здесь
    добавляется кнопка "➕ Добавить", которая сразу заводит её в БД —
    без похода пользователя в раздел привычек."""

    rows = []

    if suggested_habit:
        rows.append([
            InlineKeyboardButton(
                text=f"➕ Добавить «{suggested_habit}»",
                callback_data=f"ai_addhabit_{message_id}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="👍",
            callback_data=f"ai_fb_up_{message_id}"
        ),
        InlineKeyboardButton(
            text="👎",
            callback_data=f"ai_fb_down_{message_id}"
        ),
        InlineKeyboardButton(
            text="🔊 Озвучить",
            callback_data=f"ai_voice_{message_id}"
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Главное меню",
            callback_data="back_menu"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_feedback_reason_keyboard(message_id: int):
    """Показывается вместо обычной клавиатуры сразу после 👎 — короткий,
    необязательный уточняющий вопрос, что именно не понравилось. Используется
    для 'обучения' AI на дизлайках (этап 2 AI Core)."""

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="Затянуто",
                    callback_data=f"ai_fbr_long_{message_id}"
                ),
                InlineKeyboardButton(
                    text="Не по теме",
                    callback_data=f"ai_fbr_off_{message_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    text="Непонятно",
                    callback_data=f"ai_fbr_unclear_{message_id}"
                ),
                InlineKeyboardButton(
                    text="Другое",
                    callback_data=f"ai_fbr_other_{message_id}"
                )
            ],

            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=f"ai_fbr_skip_{message_id}"
                )
            ]

        ]
    )


def crisis_keyboard():
    """Клавиатура для кризисного ответа — намеренно без оценок 👍/👎 и без
    кнопки добавления привычки: это не обычный совет по продуктивности."""

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
# УМНЫЕ НАПОМИНАНИЯ
# =====================================
# Стиль AI-наставника и сброс прогресса переехали в Mini App (Profile →
# Настройки). В боте из «Настроек» остаётся только то, что относится к
# самой сути напоминаний — вкл/выкл и время.

def reminders_keyboard(settings_data=None):
    """settings_data — строка из get_settings(), нужна, чтобы подписать
    гранулярные тумблеры текущим статусом (🟢/🔴) прямо на кнопке, а не
    только в тексте выше. None — до первого вызова get_settings (не должно
    происходить в реальном сценарии, но кнопки тогда просто без статуса)."""

    def _mark(key):
        if not settings_data:
            return ""
        try:
            return " 🟢" if settings_data[key] else " 🔴"
        except (IndexError, KeyError):
            return ""

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔔 Вкл/выкл все напоминания",
                    callback_data="toggle_reminders"
                )
            ],

            [
                InlineKeyboardButton(
                    text=f"📋 Привычки и план дня{_mark('reminders_habits')}",
                    callback_data="toggle_reminder_category:habits"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔥 Ударный режим{_mark('reminders_streak')}",
                    callback_data="toggle_reminder_category:streak"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📊 Сводки и отчёты{_mark('reminders_digests')}",
                    callback_data="toggle_reminder_category:digests"
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
                    text="⬅️ Главное меню",
                    callback_data="back_menu"
                )
            ]

        ]
    )



# =====================================
# СТИЛЬ AI-НАСТАВНИКА
# =====================================

def ai_style_keyboard(current: str = "neutral"):

    labels = {
        "soft": "🌿 Мягкий",
        "neutral": "⚖️ Нейтральный",
        "strict": "🔥 Жёсткий тренер",
    }

    def label(style):
        text = labels[style]
        return f"✅ {text}" if style == current else text

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=label("soft"),
                    callback_data="ai_style_soft"
                )
            ],

            [
                InlineKeyboardButton(
                    text=label("neutral"),
                    callback_data="ai_style_neutral"
                )
            ],

            [
                InlineKeyboardButton(
                    text=label("strict"),
                    callback_data="ai_style_strict"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⬅️ Настройки",
                    callback_data="settings"
                )
            ]

        ]
    )


# ==============================# ПРОГРЕСС
# =====================================

def progress_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🤖 AI-анализ прогресса",
                    callback_data="progress_ai_analysis"
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
                    text="🔎 Найти единомышленника",
                    callback_data="find_match"
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
                    text="🏁 Недельный челлендж",
                    callback_data="start_challenge"
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


def challenge_partner_keyboard(referred_users):
    """Кнопка на каждого уже приглашённого друга — выбрать его для
    недельного челленджа."""
    rows = [
        [InlineKeyboardButton(
            text=("@" + u["username"]) if u["username"] else (u["first_name"] or str(u["telegram_id"])),
            callback_data=f"challenge_with:{u['telegram_id']}",
        )]
        for u in referred_users
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="community")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


# =====================================
# ВЕХИ ПО ЦЕЛИ
# =====================================

def milestones_keyboard(milestones):
    rows = []
    for m in milestones:
        mark = "✅" if m["done"] else "▫️"
        label = f"{mark} {m['milestone_text']}"[:64]
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"milestone_toggle_{m['id']}")
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =====================================
# PREMIUM (покупка через Telegram Stars)
# =====================================

def premium_buy_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Купить Premium", callback_data="buy_premium")],
            [InlineKeyboardButton(text="🎁 Подарить Premium другу", callback_data="gift_premium_start")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_menu")],
        ]
    )


def gift_premium_candidates_keyboard(candidates):
    """candidates — список приглашённых пользователей (get_referred_users) —
    самый быстрый путь выбрать получателя без ручного ввода ID/пересылки
    сообщения."""
    rows = [
        [InlineKeyboardButton(
            text=f"@{c['username']}" if c["username"] else (c["first_name"] or str(c["telegram_id"])),
            callback_data=f"gift_premium_to_{c['telegram_id']}",
        )]
        for c in candidates[:8]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Отмена", callback_data="premium_info")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gift_premium_confirm_keyboard(recipient_id: int, price_stars: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 Подарить за {price_stars} ⭐", callback_data=f"gift_premium_confirm_{recipient_id}")],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="premium_info")],
        ]
    )


def subscription_buy_keyboard(price_stars: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ Оплатить {price_stars} Stars", callback_data="buy_subscription")],
        ]
    )





# Backward-compatible alias used by onboarding.py
pending_review_keyboard = pending_keyboard
