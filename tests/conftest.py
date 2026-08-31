"""
Общая настройка тестов. ВАЖНО: RAILWAY_VOLUME_MOUNT_PATH выставляется
ДО первого импорта db.* где бы то ни было в тестовом процессе —
DATA_DIR/DB_PATH в db/core.py вычисляются один раз при импорте модуля
(это осознанное решение для продакшена, не баг — см. комментарий там),
поэтому это первое, что делает conftest.py, раньше любых других импортов.

Один общий изолированный SQLite-файл на весь тестовый прогон, отдельный
от продакшен users.db. Тесты используют разные telegram_id (см. фикстуру
`uid` ниже), чтобы не пересекаться друг с другом в одной базе.
"""
import itertools
import os
import shutil
import sys
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="adam_test_db_")
os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = _TEST_DATA_DIR
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_REAL")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import pytest  # noqa: E402

from db.core import create_tables  # noqa: E402

create_tables()

_uid_counter = itertools.count(900_000_000)


@pytest.fixture
def uid():
    """Свежий уникальный telegram_id на каждый тест."""
    return next(_uid_counter)


def sign_init_data(telegram_id, first_name="Tester", username="tester"):
    """Подписывает валидный Telegram initData тем же алгоритмом, что
    webapp/telegram_auth.py::validate_init_data — для HTTP-тестов
    Mini App API через aiohttp TestClient."""
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import quote

    from config import BOT_TOKEN

    user = json.dumps({"id": telegram_id, "first_name": first_name, "username": username})
    params = {"user": user, "auth_date": str(int(time.time())), "query_id": "AAtest"}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    params["hash"] = calculated_hash
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())


@pytest.fixture
async def client():
    """aiohttp TestClient, поднятый на реальном create_app()."""
    from aiohttp.test_utils import TestClient, TestServer
    from webapp.webapp_server import create_app

    app = create_app(bot=None)
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c
    await c.close()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
