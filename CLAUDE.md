# ADAM (ai_habit) — памятка для Claude

Трекер привычек с ИИ-наставником ADAM: Telegram-бот (aiogram 3) + Mini App (aiohttp + vanilla JS) + SQLite.
Продакшен — Railway, деплоит ветку `main` напрямую (`Procfile`: `web: python main.py`). Бот: @aihabbit_bot,
Mini App: https://aihabit-production.up.railway.app. Язык кода, комментариев, коммитов и общения — русский.

## Команды

```bash
pip install -r requirements-dev.txt          # зависимости + pytest
python -m pytest -q                           # все тесты (~2500, ~7 минут)
python -m pytest -q tests/test_pay_tribute.py # один файл — быстро; гоняй его во время работы
python -m pytest -q -k "share_link"           # по имени
BOT_TOKEN=123:abc python test_webapp.py       # Mini App локально на :8080 (без бота; таблицы создаются сами)
python -m pyflakes $(git ls-files '*.py' | grep -v -e ^tests/ -e ^_legacy/)  # быстрая статическая проверка
node --check webapp/static/app.js             # синтаксис JS
```

CI (`.github/workflows/tests.yml`): pytest + `pip-audit` на каждый push/PR в `main`.

## Устройство

- `main.py` — запуск: `create_tables()`, роутеры бота из `handlers/`, планировщики (APScheduler), веб-сервер.
- `handlers/` — обработчики бота (aiogram routers). `keyboards.py` — клавиатуры. `bot_style.py` — оформление «Royal Gold».
- `webapp/webapp_server.py` — основной HTTP API Mini App (`/api/*`, ~3600 строк). Остальные маршруты — `webapp/routes_*.py`
  (оплата, админка, ИИ-чат, аналитика, партнёры, стиль). `webapp/services/` — ИИ (чат ADAM, коуч, PDF-отчёт).
- `webapp/auth_helpers.py` + `webapp/telegram_auth.py` — проверка подписи Telegram initData и доступа (бан / анкета /
  подписка). Любой новый `/api/*` маршрут должен проходить через `_authenticate` / `authenticate`, а не только подпись.
- `db/` — вся работа с SQLite, по модулю на тему; общее — `db/core.py` (`connect()`, `create_tables()`, путь к базе из
  `RAILWAY_VOLUME_MOUNT_PATH`). Схема новых таблиц — `CREATE TABLE IF NOT EXISTS` в своём модуле, миграции добавлением колонок.
- `*_scheduler.py`, `coach.py`, `adam_*.py`, `trial_*.py` — фоновые рассылки и сообщения Адама.
- Оплата: звёзды Telegram (`handlers/payments.py`, `subscription_flow.py`), внешние — `payments_ext.py`, `pay_providers.py`
  (Heleket), `pay_tribute.py` (карта через Tribute), заказы — `db/pay_orders.py`. Выключатель `EXTERNAL_PAYMENTS_ENABLED`.
- `config.py` — все настройки из переменных окружения (см. `.env.example`).

## Фронтенд Mini App

- **Рабочие файлы — только в `webapp/static/`** (`index.html`, `app.js`, `style.css`, `ai_coach.js`, `ai_miniapp_styled.html` и др.).
  `app.js`, `index.html`, `style.css` в КОРНЕ репозитория — устаревшие копии, сервер их не отдаёт. Не правь их.
- Скрипты и стили подключены с `?v=...` в `webapp/static/index.html`. Сервер отдаёт `/`, `/api/*` и `/static/*` с `no-cache`
  (`error_middleware` в `webapp_server.py`; комментарий там про «long caching» устарел), но WebView Telegram и прокси бывают
  упрямыми — **изменил JS/CSS, подними версию в `?v=`**, это принятое в проекте правило.
- Без сборщика: один большой `app.js` (ES-модуль), чат ADAM — React без JSX (`ai_coach.js`, `React.createElement`).
- Проверка в браузере: Playwright + Chromium уже установлены. Вход как пользователь — подписать initData тем же
  `BOT_TOKEN`, что у локального сервера (`tests/conftest.py::sign_init_data`), и подменить `https://telegram.org/js/telegram-web-app.js`
  заглушкой `window.Telegram.WebApp` с этим `initData` (через `page.route`).

## Тесты

- `tests/conftest.py` ставит `RAILWAY_VOLUME_MOUNT_PATH` на временную папку ДО импорта `db` — одна общая база на прогон.
  Каждый тест берёт свой `telegram_id` (фикстура `uid` / свои счётчики), чтобы не пересекаться с соседями.
- HTTP-тесты — фикстура `client` (aiohttp TestClient на `create_app()`), авторизация: `{"Authorization": f"tma {sign_init_data(uid)}"}`.
- `pytest-asyncio` в режиме auto: async-тесты без декораторов.
- Исправляя баг — сначала тест, который его воспроизводит (падает без исправления), потом исправление.

## Правила

- Деньги и доступ — осторожно: выдача покупок должна быть идемпотентной (`claim_fulfilment`, `claim_event`), вебхуки —
  с проверкой подписи; при ошибке начисления отвечать 500, чтобы сервис повторил.
- Тексты бота — HTML parse mode: пользовательские данные экранировать (`html.escape`).
- Секреты только через переменные окружения; `.env`, базы и журналы не коммитить.
- Коммиты: `тип(область): что сделано` по-русски, тело — почему (см. `git log`).
- Большие `CHANGES_*.md`, `PLAN_*.md` в корне — история и планы владельца; читать при необходимости, не переписывать без просьбы.
