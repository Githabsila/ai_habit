"""
Roadmap #21 (голосовые сообщения AI-коучу) и #47 (озвучка ответов) —
db-слой (get_ai_message_text) и graceful-degradation multi_agent.py, когда
клиент OpenAI не настроен. Реальные вызовы Whisper/TTS не тестируем
(нужен живой API-ключ и деньги за вызов) — только контракт и защиту.
"""
from db import add_user, add_ai_message, get_ai_message_text


def test_get_ai_message_text_returns_content(uid):
    add_user(uid, "u", "Test")
    message_id = add_ai_message(uid, "assistant", "Привет, это ответ AI")
    assert get_ai_message_text(message_id, uid) == "Привет, это ответ AI"


def test_get_ai_message_text_none_for_wrong_owner(uid):
    add_user(uid, "u", "Test")
    other = uid + 90_000_000
    add_user(other, "u2", "Other")
    message_id = add_ai_message(uid, "assistant", "Секрет")
    assert get_ai_message_text(message_id, other) is None


def test_get_ai_message_text_none_for_unknown_id(uid):
    add_user(uid, "u", "Test")
    assert get_ai_message_text(999999999, uid) is None


async def test_transcribe_voice_none_without_client(monkeypatch):
    import multi_agent
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: None)
    result = await multi_agent.transcribe_voice(b"fake audio bytes")
    assert result is None


async def test_generate_speech_none_without_client(monkeypatch):
    import multi_agent
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: None)
    result = await multi_agent.generate_speech("Привет")
    assert result is None


async def test_generate_speech_none_for_empty_text(monkeypatch):
    import multi_agent
    # Даже с "настроенным" клиентом — пустой текст не должен пытаться
    # дойти до API вообще.
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: object())
    result = await multi_agent.generate_speech("   ")
    assert result is None
