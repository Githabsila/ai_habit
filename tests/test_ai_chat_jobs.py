"""
Ответ ADAM дописывается, даже если человек ушёл из чата (на главный экран / свернул приложение) и вернулся.

Раньше ответ собирал сам обработчик запроса, а при возврате в диалог экран брал переписку из sessionStorage, где ответа не было.
Теперь ответ готовит фоновая задача (webapp/services/ai_jobs.py): обрыв клиента её не отменяет, итог лежит в реестре и
забирается через GET /api/ai/chat/result; клиент (ai_coach.js) при возврате дорисовывает ответ внизу диалога.
"""
import asyncio
import contextlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from db import add_user, get_ai_history
from db.shop import get_ai_quota
from tests.conftest import sign_init_data
from tests.test_ai_quota import _mock_ai_chat_deps
from webapp.services import ai_jobs as jobs_mod
from webapp.services.ai_jobs import ai_jobs

ANSWER = "Начни с 10 минут в день."


def _gated_pipeline(monkeypatch, answer=ANSWER):
    """Модель «думает», пока тест не откроет ворота; started — пайплайн уже запущен."""
    import webapp.routes_ai_miniapp as route_mod

    _mock_ai_chat_deps(monkeypatch)
    gate, started, calls = asyncio.Event(), asyncio.Event(), []

    async def slow_solve(**kwargs):
        calls.append(kwargs["task"])
        started.set()
        await gate.wait()
        return {"answer": answer, "is_crisis": False, "suggested_habit": None, "complexity": "сложно"}

    monkeypatch.setattr(route_mod, "solve_task_multiagent", slow_solve)
    return gate, started, calls


def _body(uid_, message="Как начать бегать?", rid=None):
    body = {"init_data": sign_init_data(uid_), "message": message}
    if rid:
        body["request_id"] = rid
    return body


async def _result(client, uid_, rid):
    resp = await client.get(f"/api/ai/chat/result?rid={rid}", headers={"X-Telegram-Init-Data": sign_init_data(uid_)})
    assert resp.status == 200
    return await resp.json()


async def _finish(uid_):
    await asyncio.wait_for(ai_jobs.get(uid_).task, 5)


async def test_answer_is_saved_after_the_client_left_and_waits_for_the_return(client, uid, monkeypatch):
    add_user(uid, "tester", "Тест")
    gate, started, _ = _gated_pipeline(monkeypatch)

    request = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-leave")))
    await asyncio.wait_for(started.wait(), 5)
    request.cancel()                                     # человек ушёл на главный экран: страница выгрузилась, fetch оборвался
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await request

    assert (await _result(client, uid, "r-leave"))["status"] == "pending", "ADAM ещё думает — клиент покажет «Формирую ответ»"
    assert get_ai_history(uid) == [], "пока ответа нет, в истории ничего не появляется"

    gate.set()                                           # ADAM закончил, пока человека не было
    await _finish(uid)

    history = get_ai_history(uid)
    assert [(m["role"], m["message"]) for m in history] == [("user", "Как начать бегать?"), ("assistant", ANSWER)]
    done = await _result(client, uid, "r-leave")         # человек вернулся в чат
    assert done["status"] == "done" and done["ok"] is True and done["http_status"] == 200
    assert done["data"]["answer"] == ANSWER and done["data"]["message_id"] == history[-1]["id"]
    assert done["data"]["quota"]["used"] == get_ai_quota(uid, False)["used"] == 1


async def test_result_is_pending_then_done_and_unknown_for_other_requests(client, uid, monkeypatch):
    add_user(uid, "tester", "Тест")
    gate, started, _ = _gated_pipeline(monkeypatch)

    request = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-1")))
    await asyncio.wait_for(started.wait(), 5)
    pending = await _result(client, uid, "r-1")
    assert pending["status"] == "pending" and pending["elapsed"] >= 0
    assert (await _result(client, uid, "другой-номер"))["status"] == "unknown"

    gate.set()
    answer = await request                               # человек остался в чате — обычный ответ тем же запросом
    assert answer.status == 200 and (await answer.json())["answer"] == ANSWER
    assert (await _result(client, uid, "r-1"))["status"] == "done"


