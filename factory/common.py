"""Shared paths, config loading, logging and ffmpeg discovery."""
import json
import os
import shutil
import sys
import datetime as _dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
FONTS = os.path.join(ASSETS, "fonts")
DATA = os.path.join(ROOT, "data")
OUTPUT = os.path.join(ROOT, "output")
REVIEW = os.path.join(OUTPUT, "review")
APPROVED = os.path.join(OUTPUT, "approved")
REJECTED = os.path.join(OUTPUT, "rejected")
POSTED = os.path.join(OUTPUT, "posted")
LOGS = os.path.join(OUTPUT, "logs")

for _d in (DATA, REVIEW, APPROVED, REJECTED, POSTED, LOGS):
    os.makedirs(_d, exist_ok=True)


ENV_KEYS = {"GEMINI_API_KEY": "gemini_api_key", "PEXELS_API_KEY": "pexels_api_key", "CHANNEL_NAME": "channel_name"}


def load_config():
    with open(os.path.join(ROOT, "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # in the cloud, keys come from GitHub Secrets instead of config.json
    for env, key in ENV_KEYS.items():
        if os.environ.get(env, "").strip():
            cfg[key] = os.environ[env].strip()
    return cfg


def log(msg):
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode(), flush=True)
    try:
        with open(os.path.join(LOGS, "factory.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def ffmpeg_exe():
    """Find ffmpeg: bundled via imageio-ffmpeg (Windows-friendly) or system PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    sys.exit("ffmpeg not found. Run SETUP.bat again (it installs imageio-ffmpeg).")


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
