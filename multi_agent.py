"""
multi_agent.py
Мультиагентная система для ИИ-модуля Telegram-бота.

Архитектура:
0. TRIAGE      — быстрая проверка: сообщение простое (можно ответить одним
                 вызовом) или содержательное (нужен полный пайплайн).
0. CRISIS GATE — быстрая проверка на признаки острого кризиса; если есть —
                 пайплайн не запускается, пользователю сразу уходит забота
                 и реальные контакты помощи.
1. DECOMPOSER  — разбивает задачу на несколько подзадач.
2. WORKERS     — решают каждую подзадачу независимо (параллельно).
3. SYNTHESIZER — собирает решения подзадач в один черновой ответ.
4. DEBATERS    — несколько агентов с разными "точками зрения" предлагают
                 свои варианты финального ответа, видя черновик.
5. JUDGE       — финальный агент сравнивает варианты и либо выбирает
                 лучший, либо синтезирует итоговый ответ из лучших частей.
6. HABIT EXTRACTOR — смотрит на готовый ответ и, если в нём явно
                 советуется конкретная новая привычка, достаёт короткое
                 название — чтобы бот мог предложить кнопку "Добавить".

Работает через Groq API (AsyncGroq), полностью асинхронно. Независимые шаги
выполняются параллельно (asyncio.gather), чтобы задержка не росла кратно
числу агентов.

Использование:
    from multi_agent import solve_task_multiagent

    result = await solve_task_multiagent("Как мне лучше выстроить привычку бегать по утрам?")
    result["answer"]           -> текст ответа
    result["is_crisis"]        -> bool
    result["suggested_habit"]  -> str | None
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
FAST_MODEL = "llama-3.1-8b-instant"     # для декомпозиции, классификаторов и быстрого пути — Groq считает TPM ОТДЕЛЬНО по каждой модели, так что вынос сюда фактически даёт отдельный бюджет токенов, не пересекающийся с MODEL

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

# Стиль общения выбирается пользователем в настройках (см. db/settings.py,
# колонка ai_style) и подмешивается во все стадии наравне с mood_note —
# так тон реально меняется, а не только в приветственном сообщении.
STYLE_NOTES = {
    "soft": (
        "Пользователь выбрал МЯГКИЙ стиль общения: будь особенно бережным и "
        "поддерживающим, избегай любого давления, категоричности и "
        "требовательного тона. Признавай трудности, хвали даже маленький прогресс."
    ),
    "neutral": "",
    "strict": (
        "Пользователь выбрал стиль ЖЁСТКОГО ТРЕНЕРА: говори прямо и по делу, "
        "без сюсюканья и лишних утешений, требуй конкретики и конкретных "
        "действий. При этом без грубости, оскорблений и перехода на личности."
    ),
}
DEFAULT_STYLE = "neutral"


@dataclass
class AgentTrace:
    """Хранит промежуточные результаты — удобно для логов/отладки в боте
    (например, показать пользователю по команде /debug, что происходило внутри)."""
    task: str
    mood: str = ""
    is_crisis: bool = False
    complexity: str = ""
    subtasks: list = field(default_factory=list)
    subtask_results: list = field(default_factory=list)
    draft: str = ""
    variants: list = field(default_factory=list)
    final_answer: str = ""
    suggested_habit: Optional[str] = None


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


def _combine_notes(*notes: str) -> str:
    return "\n\n".join(n for n in notes if n)


# ============ 0a. НАСТРОЕНИЕ ПОЛЬЗОВАТЕЛЯ ============

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


# ============ 0b. ТРИАЖ СЛОЖНОСТИ ============
# Лёгкая классификация перед тяжёлым пайплайном: короткие/простые сообщения
# ("привет", "спасибо", "как дела") не нуждаются в декомпозиции, дебатах и
# судье — это только лишние 10-15 секунд ожидания и токены. Полный пайплайн
# запускается только для содержательных вопросов по привычкам/мотивации.

TRIAGE_SYSTEM = (
    "Ты определяешь, нужен ли для сообщения пользователя коуча по привычкам "
    "развёрнутый, продуманный совет — или это простая реплика (приветствие, "
    "благодарность, короткий смол-ток, вопрос не по теме привычек/мотивации/"
    "дисциплины/продуктивности/саморазвития), на которую достаточно короткого "
    "прямого ответа без глубокого анализа. "
    "Ответь СТРОГО одним словом: 'просто' или 'сложно'."
)


async def triage_complexity(task: str) -> str:
    raw = await _ask(TRIAGE_SYSTEM, task, temperature=0.0, max_tokens=5, model=FAST_MODEL)
    value = raw.strip().lower()
    return "просто" if value.startswith("прост") else "сложно"


FAST_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Это простая реплика пользователя — ответь коротко (1-3 предложения) и "
    "по-человечески, не разворачивай её в план или лекцию о привычках, если "
    "об этом прямо не просили."
)


async def fast_answer(
    task: str,
    history: str = "",
    user_context: str = "",
    mood_note: str = "",
    style_note: str = "",
) -> str:
    user = task
    if user_context:
        user = f"Данные о пользователе:\n{user_context}\n\n{user}"
    if history:
        user = f"История переписки:\n{history}\n\n{user}"

    system = FAST_SYSTEM + "\n\n" + _combine_notes(mood_note, style_note)
    return await _ask(system, user, temperature=0.6, max_tokens=300)


# ============ 0c. КРИЗИС-ГЕЙТ ============
# Это бот-коуч по привычкам, но люди пишут в чат и о по-настоящему тяжёлом
# состоянии. Если это произошло — многоагентный пайплайн (который считает
# запрос темой "привычек") не запускается вовсе: пользователь сразу получает
# заботливый, стабилизирующий ответ и реальные контакты помощи, без советов
# по дисциплине и без вопросов, которые могли бы утянуть глубже.

CRISIS_SYSTEM = (
    "Ты — классификатор безопасности в боте-коуче по привычкам. Определи, "
    "есть ли в сообщении пользователя признаки острого кризиса: мысли о "
    "самоубийстве или самоповреждении, описание намерения причинить себе "
    "вред, ощущение непереносимой безнадёжности или тяжёлое психическое "
    "состояние, требующее настоящей человеческой помощи — а не совета по "
    "привычкам или мотивации. "
    "Ответь СТРОГО одним словом: 'да' или 'нет'."
)

CRISIS_RESPONSE = (
    "Мне важно, чтобы с тобой сейчас всё было в порядке. Похоже, тебе может "
    "быть очень тяжело — и это не то, с чем стоит справляться в одиночку или "
    "через советы по привычкам.\n\n"
    "Пожалуйста, обратись за настоящей помощью прямо сейчас:\n"
    "• Экстренная служба: 112\n"
    "• Горячая линия психологической помощи (бесплатно, круглосуточно): 8-800-2000-122\n"
    "• Или к любому близкому человеку, которому доверяешь\n\n"
    "Я останусь на связи, если захочешь просто поговорить, но за реальной "
    "поддержкой, пожалуйста, обратись к специалистам или на горячую линию."
)


async def detect_crisis(task: str) -> bool:
    raw = await _ask(CRISIS_SYSTEM, task, temperature=0.0, max_tokens=5, model=FAST_MODEL)
    return raw.strip().lower().startswith("да")


# ============ 0d. ОБЪЕДИНЁННЫЙ КЛАССИФИКАТОР ============
# Этап 4 "Оптимизация" — настроение, триаж сложности и кризис-гейт раньше
# были тремя отдельными вызовами Groq (пусть и параллельными). Здесь они
# объединены в ОДИН вызов: это втрое меньше запросов и заметно быстрее на
# практике (даже параллельные вызовы всё равно ограничены семафором и
# рейт-лимитом). Если модель вернёт не-JSON или что-то нераспознаваемое —
# безопасно откатываемся на три отдельных вызова, чтобы не потерять точность
# (особенно важно для кризис-гейта).

CLASSIFY_SYSTEM = (
    "Ты — классификатор входящих сообщений в боте-коуче по привычкам. "
    "Проанализируй сообщение пользователя и верни СТРОГО JSON без пояснений "
    "и markdown-разметки, в формате:\n"
    '{"mood": "<одно слово из: воодушевлён, нейтрально, устал, расстроен, '
    'раздражён, тревожен>", '
    '"complexity": "<просто — если это приветствие, благодарность, смол-ток '
    'или вопрос не по теме привычек/мотивации/дисциплины/продуктивности; '
    'сложно — если нужен развёрнутый, продуманный совет>", '
    '"crisis": <true, если есть признаки острого кризиса: мысли о '
    "самоубийстве или самоповреждении, намерение причинить себе вред, "
    'невыносимая безнадёжность — иначе false>}'
)


async def classify_message(task: str) -> tuple[str, str, bool]:
    raw = await _ask(CLASSIFY_SYSTEM, task, temperature=0.0, max_tokens=60, model=FAST_MODEL)
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)

        mood = str(data.get("mood", "")).strip().lower()
        if mood not in _KNOWN_MOODS:
            mood = "нейтрально"

        complexity_raw = str(data.get("complexity", "")).strip().lower()
        complexity = "просто" if complexity_raw.startswith("прост") else "сложно"

        crisis = bool(data.get("crisis", False))

        return mood, complexity, crisis
    except Exception as e:
        logger.warning(
            f"Объединённый классификатор не распарсился ({e}), "
            f"откатываемся на 3 отдельных вызова"
        )
        mood, complexity, crisis = await asyncio.gather(
            detect_mood(task), triage_complexity(task), detect_crisis(task)
        )
        return mood, complexity, crisis


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
    style_note: str = "",
) -> str:
    user = f"Общая задача пользователя: {original_task}\n\nТвоя подзадача: {subtask}"
    if user_context:
        user = f"Данные о пользователе (используй конкретные цифры и названия привычек, если это уместно, не выдумывай то, чего здесь нет):\n{user_context}\n\n{user}"
    if history:
        user = f"История переписки с пользователем:\n{history}\n\n{user}"

    system = WORKER_SYSTEM
    notes = _combine_notes(mood_note, style_note)
    if notes:
        system = system + "\n\n" + notes

    return await _ask(system, user, temperature=0.4, max_tokens=400, model=FAST_MODEL)


async def solve_all_subtasks(
    original_task: str,
    subtasks: list,
    history: str = "",
    user_context: str = "",
    mood_note: str = "",
    style_note: str = "",
) -> list:
    coros = [
        solve_subtask(original_task, st, history, user_context, mood_note, style_note)
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
    style_note: str = "",
) -> str:
    joined = "\n\n".join(f"— {r}" for r in subtask_results)
    user = f"Исходная задача: {task}\n\nРешения подзадач:\n{joined}"
    if user_context:
        user = f"Данные о пользователе (используй конкретные цифры и названия привычек, если это уместно, не выдумывай то, чего здесь нет):\n{user_context}\n\n{user}"
    if history:
        user = f"История переписки с пользователем:\n{history}\n\n{user}"

    system = SYNTH_SYSTEM
    notes = _combine_notes(mood_note, style_note)
    if notes:
        system = system + "\n\n" + notes

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


async def generate_variant(task: str, draft: str, persona: str, temperature: float, style_note: str = "") -> str:
    system = persona
    if style_note:
        system = system + "\n\n" + style_note
    user = (
        f"Исходная задача пользователя: {task}\n\n"
        f"Черновой ответ (предложен другим агентом): {draft}\n\n"
        "Предложи свой вариант финального ответа пользователю."
    )
    return await _ask(system, user, temperature=temperature, max_tokens=600)


async def generate_all_variants(task: str, draft: str, style_note: str = "") -> list:
    coros = [
        generate_variant(task, draft, persona, temp, style_note)
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


async def judge_variants(task: str, variants: list, style_note: str = "") -> str:
    system = JUDGE_SYSTEM
    if style_note:
        system = system + "\n\n" + style_note
    joined = "\n\n".join(f"Вариант {i + 1}:\n{v}" for i, v in enumerate(variants))
    user = f"Исходная задача: {task}\n\n{joined}"
    return await _ask(system, user, temperature=0.3, max_tokens=1000)


# ============ 6. HABIT EXTRACTOR ============
# Смотрит на готовый ответ коуча и, если в нём явно советуется ОДНА конкретная
# новая привычка, достаёт короткое название для неё — чтобы бот мог показать
# кнопку "➕ Добавить привычку" и сразу превратить совет в запись в БД, без
# того чтобы пользователь сам шёл в раздел привычек и вбивал её руками.

HABIT_EXTRACT_SYSTEM = (
    "Ты анализируешь финальный ответ AI-коуча по привычкам. Если в ответе явно "
    "рекомендуется пользователю завести ОДНУ конкретную новую привычку — верни "
    "JSON {\"habit\": \"название\"}, где название короткое (2-5 слов), как оно "
    "бы выглядело в списке привычек, например 'Читать 10 минут' или 'Пить 2 "
    "литра воды'. Если явной рекомендации одной конкретной новой привычки нет, "
    "или их несколько и непонятно какую выделить — верни {\"habit\": null}. "
    "Ответь СТРОГО в формате JSON, без пояснений и markdown-разметки."
)


async def extract_suggested_habit(answer: str) -> Optional[str]:
    raw = await _ask(HABIT_EXTRACT_SYSTEM, answer, temperature=0.0, max_tokens=60, model=FAST_MODEL)
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)
        habit = data.get("habit")
        if habit and isinstance(habit, str) and habit.strip():
            return habit.strip()[:60]
    except Exception as e:
        logger.warning(f"Не удалось распарсить предложенную привычку ({e})")
    return None


# ============ ОРКЕСТРАТОР ============

async def solve_task_multiagent(
    task: str,
    history: str = "",
    user_context: str = "",
    style: str = DEFAULT_STYLE,
    trace: Optional[AgentTrace] = None,
) -> dict:
    """
    Полный пайплайн:
      0. Параллельно: настроение, триаж сложности, кризис-гейт.
      1a. Если кризис — сразу отдаём заботливый ответ с реальными контактами
          помощи, дальше пайплайн не идёт.
      1b. Если сообщение простое — один быстрый вызов вместо всего пайплайна.
      2. Иначе — декомпозиция -> решение подзадач -> черновик -> несколько
         вариантов финального ответа -> судья выбирает/объединяет ->
         извлечение предложенной привычки (если она есть в ответе).

    task         — текущее сообщение пользователя.
    history      — текст с предыдущими репликами переписки (опционально),
                   нужен, чтобы бот помнил контекст диалога.
    user_context — отдельный блок с реальными данными о пользователе
                   (привычки, уровень, серия, прогресс) — НЕ смешивается
                   с историей переписки, чтобы советы опирались на факты,
                   а не терялись среди реплик диалога.
    style        — 'soft' / 'neutral' / 'strict', выбор пользователя из
                   настроек (см. db/settings.py, колонка ai_style).
    trace        — передай AgentTrace(task=task), если нужно посмотреть все
                   промежуточные шаги (для отладки/логов).

    Возвращает словарь:
        {
            "answer": str            — готовый текст ответа для пользователя,
            "mood": str,
            "is_crisis": bool,
            "suggested_habit": str | None,
        }
    """
    if trace is None:
        trace = AgentTrace(task=task)

    style_note = STYLE_NOTES.get(style, "")

    # Настроение, триаж и кризис-гейт — теперь ОДИН вызов вместо трёх
    # (см. classify_message выше), с безопасным откатом при сбое парсинга.
    mood, complexity, is_crisis = await classify_message(task)
    trace.mood = mood
    trace.complexity = complexity
    trace.is_crisis = is_crisis

    if is_crisis:
        trace.final_answer = CRISIS_RESPONSE
        return {
            "answer": CRISIS_RESPONSE,
            "mood": mood,
            "is_crisis": True,
            "suggested_habit": None,
            "complexity": "кризис",
        }

    mood_note = MOOD_GUIDANCE.get(mood, "")

    if complexity == "просто":
        answer = await fast_answer(task, history, user_context, mood_note, style_note)
        trace.final_answer = answer
        return {
            "answer": answer,
            "mood": mood,
            "is_crisis": False,
            "suggested_habit": None,
            "complexity": "просто",
        }

    subtasks = await decompose_task(task)
    trace.subtasks = subtasks

    subtask_results = await solve_all_subtasks(task, subtasks, history, user_context, mood_note, style_note)
    trace.subtask_results = subtask_results

    draft = await synthesize_draft(task, subtask_results, history, user_context, mood_note, style_note)
    trace.draft = draft

    variants = await generate_all_variants(task, draft, style_note)
    trace.variants = variants

    final_answer = await judge_variants(task, variants, style_note)

    # Fallback при ошибках API (этап 2): если где-то по цепочке всплыл
    # текст ошибки агента (например, Groq не смог обойти рейт-лимит даже
    # после ретраев), не показываем пользователю кривой текст — откатываемся
    # на один прямой вызов вместо всего пайплайна.
    broken = "[ошибка агента" in final_answer or "[ошибка агента" in draft
    if broken:
        logger.warning("Обнаружена ошибка агента в пайплайне — откат на fast_answer")
        final_answer = await fast_answer(task, history, user_context, mood_note, style_note)

    trace.final_answer = final_answer

    suggested_habit = None if broken else await extract_suggested_habit(final_answer)
    trace.suggested_habit = suggested_habit

    return {
        "answer": final_answer,
        "mood": mood,
        "is_crisis": False,
        "suggested_habit": suggested_habit,
        "complexity": complexity,
    }


# ============ ДОЛГОСРОЧНАЯ ПАМЯТЬ ============
# Раз в несколько сообщений (см. handlers/ai.py) текущий профиль пользователя
# пересобирается из старого профиля + свежих реплик — так бот помнит то, что
# терялось бы за пределами короткой истории (build_history_text режет её до
# нескольких последних сообщений). Этап 2 AI Core.

MEMORY_SUMMARY_SYSTEM = (
    "Ты обновляешь краткий профиль пользователя для AI-коуча по привычкам. "
    "У тебя есть текущий профиль (может быть пустым) и новые реплики "
    "переписки. Верни ОБНОВЛЁННЫЙ профиль: 3-5 кратких фактов о пользователе, "
    "полезных для будущих разговоров (цели, ограничения, обстоятельства "
    "жизни, устойчивые предпочтения, повторяющиеся трудности). Не включай "
    "то, что и так видно из списка привычек (сами названия привычек, "
    "серию дней). Пиши по-русски, каждый факт — отдельная строка, начинай "
    "строку с '- '. Без вступлений и заключений. Если ничего существенно "
    "нового не появилось — верни текущий профиль без изменений."
)


async def summarize_user_memory(existing_summary: str, recent_history: str) -> str:
    user = (
        f"Текущий профиль:\n{existing_summary or '(пусто)'}\n\n"
        f"Новые реплики:\n{recent_history or '(нет)'}"
    )
    result = await _ask(MEMORY_SUMMARY_SYSTEM, user, temperature=0.2, max_tokens=220, model=FAST_MODEL)
    if result.startswith("[ошибка агента"):
        # Не затираем то, что уже было накоплено, если вызов упал.
        return existing_summary
    return result.strip()[:1500]


# ============ СОВЕТ ДНЯ ============
# Отдельная лёгкая функция (не весь мультиагентный пайплайн) для кнопки
# "💡 Совет дня" — этап 3 AI Coach, "персональные советы". Результат
# кэшируется на уровне хендлера (db.ai.cache_*) на день, поэтому повторное
# нажатие не тратит новый вызов Groq.

TIP_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Сформулируй ОДИН короткий персональный совет пользователю на сегодня "
    "(2-4 предложения), опираясь на данные о нём. Без длинных вступлений — "
    "сразу суть, конкретно и по делу."
)


async def generate_daily_tip(user_context: str, style: str = DEFAULT_STYLE) -> str:
    style_note = STYLE_NOTES.get(style, "")
    system = TIP_SYSTEM
    if style_note:
        system = system + "\n\n" + style_note

    if user_context:
        user = f"Данные о пользователе:\n{user_context}"
    else:
        user = "Данных о пользователе пока нет — дай общий полезный совет по формированию привычек."

    return await _ask(system, user, temperature=0.6, max_tokens=250, model=FAST_MODEL)


# ============ АНАЛИЗ ПРОГРЕССА ============
# Для кнопки "🤖 AI-анализ" в разделе прогресса — этап 3 AI Coach.
# Тоже лёгкий одиночный вызов, а не весь пайплайн, и тоже кэшируется на
# день на уровне хендлера.

ANALYSIS_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Проанализируй прогресс пользователя за последнюю неделю коротко "
    "(3-5 предложений): что получается хорошо, на что обратить внимание, "
    "и один конкретный совет на следующую неделю. Без длинных вступлений."
)


async def generate_progress_analysis(user_context: str, weekly_stats_text: str, style: str = DEFAULT_STYLE) -> str:
    style_note = STYLE_NOTES.get(style, "")
    system = ANALYSIS_SYSTEM
    if style_note:
        system = system + "\n\n" + style_note

    parts = []
    if user_context:
        parts.append(f"Данные о пользователе:\n{user_context}")
    parts.append(f"Статистика за последнюю неделю:\n{weekly_stats_text}")
    user = "\n\n".join(parts)

    return await _ask(system, user, temperature=0.5, max_tokens=350, model=FAST_MODEL)


# ============ ИНТЕГРАЦИЯ В ПРОЕКТ ============
#
# В этом проекте интеграция уже сделана в handlers/ai.py: там роутер
# на состоянии AiState.chatting ловит любое сообщение пользователя без
# отдельных команд, троттлит частые сообщения и вызывает
# solve_task_multiagent(task, history=..., user_context=..., style=...).
# Результат — словарь: answer идёт пользователю, is_crisis подавляет
# клавиатуру с оценкой/добавлением привычки, suggested_habit превращается
# в кнопку "➕ Добавить привычку". Смотри файл ai.py, который идёт вместе
# с этим модулем.


if __name__ == "__main__":
    # Запуск ИМЕННО из папки проекта: python multi_agent.py
    # (нужен доступный рядом config.py с GROQ_API_KEY, как в остальном боте)
    #
    # Это печатает ВСЕ промежуточные этапы, чтобы можно было своими глазами
    # убедиться, что триаж/декомпозиция/дебаты/судья реально работают, а не
    # просто проксируют текст один в один.

    logging.basicConfig(level=logging.INFO)

    async def _demo():
        task = "Как выработать привычку читать книги каждый день, если совсем нет времени?"
        trace = AgentTrace(task=task)

        print("=" * 60)
        print("ЗАДАЧА:", task)

        result = await solve_task_multiagent(task, trace=trace)

        print("\n" + "=" * 60)
        print("0. НАСТРОЕНИЕ:", trace.mood, "| СЛОЖНОСТЬ:", trace.complexity, "| КРИЗИС:", trace.is_crisis)

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
        print(result["answer"])
        print("\n6. ПРЕДЛОЖЕННАЯ ПРИВЫЧКА:", trace.suggested_habit)
        print("=" * 60)

        # Простая проверка на корректность работы пайплайна:
        assert len(trace.subtasks) >= 1, "Декомпозер не вернул ни одной подзадачи"
        assert len(trace.subtask_results) == len(trace.subtasks), "Не все подзадачи решены"
        assert trace.draft and "[ошибка агента" not in trace.draft, "Синтезатор вернул ошибку"
        assert len(trace.variants) == NUM_DEBATERS, "Не все спорщики отработали"
        assert result["answer"] and "[ошибка агента" not in result["answer"], "Судья вернул ошибку"
        print("\n✅ Все этапы пайплайна отработали без ошибок")

    asyncio.run(_demo())