async def test_result_requires_auth_and_does_not_leak_between_users(client, uid, monkeypatch):
    add_user(uid, "tester", "Тест")
    add_user(uid + 1, "other", "Другой")
    _mock_ai_chat_deps(monkeypatch)
    assert (await client.post("/api/ai/chat", json=_body(uid, rid="r-mine"))).status == 200

    assert (await client.get("/api/ai/chat/result?rid=r-mine")).status == 401
    assert (await _result(client, uid + 1, "r-mine"))["status"] == "unknown", "чужой итог не отдаём"
    assert (await _result(client, uid, "r-mine"))["status"] == "done"


async def test_the_same_request_id_joins_the_running_job_and_is_charged_once(client, uid, monkeypatch):
    add_user(uid, "tester", "Тест")
    gate, started, calls = _gated_pipeline(monkeypatch)
    before = get_ai_quota(uid, False)["used"]

    first = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-same")))
    await asyncio.wait_for(started.wait(), 5)
    retry = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-same")))   # связь мигнула — клиент повторил
    await asyncio.sleep(0.05)
    gate.set()
    r1, r2 = await asyncio.gather(first, retry)

    assert r1.status == r2.status == 200
    assert (await r1.json())["message_id"] == (await r2.json())["message_id"]
    assert len(calls) == 1, "модель вызвана один раз"
    assert get_ai_quota(uid, False)["used"] == before + 1, "лимит списан один раз"
    assert len(get_ai_history(uid)) == 2

    again = await client.post("/api/ai/chat", json=_body(uid, rid="r-same"))                   # и после готовности — тот же итог
    assert again.status == 200 and len(calls) == 1 and get_ai_quota(uid, False)["used"] == before + 1


async def test_a_second_message_is_refused_while_adam_is_still_answering(client, uid, monkeypatch):
    add_user(uid, "tester", "Тест")
    gate, started, calls = _gated_pipeline(monkeypatch)

    first = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-a")))
    await asyncio.wait_for(started.wait(), 5)
    busy = await client.post("/api/ai/chat", json=_body(uid, message="А ещё вопрос", rid="r-b"))
    assert busy.status == 409 and (await busy.json())["error"] == "busy"
    gate.set()
    assert (await first).status == 200 and len(calls) == 1

    next_ok = await client.post("/api/ai/chat", json=_body(uid, message="А ещё вопрос", rid="r-b"))
    assert next_ok.status == 200 and len(calls) == 2, "после ответа можно писать дальше"


async def test_new_dialog_cancels_an_unfinished_answer(client, uid, monkeypatch):
    add_user(uid, "tester", "Тест")
    gate, started, _ = _gated_pipeline(monkeypatch)
    before = get_ai_quota(uid, False)["used"]

    request = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-clear")))
    await asyncio.wait_for(started.wait(), 5)
    cleared = await client.post("/api/ai/clear", json={"init_data": sign_init_data(uid)})
    assert cleared.status == 200
    gate.set()

    response = await request
    assert response.status == 499 and (await response.json())["error"] == "cancelled"
    assert get_ai_history(uid) == [], "ответ не дописался в очищенную переписку"
    assert get_ai_quota(uid, False)["used"] == before, "лимит не списан"
    assert (await _result(client, uid, "r-clear"))["status"] == "unknown"


async def test_pipeline_failure_comes_back_through_the_result_endpoint(client, uid, monkeypatch):
    import webapp.routes_ai_miniapp as route_mod

    add_user(uid, "tester", "Тест")
    _mock_ai_chat_deps(monkeypatch)
    gate = asyncio.Event()

    async def broken(**kwargs):
        await gate.wait()
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(route_mod, "solve_task_multiagent", broken)
    request = asyncio.ensure_future(client.post("/api/ai/chat", json=_body(uid, rid="r-err")))
    await asyncio.sleep(0.05)
    request.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await request
    gate.set()
    await _finish(uid)

    result = await _result(client, uid, "r-err")
    assert result["status"] == "done" and result["ok"] is False and result["http_status"] == 500
    assert result["data"]["error"] == "ai_error" and result["data"]["message"]
    assert get_ai_history(uid) == [] and get_ai_quota(uid, False)["used"] == 0, "сбой ничего не списывает и не сохраняет"


