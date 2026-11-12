# bot_middlewares.py

bot_middlewares.py

Request-мидлвари уровня Bot API (aiogram session.middleware) — в отличие от
обычных middleware диспетчера (перехватывают входящие апдейты), эти
оборачивают КАЖДЫЙ исходящий вызов методов Bot API (send_message,
edit_message_text и т.д.) из любого места кода: хендлеров, джобов
планировщика, вебхуков.

Функции: [[Бот]], [[Журнал отправок]]

#код
