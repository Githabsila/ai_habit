# Verified security sweep: miniapp-api, 2026-11-07

## Context
Зона `miniapp-api`: `webapp/webapp_server.py`, `webapp/routes_admin.py`, `webapp/routes_ai_miniapp.py` и прочие `webapp/routes_*.py`,
кроме `webapp/routes_pay.py`. Повод: запрос владельца. Первый прогон зоны; код на коммите `551825c878e82406f28a1c24abb942a96cd9f13f`.

## Delegation
Один ревьюер (агент general-purpose, только чтение), задание как у зоны `payments`; закрытое ранее — 500 у share-link. Ревьюер
проверил отправку в Telegram заглушкой, которая отклоняет неподдерживаемые теги, и замерил лимиты запросов тестом. Проверка,
воспроизведение и отчёт — мои.

## Verification
| Finding | Zone | Verdict | Claimed | Verified | Reservations | Proof |
|---|---|---|---|---|---|---|
| M1 совет дня и greet demo вызывают модель без ограничений | miniapp-api | VERIFIED REAL | Medium | Medium | нужен действующий initData; вред — расходы на ИИ | `tests/test_review_escaping.py::test_review_the_daily_tip_cannot_be_hammered`, ran, fails as expected, passes after fix |
| M2 отзыв из Mini App с «<» не доходит до админов | miniapp-api | VERIFIED REAL | Medium | Medium | ответ Telegram смоделирован по документации | `tests/test_review_escaping.py::test_review_feedback_with_angle_brackets_reaches_the_admins`, ran, fails as expected, passes after fix |
| M3 жалоба на игрока с «<» в имени не доходит | miniapp-api | VERIFIED REAL | Medium | Medium | то же | `tests/test_review_escaping.py::test_review_bot_texts_escape_what_people_write`, ran, fails as expected, passes after fix |
| M4 маленький PNG огромного разрешения разворачивается в сотни МБ | miniapp-api | VERIFIED REAL | Medium | Medium | один запрос роняет общий процесс бота и Mini App | `tests/test_review_escaping.py::test_review_a_huge_but_tiny_png_avatar_is_refused_before_decoding`, ran, fails as expected, passes after fix |
| M5 рейтинг отдаёт Telegram-ники игроков | miniapp-api | OVER-RATED | Low | Low | ник в Telegram публичен; вопрос продукта | `webapp/webapp_server.py:1222` |
| M6 500 вместо 400 на теле запроса не того типа | miniapp-api | FALSE POSITIVE | Low | - | вред только самому отправителю | `webapp/webapp_server.py:1952` |

## Confirmed defects

**M1** — `webapp/routes_ai_miniapp.py:729`, категория «Секреты» (расход платного API). Общий лимит запросов живёт в
`webapp_server._authenticate`, маршруты ИИ проходят `auth_helpers.authenticate`, дневной лимит токенов по умолчанию выключен.
Исправление: окно не больше 6 вызовов за 10 минут на человека для совета дня и greet demo (`_ai_burst_exceeded`), сверх — 429.

**M2** — `webapp/webapp_server.py:2880`, категория «Экранирование в сообщениях бота». Бот по умолчанию в HTML
(`DefaultBotProperties(parse_mode=HTML)`), текст отзыва — без экранирования; «<» ломает разбор, человек видит «не удалось отправить».
Исправление: `parse_mode=None`.

**M3** — `webapp/webapp_server.py:2495`, то же для жалобы: имена и комментарий без экранирования, ошибка глушится, жалоба
считается принятой. Исправление: `parse_mode=None`.

**M4** — `webapp/webapp_server.py:3404`, категория «Input validation». Ограничение только 5 МБ файла; `convert("RGB")` декодирует
картинку целиком. Исправление: проверка `width*height <= 25 млн` по заголовку до декодирования, `DecompressionBombError` → 400.

## Refused or unconfirmed candidates
- M7, пуш «+1 день ударного режима» с `first_name` из `generate_praise` без экранирования (`webapp/webapp_server.py:1466`): needs deeper
  review — не дойдёт только пуш самому человеку с «<» в имени; нужно проверить `_push` и `generate_praise` вместе.
- M5, ники в рейтинге: refused, lens exploitability — Telegram-ник публичен; decision pending, показывать ли его в рейтинге.
- M6, 500 на кривом теле: refused, lens exploitability — фронт такие запросы не шлёт, вред только отправителю.
- IDOR привычек, задач, вех: refused, lens claim correctness — `_owned_habit_or_404`, запросы с `telegram_id`.
- Повторные награды (сундуки, квесты, подарки): refused, lens existing mitigation — PRIMARY KEY, условные UPDATE с rowcount,
  синхронные обработчики после `await _authenticate`.
- Админ-маршруты без проверки: refused, lens claim correctness — все через `_authenticate_admin`.

## Coverage
`webapp/webapp_server.py` — авторизация, квесты, сундуки, парные, привычки, план, соцблок, магазин, аватар, фидбек, триал; bootstrap
и часть `/api/settings/*` бегло. `webapp/routes_ai_miniapp.py` целиком. Остальные `routes_*.py` — проверка админ-доступа и отправок
ботом. Не читались: `webapp/services/*`, фронтенд. Линзы: exploitability, claim correctness, existing mitigation.

## Hand-off
- Исправления M1–M4 внесены в ветку с тестами.
- M7: экранировать имя в `generate_praise` или отправлять пуш без HTML — в следующем прогоне зоны.
- M5: решение владельца.
