from datetime import datetime

from .core import connect


def save_google_tokens(user_id, access_token, refresh_token, token_expiry):
    """
    Сохраняет токены после первого подключения.
    refresh_token Google присылает только при первом согласии пользователя,
    поэтому при повторном подключении старый refresh_token не затираем пустым.
    """
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO google_tokens(user_id, access_token, refresh_token, token_expiry, connected_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, google_tokens.refresh_token),
            token_expiry = excluded.token_expiry,
            connected_at = excluded.connected_at
    """, (user_id, access_token, refresh_token, token_expiry, str(datetime.utcnow())))

    conn.commit()
    conn.close()


def get_google_tokens(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM google_tokens WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def update_google_access_token(user_id, access_token, token_expiry):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE google_tokens SET access_token=?, token_expiry=? WHERE user_id=?
    """, (access_token, token_expiry, user_id))
    conn.commit()
    conn.close()


def save_google_event_id(user_id, event_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE google_tokens SET calendar_event_id=? WHERE user_id=?",
        (event_id, user_id)
    )
    conn.commit()
    conn.close()


def delete_google_tokens(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM google_tokens WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def has_google_calendar(user_id):
    return get_google_tokens(user_id) is not None
