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

Работает через OpenAI API, полностью асинхронно. Независимые шаги
выполняются параллельно (asyncio.gather), чтобы задержка не росла кратно
числу агентов.

Использование:
    from multi_agent import solve_task_multiagent

   from services.ai_service import chat

answer = await chat(
    user_id,
    user_message,
)"Как мне лучше выстроить привычку бегать по утрам?")
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

from openai import AsyncOpenAI

# Берём ключ из того же config.py, что уже использует остальной проект
# (там он уже подгружен из .env) — так исчезает риск рассинхронизации
# между тем, как ключ читается в ai.py и здесь.
from config import OPENAI_API_KEY, GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger("multi_agent")

# ============ КОНФИГ ============

MODEL = "gpt-5.6"
FAST_MODEL = "gpt-5.6-terra"
OPENAI_TIMEOUT = 8.0
GROQ_TIMEOUT = 8.0

MAX_SUBTASKS = 3          # было 4 — меньше подзадач = меньше запросов и токенов
NUM_DEBATERS = 2          # было 3 — меньше "спорщиков" = меньше токенов на дебаты
MAX_CONCURRENT_CALLS = 4  # ограничение параллельных запросов к OpenAI (защита от рейт-лимита)
MAX_RETRIES = 1           # автоповтор при ошибке 429, вместо падения с текстом ошибки

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

# Reuse HTTP connection pools between requests. Creating a new AsyncOpenAI
# client for every stage throws away keep-alive/TLS connection reuse.
_openai_client = None
_groq_client = None

def _get_openai_client():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        _openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)
    return _openai_client

def _get_groq_client():
    global _groq_client
    if _groq_client is None and GROQ_API_KEY:
        _groq_client = AsyncOpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
            timeout=GROQ_TIMEOUT,
        )
    return _groq_client

# Персона бота — та же, что раньше жила в системном промпте ask_ai().
# Подмешивается во все стадии, которые формируют текст, видимый пользователю
# (исполнители, синтезатор, спорщики, судья), чтобы тон и язык не потерялись
# при переходе с одного вызова на мультиагентный пайплайн.
BASE_PERSONA = (
    "Ты — AI ADAM, персональный ИИ-наставник пользователя. "

    "Твоя главная цель — помогать человеку становиться лучше каждый день. "

    "Ты эксперт в следующих областях: "

    "• создание бизнеса; "
    "• поиск клиентов; "
    "• продажи; "
    "• маркетинг; "
    "• программирование; "
    "• карьера; "
    "• саморазвитие; "
    "• дисциплина; "
    "• привычки; "
    "• продуктивность; "
    "• психология мотивации; "
    "• обучение; "
    "• управление временем; "
    "• принятие решений. "

    "Если пользователь спрашивает про бизнес — отвечай как опытный предприниматель и наставник. "

    "Если вопрос касается работы — помогай как карьерный консультант. "

    "Если пользователь говорит о мотивации, тревоге, неуверенности или прокрастинации — отвечай как поддерживающий наставник, не ставя диагнозов и не заменяя профессионального психолога. "

    "Если вопрос связан с привычками — выступай как персональный коуч. "

    "Если пользователь спрашивает о программировании — отвечай как опытный разработчик. "

    "Если вопрос общий — просто будь полезным универсальным помощником. "

    "Всегда отвечай на русском языке. "
    "Пиши понятно, дружелюбно и по делу. "
    "Если можно дать пошаговый план — обязательно давай его. "
    "Не ограничивай ответы темой привычек. "

    "Никогда не используй markdown-разметку: не оборачивай слова в звёздочки "
    "**вот так** или *вот так*, не используй решётки для заголовков, обратные "
    "кавычки для кода или дефисы-буллеты. Это обычный текстовый чат, разметка "
    "там не отображается и превращается в мусорные символы. Если нужно что-то "
    "выделить — используй сами слова или уместный эмодзи, без звёздочек.\n\n"

    "Прежде чем давать любой совет по задачам, целям или привычкам "
    "пользователя — сначала проверь в переданных данных о пользователе "
    "статус каждой из них (там прямо написано 'выполнено' / 'не выполнено' "
    "или 'выполнена' / 'НЕ выполнена'). Если что-то уже отмечено как "
    "выполненное — НИКОГДА не говори, что это ещё нужно сделать, и не "
    "включай это в список того, что предстоит. Вместо этого похвали "
    "пользователя за выполнение и, если в данных есть невыполненные задачи "
    "или привычки, предложи заняться следующей из них. Если абсолютно всё "
    "выполнено — так и скажи и похвали, не выдумывай несуществующую задачу. "
    "У привычек может быть задано время выполнения, например 20:00. Учитывай текущее локальное время пользователя. "
    "Если сейчас раньше назначенного времени, не подталкивай пользователя делать эту привычку сейчас и не называй её просроченной. "
    "Если привычка запланирована на вечер, утром и днём считай её ожидаемой, а не пропущенной. "
    "Долгую память используй тихо для персонализации: не говори 'ты раньше рассказывал', "
    "не пересказывай прошлые разговоры и не поднимай старые темы сам. Вспоминай прошлый "
    "разговор явно только если пользователь сам спросил о нём или это необходимо для "
    "прямого ответа на текущий вопрос. Проактивные напоминания не должны вытаскивать "
    "старые темы из памяти."
)

