"""Кому можно слать рассылки бота.

Утренние сообщения, контрольные точки, «Привет! Я Adam…» и прочие напоминания уходили людям, которые
только нажали /start и ещё отвечают на анкету («закрытый проект»): доброе утро прилетало прямо между
вопросами. Рассылки получают те, у кого анкета пройдена (access_status='approved'), и администраторы
(их анкета не касается — см. handlers/start.py). Забаненные тоже не получают.
"""


def push_allowed_sql(alias="users"):
    """SQL-условие для WHERE: «этому пользователю можно слать рассылки»."""
    try:
        from config import ADMIN_IDS
        admin_ids = [int(i) for i in ADMIN_IDS]
    except Exception:
        admin_ids = []
    admins = f" OR {alias}.telegram_id IN ({','.join(str(i) for i in admin_ids)})" if admin_ids else ""
    return (
        f"((COALESCE({alias}.access_status, 'approved') = 'approved'{admins}) "
        f"AND COALESCE({alias}.banned, 0) = 0)"
    )
