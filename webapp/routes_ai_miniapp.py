"""
routes_ai_miniapp.py
API endpoints для мини-приложения AI-наставника.

Эндпоинты:
  POST /api/ai/chat        — отправить сообщение AI, получить ответ
  GET  /api/ai/history     — загрузить историю чата
  POST /api/ai/feedback    — оценить ответ (👍/👎)
  POST /api/ai/tip         — получить совет дня
"""

import json
import logging
from aiohttp import web
from datetime import date
from db import (
    add_ai_message,
    get_ai_history,
    get_progress,
    get_habits,
    save_ai_feedback,
    get_ai_style,
    add_habit,
    save_feedback_reason,
    get_recent_negative_reasons,
    get_user_profile,
    update_user_profile,
    bump_profile_counter,
    cache_get,
    cache_set,
    log_error,
    get_last_ai_message_at,
    touch_last_ai_message,
    claim_ai_first_message,
    has_premium, get_ai_quota, consume_ai_answer,
)
from multi_agent import solve_task_multiagent, generate_daily_tip, summarize_user_memory
from datetime import datetime, timezone
from config import AI_MAX_INPUT_CHARS, AI_LONG_COST_CHARS, AI_VERY_LONG_COST_CHARS
import hashlib
import asyncio

# ✅ Общая (уже исправленная) логика вместо задублированных копий ниже —
# раньше в этом файле были СВОИ копии build_user_context/build_history_text/
# _cache_key, из-за чего вся правки для Telegram-бота (privычки+план дня в
# контексте, исправленный кэш) не применялись к чату из Mini App ("/coach"),
# и AI-наставник там не видел ни план дня, ни умел выполнять/удалять
# привычки по просьбе — только разговаривал.
from webapp.services.ai_utils import (
    build_history_text,
    build_user_context,
    build_proactive_context,
    _cache_key,
)
from habit_intents import try_handle_habit_intent, try_handle_habit_intent_ai

logger = logging.getLogger("webapp.ai_miniapp")

# ============ КОНФИГ ============

MIN_INTERVAL_SECONDS = 3.0
MEMORY_UPDATE_EVERY = 6


def _looks_like_habit_action(text: str) -> bool:
    """Дешёвый локальный фильтр перед AI-классификатором привычек.
    Обычные вопросы не должны делать дополнительный LLM-вызов."""
    t = (text or "").strip().lower()
    if len(t) > 700:
        return False
    markers = (
        "добавь привыч", "удали привыч", "убери привыч", "удалить привыч",
        "выполни привыч", "отметь привыч", "отметить привыч", "привычка готов",
        "я сделал", "я выполнил", "я выполнила", "я сделалa", "готово, я",
        "я пробежал", "я почитал", "я помедитировал",
    )
    return any(m in t for m in markers)


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

def _is_throttled(user_id: int) -> float | None:
    """Возвращает, сколько секунд осталось ждать, или None, если можно слать."""
    last_str = get_last_ai_message_at(user_id)
    if last_str:
        try:
            last_dt = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - last_dt).total_seconds()
            if elapsed < MIN_INTERVAL_SECONDS:
                return round(MIN_INTERVAL_SECONDS - elapsed, 1)
        except ValueError:
            pass
    touch_last_ai_message(user_id)
    return None


async def _update_memory(user_id: int):
    """Обновляет тихую долгую память и, при необходимости, одну тему на следующие 24 часа."""
    try:
        profile = get_user_profile(user_id)
        existing_summary = profile["summary"] if profile else ""
        recent_history = build_history_text(user_id, limit=MEMORY_UPDATE_EVERY + 2)
        memory = await summarize_user_memory(existing_summary, recent_history)
        import json as _json
        from datetime import datetime as _dt, timedelta as _td
        summary = existing_summary
        followup = ""
        if isinstance(memory, dict):
            summary = str(memory.get("summary") or existing_summary).strip()[:1500]
            followup = str(memory.get("followup") or "").strip()[:180]
        else:
            summary = str(memory or existing_summary).strip()[:1500]
        until = (_dt.utcnow() + _td(hours=24)).isoformat(sep=" ") if followup else None
        update_user_profile(user_id, summary, followup, until)
    except Exception as e:
        logger.exception(f"Не удалось обновить профиль памяти для {user_id}")
        log_error("memory_update", e, user_id)


