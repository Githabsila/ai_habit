"""
Жалоба пользователя: ADAM в нумерованных списках/планах ставит "1." перед
КАЖДЫМ пунктом вместо последовательной нумерации (1, 2, 3...). Просьба в
промпте (см. BASE_PERSONA в multi_agent.py) это не вылечила до конца —
модель не всегда надёжно считает пункты сама. multi_agent._renumber_
numbered_lists — детерминированная постобработка поверх этой инструкции,
применяется к финальному тексту в _ask() (см. её вызовы).
"""
import multi_agent


def test_renumber_fixes_repeated_ones():
    text = "1. Заряди телефон.\n\n1. Зафиксируй условия.\n\n1. Нагружай телефон."
    result = multi_agent._renumber_numbered_lists(text)
    assert result == "1. Заряди телефон.\n\n2. Зафиксируй условия.\n\n3. Нагружай телефон."


def test_renumber_leaves_already_correct_sequence_untouched():
    text = "1. Первое.\n2. Второе.\n3. Третье."
    assert multi_agent._renumber_numbered_lists(text) == text


def test_renumber_ignores_numbers_mid_sentence():
    text = "Результат: 3.5 из 5. Погрешность 0.2."
    assert multi_agent._renumber_numbered_lists(text) == text


def test_renumber_handles_indented_items():
    text = "План:\n  1. Шаг раз\n  1. Шаг два"
    assert multi_agent._renumber_numbered_lists(text) == "План:\n  1. Шаг раз\n  2. Шаг два"


def test_renumber_handles_parenthesis_format():
    text = "1) Первое\n1) Второе\n1) Третье"
    assert multi_agent._renumber_numbered_lists(text) == "1) Первое\n2) Второе\n3) Третье"


def test_renumber_restarts_after_new_list():
    text = "План 1:\n1. Первый шаг\n1. Второй шаг\n\n\nПлан 2:\n1. Первый шаг\n1. Второй шаг"
    assert multi_agent._renumber_numbered_lists(text) == "План 1:\n1. Первый шаг\n2. Второй шаг\n\n\nПлан 2:\n1. Первый шаг\n2. Второй шаг"


def test_renumber_empty_and_plain_text_untouched():
    assert multi_agent._renumber_numbered_lists("") == ""
    assert multi_agent._renumber_numbered_lists("Просто текст без списка") == "Просто текст без списка"


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


async def test_ask_renumbers_final_groq_result(monkeypatch):
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "fake-key-for-test")

    class _Client:
        def __init__(self):
            self.chat = type("_C", (), {})()
            self.chat.completions = self

        async def create(self, **kwargs):
            return _FakeCompletionResponse("1. Первое.\n\n1. Второе.", "stop")

    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: _Client())

    result = await multi_agent._ask("system", "user", max_tokens=50)

    assert result == "1. Первое.\n\n2. Второе."
