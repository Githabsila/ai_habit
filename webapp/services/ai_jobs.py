"""
Фоновые задачи «ответ ADAM» — ответ дописывается, даже если человек ушёл из чата.

Раньше ответ генерировался прямо в обработчике запроса: ушёл на главный экран (страница /coach выгрузилась, fetch оборвался),
вернулся — в диалоге висело только его сообщение, ответа не было (экран берёт переписку из sessionStorage, а ответ туда так и
не попал). Теперь запрос запускает отдельную задачу (asyncio.Task), которую обрыв клиента не отменяет: она сама дописывает ответ
в историю и кладёт итог в реестр. Вернувшийся в чат клиент спрашивает итог у GET /api/ai/chat/result по номеру запроса
(request_id) и дорисовывает ответ внизу диалога.

Реестр — в памяти процесса (один сервис на Railway): на человека хранится последняя задача, готовый итог живёт
JOB_TTL_SECONDS. Потерялся при перезапуске сервиса — клиент берёт переписку из истории (/api/ai/history).
"""
import asyncio
import logging
import time

logger = logging.getLogger("webapp.ai_jobs")

JOB_TIMEOUT_SECONDS = 180       # зависшая модель не должна навсегда занимать человека («ADAM ещё отвечает»)
JOB_TTL_SECONDS = 15 * 60       # сколько хранится готовый итог


class ChatJob:
    __slots__ = ("rid", "text", "started_at", "finished_at", "status", "payload", "task")

    def __init__(self, rid: str, text: str):
        self.rid = rid
        self.text = text
        self.started_at = time.monotonic()
        self.finished_at = None        # monotonic-время завершения; None — ещё думает
        self.status = None             # HTTP-статус итога
        self.payload = None            # JSON-тело итога
        self.task = None

    @property
    def done(self) -> bool:
        return self.finished_at is not None


class ChatJobs:
    def __init__(self):
        self._by_user = {}

    def get(self, user_id: int, rid: str | None = None):
        """Последняя задача человека (если указан rid — только с этим номером). Устаревшие итоги выбрасываются."""
        job = self._by_user.get(user_id)
        if job is None:
            return None
        if job.done and time.monotonic() - job.finished_at > JOB_TTL_SECONDS:
            self._by_user.pop(user_id, None)
            return None
        if rid is not None and job.rid != rid:
            return None
        return job

    def active(self, user_id: int):
        job = self.get(user_id)
        return job if job is not None and not job.done else None

    def start(self, user_id: int, rid: str, text: str, runner):
        """runner — async-функция без аргументов, возвращает (http_status, json_payload). Исключения наружу не выходят."""
        job = ChatJob(rid, text)
        self._by_user[user_id] = job
        job.task = asyncio.create_task(self._run(job, runner))
        return job

    async def _run(self, job: ChatJob, runner):
        try:
            job.status, job.payload = await asyncio.wait_for(runner(), JOB_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.error("ответ ADAM не уложился в %s с", JOB_TIMEOUT_SECONDS)
            job.status = 504
            job.payload = {"error": "ai_timeout", "message": "ADAM слишком долго думал над ответом. Попробуй ещё раз."}
        except asyncio.CancelledError:
            job.status, job.payload = 499, {"error": "cancelled", "message": "Диалог был очищен."}
            job.finished_at = time.monotonic()
            raise
        except Exception:
            logger.exception("ответ ADAM: необработанная ошибка задачи")
            job.status = 500
            job.payload = {"error": "ai_error", "message": "Не получилось сформировать ответ. Попробуйте ещё раз через минуту."}
        job.finished_at = time.monotonic()

    def cancel(self, user_id: int):
        """«Новый диалог»: незаконченный ответ не должен дописаться в уже очищенную переписку."""
        job = self._by_user.pop(user_id, None)
        if job is not None and job.task is not None and not job.task.done():
            job.task.cancel()

    def result(self, user_id: int, rid: str | None):
        """Что ответить клиенту, вернувшемуся за итогом."""
        job = self.get(user_id, rid)
        if job is None:
            return {"status": "unknown"}
        if not job.done:
            return {"status": "pending", "elapsed": round(time.monotonic() - job.started_at, 1)}
        return {"status": "done", "ok": job.status == 200, "http_status": job.status, "data": job.payload}


ai_jobs = ChatJobs()