ROLE_ROUTER_SYSTEM = """
Ты определяешь, какой специалист лучше всего подходит для ответа.

Верни только одно слово:

business
coach
psychology
programming
career
finance
general

Никаких пояснений.
"""

ROLE_PROMPTS = {

"business":"""
Сейчас ты бизнес-наставник.

Ты эксперт в:

• запуске бизнеса
• продажах
• клиентах
• маркетинге
• переговорах
""",

"programming":"""
Сейчас ты Senior Software Engineer.

Отвечай максимально профессионально.
""",

"coach":"""
Сейчас ты лучший коуч по дисциплине.
""",

"psychology":"""
Сейчас ты наставник по психологии.

Не ставь диагнозов.
""",

"career":"""
Сейчас ты карьерный консультант.
""",

"finance":"""
Сейчас ты финансовый консультант.
""",

"general":"""
Будь универсальным помощником.
"""

}

async def detect_role(task: str):

    role = await _ask(
        ROLE_ROUTER_SYSTEM,
        task,
        temperature=0,
        max_tokens=10,
        model=FAST_MODEL
    )

    role = role.strip().lower()

    return ROLE_PROMPTS.get(
        role,
        ROLE_PROMPTS["general"]
    )



BUSINESS_PROMPT = """
Ты опытный предприниматель.

Помогаешь:
- искать клиентов;
- увеличивать продажи;
- создавать бизнес;
- маркетингу;
- финансам;
- переговорам.

Давай конкретные советы.
"""

COACH_PROMPT = """
Ты персональный коуч.

Помогаешь:
- дисциплине;
- привычкам;
- продуктивности;
- режиму дня;
- саморазвитию.
"""

PSYCHOLOGY_PROMPT = """
Ты наставник по психологии.

Помогаешь бороться:

- с прокрастинацией;
- страхами;
- тревожностью;
- отсутствием мотивации.

Не ставишь диагнозы.
"""

PROGRAMMER_PROMPT = """
Ты Senior Software Engineer.

Отвечай максимально профессионально.

Пиши хороший код.

Объясняй архитектуру.
"""

GENERAL_PROMPT = """
Ты универсальный помощник AI ADAM.

Отвечай на любые вопросы пользователя.
"""


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
    """Достаёт из текста ошибки OpenAI время ожидания ('try again in 860ms' / '2.3s')."""
    m = _RETRY_MS_RE.search(error_text)
    if m:
        return float(m.group(1)) / 1000
    m = _RETRY_S_RE.search(error_text)
    if m:
        return float(m.group(1))
    return default


_MD_BOLD_ITALIC = re.compile(r"(\*\*\*|\*\*|___|__)(.+?)\1", re.DOTALL)
_MD_HEADER = re.compile(r"(?m)^#{1,6}\s*")
_MD_CODE_FENCE = re.compile(r"```[a-zA-Z0-9]*\n?|```")
_MD_INLINE_CODE = re.compile(r"`([^`]*)`")


def _strip_markdown(text: str) -> str:
    """Подстраховка: если модель всё же вставит markdown-разметку (звёздочки,
    решётки заголовков, кавычки кода), срезаем её символы, оставляя сам текст,
    т.к. фронтенд чата рендерит ответ как обычный текст, а не как markdown."""
    if not text:
        return text

    prev = None
    while prev != text:
        prev = text
        text = _MD_BOLD_ITALIC.sub(r"\2", text)

    text = _MD_HEADER.sub("", text)
    text = _MD_CODE_FENCE.sub("", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)

    return text.strip()


# Граница конца предложения: точка/!/?/… (с опциональной закрывающей кавычкой
# или скобкой) и пробел после неё. Используется только как аварийная подрезка
# ответа, который модель всё равно не смогла уместить в лимит токенов даже
# после повтора — чтобы пользователь увидел целое предложение, а не обрыв
# на полуслове.
_SENTENCE_END_RE = re.compile(r'[.!?…]["\')\]]?\s')


def _trim_to_last_sentence(text: str) -> str:
    matches = list(_SENTENCE_END_RE.finditer(text))
    if not matches:
        return text.rstrip()
    return text[:matches[-1].end()].rstrip()


