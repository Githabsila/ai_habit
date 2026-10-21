"""
Жалоба владельца (21.10): на длинный запрос ADAM один раз ответил «Не получилось сформировать ответ», а такой же запрос через пару минут
прошёл. Канал ответов у нас один — OpenAI (GROQ_API_KEY не задан), и у него было три слабых места: общий срок 15 с на ЛЮБОЙ вызов (ответ на
~1300 токенов за 15 с успевает не всегда), «пустой ответ», когда модель потратила весь лимит на размышления, и тяжёлая модель на
запросах длиннее 4500 символов. Теперь: срок попытки зависит от размера ответа, до трёх своих попыток, при пустом ответе лимит растёт,
после неудачи тяжёлая модель заменяется быстрой, а ошибка в журнале называет тип сбоя.
"""
import httpx
import openai
import pytest

import multi_agent
from multi_agent import FAST_MODEL, MODEL


class _Incomplete:
    def __init__(self, reason):
        self.reason = reason


class _Response:
    def __init__(self, text, status="completed", reason=None):
        self.output_text = text
        self.status = status
        self.incomplete_details = _Incomplete(reason) if reason else None
        self.usage = None


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"http {status}")
        self.status_code = status


def _timeout():
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))


class _Client:
    """Как AsyncOpenAI: with_options(...) возвращает клиента, responses.create(...) отдаёт следующий пункт сценария."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.options = []
        self.responses = self

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def openai_only(monkeypatch):
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "")
    monkeypatch.setattr(multi_agent, "OPENAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(multi_agent, "_extract_wait_seconds", lambda *a, **k: 0)        # без пауз между попытками

    def install(script):
        client = _Client(script)
        monkeypatch.setattr(multi_agent, "_get_openai_client", lambda: client)
        return client

    return install


# ---------------------------------------------------------------------------------------------------------------- срок попытки

def test_the_attempt_timeout_grows_with_the_size_of_the_answer():
    assert multi_agent.OPENAI_TIMEOUT <= multi_agent._openai_attempt_timeout(10) < multi_agent.OPENAI_TIMEOUT + 1, "классификатор — как прежде, ~15 с"
    assert multi_agent._openai_attempt_timeout(900) > multi_agent.OPENAI_TIMEOUT
    assert multi_agent._openai_attempt_timeout(1500) == multi_agent.OPENAI_ATTEMPT_MAX_TIMEOUT, "но не дольше потолка"
    assert multi_agent._openai_attempt_timeout(10 ** 6) == multi_agent.OPENAI_ATTEMPT_MAX_TIMEOUT


def test_all_attempts_fit_into_the_job_deadline():
    from webapp.services.ai_jobs import JOB_TIMEOUT_SECONDS
    assert multi_agent.OPENAI_TOTAL_BUDGET + multi_agent.OPENAI_ATTEMPT_MAX_TIMEOUT < JOB_TIMEOUT_SECONDS, \
        "последняя попытка + продолжение ответа не должны упираться в общий срок задачи ответа"


async def test_each_attempt_gets_its_own_timeout_and_no_hidden_sdk_retries(openai_only):
    client = openai_only([_Response("Готово.")])
    await multi_agent._ask("system", "user", max_tokens=1300)
    assert client.options == [{"timeout": multi_agent._openai_attempt_timeout(1300), "max_retries": 0}]
    assert client.options[0]["timeout"] > multi_agent.OPENAI_TIMEOUT, "ответ на длинный запрос получает больше 15 с"


# ---------------------------------------------------------------------------------------------------------------- повторы

async def test_a_timeout_is_retried_and_the_person_gets_the_answer(openai_only):
    client = openai_only([_timeout(), _Response("Вот план на день.")])
    assert await multi_agent._ask("system", "user", max_tokens=900) == "Вот план на день."
    assert len(client.calls) == 2


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_rate_limits_and_server_errors_are_retried(openai_only, status):
    client = openai_only([_HTTPError(status), _Response("Ответ.")])
    assert await multi_agent._ask("system", "user") == "Ответ."
    assert len(client.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_a_hopeless_error_is_not_retried(openai_only, status):
    client = openai_only([_HTTPError(status), _Response("не должно понадобиться")])
    with pytest.raises(RuntimeError) as info:
        await multi_agent._ask("system", "user")
    assert len(client.calls) == 1, "неверный ключ или запрос повторами не лечится"
    assert "_HTTPError" in str(info.value), "в журнале видно тип сбоя"


async def test_all_attempts_failing_ends_in_one_clear_error_that_names_the_cause(openai_only):
    client = openai_only([_timeout()] * multi_agent.OPENAI_ATTEMPTS)
    with pytest.raises(RuntimeError) as info:
        await multi_agent._ask("system", "user")
    assert len(client.calls) == multi_agent.OPENAI_ATTEMPTS
    assert str(info.value).startswith("AI временно недоступен: APITimeoutError:")


async def test_the_total_budget_stops_the_attempts_early(openai_only, monkeypatch):
    monkeypatch.setattr(multi_agent, "OPENAI_TOTAL_BUDGET", 1.0)          # после первой неудачи на новую попытку времени не остаётся
    client = openai_only([_timeout(), _Response("не успеем")])
    with pytest.raises(RuntimeError):
        await multi_agent._ask("system", "user")
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------------------------------------------- пустой ответ

async def test_an_answer_eaten_by_reasoning_is_retried_with_a_bigger_limit(openai_only):
    client = openai_only([
        _Response("", status="incomplete", reason="max_output_tokens"),
        _Response("Разложил день по часам."),
    ])
    assert await multi_agent._ask("system", "user", max_tokens=1300) == "Разложил день по часам."
    first, second = (call["max_output_tokens"] for call in client.calls)
    assert first == 1300 and second > first and second <= multi_agent.OPENAI_MAX_OUTPUT_TOKENS


async def test_a_plain_empty_answer_is_retried_too(openai_only):
    client = openai_only([_Response(""), _Response("Ответ со второй попытки.")])
    assert await multi_agent._ask("system", "user") == "Ответ со второй попытки."
    assert [call["max_output_tokens"] for call in client.calls] == [500, 500], "обычная пустота лимит не раздувает"


async def test_the_limit_never_goes_above_the_ceiling(openai_only):
    openai_only([_Response("", status="incomplete", reason="max_output_tokens")] * multi_agent.OPENAI_ATTEMPTS)
    with pytest.raises(RuntimeError) as info:
        await multi_agent._ask("system", "user", max_tokens=3500)
    assert "_OutOfBudget" in str(info.value)


# ---------------------------------------------------------------------------------------------------------------- модель

async def test_the_heavy_model_gives_way_to_the_fast_one_after_a_failure(openai_only):
    client = openai_only([_timeout(), _Response("Успел быстрой.")])
    assert await multi_agent._ask("system", "user", model=MODEL) == "Успел быстрой."
    assert [call["model"] for call in client.calls] == [MODEL, FAST_MODEL]


async def test_the_fast_model_stays_the_fast_model(openai_only):
    client = openai_only([_timeout(), _Response("Ответ.")])
    await multi_agent._ask("system", "user", model=FAST_MODEL)
    assert [call["model"] for call in client.calls] == [FAST_MODEL, FAST_MODEL]


# ---------------------------------------------------------------------------------------------------------------- прежнее поведение

async def test_a_cut_off_answer_still_gets_its_continuation_through_the_scoped_client(openai_only):
    client = openai_only([
        _Response("План:\n\n1.", status="incomplete", reason="max_output_tokens"),
        _Response(" Разбери почту."),
    ])
    result = await multi_agent._ask("system", "user", max_tokens=50, allow_continuation=True)
    assert "1. Разбери почту." in result and len(client.calls) == 2


async def test_a_pipeline_failure_reaches_the_person_in_adams_first_person(client, uid, monkeypatch):
    """Текст ошибки, который видит человек, — от первого лица и с понятным действием (а не «попробуй через минуту»)."""
    from db import add_user
    from tests.conftest import sign_init_data
    import webapp.routes_ai_miniapp as route_mod

    add_user(uid, "tester", "Тест")

    async def broken(**kwargs):
        raise RuntimeError("AI временно недоступен: APITimeoutError: Request timed out.")

    monkeypatch.setattr(route_mod, "solve_task_multiagent", broken)
    response = await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "помоги разложить день", "request_id": "r-first-person"})
    body = await response.json()
    assert response.status == 500 and body["error"] == "ai_error"
    assert body["message"].startswith("Я ") and "ещё раз" in body["message"]