async def test_a_hung_model_times_out_instead_of_blocking_the_user_forever(client, uid, monkeypatch):
    import webapp.routes_ai_miniapp as route_mod

    add_user(uid, "tester", "Тест")
    _mock_ai_chat_deps(monkeypatch)
    monkeypatch.setattr(jobs_mod, "JOB_TIMEOUT_SECONDS", 0.2)

    async def hang(**kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(route_mod, "solve_task_multiagent", hang)
    response = await client.post("/api/ai/chat", json=_body(uid, rid="r-hang"))
    assert response.status == 504 and (await response.json())["error"] == "ai_timeout"
    assert ai_jobs.active(uid) is None, "человек снова может писать"


async def test_old_results_expire_but_never_while_still_thinking(uid, monkeypatch):
    jobs = jobs_mod.ChatJobs()

    async def runner():
        return 200, {"answer": "ok"}

    job = jobs.start(uid, "r-ttl", "привет", runner)
    await job.task
    assert jobs.result(uid, "r-ttl")["status"] == "done"
    monkeypatch.setattr(jobs_mod, "JOB_TTL_SECONDS", -1)
    assert jobs.result(uid, "r-ttl") == {"status": "unknown"}

    monkeypatch.setattr(jobs_mod, "JOB_TTL_SECONDS", 15 * 60)
    release = asyncio.Event()

    async def slow():
        await release.wait()
        return 200, {"answer": "late"}

    slow_job = jobs.start(uid, "r-slow", "долго", slow)
    monkeypatch.setattr(jobs_mod, "JOB_TTL_SECONDS", -1)
    assert jobs.result(uid, "r-slow")["status"] == "pending", "срок жизни считается от готовности, а не от старта"
    release.set()
    await slow_job.task


# --- клиент (ai_coach.js) ----------------------------------------------------------------------------------

COACH_JS = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "ai_coach.js").read_text(encoding="utf-8")
COACH_HTML = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "ai_miniapp_styled.html").read_text(encoding="utf-8")


def test_the_route_is_registered_and_the_handler_does_not_do_the_work_itself():
    import webapp.routes_ai_miniapp as route_mod

    paths = {r.path for r in route_mod.routes}
    assert {"/api/ai/chat", "/api/ai/chat/result"} <= paths
    handler = Path(route_mod.__file__).read_text(encoding="utf-8")
    body = handler[handler.index("async def ai_chat_miniapp("):handler.index("async def _answer_chat(")]
    assert "ai_jobs.start(" in body and "asyncio.shield(job.task)" in body
    assert "solve_task_multiagent(" not in body, "модель зовёт фоновая задача, а не обработчик запроса"
    assert "ai_jobs.cancel(user_id)" in handler[handler.index("async def ai_clear_history_miniapp("):][:1400]


NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="нужен node")

POLL_SCRIPT = r"""
const CHAT_RESULT_WAIT_MS = 190000;
%(code)s
let t = 0; const calls = []; let pendingAnnounced = 0;
const script = %(script)s;                         // ответы сервера по очереди; "net" — нет сети; последний повторяется
let i = 0;
const fetchResult = async () => { calls.push(t); const r = script[Math.min(i++, script.length - 1)]; if (r === "net") throw new Error("offline"); return r; };
const sleep = async (ms) => { t += ms; };
const stopAfter = %(stop_after)s;
(async () => {
  const out = await waitForChatResult("rid", { fetchResult, sleep, now: () => t, maxMs: %(max_ms)s,
    shouldStop: () => stopAfter !== null && calls.length >= stopAfter, onPending: () => { pendingAnnounced++; } });
  console.log(JSON.stringify({ out, calls, pendingAnnounced }));
})();
"""


