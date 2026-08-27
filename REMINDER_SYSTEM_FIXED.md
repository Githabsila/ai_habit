# Habit & Daily Reminder System — fixed

## Schedule
- 06:00 local user time: one morning ADAM message.
- 10:00 local user time: one combined habit checkpoint with all unfinished habits.
- 19:00 local user time: one combined daily-plan checkpoint counting BOTH:
  - the main task;
  - all secondary tasks.
- 23:00 local user time: first final habit warning.
- 23:30 local user time: final habit warning + countdown.
- No separate 12:00 habit reminder.
- No 20:00/22:00 habit reminder.
- No new habit reminder at 00:00.

## Multi-bot delivery
One-time notification claims are now scoped by the Telegram bot token hash.
If two different bots share the same database, one bot claiming a 06:00/10:00/etc.
notification no longer suppresses the other bot's notification.

## Anti-duplication
The generic per-task reminder is muted from 18:00 through 19:59 so it cannot
collide with the 19:00 combined plan message.

## Grammar
The 19:00 formatter uses correct Russian singular/plural forms, including
"1 задача", "2 задачи", "5 задач" and "1 из 2 задач".
