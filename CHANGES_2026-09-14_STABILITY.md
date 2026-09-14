# Project ADAM — Stability pass 2026-09-14

- Fixed the 10:00/12:00 habit checkpoint overlap for users who are genuinely inactive: when streak re-engagement is enabled and the user has not completed anything today, the generic checkpoint yields to the contextual re-engagement flow (10:00 / 16:00 / 21:00).
- Fixed double-tap expansion of normal habit titles: the existing double-tap handler now actually applies the expanded-text class to regular habit rows, so long names can be read in full.
- Removed the long XP progress-line transition that caused visible catch-up/lag during HUD rerenders.
- Stabilized the level ring geometry by keeping the ring wrapper and SVG on a fixed square coordinate system and preventing flexbox shrink.
- Reserved a top band for Telegram native close/menu chrome in Telegram WebApp mode so the app UI starts below those controls, including wide Android/tablet viewports.
- Bumped static asset version to V24 to reduce stale Telegram WebView assets after deployment.

No broad visual redesign was made in this pass; changes are intentionally limited to stability and the requested UX fixes.
