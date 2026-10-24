"""
24.10.2026, логи Railway: Groq с сервера недоступен («Connection error») — каждый вызов модели терял ~3 секунды на две попытки Groq и только потом
шёл в OpenAI, а в одном ответе таких вызовов несколько. Теперь после ошибки подключения к Groq он пропускается на GROQ_COOLDOWN_SECONDS (если OpenAI
настроен) — остальные ответы идут сразу в OpenAI. Без OpenAI Groq остаётся единственным каналом и пропускаться не должен.
"""
import httpx
import pytest
from openai import APIConnectionError

import multi_agent


def _connection_error():
    return APIConnectionError(request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))


class _FailingGroq:
    def __init__(self, error):
        self.calls = 0
        self._error = error
        self.chat = type("_C", (), {})()
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls += 1
                raise outer._error

        self.chat.completions = _Completions()


class _FakeOpenAI:
    def __init__(self, text="Ответ из OpenAI"):
        self.calls = 0
        self._text = text
        outer = self

        class _Responses:
            async def create(self, **kwargs):
                outer.calls += 1
                return type("_R", (), {"output_text": outer._text, "status": "completed", "usage": None, "incomplete_details": None})()

        self.responses = _Responses()

    def with_options(self, **kwargs):
        return self


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch):
    monkeypatch.setattr(multi_agent, "_groq_down_until", 0.0)
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "groq-key-for-test")


async def test_a_dead_groq_costs_one_attempt_and_is_then_skipped(monkeypatch):
    groq, openai = _FailingGroq(_connection_error()), _FakeOpenAI()
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "openai-key-for-test")
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: groq)
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: openai)

    first = await multi_agent._ask("system", "user")
    assert first == "Ответ из OpenAI" and groq.calls == 1, "связи нет — второй попытки к Groq не делаем"
    assert multi_agent._groq_down_until > 0

    second = await multi_agent._ask("system", "user")
    third = await multi_agent._ask("system", "user")
    assert second == third == "Ответ из OpenAI"
    assert groq.calls == 1, "пока Groq «лежит», к нему не ходим вообще"
    assert openai.calls == 3


async def test_groq_is_tried_again_after_the_cooldown_and_a_success_clears_it(monkeypatch):
    class _Recovered:
        calls = 0
        chat = type("_C", (), {})()

    async def _create(**kwargs):
        _Recovered.calls += 1
        choice = type("_Ch", (), {"message": type("_M", (), {"content": "Ответ из Groq"})(), "finish_reason": "stop"})()
        return type("_R", (), {"choices": [choice], "usage": None})()

    _Recovered.chat.completions = type("_Comp", (), {"create": staticmethod(_create)})()
    openai = _FakeOpenAI()
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "openai-key-for-test")
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: _Recovered)
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: openai)

    monkeypatch.setattr(multi_agent, "_groq_down_until", multi_agent.time.monotonic() + 100)
    assert await multi_agent._ask("system", "user") == "Ответ из OpenAI" and _Recovered.calls == 0

    monkeypatch.setattr(multi_agent, "_groq_down_until", multi_agent.time.monotonic() - 1)      # срок вышел
    assert await multi_agent._ask("system", "user") == "Ответ из Groq" and _Recovered.calls == 1
    assert multi_agent._groq_down_until == 0.0


async def test_without_openai_groq_is_never_skipped(monkeypatch):
    groq = _FailingGroq(_connection_error())
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "")
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: groq)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await multi_agent._ask("system", "user")
    assert groq.calls == 4, "обе попытки на каждый вызов: другого канала нет"
    assert multi_agent._groq_down_until == 0.0


async def test_other_groq_errors_do_not_start_the_cooldown(monkeypatch):
    groq, openai = _FailingGroq(ValueError("плохой ответ")), _FakeOpenAI()
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "openai-key-for-test")
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: groq)
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: openai)

    assert await multi_agent._ask("system", "user") == "Ответ из OpenAI"
    assert groq.calls == 2 and multi_agent._groq_down_until == 0.0, "обычная ошибка — две попытки, без паузы на минуты"
