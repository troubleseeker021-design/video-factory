"""Hindi channel: every English video is remade in natural, conversational Hindi from the
same visuals - translated script (Gemini), Hindi AI voice and Roman-Hinglish captions
("Yeh jagah nakli lagti hai...") on screen, in the thumbnail, caption text, hashtags and
comment question. Devanagari is used only behind the scenes so the voice pronounces it right.

It only runs when the Hindi channel's accounts are connected (GitHub secrets
HI_YT_REFRESH_TOKEN and/or HI_META_PAGE_TOKEN), or for a test (task: hindi_test)."""
import json
import re
import os
import random

import requests

from .common import FONTS, REVIEW, log, read_json, write_json

FONT = os.path.join(FONTS, "NotoSansDevanagari.ttf")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf"
VOICE = "hi-IN-MadhurNeural"  # chosen by the owner; change with "hindi_voice" in config.json
LINES = ("hook", "body", "cta", "outro")

PROMPT = """Rewrite this YouTube Shorts script in natural HINGLISH - the way popular Indian science/facts
creators talk on Instagram Reels: Hindi sentence structure with common English words mixed in exactly where
people really use them (science, normal, actually, real, fact, body, brain, process, system, scientists,
experiment, signal, energy, percent, numbers). Fluent, fast, confident, curious - like explaining something
amazing to a friend - TALKING, not reading out an article. Explain HOW it works step by step ("Pehle..., phir...,
isliye..., aur jab..."), with spoken connectors like "Ab socho...", "Dekho, hota kya hai...", "Aur yahin
twist hai...", "Matlab...". Mix very short punchy sentences with medium ones.
Simple everyday comparisons. No dramatic pauses, no "...", no questions to the viewer, no "follow" or "comment"
(except in "outro").
NOT word-for-word.
Keep exactly the same number of "body" lines, "labels" and "facts".
Keep it SHORT: each Hindi line should have about as many words as the English one (never more than 10% extra),
because long videos get rejected by Instagram and lose viewers.

Return JSON only.
- "hook", "body", "cta": the spoken lines in DEVANAGARI script (used for the voice). EVERY word must be
  in Devanagari - the English words too, spelled as Indians say them (साइंस, नॉर्मल, एक्चुअली, सिस्टम) -
  and place names (लेक हिलियर, ऑस्ट्रेलिया).
  Write numbers and symbols as spoken words (100% -> सौ प्रतिशत, 1960s -> उन्नीस सौ साठ के दशक).
  Keep it flowing: one smooth sentence per line, very few commas (every comma makes the voice pause),
  never "..."; no brackets, emojis, symbols or hyphens.
  Use only EASY, common words the voice says smoothly: everyday Hindi words, and only the very common
  English words listed above. Avoid long or rare English words (say "bahut purana" not "ancient",
  "gulabi" not "pink-coloured"). Write big numbers simply ("करीब पचास हज़ार साल", not exact digits).
  Place names: short and in their common Hindi form (लोनार झील, भारत), skip extra region names.
- "outro" (only if the source has one): the viewer question in Hinglish, then ask them to comment, like and
  follow in ONE short line, e.g. "...? Comment mein batao, aur follow karo."
  Spoken version in DEVANAGARI like the other lines.
- "hook_roman", "body_roman", "cta_roman", "outro_roman": the SAME lines word for word in Roman letters, written the way
  Indians type on WhatsApp, English words in normal English spelling
  (e.g. "Humein lagta hai aankh malna normal hai, but science ise kuch aur hi bolti hai.").
  Same number of lines as the Devanagari ones.
- "facts": the small on-screen fact boxes in short Roman Hinglish, keep the numbers ("" stays "").
- "title", "caption", "question", "thumb", "labels": in Hindi written in Roman letters (labels and
  thumb in CAPS; place names stay as they are, e.g. "LAKE HILLIER, AUSTRALIA").
- "hashtags": 6 hashtags mixing Hindi in Roman letters and popular ones (#shorts, #travel).
- "series": the series name in short Hindi in Roman letters, e.g. "Anokhi Jagahein" for Unreal Places.

Script:
{src}"""


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
    keys = ["title", "hook", "body", "cta", "outro", "caption", "hashtags", "question", "thumb", "labels", "facts"]
    src = json.dumps({k: script.get(k) for k in keys}, ensure_ascii=False)
    out = None
    for _ in range(2):
        out = writer.gemini_json(cfg, PROMPT.format(src=src), temperature=0.7)
        n = len(script["body"])
        if (out and out.get("hook") and out.get("hook_roman") and len(out.get("body") or []) == n
                and len(out.get("body_roman") or []) == n and out.get("cta_roman")):
            break
        out = None
    if not out:
        raise RuntimeError("Hindi translation failed or changed the number of lines")
    hi = dict(script)
    for k in keys:
        if out.get(k):
            hi[k] = out[k]
    lines = [k for k in LINES if k != "outro" or (script.get("outro") and out.get("outro") and out.get("outro_roman"))]
    if "outro" not in lines:
        hi.pop("outro", None)
    hi["spoken"] = {k: out[k] for k in lines}  # Devanagari, for the voice only
    for k in lines:  # on screen everything is Roman Hinglish
        hi[k] = out[f"{k}_roman"]
    labels = out.get("labels") or []
    if len(labels) != len(script.get("labels") or []):
        hi["labels"] = script.get("labels") or []
    if len(out.get("facts") or []) != len(script.get("facts") or []):
        hi["facts"] = script.get("facts") or []
    tag = script.get("series_tag", "")
    num = tag[len(script.get("series", "")):].strip() if tag.startswith(script.get("series", "")) else tag.rsplit(" ", 1)[-1]
    hi["series_tag"] = f"{out.get('series') or script.get('series', '')} {num}".strip()
    hi["lang"] = "hi"
    return hi


