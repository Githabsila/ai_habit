# ADAM Mini App bootstrap fix — 2026-09-13

## What was fixed
- Telegram initData is read from `WebApp.initData` and raw `tgWebAppData` as a compatibility fallback.
- The browser sends signed initData in BOTH `Authorization: tma ...` and `X-Telegram-Init-Data` headers.
- The server accepts both headers and GET `tgWebAppData`, but always validates the Telegram HMAC before trusting the user.
- Cold launches wait up to 3 seconds for Telegram's WebApp bridge to populate initData.
- `/api/bootstrap` gets 45 seconds per attempt and up to 3 attempts for transient 5xx/timeout failures.
- API responses are marked `no-store` so a WebView/proxy cannot reuse a personalized bootstrap response.
- Invalid Telegram auth is reported explicitly instead of being shown as a generic network failure.
- `auth_date` parsing is hardened against malformed values and small future clock skew.

## Railway
Set these variables in Railway (do NOT commit `.env`):
- `BOT_TOKEN` — the token of the SAME bot that opens ADAM.
- `WEBAPP_URL` — the current Railway public HTTPS domain of this service.
- Other existing AI/admin variables as before.

After deployment, restart the service so `set_chat_menu_button()` writes the current `WEBAPP_URL` into Telegram again.

If the app still reports `invalid_init_data` after this build, the remaining issue is outside the bootstrap code: the client is not supplying signed Telegram initData, or the Mini App is being opened from a client/link that does not provide it. Do not replace this with `initDataUnsafe` — that would make authentication insecure.
