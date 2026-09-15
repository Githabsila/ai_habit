from adam_messages import format_day_progress_message


def test_secondary_tasks_are_grouped():
    msg = format_day_progress_message(1, 4, {"main": [], "secondary": ["Позвонить", "Кайдзен час", "Вечерний ритуал"]})
    assert "Второстепенные:" in msg
    assert msg.count("второстепенная") == 0
    assert "«Позвонить»" in msg and "«Кайдзен час»" in msg and "«Вечерний ритуал»" in msg


def test_main_and_secondary_are_grouped_separately():
    msg = format_day_progress_message(2, 4, {"main": ["Главная цель"], "secondary": ["Задача 1", "Задача 2"]})
    assert "Главная: «Главная цель»" in msg
    assert "Второстепенные: «Задача 1», «Задача 2»" in msg
    assert msg.count("Второстепенные:") == 1
