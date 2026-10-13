"""
Статусы серии — лестница от текущей серии (дней подряд), у каждой ступени своё название, цвет и значок.

Раньше плитка в профиле показывала «30 дн.» (веха последней награды) рядом с серией «41» — это сбивало с толку. Теперь плитка называет
ступень («Кремень»), а число дней видно только в подсказке «до следующего». Пороги совпадают с вехами наград за серию
(db/streak.py::MILESTONES), поэтому рамки открываются на тех же днях, что и раньше.

Статус считается по ТЕКУЩЕЙ серии (как и плитка «серия» рядом): сорвалась серия — вернулся «Старт». Постоянные достижения за лучшую серию —
это эволюции и ранги (db/evolution.py), они не отбираются; названия здесь с ними и с названиями рамок намеренно не совпадают
(это проверяет tests/test_streak_status.py).
"""

# (дней серии, ключ, название, акцентный цвет, подпись для окна со всеми статусами)
_LADDER = (
    (0, "start", "Старт", "#9a94ad", "Первые шаги: каждый день закрывает одну ступень."),
    (14, "tempering", "Закалка", "#3ee0c3", "Две недели подряд — привычка начинает держаться."),
    (30, "flint", "Кремень", "#ff8a3d", "Месяц без срывов: искры уже не гаснут от ветра."),
    (60, "damask", "Булат", "#b9c4d4", "Два месяца: характер прочный и гибкий, как клинок."),
    (100, "monolith", "Монолит", "#ff5d73", "Сотня дней — это уже не привычка, а часть тебя."),
    (200, "titan", "Титан", "#a78bfa", "Двести дней: на таком уровне держатся единицы."),
    (300, "zenith", "Зенит", "#ff8fd0", "Триста дней — высшая точка перед годом."),
    (365, "absolute", "Абсолют", "#ffd36b", "Год подряд. Выше этого статуса нет."),
)

LADDER = tuple(
    {"tier": index, "days": days, "key": key, "name": name, "accent": accent, "blurb": blurb}
    for index, (days, key, name, accent, blurb) in enumerate(_LADDER)
)

# Название ступени по вехе награды: используется и в наградах за серию, и в праздничном экране.
NAME_BY_MILESTONE = {row["days"]: row["name"] for row in LADDER if row["days"]}


def status_for_days(days):
    """Текущая ступень, следующая и вся лестница (для окна «Статусы серии»)."""
    days = max(0, int(days or 0))
    index = 0
    for row in LADDER:
        if days >= row["days"]:
            index = row["tier"]
    current = LADDER[index]
    following = LADDER[index + 1] if index + 1 < len(LADDER) else None
    return {
        "tier": current["tier"], "key": current["key"], "name": current["name"], "accent": current["accent"], "since_days": current["days"],
        "blurb": current["blurb"],
        "next": ({"tier": following["tier"], "key": following["key"], "name": following["name"], "accent": following["accent"],
                  "days": following["days"], "left": following["days"] - days} if following else None),
        "ladder": [{**row, "reached": days >= row["days"], "left": max(0, row["days"] - days)} for row in LADDER],
    }
