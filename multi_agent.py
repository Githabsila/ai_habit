"""
multi_agent.py
Мультиагентная система для ИИ-модуля Telegram-бота.

Архитектура:
1. DECOMPOSER  — разбивает задачу на несколько подзадач.
2. WORKERS     — решают каждую подзадачу независимо (параллельно).
3. SYNTHESIZER — собирает решения подзадач в один черновой ответ.
4. DEBATERS    — несколько агентов с разными "точками зрения" предлагают
                 свои варианты финального ответа, видя черновик.
5. JUDGE       — финальный агент сравнивает варианты и либо выбирает
                 лучший, либо синтезирует итоговый ответ из лучших частей.

Работает через Groq API (AsyncGroq), полностью асинхронно. Шаги 2 и 4
выполняются параллельно (asyncio.gather), чтобы задержка не росла
кратно числу агентов.

Использование:
    from multi_agent import solve_task_multiagent

    answer = await solve_task_multiagent("Как мне лучше выстроить привычку бегать по утрам?")
"""

import json
import re
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from groq import AsyncGroq, RateLimitError

# Берём ключ из того же config.py, что уже использует остальной проект
# (там он уже подгружен из .env) — так исчезает риск рассинхронизации
# между тем, как ключ читается в ai.py и здесь.
from config import GROQ_API_KEY

logger = logging.getLogger("multi_agent")

# ============ КОНФИГ ============

client = AsyncGroq(api_key=GROQ_API_KEY)

MODEL = "llama-3.3-70b-versatile"       # для синтеза, спорщиков и судьи — тех этапов, что формируют текст пользователю
FAST_MODEL = "llama-3.1-8b-instant"     # для декомпозиции и решения подзадач — Groq считает TPM ОТДЕЛЬНО по каждой модели, так что вынос сюда фактически даёт отдельный бюджет токенов, не пересекающийся с MODEL

MAX_SUBTASKS = 3          # было 4 — меньше подзадач = меньше запросов и токенов
NUM_DEBATERS = 2          # было 3 — меньше "спорщиков" = меньше токенов на дебаты
MAX_CONCURRENT_CALLS = 4  # ограничение параллельных запросов к Groq (защита от рейт-лимита)
MAX_RETRIES = 3           # автоповтор при ошибке 429, вместо падения с текстом ошибки

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

# Персона бота — та же, что раньше жила в системном промпте ask_ai().
# Подмешивается во все стадии, которые формируют текст, видимый пользователю
# (исполнители, синтезатор, спорщики, судья), чтобы тон и язык не потерялись
# при переходе с одного вызова на мультиагентный пайплайн.
BASE_PERSONA = (
    "Ты — часть команды Project ADAM, профессионального наставника по "
    "привычкам, дисциплине, продуктивности, саморазвитию и психологии "
    "мотивации. Всегда отвечай на русском языке, понятно и дружелюбно, без "
    "длинных вступлений. Если уместен план — оформляй его по пунктам. "
    "Мотивируй, но не осуждай."
)


@dataclass
class AgentTrace:
    """Хранит промежуточные результаты — удобно для логов/отладки в боте
    (например, показать пользователю по команде /debug, что происходило внутри)."""
    task: str
    mood: str = ""
    subtasks: list = field(default_factory=list)
    subtask_results: list = field(default_factory=list)
    draft: str = ""
    variants: list = field(default_factory=list)
    final_answer: str = ""


_RETRY_MS_RE = re.compile(r"try again in ([\d.]+)ms")
_RETRY_S_RE = re.compile(r"try again in ([\d.]+)s")


def _extract_wait_seconds(error_text: str, default: float = 2.0) -> float:
    """Достаёт из текста ошибки Groq время ожидания ('try again in 860ms' / '2.3s')."""
    m = _RETRY_MS_RE.search(error_text)
    if m:
        return float(m.group(1)) / 1000
    m = _RETRY_S_RE.search(error_text)
    if m:
        return float(m.group(1))
    return default


