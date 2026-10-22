"""
Карта функций продукта — единое место, где аналитика узнаёт, ЧТО в продукте есть и какие экраны, кнопки, запросы к серверу, события и
действия в боте к какой функции относятся. Страница /analytics строит раздел «Функции» именно по ней: сколько людей пользуются, как
часто возвращаются, как сработала функция после выпуска.

Как это работает вместе с автоподхватом (db/usage.py):
  • Любая НОВАЯ кнопка, экран или маршрут попадает в данные сама, как только их использует первый человек (каталог ui_catalog,
    дневные счётчики ui_clicks / ui_screens / api_usage). Если у неё нет пары в этой карте, она появляется на странице в списке
    «Не привязано к функции» — ничего не теряется и ничего не искажает цифры.
  • Чтобы функция попала в раздел «Функции» с названием и датой выпуска, её нужно описать здесь. Это не забывается: тест
    tests/test_feature_map.py падает, если в коде есть экран, маршрут API, команда/кнопка бота или событие, которых нет в карте.

ЧТО ДЕЛАТЬ ПРИ НОВОЙ ФУНКЦИИ (3 минуты):
  1. Новый экран-окно (id="…Overlay" в index.html) → добавить его имя (без «Overlay») в screens нужной функции;
  2. Новый маршрут /api/… → добавить шаблон "МЕТОД /api/путь/{параметр}" в api функции (пассивные загрузки — в PASSIVE_API);
  3. Новая команда/кнопка бота → шаблон "cmd:/команда" или "cb:данные_#" (цифры в данных заменены на #) в bot функции;
  4. Новое событие (track_server / trk) → имя в events функции (и подпись в db/tracking.py::EVENT_LABELS);
  5. Новая функция целиком → новая запись Feature(...) с датой выпуска since — от неё считается «новая за N дней» и сравнение до/после.
  Кнопкам внутри существующего экрана ничего прописывать не нужно: они учтутся сами и попадут к функции, которой принадлежит экран.
  Хочется красивого названия кнопки на странице — добавь её в BUTTON_LABELS (необязательно).
"""
from dataclasses import dataclass, field
from fnmatch import fnmatchcase


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    group: str                      # core / growth / social / money / system — для группировки на странице
    since: str                      # дата выпуска YYYY-MM-DD: от неё считается «новая функция» и сравнение «до / после»
    screens: tuple = ()             # окна (id без «Overlay») и вкладки, которыми функция «владеет»
    clicks: tuple = ()              # шаблоны подписей кнопок (fnmatch), если их нельзя отнести по экрану
    events: tuple = ()              # события user_events
    api: tuple = ()                 # "МЕТОД /путь" — осознанные действия (не фоновые загрузки)
    bot: tuple = ()                 # "cmd:/x", "cb:x_*" — действия в боте
    note: str = ""                  # что считается использованием, если неочевидно
    hidden: bool = False            # не показывать в списке функций (служебное)


GROUPS = {
    "core": "Ядро: привычки, задачи, Адам",
    "growth": "Вовлечение и награды",
    "social": "Друзья и общение",
    "money": "Магазин и оплата",
    "system": "Профиль, настройки, служебное",
}