def _schedule_memory_update(user_id: int):
    """Запланировать обновление памяти."""
    count = bump_profile_counter(user_id)
    if count >= MEMORY_UPDATE_EVERY:
        asyncio.create_task(_update_memory(user_id))


# Zero-cost gate: only command-like messages need the optional AI intent classifier.
# Ordinary chat otherwise uses exactly one generation request.
_ACTION_MARKERS = (
    "добавь", "добавить", "создай", "создать", "заведи", "завести",
    "поставь", "поставить", "отметь", "отметить", "выполни", "выполнить",
    "сделал", "сделала", "сделано", "сделай", "удали", "удалить",
    "убери", "убрать", "переименуй", "переименовать", "измени",
    "поменяй", "перенеси", "назначь", "сними", "покажи мои привычки",
    "какие у меня привычки", "покажи мой план", "какой у меня план",
)

def _looks_like_action_request(text: str) -> bool:
    lowered = " ".join((text or "").lower().split())
    return any(marker in lowered for marker in _ACTION_MARKERS)


# ============ API ROUTES ============

routes = web.RouteTableDef()


@routes.post("/api/ai/chat")
async def ai_chat_miniapp(request):
    """
    Отправить сообщение AI и получить ответ.
    
    Request JSON:
    {
        "init_data": "...",  # Telegram init_data для аутентификации
        "message": "Как начать бегать?"
    }
    
    Response JSON:
    {
        "answer": "...",
        "is_crisis": false,
        "suggested_habit": "Бегать по утрам" или null,
        "message_id": 123
    }
    """
    from webapp.auth_helpers import authenticate

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"error": "invalid_json", "message": "Не получилось прочитать сообщение, попробуй ещё раз."},
            status=400
        )

    init_data = data.get("init_data", "")
    message_text = data.get("message", "").strip()

    if not message_text:
        return web.json_response(
            {"error": "empty_message", "message": "Напиши что-нибудь :)"},
            status=400
        )

    # Аутентификация + проверка бана/анкеты/гейта подписки (пром: раньше
    # тут была только проверка подписи initData — забаненный или не
    # прошедший гейт пользователь мог бесплатно жечь AI-квоту).
    user_id, _is_admin = await authenticate(init_data)

    pro = has_premium(user_id)

    if len(message_text) > AI_MAX_INPUT_CHARS:
        return web.json_response({
            "error": "message_too_long",
            "message": f"✂️ Сообщение слишком длинное. Максимум {AI_MAX_INPUT_CHARS} символов.",
            "max_chars": AI_MAX_INPUT_CHARS,
        }, status=413)

    # Проверка троттлинга
    wait = _is_throttled(user_id)
    if wait is not None:
        return web.json_response(
            {
                "error": "throttled",
                "wait_seconds": wait,
                "message": f"⏳ Не так быстро — подожди {wait} сек. и напиши ещё раз.",
            },
            status=429
        )

    # Фиксируем первое реально обработанное сообщение только после
    # успешного прохождения троттлинга.
    first_message = claim_ai_first_message(user_id)

    # ✅ Команды управления привычками и планом дня («добавь привычку …»,
    # «выполни привычку …», «покажи план» и т.п.) — сначала жёсткие
    # шаблоны, затем резервный AI-классификатор для более вольных
    # формулировок («я сделал зарядку»). Выполняются напрямую в базе, в
    # обход мультиагентного пайплайна — быстро и без риска, что модель
    # РАЗГОВОРНО подтвердит действие, ничего на самом деле не изменив.
    habit_reply = try_handle_habit_intent(user_id, message_text)
    if habit_reply is None and _looks_like_habit_action(message_text):
        habit_reply = await try_handle_habit_intent_ai(user_id, message_text)

    if habit_reply is not None:
        add_ai_message(user_id, "user", message_text)
        message_id = add_ai_message(user_id, "assistant", habit_reply)
        return web.json_response({
            "answer": habit_reply,
            "is_crisis": False,
            "suggested_habit": None,
            "message_id": message_id,
            "quota": get_ai_quota(user_id, pro),
        })

    # Обычные свободные ответы расходуют дневной лимит; управление привычками/планом — нет.
    cost = 3 if len(message_text) > AI_VERY_LONG_COST_CHARS else 2 if len(message_text) > AI_LONG_COST_CHARS else 1
    quota = get_ai_quota(user_id, pro)
    if quota["remaining"] < cost:
        return web.json_response({
            "error":"ai_quota_exceeded",
            "message": "💬 Лимит ответов ADAM на сегодня исчерпан. Купи дополнительные ответы или активируй ADAM PRO.",
            "quota": quota,
        }, status=402)

    # Собирем контекст
    history_text = build_history_text(user_id, limit=4, max_chars_per_msg=180)
    user_context = build_user_context(user_id)
    style = get_ai_style(user_id)

    previous = get_ai_history(user_id, limit=6)
    assistant_count = sum(1 for row in previous if row.get("role") == "assistant")
    humor_note = ""
    if (assistant_count + 1) % 3 == 0:
        humor_note = (
            "Если уместно, можешь закончить ответ одной короткой умной шуткой, "
            "ироничной репликой или интересным фактом по теме. Только если это "
            "естественно; без кринжа и без шуток в серьёзных ситуациях."
        )

    # Проверяем кэш (ключ включает user_id — иначе разные пользователи с
    # одинаковым текстом вопроса получали бы чужой закэшированный ответ,
    # посчитанный по чужим привычкам/плану дня)
    cache_key = f"{user_id}:{_cache_key(message_text, style)}"
    cached_answer = cache_get(cache_key)

    if cached_answer is not None:
        answer = cached_answer
        is_crisis = False
        suggested_habit = None
        complexity = "просто"
    else:
        try:
            result = await solve_task_multiagent(
                task=message_text,
                history=history_text,
                user_context=user_context,
                style=style,
                first_message=first_message,
                humor_note=humor_note,
            )
            answer = result["answer"]
            is_crisis = result["is_crisis"]
            suggested_habit = result["suggested_habit"]
            complexity = result.get("complexity", "сложно")
        except Exception as e:
            logger.exception(f"Ошибка AI-пайплайна для {user_id}")
            log_error("ai_pipeline", e, user_id)
            return web.json_response(
                {
                    "error": "ai_error",
                    "message": "Не получилось сформировать ответ. Попробуйте ещё раз через минуту."
                },
                status=500
            )

        if complexity == "просто" and not is_crisis:
            cache_set(cache_key, answer)

    # Списываем ответ после получения реального ответа — НЕЗАВИСИМО от того,
    # пришёл он из кэша или был только что сгенерирован. Раньше consume_ai_answer
    # вызывался только для НЕ закэшированных ответов — для пользователя это
    # выглядело как "счётчик 15/15 не уменьшается", хотя на деле уменьшался,
    # просто не на каждый ответ: кэш живёт 12 часов (db/ai.py::CACHE_TTL_HOURS)
    # и совпадает по любому похожему/повторному вопросу (см. _cache_key —
    # текст нормализуется без учёта регистра/пробелов), так что часть ответов
    # реально не расходовала лимит. С точки зрения пользователя это всё
    # равно полноценный ответ ADAM, поэтому он должен считаться так же.
    # Сбой AI/пустой ответ по-прежнему не "съедает" дневной лимит — до этой
    # строки код не доходит, если solve_task_multiagent бросил исключение.
    if not consume_ai_answer(user_id, pro, cost=cost):
        quota = get_ai_quota(user_id, pro)
        return web.json_response({"error":"ai_quota_exceeded","message":"💬 Лимит ответов ADAM исчерпан.","quota":quota}, status=402)

    # Сохраняем в БД
    add_ai_message(user_id, "user", message_text)
    message_id = add_ai_message(user_id, "assistant", answer)

    if not is_crisis:
        _schedule_memory_update(user_id)

    return web.json_response({
        "answer": answer,
        "is_crisis": is_crisis,
        "suggested_habit": suggested_habit,
        "message_id": message_id,
        "quota": get_ai_quota(user_id, pro),
    })


