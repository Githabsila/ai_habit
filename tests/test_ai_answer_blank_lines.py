"""
Жалоба пользователя: внутри одного пузыря ответа ADAM иногда виден большой
пустой промежуток (например после списка, перед последним предложением) —
на видео с реального устройства это выглядело как "непрорисовка"/баг вёрстки.
На деле модель иногда кладёт подряд 3+ переноса строки между абзацами, а
фронтенд (white-space:pre-line в ai_coach.js) честно рисует КАЖДЫЙ перенос
как пустую строку. multi_agent._strip_markdown теперь схлопывает такие
цепочки до одной пустой строки максимум — обычный отступ между абзацами,
а не дыра в вёрстке.
"""
import multi_agent


def test_strip_markdown_collapses_triple_newlines():
    text = "Абзац первый.\n\n\nАбзац второй."
    assert multi_agent._strip_markdown(text) == "Абзац первый.\n\nАбзац второй."


def test_strip_markdown_collapses_many_newlines():
    text = "Пункт списка.\n\n\n\n\nЗаключительное предложение."
    assert multi_agent._strip_markdown(text) == "Пункт списка.\n\nЗаключительное предложение."


def test_strip_markdown_leaves_single_blank_line_untouched():
    text = "Абзац первый.\n\nАбзац второй."
    assert multi_agent._strip_markdown(text) == text


def test_strip_markdown_leaves_single_newline_untouched():
    text = "Строка первая.\nСтрока вторая."
    assert multi_agent._strip_markdown(text) == text
