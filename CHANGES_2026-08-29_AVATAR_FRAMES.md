# Avatar + Frames update — 2026-08-29

## Added
- Upload custom avatar from the Profile tab (JPG/PNG/WEBP, up to 5 MB).
- Uploaded avatar is stored persistently under the Railway volume and shown in Profile and Rating.
- Existing Adam Coin frames Neon and Gold are now fully functional: purchase, ownership and equip.
- Two permanent Streak Mode frames:
  - 14 days — `streak_14` / «Неоновый импульс»
  - 30 days — `streak_30` / «Золотой характер»
- Existing users with a current streak >= 14/30 are backfilled into the corresponding reward unlocks.
- Premium paid frame Double Gold for Telegram Stars (299 ⭐), with double gold border and glow.
- Telegram Stars invoice endpoint + successful-payment activation.
- Frame picker in Profile for default, shop frames, streak rewards and paid Double Gold.
- Rating now uses the user's uploaded avatar and equipped frame.

## Notes
- Custom avatars are served from `/media/avatars/<telegram_id>.jpg`.
- Stars checkout requires the Mini App to be opened inside Telegram.
- The paid frame is activated after Telegram confirms the successful payment.