@routes.get("/api/ai/history")
async def get_ai_history_miniapp(request):
    """
    Загрузить полную историю чата с AI.
    
    Query params:
      init_data: Telegram init_data
      limit: количество последних сообщений (default: 50)
    
    Response JSON:
    {
        "history": [
            {"role": "user", "message": "...", "timestamp": "2024-01-01 12:00:00"},
            {"role": "assistant", "message": "...", "timestamp": "2024-01-01 12:00:05"}
        ]
    }
    """
    from webapp.auth_helpers import authenticate

    init_data = request.rel_url.query.get("init_data", "")
    limit = int(request.rel_url.query.get("limit", "50"))

    user_id, _is_admin = await authenticate(init_data)
    history = get_ai_history(user_id)

    if not history:
        return web.json_response({"history": []})

    safe_history = []
    for row in (history[-limit:] if history else []):
        if hasattr(row, "keys"):
            row = dict(row)
        safe_history.append({
            "id": row.get("id"),
            "role": row.get("role", ""),
            "message": row.get("message", row.get("content", "")),
            "created_at": row.get("created_at", row.get("timestamp")),
        })

    return web.json_response({
        "history": safe_history
    })


@routes.post("/api/ai/feedback")
async def ai_feedback_miniapp(request):
    """
    Оценить ответ AI (👍 = up, 👎 = down).
    
    Request JSON:
    {
        "init_data": "...",
        "message_id": 123,
        "rating": "up" или "down",
        "reason": "слишком длинный" или null (опционально)
    }
    """
    from webapp.auth_helpers import authenticate

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)

    init_data = data.get("init_data", "")
    message_id = data.get("message_id")
    rating = data.get("rating")
    reason = data.get("reason")

    user_id, _is_admin = await authenticate(init_data)

    if rating not in ["up", "down"]:
        return web.json_response({"error": "invalid_rating"}, status=400)

    save_ai_feedback(message_id, user_id, rating)

    if reason and rating == "down":
        save_feedback_reason(message_id, user_id, reason)

    return web.json_response({"ok": True})


