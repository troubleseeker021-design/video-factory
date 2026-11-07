"""The Factory Manager - runs every agent in order and drops finished videos
into output/review for your approval.

Usage:  python -m factory.make_videos [--count 3] [--no-voice]"""
import argparse
import datetime as dt
import os
import random
import shutil
import traceback

from .common import REVIEW, load_config, log, write_json
from . import writer, voice, music, media, render


def build_post_pack(script, cfg):
    tags = " ".join(script.get("hashtags", []))
    credits = ("\n\nImage credits:\n" + "\n".join(script["_credits"])) if script.get("_credits") else ""
    return {
        "title": script["title"],
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "source": script.get("source"),
        "youtube": {
            "title": (script["title"][:90] + " #shorts"),
            "description": f"{script['caption']}\n\n{tags} #shorts" + credits,
            "tags": [h.lstrip("#") for h in script.get("hashtags", [])] + ["shorts", "facts"],
        },
        "tiktok": f"{script['caption']} {tags} #fyp #facts",
        "instagram": f"{script['caption']}\n.\n.\n{tags} #reels #facts #didyouknow",
        "facebook": f"{script['caption']} {tags}",
        "script": writer.spoken_text(script),
        "status": "review",
    }


def make_one(cfg, no_voice=False):
    script = writer.next_script(cfg)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    vid_id = f"{stamp}-{script['id']}"[:70]
    work = os.path.join(REVIEW, vid_id)
    os.makedirs(work, exist_ok=True)
    log(f"Making: {script['title']}  [{script.get('source')}]")
    seed = random.randint(0, 10_000_000)
    text = writer.spoken_text(script)
    try:
        if no_voice:
            words, duration = voice.estimate_timings(text)
            wav = None
        else:
            wav, vdur, words = voice.narrate(text, cfg, work)
            duration = vdur + 0.8
        mus = music.pick_music(duration, seed, work)
        scenes = render.plan_scenes(script, words, duration, random.Random(seed))
        media.plan(script, scenes, cfg, seed)
        render.render(script, scenes, words, duration, wav, mus, os.path.join(work, "video.mp4"), cfg, seed,
                      cover_path=os.path.join(work, "cover.jpg"), workdir=work)
        credits = sorted({sc["media"]["credit"] for sc in scenes if sc.get("media") and sc["media"].get("credit")})
        script["_credits"] = credits
        write_json(os.path.join(work, "post.json"), build_post_pack(script, cfg))
        for tmp in ("voice.mp3", "voice.wav", "music.wav", "sfx.wav"):
            p = os.path.join(work, tmp)
            if os.path.exists(p):
                os.remove(p)
        return vid_id
    except Exception:
        log("  FAILED:\n" + traceback.format_exc())
        shutil.rmtree(work, ignore_errors=True)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--no-voice", action="store_true", help="captions + music only")
    a = ap.parse_args()
    cfg = load_config()
    count = a.count or int(cfg.get("videos_per_run", 3))
    log(f"=== Factory run: {count} video(s) ===")
    done = [v for v in (make_one(cfg, a.no_voice) for _ in range(count)) if v]
    log(f"=== Done: {len(done)}/{count} videos waiting in output/review. Open REVIEW.bat to approve. ===")


if __name__ == "__main__":
    main()
