"""
Roadmap #28 — экспортируемый PDF-отчёт о прогрессе с графиком.

Кириллица: reportlab по умолчанию поддерживает только Base-14 латинские
шрифты (Helvetica и т.п.) — русский текст ими не рисуется. Используем
бесплатный Noto Sans (SIL Open Font License, см. assets/fonts/NotoSans-OFL.txt)
как встраиваемый TTF. Это переменный шрифт (variable font) с одной осью
инстанса по умолчанию — reportlab не умеет выбирать отдельные насыщенности
из variable-шрифта, поэтому вся типографика построена на размере/цвете,
а не на bold/italic вариантах.
"""
import io
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from db import (
    get_user, get_progress, get_habits, get_weekly_habit_breakdown,
    get_progress_comparison, get_achievements, get_league_tier,
)

FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "fonts", "NotoSans-VF.ttf",
)
FONT_NAME = "NotoSansADAM"
_font_registered = False


def _ensure_font():
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
        _font_registered = True


VIOLET = colors.HexColor("#6847DC")
GOLD = colors.HexColor("#F0B429")
DARK = colors.HexColor("#1A1626")
MUTED = colors.HexColor("#666")
GREEN = colors.HexColor("#22C55E")
RED = colors.HexColor("#EF4444")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


def generate_progress_pdf(user_id):
    """Возвращает bytes готового PDF-отчёта, или None если пользователь
    не найден."""
    _ensure_font()

    user = get_user(user_id)
    if user is None:
        return None
    progress = get_progress(user_id) or {}
    habits = get_habits(user_id)
    breakdown = get_weekly_habit_breakdown(user_id)
    comparison = get_progress_comparison(user_id)
    achievements = get_achievements(user_id)
    league = get_league_tier(user["total_xp"] if "total_xp" in user.keys() else user["xp"])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = PAGE_H - MARGIN

    def line(text, size=11, color=DARK, dy=6 * mm, x=MARGIN):
        nonlocal y
        c.setFont(FONT_NAME, size)
        c.setFillColor(color)
        c.drawString(x, y, text)
        y -= dy

    # ---- Заголовок ----
    c.setFillColor(VIOLET)
    c.rect(0, PAGE_H - 30 * mm, PAGE_W, 30 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont(FONT_NAME, 20)
    c.drawString(MARGIN, PAGE_H - 15 * mm, "Project ADAM — отчёт о прогрессе")
    c.setFont(FONT_NAME, 11)
    c.drawString(MARGIN, PAGE_H - 23 * mm, user["first_name"] or "Игрок")
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 23 * mm, datetime.now().strftime("%d.%m.%Y"))
    y = PAGE_H - 40 * mm

    # ---- Ключевые метрики ----
    stats = [
        ("Уровень", str(progress.get("level", 1))),
        ("Серия дней", str(progress.get("streak", 0))),
        ("Adam Coin", str(progress.get("xp", 0))),
        ("Лига", league),
        ("Наград получено", str(len(achievements))),
    ]
    box_w = (PAGE_W - 2 * MARGIN) / len(stats)
    for i, (label, value) in enumerate(stats):
        bx = MARGIN + i * box_w
        c.setFillColor(colors.HexColor("#F3F1FA"))
        c.roundRect(bx, y - 20 * mm, box_w - 3 * mm, 18 * mm, 3, fill=1, stroke=0)
        c.setFillColor(VIOLET)
        c.setFont(FONT_NAME, 13)
        c.drawCentredString(bx + (box_w - 3 * mm) / 2, y - 9 * mm, value)
        c.setFillColor(MUTED)
        c.setFont(FONT_NAME, 7.5)
        c.drawCentredString(bx + (box_w - 3 * mm) / 2, y - 16 * mm, label)
    y -= 28 * mm

    # ---- Сравнение с месяцем назад ----
    if comparison and comparison.get("trend") != "not_enough_data":
        delta = comparison["delta"]
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        color = GREEN if delta > 0 else (RED if delta < 0 else MUTED)
        line(
            f"За последнюю неделю: {comparison['current_rate']}% выполнения "
            f"({arrow} {abs(delta)}% к предыдущему месяцу)",
            size=10.5, color=color, dy=9 * mm,
        )

    # ---- Разбор по привычкам (7 дней) + мини-бар-чарт ----
    line("Привычки за последние 7 дней", size=13, color=DARK, dy=8 * mm)
    if not breakdown:
        line("Пока недостаточно данных.", size=10, color=MUTED)
    else:
        chart_x = MARGIN
        chart_w = PAGE_W - 2 * MARGIN - 40 * mm
        row_h = 9 * mm
        for row in breakdown[:10]:
            total = row["total"] or 1
            rate = round(100 * row["done"] / total)
            title = row["habit_title"] or "?"
            if len(title) > 28:
                title = title[:27] + "…"
            c.setFont(FONT_NAME, 9)
            c.setFillColor(DARK)
            c.drawString(chart_x, y - 4, title)
            bar_y = y - row_h + 2 * mm
            c.setFillColor(colors.HexColor("#E8E4F5"))
            c.roundRect(chart_x + 45 * mm, bar_y, chart_w - 45 * mm, 4 * mm, 2, fill=1, stroke=0)
            bar_color = GREEN if rate >= 70 else (GOLD if rate >= 40 else RED)
            fill_w = max(2, (chart_w - 45 * mm) * rate / 100)
            c.setFillColor(bar_color)
            c.roundRect(chart_x + 45 * mm, bar_y, fill_w, 4 * mm, 2, fill=1, stroke=0)
            c.setFont(FONT_NAME, 8)
            c.setFillColor(MUTED)
            c.drawRightString(PAGE_W - MARGIN, y - 4, f"{rate}%  ({row['done']}/{total})")
            y -= row_h
            if y < MARGIN + 20 * mm:
                c.showPage()
                y = PAGE_H - MARGIN

    # ---- Подвал ----
    c.setFont(FONT_NAME, 7.5)
    c.setFillColor(MUTED)
    c.drawCentredString(PAGE_W / 2, MARGIN / 2, "Сформировано автоматически в Project ADAM")

    c.save()
    return buf.getvalue()
