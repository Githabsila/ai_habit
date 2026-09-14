# ADAM — Early Test Experience v23

Implemented the first early-user product loop:

- New application notifications are sent to every admin immediately after survey submission.
- Admin can approve/reject an application directly from the Telegram notification; no admin panel is required.
- Added product-event telemetry for the first-session funnel and key actions.
- Added a deliberately short first-run tour plus contextual hints shown progressively during the first 48 hours.
- First real completion (habit or plan task) triggers one separate ADAM Telegram message with a direct chat button and a tomorrow/continuation call-to-action.
- Added a structured bug-report flow with severity, expected result and screenshot upload.
- Bug reports automatically attach the user's recent 10-minute product events and client context.
- Admin Telegram now has recent-bug views and status buttons; web admin API exposes bug list/detail/status endpoints.
- Added atomic first-win state fields and database indexes for support/telemetry.
- Existing performance telemetry and lazy secondary loading remain intact; no full-screen render was added to completion actions.

## Validation

- Python syntax compilation: passed.
- JavaScript `node --check`: passed.
- Database migration + event/bug smoke test: passed.
- Full pytest collection could not run in this environment because runtime dependencies (`aiogram`, `openai`) are not installed in the execution container.
