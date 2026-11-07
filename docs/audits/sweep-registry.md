# Реестр прогонов: какие зоны есть, когда проверялись, сколько нашлось

Колонку «Last run» выводит `scripts/sweep_status.py` из имён отчётов; остальное пишется руками.

## Candidates

| Zone | Argument | Last run | Yield |
|---|---|---|---|
| `payments` | Звёзды, Tribute, внешние заказы, возвраты, автопродление — реальные деньги с 07.11. | - | - |
| `access` | Подпись initData, бан, анкета, возраст, триал/гейт подписки, закрытый канал. | - | - |
| `miniapp-api` | `webapp_server.py` (~3600 строк) и `routes_*.py`, админка. | - | - |
| `bot-schedulers` | Хендлеры бота, фоновые рассылки, резервные копии. | - | - |

## Proposed, not yet run

| Zone | Argument | Premise to re-verify before starting |
|---|---|---|

## Candidates verified and refused
