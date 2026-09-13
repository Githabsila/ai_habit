# Quest button motion fix — 2026-09-13

- Fixed the moving quest-button border being hidden behind the button background in Telegram WebView.
- The bright conic-gradient segment now renders above the button background and is masked in the center, so a visible light trail continuously travels around the yellow border.
- Kept the soft outer pulse, but removed the scale transform from that pulse so touch/hover rules cannot suppress the attention animation.
- Bumped the stylesheet cache version to `QUESTGLOW_V19`.
- `prefers-reduced-motion` still disables the animation for accessibility.