async def _ask(
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 700,
    model: str = MODEL,
) -> str:
    """Базовый вызов LLM с обработкой ошибок, ограничением параллелизма
    и автоповтором при превышении лимита токенов в минуту (429)."""
    async with _semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                return response.choices[0].message.content.strip()
            except RateLimitError as e:
                wait = _extract_wait_seconds(str(e))
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Groq rate limit (модель {model}), жду {wait:.1f}с и повторяю "
                        f"(попытка {attempt + 1}/{MAX_RETRIES})"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"Rate limit не удалось обойти после {MAX_RETRIES} попыток: {e}")
                return "[ошибка агента: превышен лимит запросов Groq, попробуй ещё раз через минуту]"
            except Exception as e:
                logger.error(f"Ошибка вызова LLM: {e}")
                return f"[ошибка агента: {e}]"


# ============ 0. НАСТРОЕНИЕ ПОЛЬЗОВАТЕЛЯ ============

MOOD_SYSTEM = (
    "Ты определяешь эмоциональный тон сообщения пользователя коуча по привычкам. "
    "Ответь ОДНИМ словом из списка: воодушевлён, нейтрально, устал, расстроен, "
    "раздражён, тревожен. Никаких пояснений, только одно слово."
)

_KNOWN_MOODS = {"воодушевлён", "нейтрально", "устал", "расстроен", "раздражён", "тревожен"}

MOOD_GUIDANCE = {
    "воодушевлён": "Пользователь настроен позитивно — поддержи этот настрой, не занудствуй с предупреждениями.",
    "нейтрально": "",
    "устал": "Пользователь звучит уставшим — начни с короткой поддержки, предложи посильный, а не амбициозный шаг.",
    "расстроен": "Пользователь расстроен — сначала поддержи и признай, что бывает трудно, без оценок и морализаторства, и только потом переходи к сути.",
    "раздражён": "Пользователь звучит раздражённо — отвечай спокойно и по делу, без давления и лишних советов сверху того, что спросили.",
    "тревожен": "Пользователь звучит тревожно — говори мягко, избегай нагнетания и категоричных формулировок, предложи маленький конкретный шаг.",
}


async def detect_mood(task: str) -> str:
    raw = await _ask(MOOD_SYSTEM, task, temperature=0.0, max_tokens=10, model=FAST_MODEL)
    mood = raw.strip().lower().strip(".").strip()
    return mood if mood in _KNOWN_MOODS else "нейтрально"


# ============ 1. DECOMPOSER ============

DECOMPOSE_SYSTEM = (
    "Ты — агент-декомпозер. Твоя задача — разбить входящий запрос пользователя "
    "на независимые подзадачи, которые вместе покрывают полный ответ. "
    f"Не больше {MAX_SUBTASKS} подзадач. Если задача простая и её не имеет смысла "
    "дробить — верни всего одну подзадачу, равную исходному запросу. "
    "Ответь СТРОГО в формате JSON-массива строк, без пояснений и markdown-разметки. "
    'Пример корректного ответа: ["подзадача 1", "подзадача 2"]'
)


async def decompose_task(task: str) -> list:
    raw = await _ask(DECOMPOSE_SYSTEM, task, temperature=0.2, max_tokens=300, model=FAST_MODEL)
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        subtasks = json.loads(cleaned)
        if not isinstance(subtasks, list) or not subtasks:
            raise ValueError("пустой или некорректный список")
        return [str(s) for s in subtasks[:MAX_SUBTASKS]]
    except Exception as e:
        logger.warning(f"Не удалось распарсить декомпозицию ({e}), используем задачу целиком")
        return [task]


# ============ 2. WORKERS ============

WORKER_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Ты — агент-исполнитель. Реши конкретную подзадачу максимально точно и по делу. "
    "Не пиши приветствий и лишних оговорок — только суть решения."
)


async def solve_subtask(
    original_task: str,
    subtask: str,
    history: str = "",
    user_context: str = "",
    mood_note: str = "",
) -> str:
    user = f"Общая задача пользователя: {original_task}\n\nТвоя подзадача: {subtask}"
    if user_context:
        user = f"Данные о пользователе (используй конкретные цифры и названия привычек, если это уместно, не выдумывай то, чего здесь нет):\n{user_context}\n\n{user}"
    if history:
        user = f"История переписки с пользователем:\n{history}\n\n{user}"

    system = WORKER_SYSTEM
    if mood_note:
        system = system + "\n\n" + mood_note

    return await _ask(system, user, temperature=0.4, max_tokens=400, model=FAST_MODEL)


