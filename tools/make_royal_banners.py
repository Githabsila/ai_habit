"""
Рисует баннеры для оформления «Royal Gold» в чате с ботом (bot_style.py): webapp/static/assets/bot/<вид>.png.

Запускается один раз вручную (нужны Pillow и шрифт Georgia Bold — на Windows он есть в C:\\Windows\\Fonts), готовые картинки лежат в репозитории,
поэтому на сервере ни шрифт, ни этот скрипт не нужны. Логотип берётся из webapp/static/assets/adam-avatar.webp и перекрашивается из синего неона в золото.

    python tools/make_royal_banners.py
"""
import colorsys
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "webapp", "static", "assets", "bot")
AVATAR = os.path.join(ROOT, "webapp", "static", "assets", "adam-avatar.webp")
FONT_CANDIDATES = [r"C:\Windows\Fonts\georgiab.ttf", r"C:\Windows\Fonts\georgia.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"]

W, H = 1280, 640
GOLD_LIGHT = (246, 218, 150)
GOLD = (222, 176, 84)
GOLD_DEEP = (150, 108, 40)
CREAM = (248, 238, 214)
MUTED = (178, 160, 122)

# Что написано на каждом баннере: (малая надпись, заголовок, подпись). Тексты без родов — как и во всём приложении.
BANNERS = {
    "welcome": ("PROJECT ADAM", "Добро пожаловать", "Привычки, цели и серия — всё в одном месте"),
    "trial": ("ПРОБНЫЙ ПЕРИОД", "Три дня Pro", "Все функции открыты без ограничений"),
    "paid": ("ADAM PRO", "Pro открыт", "Спасибо, что с нами — все функции твои"),
}


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("Не найден шрифт с кириллицей (Georgia Bold или DejaVu Serif Bold)")


def fit_font(draw, text, max_w, start):
    """Шрифт самого большого размера (не больше start), при котором текст влезает в max_w."""
    size = start
    while size > 24 and draw.textlength(text, font=font(size)) > max_w:
        size -= 2
    return font(size)


def wrap(draw, text, fnt, max_w):
    lines, line = [], ""
    for word in text.split():
        probe = (line + " " + word).strip()
        if line and draw.textlength(probe, font=fnt) > max_w:
            lines.append(line)
            line = word
        else:
            line = probe
    if line:
        lines.append(line)
    return lines


def gold_avatar(size):
    """Логотип ADAM: синий неон → золото (оттенок сдвигается, белое остаётся белым), обрезан кругом."""
    src = Image.open(AVATAR).convert("RGB").resize((size, size), Image.LANCZOS)
    px = src.load()
    for y in range(size):
        for x in range(size):
            r, g, b = px[x, y]
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s > 0.12 and 0.45 < h < 0.75:                                # синие и голубые пиксели
                h = 0.115 + (h - 0.6) * 0.1
                s = min(1.0, s * 0.9)
                v = min(1.0, v * 1.04)
                r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))
                px[x, y] = (r, g, b)
    mask = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 3 - 1, size * 3 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    return out


def background():
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        base = (int(12 + 10 * t), int(9 + 7 * t), int(5 + 3 * t))
        for x in range(W):
            px[x, y] = base
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((40, 40, 700, 600), fill=(84, 58, 18))
    gd.ellipse((820, 360, 1300, 700), fill=(46, 32, 10))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return ImageChops.add(img, glow)


def gold_text(img, xy, text, fnt, anchor="la"):
    """Текст с вертикальным золотым градиентом."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).text(xy, text, font=fnt, fill=255, anchor=anchor)
    bbox = mask.getbbox()
    if not bbox:
        return
    top, bottom = bbox[1], bbox[3]
    grad = Image.new("RGB", img.size)
    gp = grad.load()
    for y in range(top, bottom + 1):
        t = (y - top) / max(1, bottom - top)
        col = tuple(int(GOLD_LIGHT[i] * (1 - t) + GOLD_DEEP[i] * t) for i in range(3))
        for x in range(bbox[0], bbox[2] + 1):
            gp[x, y] = col
    img.paste(grad, (0, 0), mask)


def frame(img):
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((26, 26, W - 27, H - 27), radius=28, outline=GOLD_DEEP, width=2)
    d.rounded_rectangle((40, 40, W - 41, H - 41), radius=20, outline=GOLD, width=1)
    for cx, cy in ((40, 40), (W - 41, 40), (40, H - 41), (W - 41, H - 41)):         # ромбики по углам рамки
        d.polygon([(cx, cy - 9), (cx + 9, cy), (cx, cy + 9), (cx - 9, cy)], fill=GOLD_LIGHT)


def draw_banner(kind):
    small, title, sub = BANNERS[kind]
    img = background()
    frame(img)
    d = ImageDraw.Draw(img)
    size = 400
    cx, cy = 300, H // 2
    ring = Image.new("RGBA", (size + 60, size + 60), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((0, 0, size + 59, size + 59), outline=GOLD + (255,), width=3)
    rd.ellipse((14, 14, size + 45, size + 45), outline=GOLD_DEEP + (255,), width=1)
    img.paste(ring, (cx - (size + 60) // 2, cy - (size + 60) // 2), ring)
    avatar = gold_avatar(size)
    img.paste(avatar, (cx - size // 2, cy - size // 2), avatar)
    d = ImageDraw.Draw(img)
    tx = 560
    max_w = W - 70 - tx                                                         # справа оставляем поле до рамки
    d.text((tx, 196), "  ".join(small), font=font(26), fill=MUTED, anchor="ls")
    gold_text(img, (tx, 300), title, fit_font(d, title, max_w, 76), anchor="ls")
    d = ImageDraw.Draw(img)
    d.line((tx, 332, tx + 150, 332), fill=GOLD, width=3)
    d.polygon([(tx + 168, 332 - 7), (tx + 175, 332), (tx + 168, 332 + 7), (tx + 161, 332)], fill=GOLD_LIGHT)
    sub_font = font(30)
    for i, line in enumerate(wrap(d, sub, sub_font, max_w)):
        d.text((tx, 388 + i * 42), line, font=sub_font, fill=CREAM, anchor="ls")
    path = os.path.join(OUT_DIR, kind + ".png")
    img.save(path, optimize=True)
    return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for kind in BANNERS:
        print(draw_banner(kind))


if __name__ == "__main__":
    sys.exit(main())