async def _ask(
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 500,
    model: str = MODEL,
) -> str:
    """Один быстрый пользовательский LLM-вызов.

    Для Mini App при наличии GROQ_API_KEY используем Groq OpenAI-compatible
    endpoint: текущие production-модели Groq рассчитаны на очень высокую
    скорость генерации. Это важно для цели 3–7 секунд. OpenAI остаётся
    резервным каналом, если Groq не настроен.
    """
    import time

    async def _groq_call():
        client = _get_groq_client()
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _strip_markdown(
            (response.choices[0].message.content or "").strip()
        )

    async def _openai_call():
        client = _get_openai_client()
        response = await client.responses.create(
            model=model,
            instructions=system,
            input=user,
            max_output_tokens=max_tokens,
        )
        text = _strip_markdown((response.output_text or "").strip())
        status = getattr(response, "status", None)
        if status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) if details else None
            if reason == "max_output_tokens":
                # Не делаем второй длинный запрос: лучше быстро вернуть
                # законченный ответ, чем превращать редкий edge case в минуту.
                text = _trim_to_last_sentence(text)
        return text

    if not GROQ_API_KEY and not OPENAI_API_KEY:
        logger.error("Ни GROQ_API_KEY, ни OPENAI_API_KEY не заданы")
        return "[ошибка агента: не настроен API-ключ AI]"

    async with _semaphore:
        started = time.perf_counter()
        try:
            if GROQ_API_KEY:
                result = await _groq_call()
            else:
                result = await _openai_call()

            elapsed = time.perf_counter() - started
            logger.info("AI response %.2fs via %s", elapsed, "Groq" if GROQ_API_KEY else "OpenAI")
            return result
        except Exception as e:
            logger.warning(
                "AI primary request failed after %.2fs: %s",
                time.perf_counter() - started,
                e,
            )
            # Только один короткий резервный вызов, если Groq настроен и
            # OpenAI-ключ тоже есть. Без каскада из трёх повторов.
            if GROQ_API_KEY and OPENAI_API_KEY:
                try:
                    fallback_started = time.perf_counter()
                    result = await _openai_call()
                    logger.info(
                        "AI OpenAI fallback %.2fs",
                        time.perf_counter() - fallback_started,
                    )
                    return result
                except Exception as fallback_error:
                    logger.error("AI fallback failed: %s", fallback_error)
            return "[ошибка агента: AI временно недоступен, попробуй ещё раз]"


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
    "Ты определяешь, требует ли сообщение пользователя развёрнутого ответа. "
    "Если это приветствие, благодарность или короткий смол-ток — ответь "
    "'просто'. Во всех остальных случаях, включая вопросы о бизнесе, "
    "карьере, деньгах, программировании, психологии, привычках, обучении "
    "или саморазвитии — ответь 'сложно'. "
    "Ответь строго одним словом: 'просто' или 'сложно'."
)


async def triage_complexity(task: str) -> str:
    raw = await _ask(TRIAGE_SYSTEM, task, temperature=0.0, max_tokens=5, model=FAST_MODEL)
    value = raw.strip().lower()
    return "просто" if value.startswith("прост") else "сложно"


FAST_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Если сообщение простое, ответь дружелюбно и кратко (1–3 предложения). "
    "Не превращай ответ в длинную лекцию. "
    "Если пользователь задаёт короткий вопрос о бизнесе, программировании, "
    "работе, привычках, психологии или любой другой теме — дай краткий, "
    "но полезный ответ."
)


async def fast_answer(
    task: str,
    history: str = "",
    user_context: str = "",
    mood_note: str = "",
    style_note: str = "",
    humor_note: str = "",
) -> str:
    user = task
    if user_context:
        user = f"Данные о пользователе:\n{user_context}\n\n{user}"
    if history:
        user = f"История переписки:\n{history}\n\n{user}"

    system = FAST_SYSTEM + "\n\n" + _combine_notes(mood_note, style_note, humor_note)
    # Было 300 — для "1-3 предложений" по-русски модели иногда впритык не
    # хватало, и это резало ответ на полуслове (Проблема №1). У _ask теперь
    # есть свой аварийный повтор/подрезка, но на первом заходе лучше сразу
    # дать реальный запас.
    # Terra — основной быстрый режим; Sol включаем только для действительно
    # длинных/сложных запросов. Это сохраняет качество там, где оно нужно,
    # но не заставляет каждый обычный вопрос ждать frontier-модель.
    selected_model = MODEL if len(task or "") > 900 or _needs_deep_pipeline(task) else FAST_MODEL
    return await _ask(system, user, temperature=0.35, max_tokens=500, model=selected_model)


# ============ 0c. КРИЗИС-ГЕЙТ ============
# Это бот-коуч по привычкам, но люди пишут в чат и о по-настоящему тяжёлом
# состоянии. Если это произошло — многоагентный пайплайн (который считает
# запрос темой "привычек") не запускается вовсе: пользователь сразу получает
# заботливый, стабилизирующий ответ и реальные контакты помощи, без советов
# по дисциплине и без вопросов, которые могли бы утянуть глубже.

