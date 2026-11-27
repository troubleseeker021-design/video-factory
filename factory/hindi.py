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
twist hai...", "Matlab...", "Hamein lagta hai... lekin...", "Aur sabse badi baat kya hai?", "Solution simple hai:".
Sound like a young Indian YouTuber in 2026, NOT a news anchor or a textbook: whenever people normally say the
English word, use the English word, never formal "shuddh" Hindi. Say percent (not pratishat), cells (not koshikayen),
transparent (not pardarshi), temperature (not taapmaan), oxygen, liver, planet, species, degree, million, process,
"bahut zyada" (not atyadhik), "bahut bada" (not vishaal), "kamaal" or "amazing" (not adbhut), "pata chala"
(not gyaat hua), "matlab" (not arthaat). Example of the right style: "Yeh frog sote time apna 90 percent blood
liver mein chhupa leta hai, aur iski body almost transparent ho jaati hai."
Turn dry science into vivid desi images (dead cells -> "yeh asal mein ek kabristan hai").
If the topic touches daily life, open the hook from a habit everyone has ("Subah uthte hi jab aap...").
Mix very short punchy sentences with medium ones.
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
  Write numbers and symbols as spoken words, the modern way (90% -> नब्बे परसेंट, 1960s -> उन्नीस सौ साठ के आसपास);
  never प्रतिशत or other formal Hindi words (see the style rules above).
  Keep it flowing: one smooth sentence per line, very few commas (every comma makes the voice pause),
  never "..."; no brackets, emojis, symbols or hyphens.
  Use only EASY words the voice says smoothly: everyday spoken Hindi plus the common English words young
  Indians actually use (see above). Avoid long or rare English words nobody uses in Hindi (say "bahut purana" not "ancient",
  "gulabi" not "pink-coloured"). Write big numbers simply ("करीब पचास हज़ार साल", not exact digits).
  Place names: short and in their common Hindi form (लोनार झील, भारत), skip extra region names.
- "outro" (only if the source has one): the viewer question in Hinglish, then ask them to comment, like and
  follow in ONE short line, as a poll: e.g. "Sach-sach batana, ungli se ya paani se? Comment mein likho aur follow karo."
  Spoken version in DEVANAGARI like the other lines.
- "hook_roman", "body_roman", "cta_roman", "outro_roman": the SAME lines word for word in Roman letters, written the way
  Indians type on WhatsApp, English words in normal English spelling
  (e.g. "Humein lagta hai aankh malna normal hai, but science ise kuch aur hi bolti hai."),
  EXCEPT every number: write it in DIGITS the way people type it ("90 percent", "50,000 saal", "3.5 km", "1960s"),
  because these lines are the on-screen captions and viewers must see real numbers, not words.
  Same number of lines as the Devanagari ones.
- "facts": the small on-screen fact boxes in short Roman Hinglish, keep the numbers ("" stays "").
- "title", "caption", "question", "thumb", "labels": in Hindi written in Roman letters (labels and
  thumb in CAPS; place names stay as they are, e.g. "LAKE HILLIER, AUSTRALIA").
- "banner": the big opening headline shown on screen in the first 3 seconds, in DEVANAGARI, 3 to 6 words,
  stating the jaw-dropping fact itself with any number in digits (e.g. "सूरज से भी गरम जीव", "30 लाख डिग्री का कोर").
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


# one fixed Hindi name per series, so the channel looks organised (Gemini used to invent a new name each time)
HI_SERIES = {"space": "Antariksh Ke Raaz", "ocean": "Samundar Ke Rahasya", "nature": "Kudrat Ke Raaz",
             "history": "Itihaas Ke Sach", "body": "Shareer Ke Raaz", "science": "Gajab Science",
             "animals": "Anokhe Jaanwar", "places": "Anokhi Jagahein", "tech": "Kaise Karta Hai Kaam",
             "mind": "Dimaag Ke Rahasya", "dark": "Kaala Itihaas", "money": "Paise Ke Raaz",
             "love": "Aakarshan Ka Vigyan", "whatif": "Kya Hota Agar?", "survival": "Zinda Rehne Ka Vigyan",
             "food": "Khane Ka Sach"}
_DEVANAGARI = re.compile(r"[\u0900-\u097F]+")
ROMAN_KEYS = ("title", "caption", "question", "thumb")


def _roman_problem(out, script):
    """Why the on-screen Hinglish is not good enough (Hindi letters mixed in, or the title left in English), or None."""
    for k in ROMAN_KEYS:
        if _DEVANAGARI.search(str(out.get(k) or "")):
            return f"Hindi letters inside the Roman {k}"
    en = set(re.findall(r"[a-z]+", str(script.get("title", "")).lower()))
    hi = re.findall(r"[a-z]+", str(out.get("title", "")).lower())
    if hi and sum(w in en for w in hi) / len(hi) > 0.6:
        return "title was left in English"
    return None


