"""
/api/bootstrap и другие JSON-ответы раньше уходили без сжатия — gzip был
только у пары ручных статических маршрутов (style.css/app.js). А ведь
именно /api/bootstrap качается на каждом открытии Mini App. См. фикс в
error_middleware (webapp/webapp_server.py) — общее gzip-сжатие для
текстовых content-type при Accept-Encoding: gzip.
"""
from db import add_user, add_habit

from tests.conftest import sign_init_data


async def test_bootstrap_response_is_gzip_compressed_when_accepted(client, uid):
    add_user(uid, "tester", "Test")
    for i in range(10):
        add_habit(uid, f"Привычка номер {i} с достаточно длинным названием")

    r = await client.get(
        "/api/bootstrap",
        headers={"Authorization": f"tma {sign_init_data(uid)}", "Accept-Encoding": "gzip"},
        auto_decompress=False,
    )

    assert r.status == 200
    assert r.headers.get("Content-Encoding") == "gzip"
    assert r.headers.get("Vary") == "Accept-Encoding"


async def test_bootstrap_response_not_compressed_without_accept_encoding(client, uid):
    add_user(uid, "tester", "Test")
    for i in range(10):
        add_habit(uid, f"Привычка номер {i} с достаточно длинным названием")

    r = await client.get(
        "/api/bootstrap",
        headers={"Authorization": f"tma {sign_init_data(uid)}", "Accept-Encoding": "identity"},
        auto_decompress=False,
    )

    assert r.status == 200
    assert "Content-Encoding" not in r.headers


async def test_small_json_response_is_not_compressed(client, uid):
    """Ниже порога _MIN_COMPRESSIBLE_SIZE сжатие не имеет смысла — gzip
    добавил бы накладные расходы вместо экономии."""
    r = await client.get(
        "/api/pet",
        headers={"Authorization": f"tma {sign_init_data(uid)}", "Accept-Encoding": "gzip"},
        auto_decompress=False,
    )

    assert r.status == 200
    assert "Content-Encoding" not in r.headers