@routes.post("/api/ai/tip")
async def ai_daily_tip_miniapp(request):
    """
    Получить совет дня.
    
    Request JSON:
    {
        "init_data": "..."
    }
    
    Response JSON:
    {
        "tip": "💡 Совет дня: ..."
    }
    """
    from webapp.auth_helpers import authenticate

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)

    init_data = data.get("init_data", "")
    user_id, _is_admin = await authenticate(init_data)

    # Не кэшируем совет: каждый тап получает свежий снимок задач и привычек.
    # Утренний ответ не должен жить до вечера, если пользователь уже закрыл
    # задачи или продвинулся по привычкам.
    style = get_ai_style(user_id)
    user_context = build_proactive_context(user_id)
    try:
        tip = await generate_daily_tip(user_context, style)
    except Exception as e:
        logger.exception(f"Не удалось сформировать совет дня для {user_id}")
        log_error("daily_tip", e, user_id)
        return web.json_response(
            {"error": "tip_error", "message": "Не получилось сформировать совет"},
            status=500
        )

    return web.json_response({"tip": tip})


@routes.post("/api/ai/habit/add")
async def ai_add_habit_miniapp(request):
    """
    Добавить привычку, предложенную AI.
    
    Request JSON:
    {
        "init_data": "...",
        "habit_title": "Бегать по утрам"
    }
    """
    from webapp.auth_helpers import authenticate

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)

    init_data = data.get("init_data", "")
    habit_title = data.get("habit_title", "").strip()

    if not habit_title:
        return web.json_response({"error": "empty_habit"}, status=400)

    user_id, _is_admin = await authenticate(init_data)

    try:
        add_habit(user_id, habit_title)
        return web.json_response({"ok": True, "habit": habit_title})
    except ValueError as exc:
        if str(exc) in ("habit_limit", "habit_add_locked"):
            return web.json_response({"error": str(exc)}, status=400)
        logger.exception(f"Ошибка при добавлении привычки для {user_id}")
        return web.json_response({"error": "internal_error"}, status=500)
    except Exception:
        logger.exception(f"Ошибка при добавлении привычки для {user_id}")
        return web.json_response({"error": "internal_error"}, status=500)


@routes.get("/api/ai/quota")
async def ai_quota_endpoint(request):
    from webapp.auth_helpers import authenticate
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_id, _is_admin = await authenticate(init_data)
    pro = has_premium(user_id)
    return web.json_response(get_ai_quota(user_id, pro))