FEATURES = (
    # ------------------------------------------------------------------ ядро
    Feature(
        "onboarding", "Знакомство: анкета, квиз, тур", "core", "2026-01-01",
        screens=("startQuiz", "appTour", "handleIntro", "streakOnboarding"),
        events=("survey_started", "survey_step", "survey_completed", "access_approved", "onb_streak_shown", "onb_streak_ok",
                "quiz_start", "quiz_step", "quiz_done", "tour_start", "tour_step", "tour_skip", "tour_done",
                "handle_intro_shown", "handle_intro_done", "hint_shown", "hint_next", "hint_skip"),
        api=("POST /api/onboarding/start", "POST /api/onboarding/stage", "POST /api/onboarding/restart"),
        bot=("cmd:/start", "cmd:/survey_preview", "cb:survey:*", "cb:survey_*"),
        note="Любое действие в анкете, квизе, туре и подсказках",
    ),
    Feature(
        "adam_intro", "Знакомство с Адамом (тест, метод планирования дня)", "core", "2026-10-20",
        screens=("adamIntro",),
        clicks=("adamIntro*", "adam-intro-*"),
        events=("adam_intro_shown", "adam_intro_step", "adam_intro_done", "adam_intro_skip", "adam_intro_habit", "adam_intro_chat"),
        api=("GET /api/adam-profile", "POST /api/adam-profile", "POST /api/adam-profile/recommend", "POST /api/adam-profile/skip"),
        note="Тест после онбординга: вопросы, итог с методом (Помодоро, 45+10, 90+20, три главных дела), первые привычки, разговор о целях",
    ),
    Feature(
        "habits", "Привычки", "core", "2026-01-01",
        clicks=("addHabitTrigger", "newHabit*", "habit-item__*", "habit-filter-chip*", "habit-template-chip*", "habit-program-card*",
                "habit-note-form__*", "struggling-habit-banner__*", "habitEdit*", "habit-edit-*"),
        events=("habit_form_open", "habit_added", "habit_deleted", "habit_edit_open", "habit_edit_save", "habit_moved", "habit_completed", "abuse_flagged"),
        api=("POST /api/habits", "PUT /api/habits/{habit_id}", "POST /api/habits/{habit_id}/move","POST /api/habits/{habit_id}/complete",
             "POST /api/habits/{habit_id}/progress", "POST /api/habits/{habit_id}/note", "DELETE /api/habits/{habit_id}"),
        bot=("cb:add_habit", "cb:complete_#", "cb:delete_#", "cb:edit_#", "cb:my_habits", "cb:habits", "cb:streak_continue"),
        note="Создание, отметка, правка и удаление привычек",
    ),
    Feature(
        "daily_plan", "План дня: главная и второстепенные задачи", "core", "2026-01-01",
        clicks=("saveMainGoalBtn", "addPlanTaskTrigger", "plan-icon-btn*"),
        events=("main_goal_set", "main_goal_done", "task_added", "task_done"),
        api=("POST /api/plan/save", "POST /api/plan/task/toggle", "POST /api/plan/main/save", "POST /api/plan/main/toggle",
             "DELETE /api/plan/main", "POST /api/plan/task", "PUT /api/plan/task/{task_id}", "DELETE /api/plan/task/{task_id}"),
    ),
    Feature(
        "milestones", "Вехи и цели", "core", "2026-01-01",
        api=("POST /api/milestones", "POST /api/milestones/{milestone_id}/toggle", "POST /api/settings/goals"),
        bot=("cb:milestones", "cb:milestone_toggle_#"),
    ),
    Feature(
        "ai_chat", "Чат с Адамом", "core", "2026-01-01",
        screens=("coach", "ai"),
        clicks=("aiCoachBtn", "heroWidgetPortrait"),
        events=("ai_message",),
        api=("POST /api/ai/chat", "POST /api/ai/greet", "POST /api/ai/clear", "POST /api/ai/feedback", "POST /api/ai/tip",
             "POST /api/ai/habit/add"),
        bot=("cb:ai", "cb:ai_tip", "cb:ai_addhabit_#", "cb:ai_fb_*", "cb:ai_fbr_*", "cb:ai_voice_*", "msg:text", "msg:voice"),
        note="Сообщение Адаму в приложении или в боте",
    ),
    Feature(
        "ai_progress", "ИИ-разбор прогресса", "core", "2026-01-01",
        clicks=("progressAiBtn",),
        api=("POST /api/progress/ai-analysis",),
        bot=("cb:progress_ai_analysis",),
    ),
    Feature(
        "adam_nudge", "Адам пишет первым", "core", "2026-10-01",
        api=("POST /api/ai/nudge/shown",),
        note="Показ подсказки Адама; сами сообщения уходят из бота по расписанию",
    ),
    Feature(
        "stats", "Статистика и отчёты", "core", "2026-01-01",
        screens=("stats",),
        clicks=("openStatsBtn",),
        api=("GET /api/progress/stats", "GET /api/progress/pdf-report", "GET /api/export/habits.csv"),
        bot=("cb:progress",),
    ),
    Feature(
        "calendar", "Календарь", "core", "2026-01-01",
        screens=("calendar",),
        clicks=("cal-cell*", "cal-trend-bar*", "tab-bar__item=calendar"),
        bot=("cb:calendar",),
    ),
    # ------------------------------------------------------------------ вовлечение
    Feature(
        "home", "Главная", "growth", "2026-01-01",
        screens=("home",),
        clicks=("tab-bar__item=home", "streak-day*", "tab-loading-state__retry*"),
        note="Открытие главной вкладки",
    ),
    Feature(
        "streak", "Серия, статусы и заморозки", "growth", "2026-01-01",
        screens=("streakCelebration", "streakWeekly", "statusLadder"),
        clicks=("freezeBuyBtn", "streakCelebrationContinue", "profileMetricStatus", "statusLadderClose"),
        api=("POST /api/streak/freeze/buy", "POST /api/streak/restore-free", "POST /api/streak/weekly-reward", "POST /api/streak/timezone"),
    ),
    Feature(
        "quests", "Квесты дня и задания месяца", "growth", "2026-01-01",
        screens=("dailyQuests", "chest"),
        clicks=("dailyQuestsBtn", "monthCardOpen", "daily-quest__*", "chestClose"),
        events=("chest_opened", "quest_done", "quests_window_auto", "quests_window_open", "quests_go"),
        api=("POST /api/quests/{quest_key}/claim", "POST /api/quests/{quest_key}/open", "POST /api/month-quests/claim", "POST /api/daily-bonus/claim"),
        note="Три постоянных квеста — две привычки, план дня, разговор с Адамом; награда выдаётся сама, окно показывает заполнение полосок и перелёт балла в месяц",
        bot=("cb:daily", "cb:daily_bonus", "cb:refresh_daily"),
    ),
    Feature(
        "pair_quest", "Парное задание", "social", "2026-10-02",
        screens=("pairGuide",),
        clicks=("pair-*", "pairSheet*", "pq-info-btn*", "pairGuideClose"),
        events=("pair_invite", "pair_left_early"),
        api=("POST /api/pair-quest/invite", "POST /api/pair-quest/{quest_id}/accept", "POST /api/pair-quest/{quest_id}/decline",
             "POST /api/pair-quest/{quest_id}/cancel", "POST /api/pair-quest/{quest_id}/claim"),
    ),
    Feature(
        "evolution", "Уровни, ранги и эволюции Адама", "growth", "2026-10-05",
        screens=("levelup", "evolution", "doubleBonus", "achievementShare"),
        clicks=("up-evo*",),
        api=("POST /api/evolution/seen",),
        bot=("cb:achievements",),
    ),
    Feature(
        "pet_team_season", "Питомец, команда и сезон", "growth", "2026-01-01",
        api=("POST /api/team/create", "POST /api/team/join", "POST /api/team/leave"),
    ),
    Feature(
        "self_reward", "Награда себе", "growth", "2026-09-01",
        screens=("selfRewardModal", "selfRewardHistory"),
        clicks=("selfRewardActionBtn",),
        api=("POST /api/self-reward",),
    ),
    # ------------------------------------------------------------------ друзья
    Feature(
        "rating", "Рейтинг", "social", "2026-01-01",
        screens=("rating",),
        clicks=("tab-bar__item=rating", "rating-*"),
        api=("POST /api/friends/{telegram_id}/react",),
        bot=("cb:rating",),
    ),
    Feature(
        "friends", "Друзья и подписки", "social", "2026-09-10",
        screens=("userProfile",),
        clicks=("friend-row__btn*", "friends-card__*", "up-*", "player-follow__*", "follow-recs__*"),
        events=("friend_added",),
        api=("POST /api/friends/{telegram_id}/nudge", "POST /api/friends/{telegram_id}/remove", "POST /api/users/{telegram_id}/follow",
             "POST /api/users/{telegram_id}/unfollow", "POST /api/users/{telegram_id}/block", "POST /api/users/{telegram_id}/unblock",
             "POST /api/users/{telegram_id}/report", "GET /api/users/search", "GET /api/users/{telegram_id}/profile",
             "POST /api/settings/friend-nudges", "GET /api/people/active", "POST /api/follows/seen"),
        bot=("cb:community", "cb:community_chat", "cb:find_match", "cb:start_challenge", "cb:challenge_with:*"),
    ),
    Feature(
        "inbox", "Уведомления (колокольчик, разделы)", "social", "2026-10-20",
        screens=("inbox",),
        clicks=("inboxBtn", "inbox-*", "inboxOverlay*"),
        events=("inbox_open", "inbox_tab", "inbox_item"),
        api=("GET /api/inbox",),
        note="Экран «Уведомления»: все подарки, сообщения Адама, друзья, подписчики и системное по вкладкам; число новых на колокольчике",
    ),
    Feature(
        "gifts", "Подарки друзьям", "social", "2026-09-20",
        screens=("adminGift", "freezeHelp"),
        clicks=("openGiftsBtn", "gift-row__btn*", "friend-freeze*", "freezeHelp*", "freeze-help-*"),
        events=("freeze_gift_sent", "freeze_help_shown", "freeze_help_open"),
        api=("POST /api/users/{telegram_id}/gift", "POST /api/gifts/seen"),
        note="Алмазы, косметика и заморозка серии; окно «Надо помочь другу» и серая заморозка у друга в рейтинге",
    ),
    Feature(
        "rescue", "Спасение серии друга", "social", "2026-10-21",
        screens=("rescueHelp",),
        clicks=("friend-rescue*", "rescueHelp*", "rescue-help-*"),
        events=("rescue_help_shown", "rescue_help_open", "rescue_sent"),
        api=("POST /api/users/{telegram_id}/rescue", "GET /api/rescue-help"),
        note="Серия друга погасла, но её ещё можно вернуть: окно «Восстановить бесплатно», значок у друга в рейтинге, пуш друзьям; раз в месяц на спасателя",
    ),
    Feature(
        "invites", "Приглашения и реферальные ссылки", "growth", "2026-01-01",
        events=("referral_joined", "referral_rejected", "referral_paid", "referral_activated", "referral_revoked", "referral_mail"),
        api=("GET /api/share-link",),
        bot=("cb:invite_friend", "cb:my_referrals"),
        note="Ссылка-приглашение и переходы по ней",
    ),
    # ------------------------------------------------------------------ деньги
    Feature(
        "shop", "Магазин и косметика", "money", "2026-01-01",
        screens=("shop",),
        clicks=("openShopBtn", "shop-*", "theme-swatch*"),
        events=("purchase",),
        api=("POST /api/buy/{item_id}", "POST /api/cosmetics/equip", "POST /api/shop/stars/{item_id}"),
        bot=("cb:shop", "cb:buy_#", "cb:buy_*"),
    ),
    Feature(
        "payments", "Подписка и оплата", "money", "2026-01-01",
        screens=("trialPaywall",),
        clicks=("trialCard*", "trialSheet*", "trialPaywall*", "trial-*"),
        events=("trial_sheet_open", "trial_pay_tap", "trial_paywall_shown", "trial_paid", "subscription_invoice", "subscription_paid"),
        api=("GET /api/trial", "POST /api/subscription/invoice"),
        bot=("cb:buy_subscription", "cb:buy_premium", "cb:premium_info", "cb:gift_premium_start", "cb:gift_premium_to_#",
             "cb:gift_premium_confirm_#", "msg:successful_payment", "cmd:/paysupport", "cmd:/terms", "cmd:/refund"),
        note="Нажатия на оплату и сами платежи",
    ),
    # ------------------------------------------------------------------ профиль и настройки
    Feature(
        "profile", "Профиль и вкладка «Профиль»", "system", "2026-01-01",
        screens=("profile", "identity", "avatarEdit", "archetypeQuiz"),
        clicks=("tab-bar__item=profile", "playerBadge", "userHandleSaveBtn", "archetype-quiz-*"),
        events=("username_changed",),
        api=("POST /api/profile/name", "POST /api/profile/avatar", "POST /api/settings/handle", "POST /api/settings/archetype",
             "POST /api/settings/public-profile", "POST /api/settings/share-habits", "POST /api/settings/stats-visibility"),
        bot=("cb:profile",),
    ),
    Feature(
        "settings", "Настройки", "system", "2026-01-01",
        screens=("settings",),
        clicks=("openSettingsBtn", "remindersToggle", "lab-bar__*"),
        api=("POST /api/settings/reminder-time", "POST /api/settings/ai-style", "POST /api/settings/theme", "POST /api/settings/color-mode",
             "POST /api/settings/language", "POST /api/settings/gender", "POST /api/settings/reminders/toggle",
             "POST /api/settings/habit-checkpoint-style", "POST /api/settings/home-layout", "POST /api/settings/reminders/category",
             "POST /api/settings/quiet-hours"),
        bot=("cb:reminders_menu", "cb:toggle_reminders", "cb:toggle_reminder_category:*"),
    ),
    Feature(
        "feedback", "Отзывы", "system", "2026-01-01",
        clicks=("feedbackSend", "openFeedbackBtn"),
        api=("POST /api/feedback",),
    ),
    Feature(
        "account", "Данные аккаунта и сброс", "system", "2026-01-01",
        clicks=("deleteAccountBtn", "resetProgressBtn", "replayOnboardingBtn"),
        api=("GET /api/account/export", "POST /api/account/delete", "POST /api/settings/reset-progress"),
    ),
)


