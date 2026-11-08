"""Hindi channel: every English video is remade in natural, conversational Hindi from the
same visuals - translated script (Gemini), Hindi AI voice, Devanagari captions, labels,
thumbnail, caption text, hashtags and comment question.

It only runs when the Hindi channel's accounts are connected (GitHub secrets
HI_YT_REFRESH_TOKEN and/or HI_META_PAGE_TOKEN), or for a test (task: hindi_test)."""
import json
import os
import random

import requests

from .common import FONTS, REVIEW, log, read_json, write_json

FONT = os.path.join(FONTS, "NotoSansDevanagari.ttf")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf"
VOICE = "hi-IN-SwaraNeural"  # clear, warm voice - like this week's 5M-view Hindi travel short

PROMPT = """Translate this YouTube Shorts script into natural, conversational Hindi the way young
Indians actually talk (Hinglish is fine: keep common English words like "video", "real", "photo",
place names and numbers as people say them). Write in Devanagari script. NOT word-for-word:
keep it punchy, upbeat and curious. Keep exactly the same number of "body" lines and "labels".

Return JSON only with the same keys:
{src}
Also add "series": the series name in Hindi (short), e.g. "अनोखी जगहें" for Unreal Places."""


def enabled():
    return bool(os.environ.get("HI_YT_REFRESH_TOKEN", "").strip() or os.environ.get("HI_META_PAGE_TOKEN", "").strip())


def ensure_font():
    if os.path.exists(FONT) and os.path.getsize(FONT) > 100_000:
        return FONT
    r = requests.get(FONT_URL, timeout=120)
    r.raise_for_status()
    with open(FONT, "wb") as f:
        f.write(r.content)
    return FONT


def translate(script, cfg):
    from . import writer
    keys = ["title", "hook", "body", "cta", "caption", "hashtags", "question", "thumb", "labels"]
    src = json.dumps({k: script.get(k) for k in keys}, ensure_ascii=False)
    out = writer.gemini_json(cfg, PROMPT.format(src=src), temperature=0.6)
    if not out or not out.get("hook") or len(out.get("body") or []) != len(script["body"]):
        raise RuntimeError("Hindi translation failed or changed the number of lines")
    hi = dict(script)
    for k in keys:
        if out.get(k):
            hi[k] = out[k]
    labels = out.get("labels") or []
    if len(labels) != len(script.get("labels") or []):
        hi["labels"] = script.get("labels") or []
    num = script.get("series_tag", "").rsplit(" ", 1)[-1]
    hi["series_tag"] = f"{out.get('series') or script.get('series', '')} {num}".strip()
    hi["lang"] = "hi"
    return hi


def make(vid, cfg, seed=None):
    """Builds REVIEW/<vid>-hi from the English video's plan. Returns the new id or None."""
    from . import make_videos, music, render, thumbnail, voice
    from .diary import note
    src = os.path.join(REVIEW, vid)
    plan = read_json(os.path.join(src, "plan.json"), None)
    if not plan:
        log("  Hindi: no plan.json from the English video - skipped")
        return None
    hid = f"{vid}-hi"
    work = os.path.join(REVIEW, hid)
    os.makedirs(work, exist_ok=True)
    seed = seed if seed is not None else random.randint(0, 10_000_000)
    rng = random.Random(seed)
    try:
        ensure_font()
        script = translate(plan["script"], cfg)
        note("writer", "Translated the script into conversational Hindi for our Hindi channel")
        hcfg = dict(cfg, channel_name=os.environ.get("HI_CHANNEL_NAME", "").strip())
        text = " ".join([script["hook"], *script["body"], script["cta"]])
        wav, vdur, words = voice.narrate(text, hcfg, work, voices=[cfg.get("hindi_voice") or VOICE, "hi-IN-MadhurNeural"],
                                         rate=cfg.get("hindi_voice_speed", "+8%"), pitch="+0Hz")
        duration = vdur + 0.8
        note("narrator", f"Recorded the Hindi voice-over: {duration:.0f} seconds")
        mus = music.pick_music(duration, seed, work)
        scenes = render.plan_scenes(script, words, duration, rng)
        pool = [m for m in plan.get("media", []) if m and os.path.exists(m.get("path", ""))]
        for k, sc in enumerate(scenes):  # same visuals as the English video
            sc["media"] = pool[(k - k // 3) % len(pool)] if pool and k % 3 != 2 else None
        render.set_language("hi")
        try:
            thumb = thumbnail.make(script, scenes, hcfg, seed, os.path.join(work, "cover.jpg"), lang="hi")
            render.render(script, scenes, words, duration, wav, mus, os.path.join(work, "video.mp4"), hcfg, seed,
                          workdir=work, thumb=thumb)
        finally:
            render.set_language("en")
        script["_credits"] = plan["script"].get("_credits", [])
        post = make_videos.build_post_pack(script, hcfg)
        post["channel"] = "hi"
        write_json(os.path.join(work, "post.json"), post)
        for tmp in ("voice.mp3", "voice.wav", "music.wav", "sfx.wav"):
            p = os.path.join(work, tmp)
            if os.path.exists(p):
                os.remove(p)
        note("editor", "Finished the Hindi version too!", kind="done")
        return hid
    except Exception as e:
        import shutil
        import traceback
        log("  Hindi version failed (English video is fine):\n" + traceback.format_exc())
        note("editor", f"The Hindi version didn't work this time: {str(e)[:80]}", kind="error")
        shutil.rmtree(work, ignore_errors=True)
        return None