def translate(script, cfg):
    from . import writer
    keys = ["title", "hook", "body", "cta", "outro", "caption", "hashtags", "question", "thumb", "labels", "facts"]
    src = json.dumps({k: script.get(k) for k in keys}, ensure_ascii=False)
    out = None
    for attempt in range(2):
        out = writer.gemini_json(cfg, PROMPT.format(src=src), temperature=0.7)
        n = len(script["body"])
        if (out and out.get("hook") and out.get("hook_roman") and len(out.get("body") or []) == n
                and len(out.get("body_roman") or []) == n and out.get("cta_roman")):
            problem = _roman_problem(out, script)
            if problem and attempt == 0:
                log(f"  Hindi translation: {problem} - asking again")
                continue
            for k in ROMAN_KEYS:  # never show stray Hindi letters inside Roman text
                if isinstance(out.get(k), str):
                    out[k] = re.sub(r"\s{2,}", " ", _DEVANAGARI.sub("", out[k])).strip()
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
    banner = str(out.get("banner") or "").strip()
    if banner and _DEVANAGARI.search(banner) and len(banner) <= 60:  # Hindi breakouts open on a Devanagari headline
        hi["banner"] = banner
    for k in lines:  # on screen everything is Roman Hinglish
        hi[k] = out[f"{k}_roman"]
    labels = out.get("labels") or []
    if len(labels) != len(script.get("labels") or []):
        hi["labels"] = script.get("labels") or []
    if len(out.get("facts") or []) != len(script.get("facts") or []):
        hi["facts"] = script.get("facts") or []
    tag = script.get("series_tag", "")
    num = tag[len(script.get("series", "")):].strip() if tag.startswith(script.get("series", "")) else tag.rsplit(" ", 1)[-1]
    hi_series = HI_SERIES.get(script.get("theme")) or out.get("series") or script.get("series", "")
    hi["series"] = hi_series
    hi["series_tag"] = f"{hi_series} {num}".strip()
    hi["lang"] = "hi"
    return hi


def _own_look(m, work):
    """Give the Hindi channel its own version of each picture (mirrored, slightly zoomed and warmer)
    so YouTube doesn't see two channels posting the same video frames."""
    if m.get("type") != "photo":
        return m
    try:
        from PIL import Image, ImageEnhance, ImageOps
        img = ImageOps.mirror(Image.open(m["path"]).convert("RGB"))
        w, h = img.size
        img = img.crop((int(w * 0.04), int(h * 0.04), int(w * 0.96), int(h * 0.96))).resize((w, h), Image.LANCZOS)
        img = ImageEnhance.Color(img).enhance(1.08)
        r, g, b = img.split()
        img = Image.merge("RGB", (r.point(lambda v: min(255, int(v * 1.04))), g, b.point(lambda v: int(v * 0.96))))
        out = os.path.join(work, "hi_" + os.path.basename(m["path"]).rsplit(".", 1)[0] + ".jpg")
        img.save(out, quality=92)
        return dict(m, path=out)
    except Exception:
        return m


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
        wav = None
        gem = cfg.get("hindi_gemini_voice", "Kore")  # owner's pick from the Nov 27 voice audition (#9); "" = Microsoft
        if gem:
            try:
                wav, vdur, dev_words = voice.gemini_narrate(spoken, hcfg, work, gem)
            except Exception as e:
                log(f"  Gemini Hindi voice failed ({str(e)[:160]}) - using the Microsoft voice instead")
                wav = None
        if wav is None:
            wav, vdur, dev_words = voice.narrate(" ".join(spoken), hcfg, work,
                                                 voices=[cfg.get("hindi_voice") or VOICE, "hi-IN-MadhurNeural"],
                                                 rate=cfg.get("hindi_voice_speed", "+6%"), pitch=cfg.get("hindi_voice_pitch", "+0Hz"))
        duration = vdur + 0.8
        note("narrator", f"Recorded the Hindi voice-over: {duration:.0f} seconds")
        words = roman_timings(dev_words, spoken, [script["hook"], *script["body"], script["cta"],
                                                  *([script["outro"]] if script.get("outro") else [])], duration)
        mus = music.pick_music(duration, seed, work)
        scenes = render.plan_scenes(script, words, duration, rng)
        pool = [_own_look(m, work) for m in plan.get("media", []) if m and os.path.exists(m.get("path", ""))]
        for k, sc in enumerate(scenes):  # the English video's pictures, mirrored and in a new order
            sc["media"] = dict(pool[(k + 1) % len(pool)]) if pool else None
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