# Что в продукте есть, но функцией не является: не показываем как «не привязано» и не требуем строки в карте.
IGNORED_SCREENS = ("loading",)                                                  # служебная заставка загрузки

# Фоновые загрузки и служебные вызовы: считаются в нагрузке и ошибках, но не в «использовании функций».
PASSIVE_API = (
    "GET /api/bootstrap", "GET /api/bootstrap-secondary", "GET /api/quests", "GET /api/pair-quest", "GET /api/pair-quest/candidates",
    "GET /api/pet", "GET /api/season", "GET /api/team", "GET /api/changelog/unseen", "GET /api/streak/status",
    "POST /api/streak/onboarding/seen", "POST /api/admin-gift/seen", "POST /api/tour/seen", "POST /api/handle-intro/seen",
    "POST /api/start-quiz/seen", "POST /api/client-error", "POST /api/perf/report", "POST /api/track", "GET /",
    "GET /health", "GET /static/*", "GET /coach", "GET /admin", "GET /privacy", "GET /terms", "GET /media/avatars/{filename}",
    "GET /analytics", "GET /static/style.css", "GET /static/app.js", "GET /static/ai_coach.js",
    # загрузки данных разделов и «просмотрено» — приходят вместе с открытием экрана, осознанным действием не считаются
    "POST /api/changelog/seen", "GET /api/activity-feed", "GET /api/notifications/history", "GET /api/reactions", "GET /api/friends",
    "GET /api/follows", "GET /api/gifts/received", "GET /api/gifts/options", "GET /api/freeze-help",
    "GET /api/inbox/summary", "POST /api/inbox/seen", "GET /api/self-reward/history",
    "GET /api/public/profile/{telegram_id}", "GET /u/{telegram_id}", "GET /api/habits/notes", "GET /api/milestones",
    "GET /api/daily-tasks", "GET /api/shop", "GET /api/ai/history", "GET /api/ai/quota", "GET /api/ai/chat/result",
)

