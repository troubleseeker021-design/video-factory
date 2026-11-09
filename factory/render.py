"""Agent 5 - The Editor (v2, "trending" style).
Builds a fast-paced vertical video: a new scene every sentence, real photos or
3D graphics, a hook banner, pop-in emoji stickers, animated number counters,
punchy transitions (zoom punch, flash, glitch), bouncing word-by-word captions
with a highlight box, a progress bar, an animated FOLLOW button, and a full
sound-design layer (whooshes, pops, dings, impact, ticks) mixed with ducked music."""
import math
import os
import random
import re
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import emoji as EMO
from . import sfx as SFX
from .common import FONTS, ffmpeg_exe, log
from .visuals import ClipBG, PhotoBG, ProceduralBG
from .depth3d import Parallax3D

W, H, FPS = 1080, 1920, 30
ACCENTS = {"yellow": (255, 214, 10), "white": (255, 255, 255), "green": (60, 240, 120), "pink": (255, 70, 160)}
RED = (255, 45, 85)
CAP_Y = 1290  # caption centre line (clear of TikTok/Reels buttons)
STOP = {"the", "a", "an", "of", "to", "and", "every", "about", "around", "up", "than", "in", "on", "for", "it", "its"}


def _shade():
    a = np.exp(-np.linspace(-2.2, 2.2, 760) ** 2) * 120
    g = np.zeros((760, W, 4), np.uint8)
    g[..., 3] = a[:, None].astype(np.uint8)
    return Image.fromarray(g, "RGBA")


SHADE = _shade()