CRISIS_SYSTEM = (
    "Ты — классификатор безопасности в боте-коуче по привычкам. Определи, "
    "есть ли в сообщении пользователя ЯВНЫЕ признаки острого кризиса: прямые "
    "мысли о самоубийстве или самоповреждении, описание намерения причинить "
    "себе вред, или явно выраженная непереносимая безнадёжность. "
    "Обычные бытовые фразы — приветствия, вопросы 'как дела', 'как ты', "
    "усталость, лёгкое раздражение или грусть по бытовому поводу — это НЕ "
    "кризис, даже если звучат уныло. Кризис — это только явное, недвусмысленное "
    "содержание, а не любое упоминание чувств или состояния.\n\n"
    "Примеры БЕЗ кризиса (ответ 'нет'): 'как дела?', 'привет, как сам?', "
    "'что-то сегодня совсем нет сил', 'устал ужасно', 'всё бесит сегодня', "
    "'грустно как-то'.\n"
    "Примеры С кризисом (ответ 'да'): 'не хочу больше жить', 'думаю о том, "
    "чтобы себя убить', 'хочу порезать себя', 'больше не могу, хочу закончить всё'.\n\n"
    "Если сомневаешься — отвечай 'нет': ложное срабатывание вредит доверию "
    "пользователя больше, чем пропуск неявного случая. "
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
    if not _has_crisis_signal(task):
        return False
    raw = await _ask(CRISIS_SYSTEM, task, temperature=0.0, max_tokens=5, model=FAST_MODEL)
    return raw.strip().lower().startswith("да")


# ============ 0c-2. KEYWORD-ФИЛЬТР (защита от ложных срабатываний) ============
# Кризис-классификатор — это маленькая быстрая модель (FAST_MODEL), а не MODEL,
# и она время от времени ошибочно помечает совершенно нейтральные сообщения
# ("как дела", "привет") как кризис. Промпт с примерами снижает это, но не
# убирает полностью. Поэтому здесь — детерминированный, не-ИИ фильтр: если в
# сообщении нет вообще ни одного слова/корня, хоть отдалённо связанного с
# риском для жизни или самоповреждением, кризис-гейт не сработает НИКОГДА,
# независимо от того, что ответит модель. ИИ-классификатор вызывается и
# учитывается только как дополнительное уточнение уже ПОСЛЕ этого фильтра —
# это убирает ложные срабатывания на бытовых фразах полностью, а не только
# "как правило".

_CRISIS_KEYWORDS = (
    "суицид", "самоуб", "самоповре", "покончить", "покончу",
    "не хочу жить", "не хочу больше жить", "хочу умереть", "хочу сдохнуть",
    "убить себя", "убью себя", "порезать себя", "порежу себя", "порезаться",
    "навредить себе", "причинить себе вред", "нет смысла жить",
    "жить не хочется", "лучше бы я умер", "лучше умереть", "закончить всё",
    "закончить с собой", "свести счёты с жизнью", "не хочу больше жить",
    "hurt myself", "kill myself", "suicide", "self harm", "self-harm",
)


def _has_crisis_signal(task: str) -> bool:
    normalized = task.strip().lower()
    return any(kw in normalized for kw in _CRISIS_KEYWORDS)


# ============ 0d. ОБЪЕДИНЁННЫЙ КЛАССИФИКАТОР ============
# Этап 4 "Оптимизация" — настроение, триаж сложности и кризис-гейт раньше
# были тремя отдельными вызовами OpenAI (пусть и параллельными). Здесь они
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
    '"crisis": <true ТОЛЬКО если есть ЯВНЫЕ признаки острого кризиса: прямые '
    "мысли о самоубийстве или самоповреждении, явное намерение причинить себе "
    "вред, недвусмысленно выраженная непереносимая безнадёжность. Обычные "
    "фразы вроде 'как дела', 'привет', усталость или лёгкая грусть по "
    "бытовому поводу — это НЕ кризис, даже если звучат уныло. При сомнении "
    'ставь false — иначе false>}'
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

        crisis = bool(data.get("crisis", False)) and _has_crisis_signal(task)

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
    # Промт п.9/13: сообщения не должны обрываться на полуслове — подняли
    # потолок токенов для черновых вариантов ответа.
    return await _ask(system, user, temperature=temperature, max_tokens=900)


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
    # Промт п.9/13: финальный (судейский) ответ уходит пользователю напрямую —
    # именно его чаще всего обрезало, потолок токенов увеличен с запасом.
    return await _ask(system, user, temperature=0.3, max_tokens=1500)


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


# ============ 6b. КЛАССИФИКАТОР КОМАНД ПО ПРИВЫЧКАМ/ПЛАНУ ============
# Резервный путь для habit_intents.py: жёсткие regex-шаблоны там ловят только
# явные формулировки ("выполни привычку X", "удали привычку X"). Если человек
# пишет иначе ("я сделал зарядку", "забей на привычку с чтением", "готово, я
# пробежался") — regex не срабатывает, и без этого классификатора сообщение
# просто уходило бы в обычный чат, где модель могла бы РАЗГОВОРНО написать
# "привычка отмечена выполненной", ничего на самом деле не изменив в базе
# (у обычного чата нет доступа к БД). Этот классификатор смотрит на реальный
# список привычек/задач пользователя и решает, какую команду хотел выполнить
# человек, строго выбирая цель ИЗ переданного списка — не выдумывая новых.

INTENT_CLASSIFY_SYSTEM = (
    "Ты определяешь, хочет ли пользователь управлять своими привычками или "
    "планом на день (в приложении для трекинга привычек), и если да — какой "
    "именно командой.\n\n"
    "Верни СТРОГО JSON без пояснений и markdown, в одном из форматов:\n"
    '{"action": "complete_habit", "habit": "<точное название привычки из списка ниже>"}\n'
    '{"action": "delete_habit", "habit": "<точное название привычки из списка ниже>"}\n'
    '{"action": "add_habit", "title": "<короткое новое название, 2-5 слов>"}\n'
    '{"action": "complete_task", "task_number": <номер задачи из списка задач ниже>}\n'
    '{"action": "none"}\n\n'
    "Правила:\n"
    "— Для complete_habit и delete_habit поле \"habit\" ДОЛЖНО дословно "
    "совпадать с одним из названий из списка привычек пользователя — не "
    "исправляй и не сокращай его. Если явно похожей привычки в списке нет — "
    "верни {\"action\": \"none\"}.\n"
    "— complete_habit — когда пользователь говорит, что уже СДЕЛАЛ/выполнил "
    "что-то, что совпадает по смыслу с существующей привычкой (например "
    "написал 'я сделал зарядку' при привычке 'Зарядка по утрам').\n"
    "— delete_habit — когда явно просит убрать/удалить/перестать отслеживать "
    "привычку.\n"
    "— add_habit — когда явно просит завести/добавить новую привычку.\n"
    "— complete_task — когда говорит, что выполнил конкретную задачу из "
    "плана на сегодня (номер задачи бери из списка).\n"
    "— Если сообщение — это просто вопрос, просьба совета или обсуждение "
    "(например 'как лучше выстроить привычку бегать?', 'посоветуй что-то "
    "по productивности') — ВСЕГДА верни {\"action\": \"none\"}, даже если там "
    "упоминается привычка. Команда — это только явное действие."
)


async def classify_habit_action(message: str, habits: list[str], plan_tasks: list[str]) -> dict:
    """Возвращает dict с ключом "action" (см. INTENT_CLASSIFY_SYSTEM) — резерв
    на случай, если жёсткие regex-шаблоны в habit_intents.py не распознали
    команду. При любой ошибке/неуверенности возвращает {"action": "none"},
    чтобы сообщение ушло в обычный AI-чат как раньше."""
    habits_block = (
        "\n".join(f"- {h}" for h in habits) if habits else "(привычек нет)"
    )
    tasks_block = (
        "\n".join(f"{i}. {t}" for i, t in enumerate(plan_tasks, start=1))
        if plan_tasks else "(задач нет)"
    )
    user = (
        f"Привычки пользователя:\n{habits_block}\n\n"
        f"Задачи плана на сегодня:\n{tasks_block}\n\n"
        f"Сообщение пользователя: {message}"
    )
    raw = await _ask(INTENT_CLASSIFY_SYSTEM, user, temperature=0.0, max_tokens=80, model=FAST_MODEL)
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)
        if isinstance(data, dict) and data.get("action") in (
            "complete_habit", "delete_habit", "add_habit", "complete_task", "none"
        ):
            return data
    except Exception as e:
        logger.warning(f"Не удалось распарсить классификацию команды по привычке ({e})")
    return {"action": "none"}