# Админские маршруты и экраны — не продукт, в статистику использования не идут.
ADMIN_API_PREFIXES = ("/api/admin/", "/api/analytics/", "/admin", "/analytics")
IGNORED_BOT = ("cb:admin*", "cb:broadcast_*", "cb:back_menu", "cmd:/admin", "cmd:/changelog", "cmd:/analytics", "msg:other",
               "msg:photo", "msg:video", "msg:video_note", "msg:document", "msg:sticker", "msg:location", "msg:contact")
IGNORED_EVENTS = ("registered", "session_start", "screen", "source_attributed")      # служебные: учтены воронкой и каналами

# Красивые названия кнопок и экранов для страницы. Всё, чего здесь нет, показывается как есть (подпись из кода).
BUTTON_LABELS = {
    "tab-bar__item=home": "Вкладка «Главная»", "tab-bar__item=calendar": "Вкладка «Календарь»", "tab-bar__item=rating": "Вкладка «Рейтинг»",
    "tab-bar__item=profile": "Вкладка «Профиль»", "habit-item__check=complete": "Отметить привычку", "habit-item__check=progress": "Прогресс счётчика",
    "habit-item__del=delete": "Удалить привычку", "habit-item__note=note": "Заметка к привычке", "habit-item__more=toggle-actions": "Меню привычки",
    "plan-icon-btn=edit": "Править задачу", "plan-icon-btn=delete": "Удалить задачу",
    "pairSheetAdd": "Союзник: «Добавить друзей»", "pair-option=invite-link": "Союзник: ссылка в Telegram",
    "pair-option=pick-people": "Союзник: активные игроки", "pair-btn=follow-person": "Союзник: подписка на игрока",
    "pair-btn=invite-link": "Союзник: ссылка в Telegram",
}
SCREEN_LABELS_EXTRA = {
    "achievementShare": "Поделиться достижением", "selfRewardModal": "Награда себе", "selfRewardHistory": "История наград себе",
    "streakWeekly": "Недельная награда за серию", "bot": "Бот",
}


