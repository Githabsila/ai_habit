# Реестр прогонов: какие зоны есть, когда проверялись, сколько нашлось

Колонку «Last run» выводит `scripts/sweep_status.py` из имён отчётов; остальное пишется руками.

## Candidates

| Zone | Argument | Last run | Yield |
|---|---|---|---|
| `payments` | Звёзды, Tribute, внешние заказы, возвраты, автопродление — реальные деньги с 07.11. | 2026-11-07 | 4/6 |
| `access` | Подпись initData, бан, анкета, возраст, триал/гейт подписки, закрытый канал. | 2026-11-07 | 5/7 |
| `miniapp-api` | `webapp_server.py` (~3600 строк) и `routes_*.py`, админка. | 2026-11-07 | 4/6 |
| `bot-schedulers` | Хендлеры бота, фоновые рассылки, резервные копии. | 2026-11-07 | 8/10 |

## Proposed, not yet run

| Zone | Argument | Premise to re-verify before starting |
|---|---|---|
| `bot-schedulers-2` | Не прочитанные модули: `morning_ping.py`, `goal_feedback.py`, `onboarding_auto.py`, `referral_mailing.py`, `abuse_alerts.py`; карточка в админке без экранирования. | Модули ещё подключены в `main.py` |

## Candidates verified and refused

- Кнопки бота с id привычки без проверки владельца — Telegram принимает только callback_data кнопок своего сообщения (2026-11-07-bot-schedulers, B11).
- 500 вместо 400 на теле запроса не того типа — вред только отправителю (2026-11-07-miniapp-api, M6).
- Повторные награды за сундуки и квесты — PRIMARY KEY, условные UPDATE, синхронные обработчики (2026-11-07-miniapp-api).