def _finish_overlays():
    """Cinematic finish: dark gradients top/bottom (text always readable), vignette, film grain."""
    y = np.linspace(0, 1, H)[:, None]
    x = np.linspace(-1, 1, W)[None, :]
    top = np.clip(1 - y / 0.22, 0, 1) ** 1.6 * 150
    bot = np.clip((y - 0.50) / 0.50, 0, 1) ** 1.3 * 200
    vig = np.clip((x ** 2 * 0.55 + ((y - 0.5) * 2) ** 2 * 0.45) - 0.35, 0, 1) * 150
    a = np.clip(top + bot + vig, 0, 235).astype(np.uint8)
    g = np.zeros((H, W, 4), np.uint8)
    g[..., 3] = a
    grains = []
    r = np.random.RandomState(7)
    for _ in range(4):
        n = r.randint(0, 255, (H // 2, W // 2), np.uint8)
        gi = np.zeros((H // 2, W // 2, 4), np.uint8)
        gi[..., :3] = n[..., None]
        gi[..., 3] = 9
        grains.append(Image.fromarray(gi, "RGBA").resize((W, H), Image.NEAREST))
    return Image.fromarray(g, "RGBA"), grains


FINISH, GRAINS = _finish_overlays()


def _font(name, size, var=None):
    path = os.path.join(FONTS, name)
    try:
        f = ImageFont.truetype(path, size)
        if var:
            f.set_variation_by_name(var)
        return f
    except Exception:
        return ImageFont.truetype(os.path.join(FONTS, "Poppins-Bold.ttf"), size)


F_CAP = _font("Montserrat-Variable.ttf", 96, "Black")
F_CAP_SMALL = _font("Montserrat-Variable.ttf", 84, "Black")
F_HOOK = _font("Anton-Regular.ttf", 84)
F_STAT = _font("Anton-Regular.ttf", 200)
F_LABEL = _font("Montserrat-Variable.ttf", 52, "Black")
F_BTN = _font("Montserrat-Variable.ttf", 70, "Black")
F_SMALL = _font("Montserrat-Variable.ttf", 30, "Bold")
F_CREDIT = _font("Poppins-Medium.ttf", 22)
F_PLACE = _font("Montserrat-Variable.ttf", 44, "Black")


# ---------------- easing ----------------
def ease_out_back(x, s=1.9):
    x = min(max(x, 0.0), 1.0) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def _norm(s):
    return re.sub(r"[\W_]", "", s.lower())  # keeps letters of any alphabet (Hindi too)


# ---------------- planning ----------------
def align(words, sentences):
    tokens = [(_norm(w), si) for si, s in enumerate(sentences) for w in s.split() if _norm(w)]
    j, last = 0, 0
    for w in words:
        n = _norm(w["word"])
        found = None
        for k in range(j, min(j + 5, len(tokens))):
            tk = tokens[k][0]
            if n and (tk == n or tk.startswith(n) or n.startswith(tk)):
                found = k
                break
        if found is not None:
            sid = tokens[found][1]
            j = found + 1
        else:
            sid = tokens[min(j, len(tokens) - 1)][1]
            j += 1
        last = max(last, sid)
        w["sid"] = last
    return words


def _parse_number(sentence):
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", sentence)
    if not m:
        return None
    raw = m.group(1).rstrip(",.")
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    rest = re.findall(r"[A-Za-z]+", sentence[m.end():])
    label = [w for w in rest[:4] if w.lower() not in STOP][:2]
    is_year = re.fullmatch(r"1\d{3}|20\d{2}", raw) is not None
    if is_year:
        label = ["YEAR"]
    return {"raw": raw, "value": val, "decimals": len(raw.split(".")[1]) if "." in raw else 0,
            "label": " ".join(label).upper(), "year": is_year}


def plan_scenes(script, words, duration, rng):
    """Split the narration into scenes: one per sentence, and long sentences are cut in two
    so the picture changes every ~2-4 seconds (pattern interrupts keep viewers watching)."""
    sentences = [script["hook"], *script["body"], script["cta"]]
    align(words, sentences)
    raw = []
    for i, s in enumerate(sentences):
        ws = [w for w in words if w["sid"] == i]
        if not ws:
            continue
        dur = ws[-1]["end"] - ws[0]["start"]
        # fast cuts: a new shot about every 2 seconds (what this week's viral shorts do)
        parts = min(3, max(1, round(dur / 2.1)), max(1, len(ws) // 3))
        if parts == 1 or i == 0:  # the hook stays one shot (its banner animates in once)
            raw.append((i, ws))
            continue
        start = 0
        for p in range(1, parts):
            target = int(len(ws) * p / parts)
            lo, hi = max(start + 2, target - 2), min(len(ws) - 2, target + 2)
            cut = next((j + 1 for j in range(lo, hi) if re.search(r"[,;:]$", ws[j]["word"])), target)
            cut = max(start + 2, min(cut, len(ws) - 2))
            raw.append((i, ws[start:cut]))
            start = cut
        raw.append((i, ws[start:]))
    scenes, used = [], set()
    custom = script.get("emojis") or []
    labels = script.get("labels") or []
    for k, (i, ws) in enumerate(raw):
        sc = {"i": i, "words": ws, "text": " ".join(w["word"] for w in ws),
              "start": 0.0 if k == 0 else ws[0]["start"] - 0.06}
        scenes.append(sc)
    for k, sc in enumerate(scenes):
        sc["end"] = scenes[k + 1]["start"] if k + 1 < len(scenes) else duration
        sc["dur"] = sc["end"] - sc["start"]
        sc["kind"] = "hook" if sc["i"] == 0 else "body"
        sc["label"] = (labels[sc["i"]] or "").strip() if sc["i"] < len(labels) else ""
        first_of_sentence = k == 0 or scenes[k - 1]["i"] != sc["i"]
        e = custom[sc["i"]] if first_of_sentence and sc["i"] < len(custom) and custom[sc["i"]] else None
        e = e or EMO.pick(sc["text"], used)
        if k > 0 and e and e == scenes[k - 1].get("emoji"):
            e = None
        if not e:
            pool = [x for x in EMO.THEME_POOL.get(script.get("theme", ""), []) if x not in used]
            e = rng.choice(pool) if pool else (rng.choice(EMO.REACTIONS) if sc["kind"] == "hook" else None)
        sc["emoji"] = e
        used.add(e)
        sc["emoji_t"] = sc["start"] + (0.0 if k == 0 else 0.08)
        sc["stat"] = _parse_number(sc["text"]) if sc["kind"] == "body" else None
        if sc["stat"]:
            sc["stat_t"] = next((w["start"] for w in sc["words"] if re.search(r"\d", w["word"])), sc["start"] + 0.25)
        sc["trans"] = k % 3  # 0 zoom-punch, 1 glitch, 2 zoom-punch + flash
    return scenes


def fill_missing_emoji(scenes, rng):
    for sc in scenes:
        if not sc["emoji"] and not sc.get("media"):
            sc["emoji"] = rng.choice(EMO.GENERIC)


# ---------------- graphics builders ----------------
def _glow(size, color):
    g = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([size * 0.2, size * 0.2, size * 0.8, size * 0.8], fill=color + (150,))
    return g.filter(ImageFilter.GaussianBlur(size * 0.12))


def _hook_banner(title):
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words, lines, cur = title.upper().split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if tmp.textlength(t, font=F_HOOK) <= 840:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    lines = lines[:3]
    # premium title: big clean type, last line in gold, soft cinematic shadow (no sticker box)
    lh = 104
    bw, bh = 1000, lh * len(lines) + 60
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    sh = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(sh)
    y = 30
    for i, l in enumerate(lines):
        col = (255, 214, 10) if i == len(lines) - 1 and len(lines) > 1 else (255, 255, 255)
        ds.text((bw / 2, y + 6), l, font=F_HOOK, fill=(0, 0, 0, 230), anchor="ma")
        d.text((bw / 2, y), l, font=F_HOOK, fill=col, anchor="ma", stroke_width=3, stroke_fill=(0, 0, 0))
        y += lh
    return Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)), img)


def _label_img(text):
    """Pill with a map pin + the place/name, shown near the top of the screen."""
    text = text.upper()[:40]
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    tw = tmp.textlength(text, font=F_PLACE)
    w, h = int(tw) + 130, 92
    img = Image.new("RGBA", (w + 20, h + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 18, w + 10, h + 18], radius=46, fill=(0, 0, 0, 110))
    d.rounded_rectangle([10, 10, w + 10, h + 10], radius=46, fill=(12, 12, 18, 235), outline=(255, 255, 255, 230), width=3)
    cx, cy = 62, 10 + h / 2 - 6  # map pin
    d.ellipse([cx - 20, cy - 22, cx + 20, cy + 18], fill=RED + (255,))
    d.polygon([(cx - 15, cy + 8), (cx + 15, cy + 8), (cx, cy + 34)], fill=RED + (255,))
    d.ellipse([cx - 8, cy - 10, cx + 8, cy + 6], fill=(255, 255, 255, 255))
    d.text((100, 10 + h / 2), text, font=F_PLACE, fill=(255, 255, 255), anchor="lm")
    return img


def set_language(lang):
    """Hindi videos need a Devanagari font for captions, labels and the hook banner."""
    global F_CAP, F_CAP_SMALL, F_HOOK, F_LABEL, F_SMALL, F_PLACE
    if lang == "hi":
        dev = "NotoSansDevanagari.ttf"
        F_CAP, F_CAP_SMALL = _font(dev, 88, "Bold"), _font(dev, 76, "Bold")
        F_HOOK, F_LABEL = _font(dev, 78, "Bold"), _font(dev, 50, "Bold")
        F_SMALL, F_PLACE = _font(dev, 30, "Bold"), _font(dev, 42, "Bold")
    else:
        F_CAP, F_CAP_SMALL = _font("Montserrat-Variable.ttf", 96, "Black"), _font("Montserrat-Variable.ttf", 84, "Black")
        F_HOOK, F_LABEL = _font("Anton-Regular.ttf", 84), _font("Montserrat-Variable.ttf", 52, "Black")
        F_SMALL, F_PLACE = _font("Montserrat-Variable.ttf", 30, "Bold"), _font("Montserrat-Variable.ttf", 44, "Black")


def _stat_card(stat, value, accent):
    if stat["decimals"]:
        num = f"{value:.{stat['decimals']}f}"
    else:
        num = f"{int(round(value)):,}" if ("," in stat["raw"] or value >= 10000) and not stat.get("year") else f"{int(round(value))}"
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    nw = tmp.textlength(num, font=F_STAT)
    lw = tmp.textlength(stat["label"], font=F_LABEL) if stat["label"] else 0
    w = int(max(nw, lw, 360)) + 100
    h = 330 if stat["label"] else 260
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=(10, 10, 14, 215), outline=accent + (255,), width=6)
    d.text((w / 2, 10), num, font=F_STAT, fill=accent, anchor="ma")
    if stat["label"]:
        d.text((w / 2, 250), stat["label"], font=F_LABEL, fill=(255, 255, 255), anchor="ma")
    return img


def _caption_img(chunk, hl, accent, style):
    texts = [w["word"].upper() for w in chunk]
    font = F_CAP if sum(len(t) for t in texts) <= 16 else F_CAP_SMALL
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    space = tmp.textlength(" ", font=font) + 6
    widths = [tmp.textlength(t, font=font) for t in texts]
    lines, cur, cur_w = [], [], 0
    for i, wd in enumerate(widths):
        add = wd + (space if cur else 0)
        if cur and cur_w + add > 900:
            lines.append(cur)
            cur, cur_w, add = [], 0, wd
        cur.append(i)
        cur_w += add
    lines.append(cur)
    lh = int(font.size * 1.28)
    img = Image.new("RGBA", (W, lh * len(lines) + 70), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(shadow)
    for li, idxs in enumerate(lines):
        total = sum(widths[i] for i in idxs) + space * (len(idxs) - 1)
        x = (W - total) / 2
        y = 35 + li * lh
        for i in idxs:
            is_num = bool(re.search(r"\d", texts[i]))
            ds.text((x + 5, y + 9), texts[i], font=font, fill=(0, 0, 0, 200))
            if i == hl and style == "box":
                d.rounded_rectangle([x - 16, y - 2, x + widths[i] + 16, y + font.size * 1.15], radius=18, fill=accent + (255,))
                d.text((x, y), texts[i], font=font, fill=(10, 10, 10))
            else:
                col = accent if (i == hl or is_num) else (255, 255, 255)
                d.text((x, y), texts[i], font=font, fill=col, stroke_width=6, stroke_fill=(0, 0, 0))
            x += widths[i] + space
    shadow = shadow.filter(ImageFilter.GaussianBlur(7))
    return Image.alpha_composite(shadow, img)


def _chunks(words):
    out, cur = [], []
    for w in words:
        if cur and (w["sid"] != cur[-1]["sid"] or len(cur) >= 3 or
                    sum(len(x["word"]) for x in cur) + len(w["word"]) > 17):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?,;:]$", w["word"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _follow_button(state):
    w, h = 600, 150
    img = Image.new("RGBA", (w + 30, h + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if state == "follow":
        d.rounded_rectangle([15, 22, w + 15, h + 22], radius=75, fill=(120, 10, 30, 255))
        d.rounded_rectangle([15, 15, w + 15, h + 15], radius=75, fill=RED + (255,))
        d.text((15 + w / 2, 15 + h / 2), "+ FOLLOW", font=F_BTN, fill=(255, 255, 255), anchor="mm")
    else:
        d.rounded_rectangle([15, 15, w + 15, h + 15], radius=75, fill=(235, 235, 240, 255))
        d.text((15 + w / 2, 15 + h / 2), "FOLLOWING", font=F_BTN, fill=(40, 40, 50), anchor="mm")
    return img


def _paste_center(frame, img, cx, cy, scale=1.0, angle=0.0, alpha=1.0):
    if img is None or scale <= 0.02 or alpha <= 0.01:
        return
    if abs(scale - 1) > 0.01:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    if abs(angle) > 0.3:
        img = img.rotate(angle, resample=Image.BILINEAR, expand=True)
    if alpha < 0.99:
        a = img.getchannel("A").point(lambda v: int(v * alpha))
        img = img.copy()
        img.putalpha(a)
    frame.alpha_composite(img, (int(cx - img.width / 2), int(cy - img.height / 2)))


def _bokeh_sprites():
    out = []
    for size, a in ((160, 60), (110, 70), (70, 90)):
        g = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([size * 0.5, size * 0.5, size * 1.5, size * 1.5], fill=(255, 255, 255, a))
        out.append(g.filter(ImageFilter.GaussianBlur(size * 0.18)))
    return out


def _bokeh(frame, sprites, t, si):
    """Out-of-focus light orbs in front of the subject = depth of field (feels 3D)."""
    for j in range(6):
        sp = sprites[j % 3]
        x = (j * 397 + si * 131 + t * (18 + j * 6)) % (W + 300) - 150
        y = 300 + (j * 263 + si * 77) % 1300 + math.sin(t * 0.8 + j) * 30
        frame.alpha_composite(sp, (int(x - sp.width / 2), int(y - sp.height / 2)))


def _hero3d(frame, img, glow, t, si, scale, bob):
    """3D emoji object: slow turntable spin, floating, soft floor shadow and rim glow."""
    if img is None:
        return
    ang = math.sin(t * 1.2 + si) * 38
    sx = max(0.55, math.cos(math.radians(ang)))
    w = max(1, int(img.width * sx * scale))
    h = max(1, int(img.height * scale))
    if w < 4 or h < 4:
        return
    obj = img.resize((w, h), Image.BILINEAR)
    # shading on the side turning away from the light
    shade = Image.linear_gradient("L").rotate(90 if ang > 0 else -90).resize((w, h))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dark.putalpha(Image.eval(shade, lambda v: int(v * 0.35 * abs(ang) / 38)))
    mask = obj.getchannel("A")
    shaded = Image.alpha_composite(obj, Image.composite(dark, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask))
    cy = 640 + bob
    # floor shadow (shrinks when the object floats up)
    sw = int(330 * scale * (1 - bob / 120))
    if sw > 10:
        sh = Image.new("RGBA", (sw + 80, 140), (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([40, 40, sw + 40, 100], fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(16))
        frame.alpha_composite(sh, (int(W / 2 - sh.width / 2), int(640 + 270 - 70)))
    _paste_center(frame, glow, W / 2, cy, 0.95 * scale, alpha=0.4)
    frame.alpha_composite(shaded, (int(W / 2 - w / 2), int(cy - h / 2)))


# ---------------- main render ----------------
def render(script, scenes, words, duration, voice_wav, music_path, out_path, cfg, seed, cover_path=None, workdir=".", thumb=None):
    rng = random.Random(seed)
    accent = ACCENTS.get(cfg.get("caption_style", "yellow"), ACCENTS["yellow"])
    style = "color"  # clean premium captions: white words, the spoken word in the accent colour
    for sc_ in scenes:  # no emoji stickers - real visuals only
        sc_["emoji"] = None
    use3d = cfg.get("photo_3d", True)
    bokeh = _bokeh_sprites()
    f_on = duration * 0.5  # mid-video FOLLOW pop-up
    f_tap = f_on + 1.0
    proc_bg = ProceduralBG(script.get("theme", "science"), W, H, seed)
    hook_img = _hook_banner(script["title"])
    glow = _glow(700, (255, 255, 255))
    btn_follow, btn_done = _follow_button("follow"), _follow_button("done")
    hand = None
    chunks = _chunks(words)
    cap_cache, stat_cache = {}, {}
    channel = cfg.get("channel_name", "")
    series_tag = script.get("series_tag", "")
    label_cache, first_label_scene = {}, {}
    for sc_ in scenes:
        if sc_.get("label"):
            first_label_scene.setdefault(sc_["label"], sc_)

    # ---- sound design timeline ----
    ev = [(0.0, "impact", 0.9), (0.05, "whoosh", 0.5)]
    for sc in scenes:
        if sc["kind"] != "hook":
            ev.append((sc["start"] - 0.12, "whoosh" if sc["trans"] != 1 else "whoosh_down", 0.55))
        if sc["emoji"]:
            ev.append((sc["emoji_t"], "pop", 0.55))
        if sc["stat"] and not sc["stat"].get("year"):
            for k in range(8):
                ev.append((sc["stat_t"] + k * 0.08, "tick", 0.6))
            ev.append((sc["stat_t"] + 0.7, "ding", 0.55))
    ev += [(f_on, "pop", 0.45), (f_tap, "click", 0.8), (f_tap + 0.05, "bell", 0.5)]
    sfx_path = SFX.build_track(ev, duration, os.path.join(workdir, "sfx.wav"))

    # ---- encoder ----
    ff = ffmpeg_exe()
    args = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    mv = float(cfg.get("music_volume", 0.16))
    fade = f"afade=t=out:st={max(0, duration - 0.5):.2f}:d=0.5"  # short fade keeps the loop seamless
    if voice_wav:
        args += ["-i", voice_wav, "-i", music_path, "-i", sfx_path, "-filter_complex",
                 f"[1:a]asplit=2[v][vk];[2:a]volume={mv * 1.8:.3f},{fade}[m0];"
                 f"[m0][vk]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[m];"
                 f"[3:a]volume=0.9[s];[v][m][s]amix=inputs=3:duration=longest:normalize=0,alimiter=limit=0.95[a]"]
    else:
        args += ["-i", music_path, "-i", sfx_path, "-filter_complex",
                 f"[1:a]volume=0.7,{fade}[m];[2:a]volume=0.9[s];[m][s]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]"]
    args += ["-map", "0:v", "-map", "[a]", "-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "2000k", "-bufsize", "4000k", "-profile:v", "high", "-level", "4.1", "-g", "60", "-r", str(FPS),
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE)

    providers = {}
    n = int(duration * FPS)
    si = 0
    try:
        for f in range(n):
            t = f / FPS
            while si + 1 < len(scenes) and t >= scenes[si + 1]["start"]:
                old = providers.pop(si, None)
                if isinstance(old, ClipBG):
                    old.close()
                si += 1
            sc = scenes[si]
            lt = t - sc["start"]
            m = sc["media"]

            # ---- background ----
            if m:
                if si not in providers:
                    try:
                        if m["type"] == "clip":
                            providers[si] = ClipBG(m["path"], W, H, sc["dur"], FPS, m.get("fit", "full"), m.get("start", 0))
                        else:
                            prov = None
                            if use3d and (si % 2 == 0 or sc["kind"] == "hook"):
                                p3 = Parallax3D(Image.open(m["path"]).convert("RGB"), W, H, random.Random(seed + si))
                                prov = p3 if p3.ok else None
                            providers[si] = prov or PhotoBG(m["path"], W, H, seed + si, m.get("credit", ""))
                    except Exception as e:
                        log(f"  media failed, using graphics: {e}")
                        sc["media"], m = None, None
                        if not sc["emoji"]:
                            sc["emoji"] = rng.choice(EMO.GENERIC)
            if m:
                bgp = providers[si]
                frame = bgp.frame(lt, sc["dur"]).convert("RGBA")
                card = getattr(bgp, "card_box", None)
            else:
                frame = proc_bg.frame(t, hue_shift=si * 0.7).convert("RGBA")
                card = None

            # cinematic finish: gradients for readable text, vignette, subtle film grain
            frame.alpha_composite(FINISH)
            frame.alpha_composite(GRAINS[f % len(GRAINS)])

            # ---- hero graphic / sticker ----
            e = sc["emoji"]
            stat_on = sc["stat"] and t >= sc["stat_t"]
            if e and t >= sc["emoji_t"]:
                k = (t - sc["emoji_t"]) / 0.28 + (0.45 if sc["kind"] == "hook" else 0)
                sc_e = ease_out_back(k)
                wob = math.sin(t * 2.2 + si) * 7
                bob = math.sin(t * 1.7 + si) * 16
                if not m and not stat_on:
                    _hero3d(frame, EMO.image(e, 480), glow, t, si, sc_e, bob)
                    _bokeh(frame, bokeh, t, si)
                elif not m and stat_on:
                    kk = ease_out((t - sc["stat_t"]) / 0.3)
                    _paste_center(frame, EMO.image(e, 460), W / 2 + 300 * kk, 660 - 260 * kk + bob, sc_e * (1 - 0.55 * kk), wob)
                elif card:
                    _paste_center(frame, EMO.image(e, 240), card[2] - 60, card[3] - 30 + bob * 0.5, sc_e, wob - 8)
                else:
                    _paste_center(frame, EMO.image(e, 260), W / 2, 560 + bob, sc_e, wob)

            # ---- number counter ----
            if stat_on:
                k = (t - sc["stat_t"]) / 0.7
                val = sc["stat"]["value"] * (1.0 if sc["stat"].get("year") else ease_out(k))
                key = (si, round(val, sc["stat"]["decimals"]))
                img = stat_cache.get(key)
                if img is None:
                    img = _stat_card(sc["stat"], val, accent)
                    if len(stat_cache) > 40:
                        stat_cache.clear()
                    stat_cache[key] = img
                cy = ((card[1] + card[3]) / 2) if card else 700
                _paste_center(frame, img, W / 2, cy, ease_out_back((t - sc["stat_t"]) / 0.25))

            # ---- hook banner ----
            if sc["kind"] == "hook":
                k_in = ease_out_back((lt + 0.12) / 0.32, 1.4)  # visible from the very first frame
                out_k = ease_out((lt - (sc["dur"] - 0.25)) / 0.25) if lt > sc["dur"] - 0.25 else 0
                y = -200 + (300 * k_in) - 400 * out_k
                _paste_center(frame, hook_img, W / 2, y + hook_img.height / 2)

            # ---- mid-video FOLLOW pop-up ----
            if f_on <= t < f_on + 2.4:
                lt2 = t - f_on
                tap = f_tap - f_on
                out = ease_out((lt2 - 2.1) / 0.3) if lt2 > 2.1 else 0
                y = 330 - 500 * out
                if lt2 < tap:
                    _paste_center(frame, btn_follow, W / 2, y, 0.62 * ease_out_back(lt2 / 0.3) * (1 + 0.04 * math.sin(lt2 * 9)))
                    hk = ease_out((lt2 - (tap - 0.45)) / 0.45)
                    _paste_center(frame, hand, W / 2 + 260 - 150 * hk, y + 200 - 120 * hk, 0.8, -10, alpha=min(1, max(0, hk * 2)))
                else:
                    _paste_center(frame, btn_done, W / 2, y, 0.62 * (1 - 0.08 * math.exp(-(lt2 - tap) * 12)))
                    pass

            # ---- captions ----
            ci = None
            for i, c in enumerate(chunks):
                if c[0]["start"] <= t:
                    ci = i
                else:
                    break
            if ci is not None and t <= chunks[ci][-1]["end"] + 0.7:
                chunk = chunks[ci]
                hl = max(j for j, w in enumerate(chunk) if w["start"] <= t)
                age = t - chunk[0]["start"]
                s = round(ease_out_back(age / 0.16, 2.2) * 0.25 + 0.75, 2) if age < 0.16 else 1.0
                key = (ci, hl)
                base = cap_cache.get(key)
                if base is None:
                    base = _caption_img(chunk, hl, accent, style)
                    if len(cap_cache) > 40:
                        cap_cache.clear()
                    cap_cache[key] = base
                cy = CAP_Y + (60 if card and card[3] > CAP_Y - 160 else 0)
                _paste_center(frame, base, W / 2, cy, s)

            # ---- HUD: photo credit, progress bar, handle (drawn on an overlay so alpha blends) ----
            hud = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(hud)
            if m and m.get("credit"):
                cy = card[3] + 14 if card else 1560
                d.text((W / 2, cy), m["credit"][:90], font=F_CREDIT, fill=(255, 255, 255, 170), anchor="ma")
            d.rectangle([0, 0, W, 14], fill=(255, 255, 255, 50))
            d.rectangle([0, 0, int(W * t / duration), 14], fill=accent + (255,))
            if channel and sc["kind"] != "hook":
                d.text((W / 2, 34), channel, font=F_SMALL, fill=(255, 255, 255, 150), anchor="ma")
            if series_tag and sc["kind"] != "hook":
                d.text((W / 2, 74), series_tag.upper(), font=F_SMALL, fill=accent + (230,), anchor="ma")
            if sc.get("label") and sc["kind"] != "hook":  # place / name on screen
                if sc["label"] not in label_cache:
                    label_cache[sc["label"]] = _label_img(sc["label"])
                li = label_cache[sc["label"]]
                k = ease_out_back(min(1.0, lt / 0.35)) if sc is first_label_scene.get(sc["label"]) else 1.0
                _paste_center(hud, li, W / 2, 205, scale=max(0.05, k))
            frame.alpha_composite(hud)

            rgb = frame.convert("RGB")

            # ---- camera: shake, transitions ----
            if t < 0.4:
                amp = 16 * (1 - t / 0.4)
                rgb = rgb.transform((W, H), Image.AFFINE, (1, 0, rng.uniform(-amp, amp), 0, 1, rng.uniform(-amp, amp)), Image.BILINEAR)
            if sc["kind"] != "hook" and lt < 0.22:
                k = lt / 0.22
                if sc["trans"] in (0, 2):
                    z = 1 + 0.14 * (1 - ease_out(k))
                    cw, ch = int(W / z), int(H / z)
                    rgb = rgb.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)
                if sc["trans"] == 1:  # smooth push-out (premium alternative to a glitch)
                    z = 1 + 0.08 * (1 - ease_out(k))
                    cw, ch = int(W / z), int(H / z)
                    rgb = rgb.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)
                    if lt < 0.1:
                        rgb = rgb.filter(ImageFilter.BoxBlur(int(10 * (1 - lt / 0.1))))
                if sc["trans"] == 2 and lt < 0.1:  # soft dip from black instead of a white flash
                    rgb = Image.blend(rgb, Image.new("RGB", (W, H), (0, 0, 0)), 0.5 * (1 - lt / 0.1))
            if stat_on and (t - sc["stat_t"]) < 0.3:  # small punch-in on the number
                z = 1 + 0.035 * math.sin(math.pi * (t - sc["stat_t"]) / 0.3)
                cw, ch = int(W / z), int(H / z)
                rgb = rgb.crop(((W - cw) // 2, (H - ch) // 2, (W + cw) // 2, (H + ch) // 2)).resize((W, H), Image.BILINEAR)

            if cover_path and f == int(FPS * 0.9):
                rgb.save(cover_path, quality=88)
            if thumb is not None and f == 0:  # first frame = thumbnail (Instagram/Facebook/YouTube use it as cover)
                rgb = thumb
            proc.stdin.write(rgb.tobytes())
    finally:
        for p in providers.values():
            if isinstance(p, ClipBG):
                p.close()
        proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed while encoding the video")
    log(f"  rendered {os.path.basename(os.path.dirname(out_path))}/video.mp4 ({duration:.1f}s, {len(scenes)} scenes)")
