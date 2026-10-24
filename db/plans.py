"""
Тарифы ADAM (решение владельца, 24.10.2026): ОДНА версия — Pro.

- Первые 3 дня бесплатно, и в триале открыты ВСЕ функции Pro (чтобы человек понял приложение и лучшие функции).
- С 4-го дня — оплата подписки Pro (db/subscription.py, Telegram Stars), чтобы продолжить; бесплатной версии нет.
- Всё, что раньше давал отдельный Premium (еженедельный разбор цели, метка PRO в чате…), теперь входит в Pro для каждого, у кого есть доступ.
- В будущем возможен более дорогой тариф Premium: пока PREMIUM_TIER_ENABLED=false (config.py) его нельзя купить, а флаг users.premium остаётся
  как есть. Новые «только для Premium» функции прячутся за has_premium_features(), всё остальное — за has_pro_features().
"""
from .users import get_user, has_premium

PLAN_PRO = "pro"
PLAN_PREMIUM = "premium"


def has_pro_features(user_id) -> bool:
    """Все функции Pro: у каждого, кто сейчас может пользоваться приложением, — в триале, с оплаченной подпиской или с доступом от админа.
    Человек, которого закрыл гейт после триала, Pro-функций не получает (и пользоваться приложением не может)."""
    if not get_user(user_id):
        return False
    from config import ADMIN_IDS
    if user_id in ADMIN_IDS:
        return True          # админ пользуется приложением всегда (webapp/auth_helpers.authenticate его не проверяет), даже на тестовом аккаунте с закончившимся триалом
    from .subscription import bot_access_allowed
    return bool(bot_access_allowed(user_id))


def has_premium_features(user_id) -> bool:
    """Будущий более дорогой тариф Premium. Пока тариф не запущен (PREMIUM_TIER_ENABLED=false), не даёт никому ничего сверх Pro."""
    from config import PREMIUM_TIER_ENABLED
    return bool(PREMIUM_TIER_ENABLED and has_premium(user_id))


def user_plan(user_id):
    """'premium' / 'pro' / None (доступа нет — гейт закрыт после триала)."""
    if has_premium_features(user_id):
        return PLAN_PREMIUM
    return PLAN_PRO if has_pro_features(user_id) else None