def _smooth(line):
    """Fewer pauses for the Hindi voice: drop commas/dashes inside a line, keep only the line's end."""
    line = re.sub(r"\s*[,;:\u2013\u2014-]\s*", " ", line or "")
    line = re.sub(r"\s*\.\.\.+\s*", " ", line)
    return re.sub(r"\s{2,}", " ", line).strip()


def roman_timings(dev_words, spoken, roman, duration):
    """The voice speaks Devanagari; the captions show Roman Hinglish. Each Roman line gets the exact
    time span of the same spoken line, spread over its words by length."""
    from .render import align
    from .voice import _split_sentence
    align(dev_words, spoken)
    out, prev_end = [], 0.3
    for i, line in enumerate(roman):
        ws = [w for w in dev_words if w.get("sid") == i]
        rw = line.split()
        if ws and len(ws) == len(rw):  # word-for-word lines: give every Roman word its spoken word's exact time
            out += [{"word": r, "start": w["start"], "end": w["end"]} for r, w in zip(rw, ws)]
            prev_end = ws[-1]["end"]
            continue
        if ws:
            start, end = ws[0]["start"], ws[-1]["end"]
        else:  # line not found in the timings - give it a fair share of the time left
            left = max(1, len(roman) - i)
            start, end = prev_end, prev_end + max(0.8, (duration - prev_end) / left)
        out += _split_sentence(line, start, max(end, start + 0.3))
        prev_end = end
    return out


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
        script = translate(plan["script"], cfg)
        note("writer", "Translated the script into conversational Hindi for our Hindi channel")
        hcfg = dict(cfg, channel_name=os.environ.get("HI_CHANNEL_NAME", "").strip())
        sp = script.pop("spoken")
        spoken = [_smooth(x) for x in [sp["hook"], *sp["body"], sp["cta"], *([sp["outro"]] if sp.get("outro") else [])]]
        wav, vdur, dev_words = voice.narrate(" ".join(spoken), hcfg, work,
                                             voices=[cfg.get("hindi_voice") or VOICE, "hi-IN-MadhurNeural"],
                                             rate=cfg.get("hindi_voice_speed", "+6%"), pitch=cfg.get("hindi_voice_pitch", "+0Hz"))
        duration = vdur + 0.8
        note("narrator", f"Recorded the Hindi voice-over: {duration:.0f} seconds")
        words = roman_timings(dev_words, spoken, [script["hook"], *script["body"], script["cta"],
                                                  *([script["outro"]] if script.get("outro") else [])], duration)
        mus = music.pick_music(duration, seed, work)
        scenes = render.plan_scenes(script, words, duration, rng)
        pool = [m for m in plan.get("media", []) if m and os.path.exists(m.get("path", ""))]
        for k, sc in enumerate(scenes):  # same visuals as the English video
            sc["media"] = dict(pool[k % len(pool)]) if pool else None
        thumb = thumbnail.make(script, scenes, hcfg, seed, os.path.join(work, "cover.jpg"))
        render.render(script, scenes, words, duration, wav, mus, os.path.join(work, "video.mp4"), hcfg, seed,
                      workdir=work, thumb=thumb)
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