# ---------------------------------------------------------------------------------------------------------------- сопоставление

def _match(patterns, value):
    return any(fnmatchcase(value, p) for p in patterns)


_BY_ID = {f.id: f for f in FEATURES}


def feature(feature_id):
    return _BY_ID.get(feature_id)


def feature_for_screen(screen):
    for f in FEATURES:
        if screen in f.screens:
            return f.id
    return None


def feature_for_click(label, screen=None):
    """Функция кнопки: сначала по шаблонам подписи, потом по экрану, которому принадлежит окно (новые кнопки внутри
    существующего экрана попадают к своей функции сами)."""
    for f in FEATURES:
        if f.clicks and _match(f.clicks, label):
            return f.id
    if label.startswith(("cmd:", "cb:", "msg:")):
        return feature_for_bot(label)
    if screen and screen != "home":                # главная — общий узел: её неопознанные кнопки должны попасть в «не привязано»
        return feature_for_screen(screen)
    return None


def feature_for_event(name):
    for f in FEATURES:
        if name in f.events:
            return f.id
    return None


def feature_for_api(key):
    """key — "МЕТОД /шаблон/{параметры}" как его пишет мидлвара. None — маршрут пассивный/служебный или не описан."""
    for f in FEATURES:
        if key in f.api:
            return f.id
    return None


