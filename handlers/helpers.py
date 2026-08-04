# handlers/helpers.py

def build_history_text(history_data) -> str:
    """
    Преобразует историю чата в текстовый формат для AI.
    (Скопируйте сюда ТОЧНУЮ реализацию из вашего handlers/ai.py)
    """
    # Пример реализации (замените на вашу):
    if not history_data:
        return ""

    formatted = []
    for entry in history_data:
        role = entry.get("role", "user")
        text = entry.get("text", "")
        formatted.append(f"{role.upper()}: {text}")
    
    return "\n".join(formatted)