# ============ ОРКЕСТРАТОР ============



def _needs_deep_pipeline(task: str) -> bool:
    """Локальный роутер без сетевого запроса.
    Большинство коротких реплик сразу идут в один быстрый вызов FAST_MODEL.
    Это убирает отдельный LLM-вызов классификатора и обычно экономит 1–3 сек."""
    text = (task or "").strip()
    if len(text) > 500:
        return True
    deep_markers = (
        "разбери", "проанализируй", "сравни", "почему", "пошагово",
        "подробно", "стратеги", "архитектур", "напиши код", "код ",
        "план на", "как лучше выстроить", "несколько вариантов",
        "плюсы и минусы", "что делать, если",
    )
    lowered = text.lower()
    return any(marker in lowered for marker in deep_markers)




def _apply_first_response_note(answer: str, first_message: bool, user_context: str) -> str:
    """Одноразовая фраза в первом ответе и только при фактически выполненных
    всех привычках. Детерминированно, чтобы модель не могла повторять её."""
    if not first_message or not answer:
        return answer
    lines = [line.strip().lower() for line in user_context.splitlines()]
    habit_lines = [line for line in lines if " — да" in line and "привычки пользователя" not in line]
    has_habit_section = "привычки пользователя:" in lines
    if has_habit_section and habit_lines:
        # Если среди строк привычек нет ни одного статуса "нет", все привычки выполнены.
        incomplete = any(" — нет" in line for line in lines if line and "привычки пользователя:" not in line)
        if not incomplete:
            prefix = "Отлично, все привычки уже выполнены — это заслуживает отдельного «плюсика»! 👏"
            if not answer.startswith(prefix):
                return prefix + "\n\n" + answer
    return answer
