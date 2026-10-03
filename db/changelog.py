"""
«Что нового» — короткий журнал обновлений, который видит пользователь
внутри Mini App (одноразовое модальное окно при следующем заходе после
того, как админ добавил запись). См. схему в db/core.py
(changelog_entries, users.last_seen_changelog_id) и роуты в
webapp/webapp_server.py (/api/changelog/*).

"Просмотрено" отслеживается по ID записи, не по времени — секундная
точность CURRENT_TIMESTAMP в SQLite дала бы гонку между добавлением
записи и отметкой "просмотрено" в ту же секунду.
"""
from .core import connect

DEFAULT_LIMIT = 5


def add_changelog_entry(title, body):
    """Добавить запись — вызывается вручную админом (/changelog в боте,
    см. handlers/admin.py), не автоматически на каждый git-коммит: не
    всякое техническое изменение (рефакторинг, тесты, инфраструктура)
    интересно показывать пользователю."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO changelog_entries(title, body) VALUES (?, ?)",
        (title, body),
    )
    entry_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return entry_id


# Записи, которые публикуются вместе с релизом (а не вручную через /changelog).
# publish_release_notes() вызывается при старте сервера (main.py) и добавляет
# только те, чьего заголовка ещё нет в журнале, — повторные запуски не плодят
# дублей, а уже прочитавшие пользователи увидят запись один раз, как любую
# другую. Новая запись = новый заголовок.
RELEASE_NOTES = (
    {
        "title": "Друзья, подписки и подарки",
        "body": (
            "👥 Подписки — как в Duolingo. Подпишись на любого игрока (например, на топ рейтинга): "
            "тап по игроку в лидерборде открывает его профиль с графиком недели. Подпишется в ответ — "
            "вы друзья и видите расширенную статистику друг друга.\n"
            "👋 «Напомнить друзьям» — отметил привычку, и можно подтолкнуть тех, кто ещё не начал день.\n"
            "🎁 Подарки друзьям — алмазы, рамки и аватар за твои Adam Coin (очки уровня не дарятся).\n"
            "🗓 Задания месяца вместо «идеального месяца»: 60 ежедневных заданий и сундуки на 15, 30, 45 и 60.\n"
            "🔒 Приватность: статистику можно скрыть от подписчиков, а названия привычек и цели видят "
            "только друзья — и только если ты сам это включишь в настройках."
        ),
    },
)


def publish_release_notes():
    """Добавляет в журнал записи RELEASE_NOTES, которых там ещё нет (по
    заголовку). Возвращает список добавленных заголовков."""
    added = []
    conn = connect()
    try:
        for note in RELEASE_NOTES:
            exists = conn.execute(
                "SELECT 1 FROM changelog_entries WHERE title=?", (note["title"],)
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO changelog_entries(title, body) VALUES (?, ?)",
                (note["title"], note["body"]),
            )
            added.append(note["title"])
        conn.commit()
    finally:
        conn.close()
    return added


def get_unseen_changelog_entries(telegram_id, limit=DEFAULT_LIMIT):
    """last_seen_changelog_id=0 (ещё ни разу не отмечал просмотренным)
    значит "показать самые свежие записи, какие есть" — не бесконечный
    бэклог, а последние `limit`, поэтому свежерегистрирующийся пользователь
    не тонет в истории изменений за весь прошлый год."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_seen_changelog_id FROM users WHERE telegram_id=?",
        (telegram_id,),
    )
    row = cursor.fetchone()
    last_seen_id = row["last_seen_changelog_id"] if row else 0

    cursor.execute(
        "SELECT id, title, body, created_at FROM changelog_entries "
        "WHERE id > ? ORDER BY id DESC LIMIT ?",
        (last_seen_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    rows.reverse()  # старые -> новые, как в исходном порядке публикации
    conn.close()
    return rows


def mark_changelog_seen(telegram_id):
    """Отмечает просмотренными ВСЕ записи, существующие на данный момент
    (не только те, что были в последнем ответе get_unseen_changelog_entries —
    если админ успел добавить что-то ровно между запросами, это не должно
    оставить пользователя в состоянии "1 непрочитанная" навсегда)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_seen_changelog_id="
        "(SELECT COALESCE(MAX(id), 0) FROM changelog_entries) WHERE telegram_id=?",
        (telegram_id,),
    )
    conn.commit()
    conn.close()
