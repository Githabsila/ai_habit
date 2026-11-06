"""
Куда писать по оплате и возвратам — одна точка для бота (/paysupport, /terms, /refund), соглашения (/terms в браузере) и экранов.

Решение 05.11: возвраты пока только через администратора — в Telegram (SUPPORT_CONTACT) или Instagram (SUPPORT_INSTAGRAM); почта остаётся
запасным каналом. Возврат Stars делает администратор командой /refund (Telegram возвращает звёзды покупателю).
"""
import html


def _instagram_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    return value if value.startswith("http") else f"https://instagram.com/{value.lstrip('@')}"


def contacts():
    import config
    return {
        "telegram": (config.SUPPORT_CONTACT or "").strip(),
        "instagram": _instagram_url(getattr(config, "SUPPORT_INSTAGRAM", "")),
        "email": (config.SUPPORT_EMAIL or "").strip(),
    }


def contacts_text(bold=True):
    """«@a, @b в Telegram, Instagram https://… или почта x@y» — для сообщений бота (HTML)."""
    c = contacts()
    wrap = (lambda t: f"<b>{html.escape(t)}</b>") if bold else html.escape
    parts = []
    if c["telegram"]:
        parts.append(f"{wrap(c['telegram'])} в Telegram")
    if c["instagram"]:
        parts.append(f"Instagram {html.escape(c['instagram'])}")
    main = ", ".join(parts)
    if c["email"]:
        return f"{main} или почта {wrap(c['email'])}" if main else wrap(c["email"])
    return main


def contacts_html():
    """То же для страницы соглашения: ссылки."""
    c = contacts()
    parts = []
    if c["telegram"]:
        links = [f'<a href="https://t.me/{html.escape(n.strip().lstrip("@"))}">{html.escape(n.strip())}</a>' for n in c["telegram"].split(",") if n.strip()]
        parts.append(", ".join(links) + " в Telegram")
    if c["instagram"]:
        parts.append(f'<a href="{html.escape(c["instagram"])}">Instagram</a>')
    if c["email"]:
        parts.append(f'почта <a href="mailto:{html.escape(c["email"])}">{html.escape(c["email"])}</a>')
    return ", ".join(parts)