def _poll(script, max_ms=190000, stop_after=None):
    start = COACH_JS.index("async function waitForChatResult(")
    code = COACH_JS[start:COACH_JS.index("function formatTime(", start)]
    source = POLL_SCRIPT % {"code": code, "script": json.dumps(script), "max_ms": max_ms, "stop_after": json.dumps(stop_after)}
    done = subprocess.run([NODE, "-e", source], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_polling_waits_while_adam_thinks_and_returns_the_finished_answer():
    done = {"status": "done", "ok": True, "http_status": 200, "data": {"answer": "Привет"}}
    result = _poll([{"status": "pending"}, {"status": "pending"}, {"status": "pending"}, done])
    assert result["out"] == done and len(result["calls"]) == 4
    assert result["pendingAnnounced"] == 1, "«ждём» объявляется один раз — пузырь с вопросом не плодится"
    gaps = [b - a for a, b in zip(result["calls"], result["calls"][1:])]
    assert gaps == [1000, 1400, 1960], "интервал растёт, не долбим сервер"


@needs_node
def test_polling_survives_a_dead_network_and_gives_up_with_a_timeout():
    done = {"status": "done", "ok": True, "http_status": 200, "data": {}}
    assert _poll(["net", "net", done])["out"] == done, "оффлайн-сбои не обрывают ожидание"
    gave_up = _poll([{"status": "pending"}], max_ms=20000)
    assert gave_up["out"] == {"status": "timeout"} and gave_up["calls"][-1] >= 20000 - 4000


@needs_node
def test_polling_returns_unknown_at_once_and_stops_when_the_answer_was_delivered_elsewhere():
    assert _poll([{"status": "unknown"}])["out"] == {"status": "unknown"}
    assert _poll([{"status": "failed"}])["out"] == {"status": "failed"}
    stopped = _poll([{"status": "pending"}], stop_after=3)
    assert stopped["out"] == {"status": "stopped"} and len(stopped["calls"]) == 3


def test_chat_marks_the_request_as_waiting_and_picks_the_answer_up_on_return():
    send = COACH_JS[COACH_JS.index("const sendText = useCallback("):][:2200]
    assert "writePendingChat(pending)" in send and "request_id: pending.rid" in send and "keepalive: true" in send
    assert send.index("writePendingChat(pending)") < send.index("fetch('/api/ai/chat'")
    assert "claimDelivery(pending.rid)" in send, "ответ показывается ровно один раз — запрос и возврат не дублируют"
    catch = send[send.index("catch (e)"):]
    assert "recoverPending(pending)" in catch and "setMessages" not in catch, "обрыв связи — не повод показывать ошибку"
    assert "finally" not in send, "«думает» снимается только когда ответ показан"
    mount = COACH_JS[COACH_JS.index("const afterHistory = (hasHistory) => {"):][:520]
    assert "readPendingChat()" in mount and "recoverPending(pending)" in mount
    assert "useState(() => !!readPendingChat())" in COACH_JS, "при возврате сразу «Формирую ответ», а не пустое поле ввода"
    kick = COACH_JS[COACH_JS.index("const kick = () => {"):][:420]
    assert "visibilitychange" in COACH_JS[COACH_JS.index("const kick = () => {"):][:900] and "'online'" in COACH_JS[COACH_JS.index("const kick = () => {"):][:900]
    assert "recoverPending(pending)" in kick


def test_returning_adds_the_users_bubble_only_when_it_is_missing_and_never_twice():
    bubble = COACH_JS[COACH_JS.index("const withUserBubble = (list, pending) => {"):][:520]
    assert "last.role === 'user' && last.text === pending.text" in bubble, "пузырь вопроса уже на месте — не добавляем"
    apply = COACH_JS[COACH_JS.index("const applyChatOutcome = ("):][:1900]
    assert "p.some(m => String(m.id) === String(data.message_id)) ? p :" in apply, "тот же ответ из истории и из итога не задваивается"
    assert "data.error === 'cancelled'" in apply
    restore = COACH_JS[COACH_JS.index("const restoreFromHistory = async (pending) => {"):][:900]
    assert "fetchHistoryList()" in restore and "Не удалось получить ответ ADAM" in restore


def test_new_dialog_drops_the_waiting_request_and_the_page_loads_the_new_script():
    clear = COACH_JS[COACH_JS.index("const startNewDialog = () => {"):][:600]
    assert "clearPendingChat()" in clear and "deliveredRef.current.add(waiting.rid)" in clear and "setLoading(false)" in clear
    assert "ai_coach.js?v=20261009_PENDING_V53" in COACH_HTML
