def test_ai_cache_key_changes_when_dynamic_context_changes():
    import hashlib

    message = "Как дела с моим ударным режимом?"
    style = "neutral"
    ctx0 = "Серия дней подряд: 0"
    ctx7 = "Серия дней подряд: 7"

    def key(ctx):
        base = " ".join(message.lower().strip().split())
        import hashlib as h
        base_key = h.md5(f"{base}|{style}".encode("utf-8")).hexdigest()
        fingerprint = hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:16]
        return f"123:{base_key}:{fingerprint}"

    assert key(ctx0) != key(ctx7)
