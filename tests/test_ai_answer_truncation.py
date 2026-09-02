"""
Жалоба пользователя: ответ ADAM обрывается посреди слова/списка ("1." и
дальше ничего) — скриншот чата с недописанным ответом. Разбор multi_agent.py
показал: Groq (основной провайдер) упирался в max_tokens и отдавал сырой,
буквально оборванный текст — в отличие от резервного OpenAI-канала
(_openai_call), у которого уже была аварийная подрезка до последнего целого
предложения при status == "incomplete". У Groq такой проверки не было вообще.

Тесты ниже покрывают оба сделанных фикса:
  1. _groq_call теперь подрезает ответ до последнего целого предложения,
     если choices[0].finish_reason == "length" (тот же сигнал "не хватило
     токенов", что и у OpenAI, просто в другом поле).
  2. _response_budget() отдаёт увеличенные лимиты токенов (были слишком
     тесными для структурированных ответов со списками).
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


class _FakeCompletions:
    def __init__(self, content, finish_reason):
        self._content = content
        self._finish_reason = finish_reason

    async def create(self, **kwargs):
        return _FakeCompletionResponse(self._content, self._finish_reason)


class _FakeChat:
    def __init__(self, content, finish_reason):
        self.completions = _FakeCompletions(content, finish_reason)


class _FakeGroqClient:
    def __init__(self, content, finish_reason):
        self.chat = _FakeChat(content, finish_reason)


async def test_ask_trims_groq_answer_to_last_sentence_when_length_cut_off(monkeypatch):
    # Ровно та ситуация со скриншота: список начался ("1.") и оборвался
    # без всякого знака препинания — модель просто кончила токены.
    cut_off = (
        "Сейчас 22:36, поэтому не пытайся делать большой объём. "
        "Порядок на сегодня такой:\n\n1."
    )
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(
        multi_agent, "_get_groq_client",
        lambda: _FakeGroqClient(cut_off, "length"),
    )

    result = await multi_agent._ask("system", "user", max_tokens=50)

    # Оборванный хвост без знака препинания срезан — ответ заканчивается на
    # последнем ПОЛНОМ предложении, а не висящим "1." без продолжения.
    assert result == "Сейчас 22:36, поэтому не пытайся делать большой объём."
    assert not result.endswith("1.")


async def test_ask_keeps_groq_answer_untouched_when_finish_reason_is_stop(monkeypatch):
    # Обычный, полностью завершённый ответ — trim не должен его трогать.
    complete = "Короткий, но законченный ответ. Вот и всё."
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(
        multi_agent, "_get_groq_client",
        lambda: _FakeGroqClient(complete, "stop"),
    )

    result = await multi_agent._ask("system", "user", max_tokens=50)

    assert result == complete


def test_response_budget_raised_above_previous_tight_limits():
    # Регрессия: раньше самый частый (короткий вход) уровень был 420 токенов —
    # видео-подтверждённо тесно для структурированного ответа со списком.
    short_tokens, _ = multi_agent._response_budget("короткий вопрос")
    assert short_tokens > 420

    long_tokens, _ = multi_agent._response_budget("x" * 5000)
    assert long_tokens > 800
    # Бюджет всё ещё растёт с длиной входа (не превратился в константу).
    assert long_tokens > short_tokens