async def solve_task_multiagent(
    task: str,
    history: str = "",
    user_context: str = "",
    style: str = DEFAULT_STYLE,
    trace: Optional[AgentTrace] = None,
    first_message: bool = False,
    humor_note: str = "",
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

    # Первое сообщение пользователя — единственный момент, когда ADAM
    # может коротко отметить текущий прогресс. Дальше эта фраза не повторяется,
    # пока пользователь сам снова не спросит о выполнении привычек.
    if first_message:
        style_note = _combine_notes(
            style_note,
            "Это первый вопрос пользователя в этой переписке. Если по данным "
            "пользователя действительно выполнены ВСЕ его привычки, в самом начале "
            "первого ответа один раз добавь фразу: «Отлично, все привычки уже "
            "выполнены — это заслуживает отдельного «плюсика»! 👏». Если выполнены "
            "не все привычки, эту фразу не используй. После первого ответа никогда "
            "не повторяй её автоматически и не начинай с неё следующие ответы, "
            "если пользователь прямо не спросил о выполненных привычках."
        )

    # Быстрый путь: один качественный вызов вместо каскада из
    # классификатора -> декомпозера -> воркеров -> синтезатора -> дебатёров ->
    # судьи. На длинных запросах старый каскад мог занимать десятки секунд.
    # ADAM получает тот же актуальный user_context и историю, но отвечает
    # напрямую — это и есть основной режим приложения.
    if not _has_crisis_signal(task):
        trace.mood = "нейтрально"
        trace.complexity = "прямой быстрый ответ"
        trace.is_crisis = False
        answer = await fast_answer(task, history, user_context, "", style_note, humor_note)
        answer = _apply_first_response_note(answer, first_message, user_context)
        trace.final_answer = answer
        return {
            "answer": answer,
            "mood": "нейтрально",
            "is_crisis": False,
            "suggested_habit": None,
            "complexity": "прямой быстрый ответ",
        }

    # Только сообщения с явным кризисным сигналом проходят отдельную
    # проверку безопасности. Это сохраняет защитный контур без замедления
    # обычного чата.
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

    answer = await fast_answer(task, history, user_context, "", style_note, humor_note)
    answer = _apply_first_response_note(answer, first_message, user_context)
    trace.final_answer = answer
    return {
        "answer": answer,
        "mood": mood,
        "is_crisis": False,
        "suggested_habit": None,
        "complexity": complexity or "прямой быстрый ответ",
    }



# ============ ДОЛГОСРОЧНАЯ ПАМЯТЬ ============
# Раз в несколько сообщений (см. handlers/ai.py) текущий профиль пользователя
# пересобирается из старого профиля + свежих реплик — так бот помнит то, что
# терялось бы за пределами короткой истории (build_history_text режет её до
# нескольких последних сообщений). Этап 2 AI Core.

MEMORY_SUMMARY_SYSTEM = (
    "Ты ведёшь два слоя памяти AI-коуча. Верни СТРОГО JSON без markdown: "
    '{"summary":"...","followup":"..."}'
    "summary — 3-6 кратких устойчивых фактов о пользователе, полезных в будущем: "
    "цели, устойчивые предпочтения, длительные ограничения или обстоятельства. "
    "Если пользователь прямо просит что-то запомнить (например, стиль ответов, "
    "желаемую длину, формат общения или постоянное правило), ОБЯЗАТЕЛЬНО сохрани "
    "это как устойчивое предпочтение. Такие явные просьбы о памяти важнее обычных "
    "разговорных деталей. НЕ записывай разовые разговоры, временные задачи, отдельные вопросы, идеи, "
    "поиск подработки/работы или тему, которую человек просто обсуждал один раз, "
    "если он прямо не просил запомнить это как постоянный факт. Не включай названия "
    "привычек и серию дней. "
    "followup — максимум ОДНА короткая тема из свежего разговора, которую уместно "
    "мягко упомянуть один раз в следующий день. Если такой темы нет — пустая строка. "
    "followup никогда не должен быть повторяющимся напоминанием и не должен жить дольше 24 часов. "
    "Не помещай в followup тему, если пользователь не просил о ней напоминать или если "
    "это обычная разовая беседа. В дальнейшем прошлые разговоры не упоминай сам."
)


async def summarize_user_memory(existing_summary: str, recent_history: str) -> str:
    user = (
        f"Текущий профиль:\n{existing_summary or '(пусто)'}\n\n"
        f"Новые реплики:\n{recent_history or '(нет)'}"
    )
    result = await _ask(MEMORY_SUMMARY_SYSTEM, user, temperature=0.2, max_tokens=260, model=FAST_MODEL)
    if result.startswith("[ошибка агента"):
        return {"summary": existing_summary, "followup": ""}
    try:
        cleaned = result.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)
        return {
            "summary": str(data.get("summary") or existing_summary).strip()[:1500],
            "followup": str(data.get("followup") or "").strip()[:180],
        }
    except Exception:
        return {"summary": result.strip()[:1500], "followup": ""}


