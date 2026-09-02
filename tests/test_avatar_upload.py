"""
Улучшение #74: загрузка аватарки декодируется и пересжимается через Pillow
вместо сохранения сырых байтов под расширением .jpg. Заодно закрывает дыру:
раньше файл с Content-Type: image/jpeg, но НЕ являющийся валидным изображением,
просто сохранялся на диск как есть.
"""
from io import BytesIO

from PIL import Image
import aiohttp

from db import add_user

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}"}


def _png_bytes(size=(800, 600), color=(200, 50, 50)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


async def test_upload_avatar_converts_png_to_real_jpeg_and_resizes(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    form = aiohttp.FormData()
    form.add_field("avatar", _png_bytes(), filename="pic.png", content_type="image/png")

    r = await client.post("/api/profile/avatar", headers=headers, data=form)
    assert r.status == 200
    data = await r.json()
    assert data["ok"] is True
    assert data["avatar_id"] == f"upload:{uid}"

    media = await client.get(data["avatar_url"].split("?")[0])
    assert media.status == 200
    body = await media.read()
    saved = Image.open(BytesIO(body))
    # Реально сохранённый формат — JPEG (не переименованный PNG), и результат
    # всегда квадратный ровно 512×512 — вход был 800×600 (не квадрат).
    assert saved.format == "JPEG"
    assert saved.size == (512, 512)


async def test_upload_avatar_center_crops_non_square_photo_to_square(client, uid):
    # Фидбек: не-квадратное фото раньше сохранялось "растянутым" — вписывалось
    # в 512×512 БЕЗ обрезки, что ломало квадратные рамки аватарки в UI.
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    # Широкая картинка 1000×400 — после центр-кропа должен остаться средний
    # квадрат 400×400, а не сплющенное изображение.
    form = aiohttp.FormData()
    form.add_field("avatar", _png_bytes(size=(1000, 400)), filename="wide.png", content_type="image/png")

    r = await client.post("/api/profile/avatar", headers=headers, data=form)
    assert r.status == 200
    data = await r.json()

    media = await client.get(data["avatar_url"].split("?")[0])
    body = await media.read()
    saved = Image.open(BytesIO(body))
    assert saved.size == (400, 400)


async def test_upload_avatar_rejects_bytes_that_are_not_really_an_image(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    form = aiohttp.FormData()
    # Content-Type врёт — это не картинка. Раньше такое молча сохранялось.
    form.add_field("avatar", b"not-actually-an-image" * 10, filename="pic.jpg", content_type="image/jpeg")

    r = await client.post("/api/profile/avatar", headers=headers, data=form)
    assert r.status == 400
    data = await r.json()
    assert data["error"] == "unsupported_image"


async def test_upload_avatar_rejects_wrong_content_type(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    form = aiohttp.FormData()
    form.add_field("avatar", _png_bytes(), filename="pic.gif", content_type="image/gif")

    r = await client.post("/api/profile/avatar", headers=headers, data=form)
    assert r.status == 400
    data = await r.json()
    assert data["error"] == "unsupported_image"


async def test_upload_avatar_rejects_oversized_file(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    form = aiohttp.FormData()
    form.add_field("avatar", b"\x00" * (5 * 1024 * 1024 + 1), filename="pic.jpg", content_type="image/jpeg")

    r = await client.post("/api/profile/avatar", headers=headers, data=form)
    assert r.status == 413
