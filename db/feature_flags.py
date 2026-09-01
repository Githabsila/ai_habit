"""
Roadmap #41 — feature flags: инфраструктура для постепенного раскатывания
новых фич (A/B, поэтапный rollout, аварийный выключатель) без деплоя
нового кода — сам код фичи проверяет is_feature_enabled() и просто ветвится,
включение/выключение и % аудитории меняются через админку на лету.

Пока не привязана насильно ни к одной существующей фиче (это была бы
искусственная, никому не нужная ветка) — инфраструктура сама по себе, до
первого реального применения (следующая крупная фича с осторожным
раскатыванием — постепенно, не всем сразу).
"""
import hashlib

from .core import connect


def is_feature_enabled(key, user_id=None):
    """True, если флаг включён И (если задан user_id) пользователь попадает
    в rollout_pct — детерминированно по hash(key:user_id), так что один и
    тот же пользователь стабильно либо всегда видит фичу, либо никогда,
    а не подбрасывает монетку при каждом вызове."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT enabled, rollout_pct FROM feature_flags WHERE key=?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row is None or not row["enabled"]:
        return False
    pct = row["rollout_pct"] if row["rollout_pct"] is not None else 100
    if pct >= 100 or user_id is None:
        return bool(row["enabled"])
    if pct <= 0:
        return False
    bucket = int(hashlib.md5(f"{key}:{user_id}".encode()).hexdigest(), 16) % 100
    return bucket < pct


def get_all_flags():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT key, enabled, rollout_pct, description FROM feature_flags ORDER BY key")
    rows = cursor.fetchall()
    conn.close()
    return [
        {"key": r["key"], "enabled": bool(r["enabled"]), "rollout_pct": r["rollout_pct"], "description": r["description"]}
        for r in rows
    ]


def set_feature_flag(key, enabled, rollout_pct=100, description=None):
    key = (key or "").strip()
    if not key or len(key) > 60:
        return False
    rollout_pct = max(0, min(100, int(rollout_pct or 0)))
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO feature_flags(key, enabled, rollout_pct, description) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled, rollout_pct=excluded.rollout_pct, "
        "description=COALESCE(excluded.description, feature_flags.description)",
        (key, 1 if enabled else 0, rollout_pct, description),
    )
    conn.commit()
    conn.close()
    return True


def delete_feature_flag(key):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM feature_flags WHERE key=?", (key,))
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed
