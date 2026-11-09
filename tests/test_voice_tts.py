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


def _voice_message(uid_, duration):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    bot = SimpleNamespace(send_chat_action=AsyncMock(), get_file=AsyncMock(return_value=SimpleNamespace(file_path="x")),
                          download_file=AsyncMock(return_value=SimpleNamespace(read=lambda: b"audio")))
    return SimpleNamespace(from_user=SimpleNamespace(id=uid_), chat=SimpleNamespace(id=uid_), text=None,
                           voice=SimpleNamespace(file_id="f", duration=duration), bot=bot, answer=AsyncMock())


async def test_review_a_long_or_unpaid_voice_is_refused_before_paid_transcription(uid, monkeypatch):
    """Прогон 09.11: Whisper (платный, поминутно) вызывался до проверки длины и лимита — голосовые часами тратили деньги даже без лимита."""
    from unittest.mock import AsyncMock
    import multi_agent
    from handlers import ai
    add_user(uid, "u", "Test")
    whisper = AsyncMock(return_value="привет")
    monkeypatch.setattr(multi_agent, "transcribe_voice", whisper)
    monkeypatch.setattr(ai, "_is_throttled", lambda _uid: None)
    long_voice = _voice_message(uid, duration=3600)
    await ai.ai_chat(long_voice, AsyncMock())
    assert whisper.await_count == 0 and long_voice.answer.await_count == 1
    monkeypatch.setattr(ai, "get_ai_quota", lambda *_a, **_k: {"remaining": 0})
    short_voice = _voice_message(uid, duration=10)
    await ai.ai_chat(short_voice, AsyncMock())
    assert whisper.await_count == 0, "лимит исчерпан — расшифровку не оплачиваем"


async def test_review_the_voice_button_cannot_be_hammered(uid, monkeypatch):
    """Прогон 09.11: каждое нажатие «🔊» — новый платный вызов TTS без ограничений."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import multi_agent
    from handlers import ai
    add_user(uid, "u", "Test")
    message_id = add_ai_message(uid, "assistant", "Ответ ADAM")
    tts = AsyncMock(return_value=b"mp3")
    monkeypatch.setattr(multi_agent, "generate_speech", tts)
    for _ in range(20):
        msg = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()), chat=SimpleNamespace(id=uid), answer=AsyncMock(), answer_audio=AsyncMock())
        cb = SimpleNamespace(data=f"ai_voice_{message_id}", from_user=SimpleNamespace(id=uid), answer=AsyncMock(), message=msg)
        await ai.ai_voice(cb)
    assert tts.await_count <= 6
