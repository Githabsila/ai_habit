"""
Готовит видео-петли героя для Mini App (см. db/hero.py, app.js::syncHeroVideo).

Берёт «сырые» клипы из нейросети (image-to-video) и делает из них лёгкие mp4:
2:3, 480x720, 24 к/с, H.264 (играет и в Android WebView, и в iOS), без звука,
moov-атом в начале файла (начинает играть, не дожидаясь полной загрузки).

Клипы называются по ключу состояния:
    start  early  growth  peak  at_risk  ended  long_break  return
в любом видео-формате (mp4, mov, webm, mkv...). Например: start.mov, peak.mp4.

Запуск (из корня проекта):
    py tools/convert_hero_videos.py C:\\путь\\к\\папке\\с\\клипами
    py tools/convert_hero_videos.py C:\\клипы --pingpong     # петля вперёд-назад
    py tools/convert_hero_videos.py C:\\клипы --only peak ended
    py tools/convert_hero_videos.py C:\\клипы --crop-y 0.25  # клип 9:16: срезать больше снизу
    py tools/convert_hero_videos.py C:\\клипы --poster       # подложка = первый кадр клипа

Если нейросеть изменила кадрирование (обрезала или приблизила картинку),
используйте --poster: иначе при появлении видео поверх картинки будет
заметный «скачок» зума.

Готовые файлы кладутся в webapp/static/assets/hero/{ключ}.mp4 — сервер сам
заметит их и начнёт отдавать hero.video; для состояний без файла остаётся
картинка. Нужен ffmpeg: системный или из пакета imageio-ffmpeg
(`py -m pip install imageio-ffmpeg`).
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "webapp" / "static" / "assets" / "hero"
KEYS = ("start", "early", "growth", "peak", "at_risk", "ended", "long_break", "return")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".gif")
WARN_BYTES = 1_500_000


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("Не найден ffmpeg. Установите его или выполните: py -m pip install imageio-ffmpeg")


def find_source(folder, key):
    for ext in VIDEO_EXTS:
        for candidate in (folder / f"{key}{ext}", folder / f"{key}{ext.upper()}"):
            if candidate.is_file():
                return candidate
    return None


def crop_scale(width, height, crop_y=0.5):
    # Кроп до 2:3 (если клип другого формата), затем масштаб. По горизонтали
    # всегда по центру; по вертикали crop_y: 0 — срезать только снизу, 0.5 —
    # поровну сверху и снизу, 1 — только сверху.
    return (
        f"crop=w='min(iw,ih*2/3)':h='min(ih,iw*3/2)':x='(iw-out_w)/2':y='(ih-out_h)*{crop_y}',"
        f"scale={width}:{height}:flags=lanczos"
    )


def build_filter(width, height, fps, pingpong, crop_y=0.5):
    base = f"{crop_scale(width, height, crop_y)},fps={fps},format=yuv420p"
    if not pingpong:
        return base
    # Вперёд, затем тот же клип задом наперёд: конец стыкуется с началом без
    # скачка (подходит для спокойного движения — дыхание, огонь, волосы).
    return f"{base},split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0"


def convert(ffmpeg, src, dst, args):
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-t", str(args.seconds), "-i", str(src),
        "-vf", build_filter(args.width, args.height, args.fps, args.pingpong, args.crop_y),
        "-an",
        "-c:v", "libx264", "-profile:v", "main", "-level", "3.1",
        "-pix_fmt", "yuv420p", "-crf", str(args.crf), "-preset", "slow",
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True)


def make_poster(ffmpeg, src, dst, args):
    """Первый кадр клипа с тем же кропом -> {ключ}.webp (картинка-подложка).
    Нужна, когда кадрирование клипа не совпадает с исходной картинкой
    (нейросеть обрезала/приблизила): иначе при появлении видео поверх
    картинки был бы заметный «скачок» зума."""
    try:
        from PIL import Image
    except ImportError:
        sys.exit("Для --poster нужен Pillow: py -m pip install pillow")
    tmp = dst.with_suffix(".poster.png")
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-vf", crop_scale(args.poster_width, args.poster_width * 3 // 2, args.crop_y),
        "-frames:v", "1", str(tmp),
    ], check=True)
    try:
        Image.open(tmp).convert("RGB").save(dst, "WEBP", quality=82, method=6)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="папка с исходными клипами (имя файла = ключ состояния)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="куда класть mp4 (по умолчанию assets/hero)")
    parser.add_argument("--only", nargs="*", choices=KEYS, help="только эти состояния")
    parser.add_argument("--pingpong", action="store_true", help="склеить клип с его реверсом для бесшовной петли")
    parser.add_argument("--seconds", type=float, default=6.0, help="максимум секунд исходного клипа (по умолчанию 6)")
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument(
        "--crop-y", type=float, default=0.5,
        help="если клип не 2:3 (например 9:16), откуда срезать лишнее по вертикали: "
             "0 — только снизу, 0.5 — поровну (по умолчанию), 1 — только сверху. "
             "Если голова у верхнего края — уменьшите (0.25)",
    )
    parser.add_argument("--crf", type=int, default=27, help="качество H.264: меньше — лучше и тяжелее (по умолчанию 27)")
    parser.add_argument(
        "--poster", action="store_true",
        help="заодно заменить картинку-подложку {ключ}.webp первым кадром клипа (с тем же кропом)",
    )
    parser.add_argument("--poster-width", type=int, default=600, help="ширина подложки (высота = 3/2 ширины)")
    args = parser.parse_args()

    if not args.folder.is_dir():
        sys.exit(f"Папка не найдена: {args.folder}")
    ffmpeg = find_ffmpeg()
    args.out.mkdir(parents=True, exist_ok=True)

    done = missing = 0
    for key in (args.only or KEYS):
        src = find_source(args.folder, key)
        if src is None:
            print(f"  {key:11s} — нет файла в {args.folder} (пропуск)")
            missing += 1
            continue
        dst = args.out / f"{key}.mp4"
        convert(ffmpeg, src, dst, args)
        size = dst.stat().st_size
        note = "  <- тяжеловато, поднимите --crf (28–30) или сократите --seconds" if size > WARN_BYTES else ""
        print(f"  {key:11s} {src.name} -> {dst.name}  {size / 1024:.0f} КБ{note}")
        if args.poster:
            poster = args.out / f"{key}.webp"
            make_poster(ffmpeg, src, poster, args)
            print(f"  {'':11s} подложка {poster.name}  {poster.stat().st_size / 1024:.0f} КБ")
        done += 1
    print(f"Готово: {done}, пропущено: {missing}.")


if __name__ == "__main__":
    main()
