# Verified security sweep: bot-schedulers, 2026-11-07

## Context
Зона `bot-schedulers`: `handlers/*.py` кроме `handlers/payments.py`, `main.py`, `streak_scheduler.py`, `coach.py`, `adam_proactive.py`,
`reminder_diagnosis.py`, `trial_messages.py`, `bot_catalog.py`, `*_scheduler.py`, `backups/*.py`. Повод: запрос владельца. Первый
прогон зоны; код на коммите `551825c878e82406f28a1c24abb942a96cd9f13f`.

## Delegation
Один ревьюер (агент general-purpose, только чтение), задание как у зоны `payments`; закрытое ранее — ключ копии с «-», дата плана.
Ревьюер собрал диспетчер в порядке `main.py` и прогнал колбэки. Проверка, воспроизведение и отчёт — мои.

## Verification
| Finding | Zone | Verdict | Claimed | Verified | Reservations | Proof |
|---|---|---|---|---|---|---|
| B2 «Оплатить подписку» в боте не работает: магазин ловит buy_subscription | bot-schedulers | VERIFIED REAL | Medium | High | - | `tests/test_review_bot_routing.py::test_review_the_shop_does_not_take_the_subscription_and_premium_buttons`, ran, fails as expected, passes after fix |
| B3 имя игрока в общем рейтинге без экранирования | bot-schedulers | VERIFIED REAL | Medium | Medium | - | `tests/test_review_escaping.py::test_review_bot_texts_escape_what_people_write`, ran, fails as expected, passes after fix |
| B4 имя обогнавшего в пуше без экранирования | bot-schedulers | VERIFIED REAL | Medium | Medium | - | то же |
| B5 ответ ИИ в боте уходит как HTML | bot-schedulers | VERIFIED REAL | Medium | Medium | только при «<» или «&» в ответе | то же |
| B6 «Адам пишет первым» в HTML с названиями привычек | bot-schedulers | VERIFIED REAL | Medium | Medium | - | то же |
| B7 названия привычек в боте без экранирования | bot-schedulers | VERIFIED REAL | Medium | Medium | ломает только экран самого человека | то же |
| B8 сверка плана 19:00 с «<» в задаче не доходит | bot-schedulers | VERIFIED REAL | Medium | Medium | - | то же |
| B9 разбор недели/месяца от ИИ в HTML | bot-schedulers | VERIFIED REAL | Medium | Medium | - | то же |
| B10 недельные сводки не проверяют тихие часы | bot-schedulers | NEEDS DEEPER REVIEW | Medium | - | пропуск в тихие часы потеряет еженедельный отчёт целиком | `coach.py:139` |
| B11 кнопки привычек не проверяют владельца по id | bot-schedulers | FALSE POSITIVE | Low | - | Telegram принимает только callback_data кнопок своего сообщения | `handlers/habits.py:285` |

## Confirmed defects
Общее для B3–B9: бот создан с `DefaultBotProperties(parse_mode=HTML)` (`main.py:101`), пользовательский текст или ответ модели без
`html.escape`; «<» даёт `can't parse entities` — сообщение не уходит, а «<a href>» в имени — ссылку от имени бота у других людей.

**B2** — `handlers/shop.py:46`, категория «Деньги: выдача покупки». `F.data.startswith("buy_")` ловит `buy_subscription` и
`buy_premium`; роутер магазина подключён раньше оплаты, падает на `int("subscription")`. Человек с закрытым доступом жмёт «Оплатить»
в боте — и ничего. Исправление: `F.data.regexp(r"^buy_\d+$")`. Страж: `test_review_the_shop_still_takes_its_own_items`.

**B3** — `handlers/rating.py:46`: `html.escape` имени. **B4** — `streak_scheduler.py:582`: `html.escape` имени обогнавшего.
**B5** — `handlers/ai.py:342`: `html.escape(answer, quote=False)`. **B6** — `adam_proactive.py:184` (и строка 263):
`parse_mode=None`. **B7** — `handlers/habits.py:112` (и строки 165, 231): `html.escape` названия. **B8** — `coach.py:604`:
`html.escape` текста задачи. **B9** — `coach.py:876` (и строка 924, запасной текст на строке 834): `html.escape` ответа модели.

## Refused or unconfirmed candidates
- B1, бан не действует в боте: out of zone: proposed sweep of access — подтверждено и исправлено как A2 (`docs/audits/2026-11-07-access.md`).
- B10, тихие часы недельных сводок: needs deeper review — пропуск в тихие часы потеряет еженедельный отчёт; нужна отправка по местному
  времени человека (decision pending).
- B11, владелец по id из кнопки: refused, lens claim correctness — Telegram сверяет callback_data с кнопками сообщения; защита в глубину
  предложена в hand-off.
- Повторные рассылки: refused, lens existing mitigation — `claim_notification` до отправки, `release` при ошибке.
- Резервные копии: refused, lens claim correctness — `.env*` и базы не попадают в архив кода, шифрование при ключе.

## Coverage
Целиком: `main.py`, `coach.py`, `adam_proactive.py`, хендлеры `habits`, `ai`, `helpers`, `daily`, `plan_bot`, `goals`, `calendar`, `rating`,
`start`, `fallback`, `shop`, `subscription_scheduler.py`, `partner_scheduler.py`, `error_alert_scheduler.py`, `backups/*.py`. Частично:
`streak_scheduler.py`, `handlers/admin.py`, `handlers/onboarding.py`, `handlers/community.py`, `handlers/profile.py`. Не читались:
`morning_ping.py`, `goal_feedback.py`, `onboarding_auto.py`, `referral_mailing.py`, `abuse_alerts.py`. Линзы: exploitability, claim
correctness, existing mitigation.

## Hand-off
- Исправления B2–B9 внесены в ветку с тестами.
- Защита в глубину: `user_id` в `WHERE` у `complete_habit`/`delete_habit`/`edit_habit` (`handlers/habits.py:285`).
- Карточка человека в админке без экранирования ответов анкеты (`handlers/admin.py:669`) — предложено в следующий прогон зоны.
- Не прочитанные модули (`morning_ping.py`, `referral_mailing.py` и др.) — следующий прогон зоны.
- B10: решение владельца — отправлять сводки по местному времени.