async def solve_all_subtasks(
    original_task: str,
    subtasks: list,
    history: str = "",
    user_context: str = "",
    mood_note: str = "",
) -> list:
    coros = [
        solve_subtask(original_task, st, history, user_context, mood_note)
        for st in subtasks
    ]
    return await asyncio.gather(*coros)


# ============ 3. SYNTHESIZER ============

SYNTH_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Ты — агент-синтезатор. У тебя есть исходная задача и решения отдельных подзадач. "
    "Собери из них единый связный черновой ответ пользователю. "
    "Убери дублирование, сделай текст цельным."
)


async def synthesize_draft(
    task: str,
    subtask_results: list,
    history: str = "",
    user_context: str = "",
    mood_note: str = "",
) -> str:
    joined = "\n\n".join(f"— {r}" for r in subtask_results)
    user = f"Исходная задача: {task}\n\nРешения подзадач:\n{joined}"
    if user_context:
        user = f"Данные о пользователе (используй конкретные цифры и названия привычек, если это уместно, не выдумывай то, чего здесь нет):\n{user_context}\n\n{user}"
    if history:
        user = f"История переписки с пользователем:\n{history}\n\n{user}"

    system = SYNTH_SYSTEM
    if mood_note:
        system = system + "\n\n" + mood_note

    return await _ask(system, user, temperature=0.5)


# ============ 4. DEBATERS ============

DEBATER_PERSONAS = [
    (
        BASE_PERSONA + "\n\n"
        "Ты дотошный агент-критик: ищешь неточности, пробелы и слабые места в черновике "
        "и предлагаешь свой, более точный вариант финального ответа.",
        0.3,
    ),
    (
        BASE_PERSONA + "\n\n"
        "Ты агент, который ценит краткость и практичность: предлагаешь свой вариант "
        "финального ответа, максимально короткий и по делу, без воды.",
        0.5,
    ),
    (
        BASE_PERSONA + "\n\n"
        "Ты агент, который заботится о том, чтобы ответ был понятен и дружелюбен "
        "пользователю: предлагаешь свой вариант финального ответа, более развёрнутый "
        "и наглядный, с примерами, если это уместно.",
        0.7,
    ),
]


async def generate_variant(task: str, draft: str, persona: str, temperature: float) -> str:
    user = (
        f"Исходная задача пользователя: {task}\n\n"
        f"Черновой ответ (предложен другим агентом): {draft}\n\n"
        "Предложи свой вариант финального ответа пользователю."
    )
    return await _ask(persona, user, temperature=temperature, max_tokens=600)


async def generate_all_variants(task: str, draft: str) -> list:
    coros = [
        generate_variant(task, draft, persona, temp)
        for persona, temp in DEBATER_PERSONAS[:NUM_DEBATERS]
    ]
    return await asyncio.gather(*coros)


# ============ 5. JUDGE ============

JUDGE_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Ты — агент-судья. Тебе даны несколько вариантов финального ответа на задачу "
    "пользователя, предложенных разными агентами. Сравни их по точности, полноте и "
    "ясности. Собери ОДИН итоговый ответ пользователю — либо выбери лучший вариант "
    "целиком, либо объедини лучшие части из разных вариантов. "
    "В ответе выведи ТОЛЬКО финальный текст для пользователя, без пояснений о том, "
    "как ты его собирал, и без фраз вроде 'Вариант 2 был лучше'."
)


async def judge_variants(task: str, variants: list) -> str:
    joined = "\n\n".join(f"Вариант {i + 1}:\n{v}" for i, v in enumerate(variants))
    user = f"Исходная задача: {task}\n\n{joined}"
    return await _ask(JUDGE_SYSTEM, user, temperature=0.3, max_tokens=1000)


# ============ ОРКЕСТРАТОР ============

