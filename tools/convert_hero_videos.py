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


def build_filter(width, height, fps, pingpong):
    # Центрированный кроп до 2:3 (если клип другого формата), затем масштаб.
    base = (
        f"crop='min(iw,ih*2/3)':'min(ih,iw*3/2)',"
        f"scale={width}:{height}:flags=lanczos,fps={fps},format=yuv420p"
    )
    if not pingpong:
        return base
    # Вперёд, затем тот же клип задом наперёд: конец стыкуется с началом без
    # скачка (подходит для спокойного движения — дыхание, огонь, волосы).
    return f"{base},split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0"


def convert(ffmpeg, src, dst, args):
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-t", str(args.seconds), "-i", str(src),
        "-vf", build_filter(args.width, args.height, args.fps, args.pingpong),
        "-an",
        "-c:v", "libx264", "-profile:v", "main", "-level", "3.1",
        "-pix_fmt", "yuv420p", "-crf", str(args.crf), "-preset", "slow",
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True)


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
    parser.add_argument("--crf", type=int, default=27, help="качество H.264: меньше — лучше и тяжелее (по умолчанию 27)")
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
        done += 1
    print(f"Готово: {done}, пропущено: {missing}.")


if __name__ == "__main__":
    main()