# ============ АНАЛИЗ АНКЕТЫ ПРИ ВХОДЕ (onboarding) ============
# Один вызов после того, как пользователь ответил на 4 вопроса анкеты:
# превращает свободный текст в короткое summary + список тегов интересов
# для админки (используется, чтобы админ мог сегментировать пользователей
# по интересам без ручного чтения анкет).

SURVEY_ANALYSIS_SYSTEM = (
    "Ты анализируешь анкету нового пользователя бота-коуча по привычкам и "
    "целям (Project ADAM). Верни СТРОГО JSON без пояснений и markdown-разметки, "
    "в формате:\n"
    '{"summary": "<2-3 предложения: кто этот человек, чем занимается, к чему '
    'стремится — от третьего лица>", '
    '"tags": ["<3-6 коротких тегов интересов/сферы деятельности, '
    'например: бизнес, спорт, финансы, здоровье, творчество, обучение>"]}'
)


async def analyze_onboarding_survey(business: str, hobbies: str, life_goal: str, bot_goal: str) -> dict:
    """Возвращает {"summary": str, "tags": list[str]}. При сбое парсинга или
    вызова — безопасный фолбэк, чтобы анкетирование не блокировалось."""
    user = (
        f"Чем занимается / бизнес: {business}\n"
        f"Увлечения: {hobbies}\n"
        f"Цель в жизни: {life_goal}\n"
        f"Цель в боте: {bot_goal}"
    )
    raw = await _ask(SURVEY_ANALYSIS_SYSTEM, user, temperature=0.3, max_tokens=300, model=FAST_MODEL)
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)
        summary = str(data.get("summary", "")).strip()[:600]
        tags = data.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if str(t).strip()][:6]
        return {"summary": summary, "tags": tags}
    except Exception as e:
        logger.warning(f"Не удалось распарсить анализ анкеты ({e})")
        return {"summary": "", "tags": []}


# ============ ПЕРВЫЙ ШАГ ПОСЛЕ ОДОБРЕНИЯ ДОСТУПА ============
# Вызывается один раз, сразу после того как пользователь получает доступ —
# превращает цель из анкеты (bot_goal) в конкретную первую привычку и
# 3 вехи на пути к цели, чтобы человек не оставался один на один с пустым
# меню сразу после "эксклюзивного доступа".

FIRST_HABIT_SYSTEM = (
    "Ты помогаешь новому пользователю бота-коуча по привычкам начать. Дана "
    "его цель в боте. Верни СТРОГО JSON без пояснений и markdown-разметки:\n"
    '{"habit": "<короткое (до 6 слов) название ОДНОЙ ежедневной привычки, '
    'которая реально приближает к этой цели>", '
    '"milestones": ["<веха 1>", "<веха 2>", "<веха 3>"]} '
    "Вехи — это 3 конкретных промежуточных результата по пути к цели, "
    "от простого к сложному, каждая до 10 слов."
)


async def suggest_first_step(bot_goal: str) -> dict:
    """Возвращает {"habit": str, "milestones": list[str]}. При сбое —
    безопасный фолбэк с общей привычкой, чтобы онбординг не падал."""
    raw = await _ask(FIRST_HABIT_SYSTEM, bot_goal, temperature=0.4, max_tokens=300, model=FAST_MODEL)
    try:
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)
        habit = str(data.get("habit", "")).strip()[:60] or "Ежедневный шаг к цели"
        milestones = data.get("milestones", [])
        if not isinstance(milestones, list):
            milestones = []
        milestones = [str(m).strip() for m in milestones if str(m).strip()][:3]
        if not milestones:
            milestones = ["Первая неделя без пропусков", "Первый заметный результат", "Цель достигнута"]
        return {"habit": habit, "milestones": milestones}
    except Exception as e:
        logger.warning(f"Не удалось распарсить первый шаг онбординга ({e})")
        return {
            "habit": "Ежедневный шаг к цели",
            "milestones": ["Первая неделя без пропусков", "Первый заметный результат", "Цель достигнута"],
        }


# ============ AI-РАЗБОР ЦЕЛИ vs РЕАЛЬНЫЙ ПРОГРЕСС (Premium) ============
# Раз в неделю сравнивает то, что человек написал в анкете, с тем, что
# реально происходит (серия, доля выполненных привычек) — и говорит,
# где он торопится, а где стоит поднажать.

GOAL_FEEDBACK_SYSTEM = (
    "Ты — AI-наставник в боте по привычкам и целям. Пользователь раньше "
    "написал в анкете свою цель. Тебе дана эта цель и его реальная "
    "статистика за последнее время. Напиши короткую (3-5 предложений) "
    "честную обратную связь от второго лица ('ты'): что реально по цели, "
    "где человек торопится или поставил нереалистичный темп, а где "
    "наоборот стоит поднажать. Без воды и общих фраз, опирайся на цифры."
)


