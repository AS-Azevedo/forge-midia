import argparse
import os
import shutil
import subprocess
from pathlib import Path

try:
    from PIL import Image
    PIL_OK = True
except Exception:
    PIL_OK = False

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
GIF_EXTS = {".gif"}

# Defaults para Windows (sem depender de PATH)
DEFAULT_FFMPEG_WIN = r"C:\ffmpeg\bin\ffmpeg.exe"
DEFAULT_FFPROBE_WIN = r"C:\ffmpeg\bin\ffprobe.exe"


def run(cmd):
    """Run command, raise on error, return stdout."""
    print(" ".join(cmd))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout)
    return p.stdout


def resolve_tool_path(cli_value: str | None, env_var: str, default_win: str, tool_name: str) -> str:
    """
    Resolve path order:
    1) CLI --ffmpeg / --ffprobe
    2) ENV FFMPEG_PATH / FFPROBE_PATH
    3) Default Windows path (C:\\ffmpeg\\bin\\...)
    4) fallback to just tool_name (PATH)
    """
    if cli_value:
        p = Path(cli_value)
        if p.exists():
            return str(p)
        raise SystemExit(f"{tool_name} não encontrado em: {cli_value}")

    env = os.getenv(env_var)
    if env:
        p = Path(env)
        if p.exists():
            return str(p)
        raise SystemExit(f"{tool_name} não encontrado no {env_var}: {env}")

    # tenta default do Windows
    p = Path(default_win)
    if p.exists():
        return str(p)

    # fallback PATH
    return tool_name


def ffprobe_has_audio(src: Path, ffprobe_path: str) -> bool:
    cmd = [
        ffprobe_path, "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(src)
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return bool(p.stdout.strip())


def optimize_video_to_mp4(
    src: Path,
    dst: Path,
    max_width: int,
    fps: int,
    crf: int,
    preset: str,
    ffmpeg_path: str
):
    # Scale: keep aspect ratio; -2 ensures even height for H.264
    vf = f"scale='min({max_width},iw)':-2:flags=lanczos,fps={fps}"

    cmd = [
        ffmpeg_path, "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level", "4.0",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",  # remove audio always
        str(dst)
    ]
    run(cmd)


def optimize_gif_to_mp4(
    src: Path,
    dst: Path,
    max_width: int,
    fps: int,
    crf: int,
    preset: str,
    ffmpeg_path: str
):
    vf = f"scale='min({max_width},iw)':-2:flags=lanczos,fps={fps}"

    cmd = [
        ffmpeg_path, "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level", "4.0",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        str(dst)
    ]
    run(cmd)


def optimize_image(src: Path, dst: Path, max_width: int, jpeg_quality: int, webp_quality: int):
    """
    Apple-tier guidance:
    - If PNG has alpha: WebP (lossy q ~ 80) to preserve transparency cheaply.
    - If no alpha: JPEG quality ~ 82 for photos/frames.
    """
    if not PIL_OK:
        shutil.copy2(src, dst)
        return

    img = Image.open(src)
    img.load()

    w, h = img.size
    if w > max_width:
        new_h = int(h * (max_width / w))
        img = img.resize((max_width, new_h), Image.LANCZOS)

    has_alpha = (img.mode in ("RGBA", "LA")) or ("transparency" in img.info)

    if has_alpha:
        dst = dst.with_suffix(".webp")
        img.save(dst, "WEBP", quality=webp_quality, method=6)
    else:
        dst = dst.with_suffix(".jpg")
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(dst, "JPEG", quality=jpeg_quality, optimize=True, progressive=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", required=True, help="Input folder with media files")
    ap.add_argument("--out", dest="out_dir", required=True, help="Output folder")
    ap.add_argument("--max_width", type=int, default=720)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--crf", type=int, default=28, help="Lower = better quality, bigger size (26-30 typical)")
    ap.add_argument("--preset", default="slow", help="ffmpeg preset: slow/medium/fast")
    ap.add_argument("--jpeg_quality", type=int, default=82)
    ap.add_argument("--webp_quality", type=int, default=80)
    ap.add_argument("--keep_originals", action="store_true", help="Keep originals in output too (in /originals)")

    # ✅ novos argumentos (opcionais)
    ap.add_argument("--ffmpeg", default=None, help="Full path to ffmpeg.exe (Windows) or ffmpeg binary")
    ap.add_argument("--ffprobe", default=None, help="Full path to ffprobe.exe (Windows) or ffprobe binary")

    args = ap.parse_args()

    ffmpeg_path = resolve_tool_path(args.ffmpeg, "FFMPEG_PATH", DEFAULT_FFMPEG_WIN, "ffmpeg")
    ffprobe_path = resolve_tool_path(args.ffprobe, "FFPROBE_PATH", DEFAULT_FFPROBE_WIN, "ffprobe")

    in_dir = Path(args.in_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    originals_dir = out_dir / "originals"
    if args.keep_originals:
        originals_dir.mkdir(parents=True, exist_ok=True)

    # Quick check: ffmpeg exists and runs
    try:
        run([ffmpeg_path, "-version"])
    except Exception as e:
        raise SystemExit(
            "FFmpeg não encontrado/execução falhou.\n"
            f"Tentado: {ffmpeg_path}\n"
            "Dica: rode com --ffmpeg \"C:\\ffmpeg\\bin\\ffmpeg.exe\""
        ) from e

    files = sorted([p for p in in_dir.rglob("*") if p.is_file()])
    if not files:
        print("No files found in input folder.")
        return

    print(f"Found {len(files)} files")
    print(f"Using ffmpeg:  {ffmpeg_path}")
    print(f"Using ffprobe: {ffprobe_path}")

    for src in files:
        dst_base = out_dir / src.name
        ext = src.suffix.lower()

        if args.keep_originals:
            shutil.copy2(src, originals_dir / src.name)

        try:
            if ext in GIF_EXTS:
                dst = (out_dir / src.stem).with_suffix(".mp4")
                optimize_gif_to_mp4(src, dst, args.max_width, args.fps, args.crf, args.preset, ffmpeg_path)

            elif ext in VIDEO_EXTS:
                dst = (out_dir / src.stem).with_suffix(".mp4")
                optimize_video_to_mp4(src, dst, args.max_width, args.fps, args.crf, args.preset, ffmpeg_path)

            elif ext in IMAGE_EXTS:
                dst = (out_dir / src.stem).with_suffix(ext)
                optimize_image(src, dst, args.max_width, args.jpeg_quality, args.webp_quality)

            else:
                shutil.copy2(src, dst_base)

        except Exception as e:
            print(f"[ERROR] Failed to process {src.name}: {e}")
            shutil.copy2(src, dst_base)

    print("Done. Output:", out_dir)
    if not PIL_OK:
        print("Note: Pillow not installed; images were copied without re-encoding. Install 'pillow' to optimize images.")


if __name__ == "__main__":
    main()
