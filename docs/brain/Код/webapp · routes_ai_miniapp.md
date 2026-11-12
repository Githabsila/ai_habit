# webapp/routes_ai_miniapp.py

routes_ai_miniapp.py
API endpoints для мини-приложения AI-наставника.

Эндпоинты:
  POST /api/ai/chat        — отправить сообщение AI, получить ответ (ответ готовит фоновая задача, см. ai_jobs.py)
  GET  /api/ai/chat/result — итог ответа для вернувшегося в чат клиента
  GET  /api/ai/history     — загрузить историю чата
  POST /api/ai/clear       — удалить всю переписку с ADAM
  POST /api/ai/feedback    — оценить ответ (👍/👎)
  POST /api/ai/tip         — получить совет дня

Функции: [[Адам — ИИ-наставник]]

#код