def feature_for_bot(label):
    for f in FEATURES:
        if f.bot and _match(f.bot, label):
            return f.id
    return None


def is_ignored_api(key):
    path = key.split(" ", 1)[-1]
    return _match(PASSIVE_API, key) or path.startswith(ADMIN_API_PREFIXES)


def is_ignored_bot(label):
    return _match(IGNORED_BOT, label)


def is_ignored_screen(screen):
    return screen in IGNORED_SCREENS


def click_label(label):
    return BUTTON_LABELS.get(label, label)


def screen_label(screen):
    from db.tracking import SCREEN_LABELS
    return SCREEN_LABELS.get(screen) or SCREEN_LABELS_EXTRA.get(screen) or screen


def classify(kind, key, screen=None):
    """Для списка «не привязано»: (feature_id | None, ignored: bool) для записи каталога."""
    if kind == "api":
        fid = feature_for_api(key)
        return fid, fid is None and is_ignored_api(key)
    if kind == "screen":
        fid = feature_for_screen(key)
        return fid, fid is None and (is_ignored_screen(key) or key == "bot")
    if kind == "click":
        if key.startswith(("cmd:", "cb:", "msg:")):
            fid = feature_for_bot(key)
            return fid, fid is None and is_ignored_bot(key)
        fid = feature_for_click(key, screen)
        return fid, False
    if kind == "version":
        return None, True
    return None, False