async def solve_task_multiagent(
    task: str,
    history: str = "",
    user_context: str = "",
    trace: Optional[AgentTrace] = None,
) -> str:
    """
    Полный пайплайн: определение настроения + декомпозиция (параллельно) ->
    решение подзадач -> черновик -> несколько вариантов финального ответа ->
    судья выбирает/объединяет.

    task         — текущее сообщение пользователя.
    history      — текст с предыдущими репликами переписки (опционально),
                   нужен, чтобы бот помнил контекст диалога.
    user_context — отдельный блок с реальными данными о пользователе
                   (привычки, уровень, серия, прогресс) — НЕ смешивается
                   с историей переписки, чтобы советы опирались на факты,
                   а не терялись среди реплик диалога.
    trace        — передай AgentTrace(task=task), если нужно посмотреть все
                   промежуточные шаги (для отладки/логов).

    Возвращает готовый текст ответа для пользователя.
    """
    if trace is None:
        trace = AgentTrace(task=task)

    # Настроение и декомпозиция не зависят друг от друга — гоним параллельно
    subtasks, mood = await asyncio.gather(
        decompose_task(task),
        detect_mood(task),
    )
    trace.subtasks = subtasks
    trace.mood = mood

    mood_note = MOOD_GUIDANCE.get(mood, "")

    subtask_results = await solve_all_subtasks(task, subtasks, history, user_context, mood_note)
    trace.subtask_results = subtask_results

    draft = await synthesize_draft(task, subtask_results, history, user_context, mood_note)
    trace.draft = draft

    variants = await generate_all_variants(task, draft)
    trace.variants = variants

    final_answer = await judge_variants(task, variants)
    trace.final_answer = final_answer

    return final_answer


# ============ ИНТЕГРАЦИЯ В ПРОЕКТ ============
#
# В этом проекте интеграция уже сделана в handlers/ai.py: там роутер
# на состоянии AiState.chatting ловит любое сообщение пользователя без
# отдельных команд и вызывает solve_task_multiagent(task, history=...).
# Смотри файл ai.py, который идёт вместе с этим модулем.


if __name__ == "__main__":
    # Запуск ИМЕННО из папки проекта: python multi_agent.py
    # (нужен доступный рядом config.py с GROQ_API_KEY, как в остальном боте)
    #
    # Это печатает ВСЕ промежуточные этапы, чтобы можно было своими глазами
    # убедиться, что декомпозиция/дебаты/судья реально работают, а не просто
    # проксируют текст один в один.

    logging.basicConfig(level=logging.INFO)

    async def _demo():
        task = "Как выработать привычку читать книги каждый день, если совсем нет времени?"
        trace = AgentTrace(task=task)

        print("=" * 60)
        print("ЗАДАЧА:", task)

        final = await solve_task_multiagent(task, trace=trace)

        print("\n" + "=" * 60)
        print("0. НАСТРОЕНИЕ:", trace.mood)

        print("\n" + "=" * 60)
        print(f"1. ПОДЗАДАЧИ ({len(trace.subtasks)}):")
        for i, st in enumerate(trace.subtasks, 1):
            print(f"   {i}. {st}")

        print("\n" + "=" * 60)
        print("2. РЕШЕНИЯ ПОДЗАДАЧ:")
        for i, res in enumerate(trace.subtask_results, 1):
            print(f"   [{i}] {res[:200]}{'...' if len(res) > 200 else ''}")

        print("\n" + "=" * 60)
        print("3. ЧЕРНОВИК:")
        print(f"   {trace.draft[:300]}{'...' if len(trace.draft) > 300 else ''}")

        print("\n" + "=" * 60)
        print(f"4. ВАРИАНТЫ ОТ СПОРЩИКОВ ({len(trace.variants)}):")
        for i, v in enumerate(trace.variants, 1):
            print(f"   Вариант {i}: {v[:200]}{'...' if len(v) > 200 else ''}")

        print("\n" + "=" * 60)
        print("5. ФИНАЛЬНЫЙ ОТВЕТ СУДЬИ:")
        print(final)
        print("=" * 60)

        # Простая проверка на корректность работы пайплайна:
        assert len(trace.subtasks) >= 1, "Декомпозер не вернул ни одной подзадачи"
        assert len(trace.subtask_results) == len(trace.subtasks), "Не все подзадачи решены"
        assert trace.draft and "[ошибка агента" not in trace.draft, "Синтезатор вернул ошибку"
        assert len(trace.variants) == NUM_DEBATERS, "Не все спорщики отработали"
        assert final and "[ошибка агента" not in final, "Судья вернул ошибку"
        print("\n✅ Все этапы пайплайна отработали без ошибок")

    asyncio.run(_demo())