async def analyze_goal_progress(life_goal: str, bot_goal: str, streak: int, completed_ratio: float) -> str:
    user = (
        f"Цель в жизни: {life_goal}\n"
        f"Цель в боте: {bot_goal}\n"
        f"Текущая серия дней подряд: {streak}\n"
        f"Доля выполненных привычек за последнее время: {round(completed_ratio * 100)}%"
    )
    try:
        return await _ask(GOAL_FEEDBACK_SYSTEM, user, temperature=0.5, max_tokens=300, model=FAST_MODEL)
    except Exception as e:
        logger.warning(f"Не удалось получить разбор цели ({e})")
        return ""


# ============ ПЕРСОНАЛИЗИРОВАННОЕ УТРЕННЕЕ СООБЩЕНИЕ ============

MORNING_SYSTEM = (
    "Ты — AI-наставник в боте по привычкам. Напиши короткое (1-3 предложения) "
    "утреннее приветствие пользователю, задающее настрой на день. Учитывай "
    "стиль общения и то, что известно о человеке. Без канцелярита, живо, "
    "по-русски. Не используй фразы 'доброе утро' дважды и не пиши markdown."
)


async def generate_morning_message(style: str, profile_summary: str, streak: int) -> str:
    user = (
        f"Стиль общения: {style}\n"
        f"Известно о пользователе: {profile_summary or 'пока немного'}\n"
        f"Текущая серия дней подряд: {streak}"
    )
    try:
        return await _ask(MORNING_SYSTEM, user, temperature=0.7, max_tokens=150, model=FAST_MODEL)
    except Exception as e:
        logger.warning(f"Не удалось сгенерировать утреннее сообщение ({e})")
        return ""


# ============ СОВЕТ ДНЯ ============
# Отдельная лёгкая функция (не весь мультиагентный пайплайн) для кнопки
# "💡 Совет дня" — этап 3 AI Coach, "персональные советы". Результат
# НЕ кэшируется: повторное нажатие должно получать свежий снимок текущих
# задач и привычек, иначе вечерний совет будет повторять утренний контекст.

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

    # Было 250 — впритык для "2-4 предложений" по-русски, резало ответ
    # на полуслове (Проблема №1).
    return await _ask(system, user, temperature=0.6, max_tokens=400, model=FAST_MODEL)


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

    # Было 350 — впритык для "3-5 предложений" по-русски, резало ответ
    # на полуслове (Проблема №1).
    return await _ask(system, user, temperature=0.5, max_tokens=500, model=FAST_MODEL)


# ============ ЕЖЕНЕДЕЛЬНЫЙ AI-РАЗБОР ПО ПРИВЫЧКАМ (закрашенные дни) ============
# Раз в неделю смотрим не на агрегат, а на КАЖДУЮ привычку отдельно —
# сколько раз выполнена/пропущена за 7 дней — и даём конкретный совет
# именно по той привычке, что сильнее всего проседает (например снизить
# нагрузку/время, чтобы вернуться в ритм), а не общие слова.

HABIT_BREAKDOWN_SYSTEM = (
    BASE_PERSONA + "\n\n"
    "Тебе дана разбивка по привычкам пользователя за последние 7 дней: "
    "сколько раз каждая выполнена и сколько раз пропущена. Напиши короткую "
    "(2-4 предложения) персонализированную обратную связь от второго лица "
    "('ты'). Обязательно назови привычку, которая сильнее всего просела, "
    "по имени, и число пропущенных дней. Если пропусков много — предложи "
    "ОДНО конкретное, небольшое послабление именно по этой привычке (например "
    "сократить время/объём), чтобы вернуться в ритм, а не бросать её "
    "совсем. Если все привычки выполняются хорошо — похвали конкретно, тоже "
    "по имени привычки. Без воды и общих фраз, опирайся на цифры."
)


async def generate_weekly_habit_feedback(breakdown_text: str, style: str = DEFAULT_STYLE) -> str:
    style_note = STYLE_NOTES.get(style, "")
    system = HABIT_BREAKDOWN_SYSTEM
    if style_note:
        system = system + "\n\n" + style_note

    user = f"Привычки за последние 7 дней:\n{breakdown_text}"

    try:
        return await _ask(system, user, temperature=0.5, max_tokens=300, model=FAST_MODEL)
    except Exception as e:
        logger.warning(f"Не удалось сгенерировать недельный разбор по привычкам ({e})")
        return ""


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
    # (нужен доступный рядом config.py с OPENAI_API_KEY, как в остальном боте)
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

def detect_role_by_keywords(text: str):

    text = text.lower()

    if any(word in text for word in [
        "бизнес",
        "клиент",
        "деньги",
        "заработ",
        "продаж",
        "маркетинг"
    ]):
        return BUSINESS_PROMPT

    if any(word in text for word in [
        "привыч",
        "дисципл",
        "мотивац",
        "цель",
        "продуктив"
    ]):
        return COACH_PROMPT

    if any(word in text for word in [
        "депресс",
        "тревог",
        "страх",
        "лень",
        "выгор"
    ]):
        return PSYCHOLOGY_PROMPT

    if any(word in text for word in [
        "python",
        "код",
        "html",
        "css",
        "javascript",
        "бот",
        "api"
    ]):
        return PROGRAMMER_PROMPT

    return GENERAL_PROMPT

