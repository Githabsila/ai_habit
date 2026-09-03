"""
Жалоба пользователя: ADAM всё чаще недописывает ответ и обрывает его на
полуслове, особенно на длинных структурированных планах — обрезка до
последнего целого предложения (см. test_ai_answer_truncation.py) спасала
от обрыва НА ПОЛУСЛОВЕ, но всё равно отдавала ЯВНО неполный ответ
(план без части шагов). Теперь при упоре в max_tokens ADAM сначала
пробует ОДИН доп. запрос "продолжи с этого места" (allow_continuation) —
и только если это не помогло, подрезает до целого предложения.
"""
import multi_agent


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeCompletionResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.usage = None


class _SequencedCompletions:
    """Каждый следующий .create() отдаёт следующий элемент из responses —
    имитирует первый вызов (упёрся в лимит) + продолжение(я)."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content, finish_reason = self._responses.pop(0)
        return _FakeCompletionResponse(content, finish_reason)


class _SequencedGroqClient:
    def __init__(self, responses):
        self.chat = type("_C", (), {})()
        self.chat.completions = _SequencedCompletions(responses)


async def test_continuation_appends_to_cut_off_groq_answer(monkeypatch):
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")
    client = _SequencedGroqClient([
        ("Порядок на сегодня такой:\n\n1.", "length"),
        (" Разбери входящие письма.", "stop"),
    ])
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: client)

    result = await multi_agent._ask("system", "user", max_tokens=50, allow_continuation=True)

    assert "1. Разбери входящие письма." in result
    # Реально ушёл второй запрос-продолжение, а не просто обрезка.
    assert len(client.chat.completions.calls) == 2


async def test_continuation_second_call_still_trims_if_cut_off_again(monkeypatch):
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")
    client = _SequencedGroqClient([
        ("Начало ответа, который очень длинный.", "length"),
        (" Продолжение тоже обрывается на полуслов", "length"),
    ])
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: client)

    result = await multi_agent._ask("system", "user", max_tokens=50, allow_continuation=True)

    # Второй обрыв — уже без повторного continuation, просто подрезка до
    # последнего целого предложения (не должно остаться висящего "полуслов").
    assert result == "Начало ответа, который очень длинный."


async def test_continuation_not_used_when_disabled(monkeypatch):
    """allow_continuation=False (по умолчанию, как у классификаторов) —
    старое поведение: сразу подрезка, без лишнего запроса."""
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")
    client = _SequencedGroqClient([
        ("Короткий классификатор без точки в конце", "length"),
    ])
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: client)

    result = await multi_agent._ask("system", "user", max_tokens=5)

    assert len(client.chat.completions.calls) == 1
    # Нет ни одного знака конца предложения — _trim_to_last_sentence не
    # находит, что обрезать, и возвращает текст как есть (см. её реализацию).
    assert result == "Короткий классификатор без точки в конце"


async def test_continuation_failure_falls_back_to_trim(monkeypatch):
    """Второй запрос сломался (сеть/провайдер) — не должны потерять уже
    полученный частичный ответ целиком, откатываемся к обрезке."""
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")

    class _BrokenCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _FakeCompletionResponse("Первое предложение готово. Второе не влезло", "length")
            raise RuntimeError("сеть упала")

    class _BrokenClient:
        def __init__(self):
            self.chat = type("_C", (), {})()
            self.chat.completions = _BrokenCompletions()

    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: _BrokenClient())

    result = await multi_agent._ask("system", "user", max_tokens=50, allow_continuation=True)

    assert result == "Первое предложение готово."


# ===================== Резервный канал OpenAI =====================
# Тот же continuation, но через client.responses.create (Responses API,
# отличается от chat.completions у Groq) — см. _continue_openai.

class _FakeIncompleteDetails:
    def __init__(self, reason):
        self.reason = reason


class _FakeOpenAIResponse:
    def __init__(self, text, status="completed", incomplete_reason=None):
        self.output_text = text
        self.status = status
        self.incomplete_details = _FakeIncompleteDetails(incomplete_reason) if incomplete_reason else None
        self.usage = None


class _SequencedResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _SequencedOpenAIClient:
    def __init__(self, responses):
        self.responses = _SequencedResponses(responses)


async def test_continuation_appends_to_cut_off_openai_answer(monkeypatch):
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "")  # форсируем резервный канал
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "fake-key-for-test")
    client = _SequencedOpenAIClient([
        _FakeOpenAIResponse("План на день:\n\n1.", status="incomplete", incomplete_reason="max_output_tokens"),
        _FakeOpenAIResponse(" Разбери почту.", status="completed"),
    ])
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: client)

    result = await multi_agent._ask("system", "user", max_tokens=50, allow_continuation=True)

    assert "1. Разбери почту." in result
    assert len(client.responses.calls) == 2


async def test_openai_continuation_disabled_falls_back_to_trim(monkeypatch):
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "")
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "fake-key-for-test")
    client = _SequencedOpenAIClient([
        _FakeOpenAIResponse("Готовое предложение. Оборванный хвост", status="incomplete", incomplete_reason="max_output_tokens"),
    ])
    monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: client)

    result = await multi_agent._ask("system", "user", max_tokens=50)  # allow_continuation по умолчанию False

    assert result == "Готовое предложение."
    assert len(client.responses.calls) == 1


async def test_fast_answer_requests_continuation(monkeypatch):
    """Интеграционная проверка: fast_answer (единственный путь реальных
    пользовательских ответов, см. multi_agent.py) действительно просит
    _ask о continuation, а не полагается только на подрезку."""
    captured = {}

    async def fake_ask(system, user, temperature=0.7, max_tokens=500, model="x", allow_continuation=False):
        captured["allow_continuation"] = allow_continuation
        return "готовый ответ."

    monkeypatch.setattr(multi_agent, "_ask", fake_ask)
    monkeypatch.setattr(multi_agent, "_needs_deep_pipeline", lambda task: False)

    await multi_agent.fast_answer("Короткий вопрос")

    assert captured["allow_continuation"] is True
