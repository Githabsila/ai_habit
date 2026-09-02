"""
Фидбек #4: умные напоминания — голос от первого лица (не "ADAM тебя не
списал", а "я тебя не списал") + согласование рода в "Ты"-обращении
(сделал/сделала). Проверяем, что подстановка реально происходит в готовом
тексте, а не только в изолированном db.users.gender_forms.
"""
import re

from db import add_user, set_gender
from db.streak import PRAISE_A, generate_praise

from tests.conftest import sign_init_data  # noqa: F401

PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


def test_praise_messages_have_no_unfilled_placeholders_for_male(uid):
    add_user(uid, "u", "Test")
    set_gender(uid, "m")
    for _ in range(50):
        text = generate_praise(uid)
        assert not PLACEHOLDER_RE.search(text), text


def test_praise_messages_have_no_unfilled_placeholders_for_female(uid):
    add_user(uid, "u", "Test")
    set_gender(uid, "f")
    for _ in range(50):
        text = generate_praise(uid)
        assert not PLACEHOLDER_RE.search(text), text


def test_praise_messages_have_no_unfilled_placeholders_when_gender_unknown(uid):
    add_user(uid, "u", "Alex")  # неоднозначное имя -> get_gender вернёт None
    for _ in range(50):
        text = generate_praise(uid)
        assert not PLACEHOLDER_RE.search(text), text


def test_praise_uses_female_form_when_gender_is_female(uid):
    add_user(uid, "u", "Test")
    set_gender(uid, "f")
    # Прогоняем много раз — random.choice должен рано или поздно выбрать
    # реплику с {nastroen}/{vybral}/{uderzhal}, и там должна быть женская форма.
    seen_gendered = False
    for _ in range(200):
        text = generate_praise(uid)
        if "настроена" in text or "выбрала" in text or "удержала" in text:
            seen_gendered = True
        assert "настроен." not in text  # мужская форма не должна утечь
    assert seen_gendered, "ни разу не выпала реплика с родовой формой за 200 попыток"


def test_praise_pool_source_has_placeholders_not_hardcoded_male_forms():
    # Регрессия: если кто-то вернёт "настроен"/"выбрал"/"удержал" текстом
    # напрямую в PRAISE_A вместо плейсхолдера — это тихо сломает согласование
    # рода. Проверяем прямо в исходном списке шаблонов.
    joined = " ".join(PRAISE_A)
    assert "настроен." not in joined
    assert "{nastroen}" in joined
    assert "{vybral}" in joined
    assert "{uderzhal}" in joined


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_reengagement_messages_have_no_unfilled_placeholders():
    import streak_scheduler as ss
    from db import add_user as _add_user

    for gender in ("m", "f", None):
        for slot_key, pool in ss.REENGAGE_MESSAGES.items():
            for key, template in pool:
                forms = {"days": 5}
                from db import gender_forms
                forms.update(gender_forms(gender))
                text = template.format(**forms)
                assert not PLACEHOLDER_RE.search(text), (slot_key, key, text)


def test_reengagement_pool_has_no_third_person_adam_references():
    import streak_scheduler as ss
    joined = " ".join(text for pool in ss.REENGAGE_MESSAGES.values() for _key, text in pool)
    # "Открой ADAM"/"Вернись в ADAM" — это имя приложения (императив), не
    # разговор об Адаме в третьем лице, поэтому не запрещаем ADAM вообще.
    assert "ADAM тебя" not in joined
    assert "вопрос от Адама" not in joined
    assert "Адам оставляет" not in joined


def test_risk_2330_pool_has_gender_placeholder_not_hardcoded_male():
    import streak_scheduler as ss
    joined = " ".join(ss.RISK_23_30)
    assert "готов сжечь" not in joined
    assert "{gotov_lower}" in joined
