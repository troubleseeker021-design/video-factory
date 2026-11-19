"""The Documentary Unit: one calm, connected, Discovery/Animal-Planet style long video every week
(landscape 1920x1080, about 6-8 minutes) instead of a pile of Shorts glued together.

Story: Gemini writes ONE flowing documentary (5 chapters, smooth transitions) that grows out of the
week's best-performing Short. Pictures: AI cinematic stills (Cloudflare, free) + real Wikipedia photos,
animated with slow camera moves and soft dissolves. Voice: a calm documentary narrator (free Edge voice).
"""
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess

from .common import DATA, FONTS, ffmpeg_exe, log, read_json

W, H, FPS = 1920, 1080, 30
WORK = os.path.join(DATA, "_doc")
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS)]
FAST = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS)]  # in-between files

PROMPT = """You are the head writer of a premium, award-winning nature & science documentary series in the style of
BBC Earth, National Geographic and Discovery Channel, for the YouTube channel Hidden Facts HQ. The writing must be
broadcast quality: elegant, emotional, precise and full of wonder - never cheesy, never clickbait.

Write ONE documentary episode (6 to 8 minutes of narration) that grows out of this topic that our viewers loved:
"{seed_title}" ({seed_hook}).
Go deeper and wider: the story behind it, how it works, where it happens, the people or animals involved,
and what it means for us. It must feel like one smooth, connected journey - never a list of separate facts.

Style rules:
- Calm, warm, curious narrator voice. Vivid "picture this" storytelling: places, times, sounds, scale.
- Open with a cold open (a gripping moment), then 5 chapters, then a reflective ending that ties back to the
  opening image.
- Every chapter ends with one short cliffhanger line that pulls the viewer into the next one, often a quiet
  question ("But will anyone answer?", "So where did it all go?").
- Simple words, short and medium sentences, concrete numbers, real names and places. 100% verified facts only;
  say "scientists think" when uncertain. No exclamation marks, no "..." pauses, no "in this video", no
  "like and subscribe".
- Total 950 to 1150 words.

Return JSON only:
{{"title": "documentary YouTube title, under 70 characters, curiosity-driven, no hashtags",
  "description": "2-3 sentence YouTube description",
  "tags": ["8 search tags"],
  "thumb": {{"top": "1-3 word white hook line in CAPS (e.g. A WATERFALL)",
            "main": "1-3 word yellow punch line in CAPS that completes it (e.g. UNDER THE OCEAN)",
            "number": "a short shocking REAL number from the story for a red badge (e.g. 3.5 KM DROP, 370 KM/H, 10,000 YEARS OLD), or null"}},
  "wiki": ["2-4 exact English Wikipedia article titles for real photos"],
  "keywords": ["4 short stock-photo search phrases for beautiful real photos of this story (e.g. eucalyptus forest)"],
  "parts": [
    {{"chapter": "" for the cold open, otherwise a short chapter title,
      "text": "1 paragraph of narration, 2 to 4 sentences",
      "shots": ["one picture PER SENTENCE, in the same order as the sentences, so the picture on screen always
               matches the exact words being spoken at that moment. Each: a cinematic landscape 16:9 shot that
               shows exactly that sentence (subject, action, setting, light, camera), photorealistic
               documentary still, no text. When the sentence explains HOW something works, make it a clear
               cut-away / cross-section 3D illustration of the inside"],
      "footage": "2-3 word stock VIDEO search for real moving footage that shows this paragraph (e.g. 'waterfall aerial',
                  'underwater current', 'earth rotating', 'eucalyptus forest'), or null",
      "ambience": "1-3 word nature sound of this paragraph's setting (e.g. 'waterfall', 'underwater', 'forest birds',
                   'desert wind', 'ocean waves', 'rain', 'cave drips')",
      "graphic": an on-screen teaching graphic that EXPLAINS this paragraph like a teacher at a whiteboard, or null.
        One of:
        {{"type": "steps", "title": "how it works", "items": ["step 1", "step 2", "step 3"]}}  (2-4 steps, max 6 words each)
        {{"type": "number", "value": "40 m", "label": "how deep the roots reach", "compare": "as tall as a 13-storey building"}}
        {{"type": "compare", "title": "", "items": [{{"label": "", "value": 40, "unit": "m"}}, {{"label": "", "value": 10, "unit": "m"}}]}}
        {{"type": "facts", "title": "", "items": ["short fact", "short fact", "short fact"]}}  (2-3 facts, max 7 words each)
      }}
  ]}}
Use 22 to 30 parts in total (about 40 words each). The cold open is the first 2-3 parts with chapter "".
Give a "graphic" to about half of the parts - every part that explains a process, a size, a number or a
comparison. Numbers in graphics must be real and match the narration. Use null for purely emotional moments.
"""

DOC_STYLE = ("photorealistic nature documentary still, BBC Earth cinematography, natural light, rich detail, "
             "wide cinematic 16:9 composition, shallow depth of field, absolutely no text, no letters, no watermark")


# ------------------------------------------------------------------ helpers
def _run(args, timeout=1800):
    p = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", *args], capture_output=True, text=True,
                       timeout=timeout)
    if p.returncode:
        raise RuntimeError(f"ffmpeg failed: {p.stderr[-400:]}")


def _font(name, size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(os.path.join(FONTS, name), size)
    except Exception:
        return ImageFont.load_default()


def _landscape(img):
    """Any picture -> 1920x1080 (centre crop), gently graded like a documentary."""
    from PIL import Image, ImageEnhance
    img = img.convert("RGB")
    tw, th = W, H
    r = max(tw / img.width, th / img.height)
    img = img.resize((max(tw, int(img.width * r + 0.5)), max(th, int(img.height * r + 0.5))), Image.LANCZOS)
    x, y = (img.width - tw) // 2, (img.height - th) // 2
    img = img.crop((x, y, x + tw, y + th))
    return _grade(img)


def _grade(img):
    """Broadcast look: crisp detail, gentle cinematic teal-and-warm grade and a soft vignette."""
    import numpy as np
    from PIL import Image, ImageEnhance, ImageFilter
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.05)
    a = np.asarray(img).astype(np.float32) / 255.0
    lum = a.mean(axis=2, keepdims=True)
    shadows, highs = (1 - lum) ** 2, lum ** 2
    a += shadows * np.array([-0.015, 0.01, 0.03]) + highs * np.array([0.03, 0.012, -0.02])  # teal shadows, warm highlights
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    r = np.sqrt(((xx - a.shape[1] / 2) / (a.shape[1] / 2)) ** 2 + ((yy - a.shape[0] / 2) / (a.shape[0] / 2)) ** 2)
    a *= (1 - 0.28 * np.clip(r - 0.55, 0, 1) ** 1.6)[..., None]  # vignette
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype("uint8"))


def _logo_bug(path):
    """Small see-through channel logo in the corner, like a TV network."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    logo_path = os.path.join(os.path.dirname(DATA), "docs", "logo.png")
    try:
        lg = Image.open(logo_path).convert("RGBA").resize((86, 86), Image.LANCZOS)
        mask = Image.new("L", lg.size, 0)
        ImageDraw.Draw(mask).ellipse([0, 0, 85, 85], fill=150)
        lg.putalpha(mask)
        img.alpha_composite(lg, (W - 150, H - 140))
    except Exception:
        pass
    d = ImageDraw.Draw(img)
    d.text((W - 160, H - 97), "HIDDEN FACTS HQ", font=_font("Poppins-Bold.ttf", 22), fill=(255, 255, 255, 140), anchor="rm")
    img.save(path)


def _end_card(title, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, H], fill=(4, 6, 14, 220))
    d.text((W / 2, H / 2 - 120), "THANKS FOR WATCHING", font=_font("Anton-Regular.ttf", 110), fill=(255, 255, 255, 255), anchor="mm")
    d.text((W / 2, H / 2 + 10), "A new Hidden Facts HQ documentary every Sunday", font=_font("Poppins-Bold.ttf", 44),
           fill=(255, 196, 0, 255), anchor="mm")
    d.rounded_rectangle([W / 2 - 230, H / 2 + 90, W / 2 + 230, H / 2 + 170], radius=40, fill=(230, 33, 23, 255))
    d.text((W / 2, H / 2 + 130), "SUBSCRIBE", font=_font("Poppins-Bold.ttf", 44), fill=(255, 255, 255, 255), anchor="mm")
    img.save(path)


def _ai_still(prompt, path, seed):
    from io import BytesIO
    from PIL import Image
    from . import aiart
    data = aiart._cloudflare(f"{prompt.strip().rstrip('.')}. {DOC_STYLE}", seed, force=True)
    if not data:
        return None
    try:
        img = Image.open(BytesIO(data))
        if min(img.size) < 400:
            return None
        _landscape(img).save(path, quality=93)
        return path
    except Exception:
        return None


def _real_photos(titles, limit=6):
    from PIL import Image
    from . import media
    out = []
    for t in titles[:4]:
        for pic in media.wiki_images(t, limit=2):
            try:
                p = media._download(pic["url"], ".jpg")
                img = Image.open(p)
                if img.width >= 900 and img.width >= img.height:  # landscape photos only
                    out.append({"path": p, "credit": pic["credit"]})
            except Exception:
                pass
            if len(out) >= limit:
                return out
    return out


def _unsplash_landscape(queries, limit=40):
    """Beautiful real landscape photos from Unsplash (used when the free AI pictures are used up)."""
    import urllib.parse
    from . import media
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not key:
        return []
    hdr = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}
    out = []
    for q in queries[:6]:
        try:
            qs = urllib.parse.urlencode({"query": q, "orientation": "landscape", "per_page": 8, "content_filter": "high"})
            for p in media._get_json(f"https://api.unsplash.com/search/photos?{qs}", hdr).get("results", []):
                raw = (p.get("urls") or {}).get("raw")
                if not raw:
                    continue
                path = media._download(raw + "&w=1920&h=1080&fit=crop&q=85&fm=jpg", ".jpg")
                try:
                    media._get_json((p.get("links") or {}).get("download_location"), hdr)  # Unsplash rule
                except Exception:
                    pass
                name = ((p.get("user") or {}).get("name") or "Unsplash")[:40]
                out.append({"path": path, "credit": f"{name} / Unsplash"})
                if len(out) >= limit:
                    return out
        except Exception as e:
            log(f"  unsplash search failed for '{q}': {str(e)[:100]}")
    return out


def _fallback_still(text, path, seed):
    """Calm dark gradient with soft light, if no picture could be made."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = rng.uniform(0.3, 0.7) * W, rng.uniform(0.3, 0.6) * H
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / W
    base = np.stack([20 + 60 * np.exp(-d * 3), 30 + 70 * np.exp(-d * 3), 50 + 90 * np.exp(-d * 2.5)], -1)
    Image.fromarray(base.clip(0, 255).astype("uint8")).save(path, quality=92)
    return path


# ------------------------------------------------------------------ overlays
def _lower_third(chapter_no, title, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(260):  # soft dark band at the bottom-left
        a = int(150 * (i / 260) ** 1.5)
        d.line([(0, H - 260 + i), (W, H - 260 + i)], fill=(0, 0, 0, a))
    d.rectangle([110, H - 205, 118, H - 95], fill=(255, 196, 0, 255))
    d.text((145, H - 200), f"CHAPTER {chapter_no}", font=_font("Poppins-Bold.ttf", 34),
           fill=(255, 196, 0, 255))
    d.text((143, H - 160), title.upper(), font=_font("Anton-Regular.ttf", 72), fill=(255, 255, 255, 255))
    img.save(path)


def _title_card(title, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(H):
        a = int(110 * (1 - abs(i - H * 0.55) / (H * 0.55)) ** 2)
        d.line([(0, i), (W, i)], fill=(0, 0, 0, max(0, a)))
    f = _font("Anton-Regular.ttf", 120)
    words, lines, cur = title.upper().split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) > 1500 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    y0 = H * 0.5 - len(lines) * 70
    for i, ln in enumerate(lines[:3]):
        d.text((W / 2, y0 + i * 140), ln, font=f, fill=(255, 255, 255, 255), anchor="mm",
               stroke_width=3, stroke_fill=(0, 0, 0, 160))
    d.text((W / 2, y0 + len(lines[:3]) * 140 + 20), "A HIDDEN FACTS HQ DOCUMENTARY",
           font=_font("Poppins-Bold.ttf", 38), fill=(255, 196, 0, 255), anchor="mm")
    img.save(path)


def _thumbnail(still, text, path):
    """Click-worthy thumbnail: one dramatic real photo, a 2-line headline (white + yellow) and a red
    number badge - e.g. "A WATERFALL / UNDER THE OCEAN" + "3.5 KM DROP"."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    if isinstance(text, dict):
        top, main, num = (str(text.get(k) or "").strip().upper() for k in ("top", "main", "number"))
    else:
        words = str(text).upper().split()
        cut = max(1, len(words) // 2)
        top, main, num = " ".join(words[:cut]), " ".join(words[cut:]), ""
    if not main:
        top, main = "", top
    TW, TH = 1280, 720
    src = Image.open(still).convert("RGB")
    r = max(TW / src.width, TH / src.height) * 1.04
    src = src.resize((int(src.width * r + 0.5), int(src.height * r + 0.5)), Image.LANCZOS)
    x, y = (src.width - TW) // 2, (src.height - TH) // 2
    img = src.crop((x, y, x + TW, y + TH))
    img = ImageEnhance.Color(ImageEnhance.Contrast(img).enhance(1.18)).enhance(1.15)
    shade = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    for yy in range(TH):  # dark at the top (headline) and the bottom (badge), clear in the middle
        a = 150 * max(0.0, 1 - yy / 330) + 150 * max(0.0, (yy - 470) / 250)
        d.line([(0, yy), (TW, yy)], fill=(0, 4, 20, int(min(200, a))))
    img = Image.alpha_composite(img.convert("RGBA"), shade)

    def fit(t, size, width):
        f = _font("Anton-Regular.ttf", size)
        while f.getlength(t) > width and size > 48:
            size -= 4
            f = _font("Anton-Regular.ttf", size)
        return f

    def headline(t, y, size, fill):
        if not t:
            return
        f = fit(t, size, TW - 100)
        glow = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
        ImageDraw.Draw(glow).text((TW / 2, y), t, font=f, fill=(0, 0, 0, 230), anchor="ma",
                                  stroke_width=16, stroke_fill=(0, 0, 0, 230))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
        ImageDraw.Draw(img).text((TW / 2, y), t, font=f, fill=fill, anchor="ma", stroke_width=10, stroke_fill=(0, 0, 0))

    headline(top, 40, 128, (255, 255, 255))
    headline(main, 175 if top else 90, 128, (255, 212, 0))
    if num:
        f = fit(num, 76, 600)
        w = int(f.getlength(num)) + 70
        bx, by = TW - w - 40, TH - 140
        badge = Image.new("RGBA", (TW, TH), (0, 0, 0, 0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle([bx, by, bx + w, by + 104], radius=20, fill=(255, 45, 45, 255), outline=(0, 0, 0), width=5)
        img.alpha_composite(badge.filter(ImageFilter.GaussianBlur(10)))  # soft red glow
        img.alpha_composite(badge)
        ImageDraw.Draw(img).text((bx + w / 2, by + 52), num, font=f, fill=(255, 255, 255), anchor="mm",
                                 stroke_width=4, stroke_fill=(0, 0, 0))
    img.convert("RGB").save(path, quality=93)


# ------------------------------------------------------------------ teaching graphics
PANEL = (1150, 150, 1860, 930)  # right side of the screen - the picture stays visible on the left
GOLD, WHITE, DIM = (255, 196, 0, 255), (255, 255, 255, 255), (170, 176, 196, 255)


def _canvas():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = PANEL
    d.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=(8, 12, 24, 222), outline=(255, 196, 0, 120), width=3)
    return img, d


def _wrap(d, text, font, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def _steps_img(title, items, active):
    img, d = _canvas()
    x0, y0, x1, y1 = PANEL
    ft, fi, fn = _font("Poppins-Bold.ttf", 40), _font("Poppins-Bold.ttf", 34), _font("Anton-Regular.ttf", 40)
    d.text((x0 + 40, y0 + 30), (title or "How it works").upper(), font=ft, fill=GOLD)
    n = len(items)
    step_h = (y1 - y0 - 140) / max(1, n)
    for j, it in enumerate(items):
        cy = y0 + 130 + j * step_h + step_h / 2
        on, done = j == active, j < active
        col = GOLD if on else (WHITE if done else DIM)
        d.ellipse([x0 + 40, cy - 34, x0 + 108, cy + 34], fill=(255, 196, 0, 255) if on else (40, 46, 70, 255),
                  outline=col, width=3)
        d.text((x0 + 74, cy), str(j + 1), font=fn, fill=(10, 10, 20, 255) if on else col, anchor="mm")
        lines = _wrap(d, it, fi, x1 - x0 - 190)[:2]
        for k, ln in enumerate(lines):
            d.text((x0 + 135, cy - 22 * len(lines) + 4 + k * 44), ln, font=fi, fill=WHITE if (on or done) else DIM)
        if j < n - 1:  # arrow down to the next step
            ay = cy + 40
            d.line([(x0 + 74, ay), (x0 + 74, ay + step_h - 82)], fill=(255, 196, 0, 160), width=4)
            d.polygon([(x0 + 64, ay + step_h - 90), (x0 + 84, ay + step_h - 90), (x0 + 74, ay + step_h - 76)],
                      fill=(255, 196, 0, 200))
    return img


def _number_img(value, label, compare):
    img, d = _canvas()
    x0, y0, x1, y1 = PANEL
    cx = (x0 + x1) / 2
    fv = _font("Anton-Regular.ttf", 170)
    while d.textlength(str(value), font=fv) > x1 - x0 - 80 and fv.size > 60:
        fv = _font("Anton-Regular.ttf", fv.size - 10)
    d.text((cx, y0 + 260), str(value), font=fv, fill=GOLD, anchor="mm")
    fl, fc = _font("Poppins-Bold.ttf", 40), _font("Poppins-Bold.ttf", 32)
    for k, ln in enumerate(_wrap(d, label or "", fl, x1 - x0 - 100)[:3]):
        d.text((cx, y0 + 420 + k * 54), ln, font=fl, fill=WHITE, anchor="mm")
    if compare:
        d.line([(x0 + 120, y0 + 600), (x1 - 120, y0 + 600)], fill=(255, 196, 0, 120), width=2)
        for k, ln in enumerate(_wrap(d, compare, fc, x1 - x0 - 120)[:3]):
            d.text((cx, y0 + 650 + k * 44), ln, font=fc, fill=DIM, anchor="mm")
    return img


def _compare_img(title, items, grow=1.0):
    img, d = _canvas()
    x0, y0, x1, y1 = PANEL
    d.text((x0 + 40, y0 + 30), (title or "Compare").upper(), font=_font("Poppins-Bold.ttf", 40), fill=GOLD)
    vals = [float(it.get("value") or 0) for it in items][:4]
    top = max(vals) or 1
    fl, fv = _font("Poppins-Bold.ttf", 32), _font("Anton-Regular.ttf", 44)
    row = (y1 - y0 - 150) / max(1, len(vals))
    for j, (it, v) in enumerate(zip(items, vals)):
        y = y0 + 140 + j * row
        d.text((x0 + 40, y), str(it.get("label", ""))[:28], font=fl, fill=WHITE)
        bw = (x1 - x0 - 240) * (v / top) * grow
        d.rounded_rectangle([x0 + 40, y + 50, x0 + 40 + max(8, bw), y + 100], radius=12,
                            fill=(255, 196, 0, 255) if j == 0 else (120, 160, 255, 255))
        val = f"{v:g} {it.get('unit', '')}".strip()
        d.text((x0 + 56 + max(8, bw), y + 75), val, font=fv, fill=WHITE, anchor="lm")
    return img


def _facts_img(title, items, shown):
    img, d = _canvas()
    x0, y0, x1, y1 = PANEL
    d.text((x0 + 40, y0 + 30), (title or "Did you know").upper(), font=_font("Poppins-Bold.ttf", 40), fill=GOLD)
    fi = _font("Poppins-Bold.ttf", 36)
    y = y0 + 140
    for j, it in enumerate(items[:shown]):
        d.rectangle([x0 + 40, y + 10, x0 + 52, y + 50], fill=GOLD)
        for k, ln in enumerate(_wrap(d, it, fi, x1 - x0 - 140)[:3]):
            d.text((x0 + 75, y + k * 48), ln, font=fi, fill=WHITE)
        y += 48 * min(3, len(_wrap(d, it, fi, x1 - x0 - 140))) + 50
    return img


def _graphic_frames(g, a, b):
    """-> [(image, start, end)]: the graphic builds up step by step while the narrator explains."""
    t = g.get("type")
    if t == "steps" and g.get("items"):
        items = [str(x) for x in g["items"]][:4]
        seg = (b - a) / len(items)
        return [(_steps_img(g.get("title"), items, k), a + k * seg, a + (k + 1) * seg + 0.05) for k in range(len(items))]
    if t == "facts" and g.get("items"):
        items = [str(x) for x in g["items"]][:3]
        seg = (b - a) / len(items)
        return [(_facts_img(g.get("title"), items, k + 1), a + k * seg, a + (k + 1) * seg + 0.05) for k in range(len(items))]
    if t == "number" and g.get("value"):
        return [(_number_img(g["value"], g.get("label"), g.get("compare")), a, b)]
    if t == "compare" and len(g.get("items") or []) >= 2:
        return [(_compare_img(g.get("title"), g["items"], 0.35), a, a + 0.35),
                (_compare_img(g.get("title"), g["items"], 0.7), a + 0.3, a + 0.65),
                (_compare_img(g.get("title"), g["items"], 1.0), a + 0.6, b)]
    return []


# ------------------------------------------------------------------ real footage + sound (free)
def _pixabay_clip(query, used):
    """A real landscape video clip from Pixabay (free for YouTube, no credit needed)."""
    import urllib.parse
    from . import media
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key or not query:
        return None
    qs = urllib.parse.urlencode({"key": key, "q": str(query)[:90], "per_page": 20, "safesearch": "true"})
    try:
        hits = media._get_json(f"https://pixabay.com/api/videos/?{qs}").get("hits", [])
    except Exception as e:
        log(f"  pixabay search failed for '{query}': {str(e)[:100]}")
        return None
    top = hits[:8]
    random.shuffle(top)
    for h in top:
        if h.get("id") in used or (h.get("duration") or 0) < 4:
            continue
        vids = h.get("videos") or {}
        for size in ("large", "medium"):
            v = vids.get(size) or {}
            if v.get("url") and (v.get("width") or 0) >= 1280 and (v.get("width") or 0) > (v.get("height") or 1):
                if (v.get("size") or 0) > 70 * (1 << 20):
                    continue  # huge 4K-sized file: use the smaller version of the same clip
                try:
                    path = media._download(v["url"], ".mp4", max_mb=120)
                except Exception as e:
                    log(f"  pixabay {size} clip skipped: {str(e)[:80]}")
                    continue  # too big? try the smaller version of the same clip
                used.add(h.get("id"))
                return path
    return None


def _pixabay_photo(query, used):
    """A real landscape photo from Pixabay that matches this paragraph (when the AI pictures are used up)."""
    import urllib.parse
    from . import media
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key or not query:
        return None
    qs = urllib.parse.urlencode({"key": key, "q": str(query)[:90], "image_type": "photo", "orientation": "horizontal",
                                 "per_page": 12, "safesearch": "true", "min_width": 1280})
    try:
        hits = media._get_json(f"https://pixabay.com/api/?{qs}").get("hits", [])
    except Exception as e:
        log(f"  pixabay photo search failed for '{query}': {str(e)[:100]}")
        return None
    for h in hits[:8]:
        url = h.get("largeImageURL")
        if not url or h.get("id") in used:
            continue
        try:
            path = media._download(url, ".jpg")
        except Exception:
            continue
        used.add(h.get("id"))
        return path
    return None


def _video_clip(src, secs, out):
    """Real footage, filled to 16:9, graded like the stills, looped if it is shorter than the shot."""
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
          "eq=contrast=1.06:saturation=1.08,vignette=PI/5,format=yuv420p")
    _run(["-stream_loop", "-1", "-i", src, "-t", f"{secs:.3f}", "-vf", vf, *FAST, "-an", out])


def _freesound(query, used):
    """A free CC0 nature sound from Freesound (no credit needed). Returns a local mp3 path."""
    import urllib.parse
    from . import media
    key = os.environ.get("FREESOUND_API_KEY", "").strip()
    if not key or not query:
        return None
    qs = urllib.parse.urlencode({"query": str(query)[:60], "token": key, "page_size": 10,
                                 "filter": 'license:"Creative Commons 0" duration:[15 TO 600]',
                                 "fields": "id,name,previews,duration"})
    try:
        res = media._get_json(f"https://freesound.org/apiv2/search/text/?{qs}").get("results", [])
    except Exception as e:
        log(f"  freesound search failed for '{query}': {str(e)[:100]}")
        return None
    for r in res[:5]:
        url = (r.get("previews") or {}).get("preview-hq-mp3")
        if not url or r.get("id") in used:
            continue
        try:
            path = media._download(url, ".mp3")
        except Exception:
            continue
        used.add(r.get("id"))
        return path
    return None


def _load_audio(path, sr=44100):
    import numpy as np
    p = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "2", "-ar", str(sr), "-"],
                       capture_output=True, timeout=300)
    return np.frombuffer(p.stdout, dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768.0


def _sound_design(parts, starts, total, sfx_events, path, sr=44100):
    """Quiet nature ambience under each scene (waterfall, wind, ocean...) + soft whooshes and pops on the
    graphics and chapter titles. One stereo wav, mixed under the narrator."""
    import wave
    import numpy as np
    from . import sfx
    n = int((total + 1) * sr)
    bed = np.zeros((n, 2), dtype=np.float32)
    segs = []  # merge neighbouring paragraphs that share the same sound
    for i, p in enumerate(parts):
        q = str(p.get("ambience") or "").strip().lower()
        a = starts[i]
        b = starts[i + 1] if i + 1 < len(starts) else total - 6.0
        if segs and segs[-1][2] == q:
            segs[-1][1] = b
        else:
            segs.append([a, b, q])
    from concurrent.futures import ThreadPoolExecutor
    uniq = list(dict.fromkeys(q for _, _, q in segs if q))[:10]
    used, found = set(), 0

    def fetch(q):
        try:
            mp3 = _freesound(q, used)
            return q, (_load_audio(mp3) if mp3 else None)
        except Exception:
            return q, None
    with ThreadPoolExecutor(5) as ex:
        cache = dict(ex.map(fetch, uniq))
    last = None
    for a, b, q in segs:
        src = cache.get(q) if q else None
        if src is None and q:
            src = last  # an 11th different sound, or one that failed: keep the previous scene's sound
        last = src if src is not None else last
        if src is None or len(src) < sr * 2:
            continue
        found += 1
        L = int((b - a + 0.5) * sr)  # starts just before its scene and is gone when the scene ends
        reps = int(np.ceil(L / len(src)))
        seg = np.tile(src, (reps, 1))[:L].copy()
        rms = float(np.sqrt(np.mean(seg ** 2))) or 1.0
        seg *= 0.028 / rms  # clearly audible but under the narrator, whatever the recording's loudness
        f = min(int(1.2 * sr), L // 3)
        ramp = np.linspace(0, 1, f, dtype=np.float32)[:, None]
        seg[:f] *= ramp
        seg[-f:] *= ramp[::-1]
        i0 = int(max(0, a - 0.5) * sr)
        m = min(L, n - i0)
        if m > 0:
            bed[i0:i0 + m] += seg[:m]
    sounds = {}
    for t, name, gain in sfx_events:
        if name not in sounds:
            sounds[name] = sfx.BANK[name]().astype(np.float32)
        s = sounds[name] * gain
        i0 = int(max(0, t) * sr)
        m = min(len(s), n - i0)
        if m > 0:
            bed[i0:i0 + m] += s[:m, None]
    bed = np.clip(bed, -0.95, 0.95)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((bed * 32767).astype("<i2").tobytes())
    log(f"  sound design: {found} nature sounds, {len(sfx_events)} effects")
    return path


# ------------------------------------------------------------------ subtitles (Netflix style)
SUB_H = 300  # subtitle strip at the bottom of the frame


def _sub_cues(parts, part_words, offsets, intro, end_at):
    """Short 1-2 line subtitle cues, timed to the narrator's words."""
    cues = []
    for i, p in enumerate(parts):
        ws = part_words[i] or []
        toks = p["text"].split()
        if not ws:
            continue
        names = toks if len(toks) == len(ws) else [str(w.get("word", "")) for w in ws]
        base = offsets[i] + intro
        cur = []
        for k, w in enumerate(ws):
            cur.append(k)
            txt = " ".join(names[j] for j in cur)
            end_sent = names[k][-1:] in ".?!"
            soft = names[k][-1:] in ",;:" and len(txt) > 42
            if len(txt) > 64 or (end_sent and len(cur) >= 2) or soft or k == len(ws) - 1:
                a = base + float(ws[cur[0]]["start"])
                b = base + float(ws[cur[-1]].get("end", ws[cur[-1]]["start"] + 0.4)) + 0.35
                if a < end_at:
                    cues.append([a, min(b, end_at), txt])
                cur = []
    for c, n in zip(cues, cues[1:]):  # never overlap the next line
        c[1] = min(c[1], n[0] - 0.04)
    return [c for c in cues if c[1] - c[0] > 0.3]


def _sub_img(text, high, path):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (W, SUB_H), (0, 0, 0, 0))
    if text:
        f = _font("Poppins-Medium.ttf", 40)
        d = ImageDraw.Draw(img)
        lines = _wrap(d, text, f, 1150)[:2]
        lh = 54
        y0 = (40 if high else SUB_H - 40 - lh * len(lines))
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        for k, ln in enumerate(lines):
            sd.text((W / 2 + 2, y0 + k * lh + 3), ln, font=f, fill=(0, 0, 0, 200), anchor="ma")
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(4)))
        d = ImageDraw.Draw(img)
        for k, ln in enumerate(lines):
            d.text((W / 2, y0 + k * lh), ln, font=f, fill=(255, 255, 255, 255), anchor="ma",
                   stroke_width=2, stroke_fill=(0, 0, 0, 170))
    img.save(path)


def _subtitle_track(cues, busy, total):
    """All subtitles as ONE image-sequence input (fast): a concat list of strips with their durations."""
    blank = os.path.join(WORK, "sub_blank.png")
    _sub_img("", False, blank)
    lines, t = [], 0.0
    for n, (a, b, txt) in enumerate(cues):
        if a > t:
            lines += [f"file '{blank}'", f"duration {a - t:.3f}"]
        png = os.path.join(WORK, f"sub{n:03d}.png")
        high = any(x < b and a < y for x, y in busy)  # move up while a chapter title is on screen
        _sub_img(txt, high, png)
        lines += [f"file '{png}'", f"duration {b - a:.3f}"]
        t = b
    lines += [f"file '{blank}'", f"duration {max(0.5, total - t):.3f}", f"file '{blank}'"]
    lst = os.path.join(WORK, "subs.txt")
    with open(lst, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return lst


# ------------------------------------------------------------------ 3D photo effect (depth parallax)
DEPTH_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
_DEPTH = {}


def _depth_session():
    """Free open-source AI (Depth Anything V2, small) that guesses how far away each part of a picture is."""
    if "s" in _DEPTH:
        return _DEPTH["s"]
    _DEPTH["s"] = None
    try:
        import onnxruntime as ort
        from . import media
        mdir = os.path.join(DATA, "_models")
        os.makedirs(mdir, exist_ok=True)
        path = os.path.join(mdir, "depth_anything_v2_small.onnx")
        if not os.path.exists(path) or os.path.getsize(path) < 10_000_000:
            tmp = media._download(DEPTH_URL, ".onnx", max_mb=200)
            shutil.move(tmp, path)
        _DEPTH["s"] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        log("  3D photo effect: depth model ready")
    except Exception as e:
        log(f"  (3D photo effect off: {str(e)[:120]})")
    return _DEPTH["s"]


def _depth_map(rgb):
    """rgb: HxWx3 uint8 -> HxW float32 0..1 (1 = nearest). None if the model is unavailable."""
    import cv2
    import numpy as np
    sess = _depth_session()
    if sess is None:
        return None
    x = cv2.resize(rgb, (518, 518), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    d = np.asarray(sess.run(None, {sess.get_inputs()[0].name: x})[0], dtype=np.float32).squeeze()
    d = cv2.resize(d, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    d = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
    return cv2.GaussianBlur(d, (0, 0), 6).astype(np.float32)


def _parallax_clip(still, secs, out, k, depth_fn=None):
    """A slow 3D camera move through a still picture: near things move more than far things."""
    import cv2
    import numpy as np
    n = max(2, int(secs * FPS))
    img = cv2.cvtColor(cv2.imread(still), cv2.COLOR_BGR2RGB)
    over = 1.14  # a little extra picture around the edges, so the move never shows an empty border
    BW, BH = int(W * over), int(H * over)
    s = max(BW / img.shape[1], BH / img.shape[0])
    img = cv2.resize(img, (int(img.shape[1] * s + 0.5), int(img.shape[0] * s + 0.5)), interpolation=cv2.INTER_AREA)
    y0, x0 = (img.shape[0] - BH) // 2, (img.shape[1] - BW) // 2
    img = np.ascontiguousarray(img[y0:y0 + BH, x0:x0 + BW])
    d = (depth_fn or _depth_map)(img)
    if d is None:
        raise RuntimeError("no depth")
    d = cv2.resize(d, (W, H), interpolation=cv2.INTER_AREA)  # depth at output size (approximation)
    X, Y = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
    cx, cy = W / 2, H / 2
    move = k % 4  # dolly in, dolly out, truck right, truck left
    p = subprocess.Popen([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *FAST, "-an", out], stdin=subprocess.PIPE)
    try:
        for f in range(n):
            t = f / (n - 1)
            e = t * t * (3 - 2 * t)  # ease in/out
            if move in (0, 1):
                z = e if move == 0 else 1 - e
                scale = 1.0 + z * (0.05 + 0.09 * d)  # nearer = grows faster
                sx = cx + (X - cx) / scale
                sy = cy + (Y - cy) / scale
            else:
                dirn = 1 if move == 2 else -1
                shift = dirn * (e - 0.5) * (40 + 70 * d)  # nearer = slides further
                sx = X + shift
                sy = Y + (e - 0.5) * 10 * d
            mx = (sx + (BW - W) / 2).astype(np.float32)
            my = (sy + (BH - H) / 2).astype(np.float32)
            frame = cv2.remap(img, mx, my, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            p.stdin.write(frame.tobytes())
    finally:
        p.stdin.close()
        p.wait()
    if p.returncode:
        raise RuntimeError("ffmpeg failed (parallax)")


# ------------------------------------------------------------------ the film
def _shot_clip(still, secs, out, k):
    """Slow documentary camera move on a still (alternating push-in / pull-out / pans)."""
    n = max(2, int(secs * FPS))
    moves = [
        f"z='1.0+0.10*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",                  # push in
        f"z='1.10-0.10*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",                 # pull out
        f"z='1.08':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",                           # pan right
        f"z='1.08':x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'",                       # pan left
    ]
    vf = (f"scale=2880:1620,zoompan={moves[k % 4]}:d={n}:s={W}x{H}:fps={FPS},"
          f"format=yuv420p")
    _run(["-i", still, "-vf", vf, "-frames:v", str(n), *FAST, "-an", out])


def make(st, cfg, privacy="public", seed_item=None, upload=True):
    """Builds the week's documentary. Returns (video_path, post, chapters, thumb_path) or uploads it."""
    from . import music, voice, writer
    from .growth import _posted, score
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    seed = random.randint(1, 10 ** 6)

    # 1. the topic: grow it out of the week's best Short
    if seed_item is None:
        week_ago = dt.datetime.now() - dt.timedelta(days=8)
        items = [h for h in _posted(st) if h.get("status") != "weekly"]
        recent = [h for h in items if h.get("created", "") >= week_ago.isoformat()] or items[-10:]
        seed_item = max(recent, key=lambda h: (score(h), h.get("created", ""))) if recent else {"title": "The deep sea"}
    log(f"Documentary: growing it out of '{seed_item.get('title')}'")
    script = None
    for _ in range(2):
        s = writer.gemini_json(cfg, PROMPT.format(seed_title=seed_item.get("title", ""),
                                                  seed_hook=seed_item.get("hook", "")), temperature=0.9)
        if s and len(s.get("parts") or []) >= 12 and s.get("title"):
            script = s
            break
    if not script:
        raise RuntimeError("Documentary script could not be written (Gemini)")
    parts = [p for p in script["parts"] if (p.get("text") or "").strip()]
    text = " ".join(p["text"].strip() for p in parts)
    log(f"  script: {len(parts)} parts, {len(text.split())} words - {script['title']}")

    # 2. the narrator: calm and unhurried - each paragraph read separately, with real pauses between them
    #    (about 1 s, and 2 s before a new chapter) so the story can breathe, BBC-documentary style
    import wave
    import numpy as np
    voices = [cfg.get("doc_voice", "en-GB-RyanNeural"), "en-US-AndrewMultilingualNeural"]
    rate = cfg.get("doc_voice_speed", "-12%")
    pieces, offsets, t, sr, part_words = [], [], 0.0, 44100, []
    for i, p in enumerate(parts):
        pdir = os.path.join(WORK, f"v{i:02d}")
        os.makedirs(pdir, exist_ok=True)
        pw, pdur, pwords = voice.narrate(p["text"].strip(), cfg, pdir, voices=voices, rate=rate, pitch="-2Hz")
        with wave.open(pw, "rb") as w:
            sr = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        new_chapter = bool(nxt and (nxt.get("chapter") or "").strip()
                           and (nxt.get("chapter") or "").strip().lower() != (p.get("chapter") or "").strip().lower())
        gap = 2.0 if new_chapter else 1.0
        offsets.append(t)
        part_words.append(pwords or [])
        pieces += [audio, np.zeros((int(sr * gap), 2), dtype=np.int16)]
        t += len(audio) / sr + gap
    wav = os.path.join(WORK, "narration.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.concatenate(pieces).tobytes())
    vdur = t
    log(f"  narration: {len(parts)} paragraphs, {vdur / 60:.1f} min with pauses")
    intro = 4.0  # title card over the first shot, before the voice starts
    starts = [0.0] + [o + intro for o in offsets[1:]]
    total = vdur + intro + 7.0  # 7 s at the end for the closing card

    # 3. pictures: one picture per sentence, cut exactly when the narrator starts that sentence
    import re
    shots = []  # (prompt, start time)
    for i, p in enumerate(parts):
        prompts = p.get("shots") if isinstance(p.get("shots"), list) else []
        prompts = [str(x).strip() for x in prompts if str(x).strip()] or [p.get("shot") or p["text"]]
        sents = [x for x in re.split(r"(?<=[.!?])\s+", p["text"].strip()) if x.strip()]
        ws = part_words[i]
        if 1 <= len(prompts) < len(sents) <= 6:  # the writer gave fewer pictures than sentences: add the missing ones
            prompts += [f"{prompts[0].split('.')[0]}, showing: {x}" for x in sents[len(prompts):]]
        if len(prompts) > len(sents) >= 1:  # more pictures than sentences: one per sentence, in order
            prompts = prompts[:len(sents)]
        k = len(prompts)
        if len(sents) == k:  # cut on each sentence's first word
            idx, c = [], 0
            for x in sents:
                idx.append(c)
                c += len(x.split())
            total_w = max(1, c)
        else:  # spread evenly over the paragraph
            total_w = 1
            idx = [j / k for j in range(k)]
        base = starts[i]
        last_t = None
        for j, pr in enumerate(prompts):
            if j == 0 or not ws:
                tt = base
            else:
                w = min(len(ws) - 1, int(round(idx[j] / total_w * len(ws))))
                tt = offsets[i] + intro + float(ws[w]["start"])
            nxt_t = starts[i + 1] if i + 1 < len(starts) else total
            if last_t is not None and (tt - last_t < 3.0 or nxt_t - tt < 3.0):  # never flash a picture by too quickly
                continue
            # the picture dissolves in just BEFORE its sentence is spoken, so it is fully on screen as the words start
            shots.append((pr, max(0.0, tt - 0.7) if shots else tt, i))
            last_t = tt
    starts_s = [s[1] for s in shots]
    durs = [max(3.0, b - a) for a, b in zip(starts_s, starts_s[1:] + [total])]

    real = _real_photos(script.get("wiki") or [], limit=10)
    stock, si = None, 0  # Unsplash photos, fetched only if the AI pictures run out
    # real moving footage: one clip per paragraph (on its 2nd picture when it has several)
    want_clip, seen = set(), set()
    for k, (_, _, pi) in enumerate(shots):
        if pi in seen or not str(parts[pi].get("footage") or "").strip():
            continue
        later = [j for j, s in enumerate(shots) if s[2] == pi]
        want_clip.add(later[1] if len(later) > 1 else later[0])
        seen.add(pi)
    stills, media_ = [], []
    ri = ai = vc = 0
    used_clips, used_photos = set(), set()
    for i, (prompt, _, pi) in enumerate(shots):
        path = os.path.join(WORK, f"s{i:02d}.jpg")
        if i in want_clip and i > 0:
            clip = _pixabay_clip(parts[pi].get("footage"), used_clips)
            if clip:
                media_.append(("vid", clip))
                vc += 1
                continue
        use_real = real and ri < len(real) and i % 6 == 4
        if use_real:
            from PIL import Image
            _landscape(Image.open(real[ri]["path"])).save(path, quality=93)
            ri += 1
        elif _ai_still(prompt, path, seed + i):
            ai += 1
        elif (pp := _pixabay_photo(parts[pi].get("footage") or parts[pi].get("ambience"), used_photos)):
            from PIL import Image
            _landscape(Image.open(pp)).save(path, quality=93)
            ri += 1
        else:
            if stock is None:
                stock = _unsplash_landscape((script.get("keywords") or []) + (script.get("wiki") or []))
                log(f"  AI pictures unavailable - using {len(stock)} real Unsplash photos instead")
            if stock and si < len(stock):
                from PIL import Image
                _landscape(Image.open(stock[si]["path"])).save(path, quality=93)
                real.append(stock[si])
                si += 1
                ri += 1
            elif real and ri < len(real):
                from PIL import Image
                _landscape(Image.open(real[ri]["path"])).save(path, quality=93)
                ri += 1
            elif len(stills) >= 3:
                shutil.copy(stills[(i * 7) % (len(stills) - 1)], path)  # reuse an earlier picture, not the last one
            else:
                _fallback_still(prompt, path, seed + i)
        stills.append(path)
        media_.append(("img", path))
    log(f"  pictures: {len(media_)} shots for {len(parts)} paragraphs ({vc} real video clips, {ai} AI, {ri} real photos)")

    # 4. one clip per shot, joined with soft 1-second dissolves
    clips, px = [], 0
    for i, ((kind, src), d) in enumerate(zip(media_, durs)):
        out = os.path.join(WORK, f"c{i:02d}.mp4")
        try:
            if kind == "vid":
                _video_clip(src, d + 1.0, out)
            elif cfg.get("doc_parallax", True) and _depth_session() is not None:
                _parallax_clip(src, d + 1.0, out, i)
                px += 1
            else:
                _shot_clip(src, d + 1.0, out, i)
        except Exception as e:  # a broken download never stops the film: fall back to a still
            log(f"  (shot {i} fallback: {str(e)[:100]})")
            _shot_clip(stills[i % len(stills)] if stills else _fallback_still("", os.path.join(WORK, f"f{i}.jpg"), i),
                       d + 1.0, out, i)
        clips.append(out)
    if px:
        log(f"  3D photo effect: {px} shots")
    inputs, chain, last, offset = [], [], "[0:v]", 0.0
    for c in clips:
        inputs += ["-i", c]
    for i in range(1, len(clips)):
        offset += durs[i - 1]
        chain.append(f"{last}[{i}:v]xfade=transition=fade:duration=1:offset={offset:.3f}[x{i}]")
        last = f"[x{i}]"
    film = os.path.join(WORK, "film.mp4")
    _run([*inputs, "-filter_complex", ";".join(chain) or "[0:v]null[x0]", "-map", last if chain else "[x0]",
          *FAST, film], timeout=3000)

    # 5. title card + chapter lower-thirds + narrator + music
    _title_card(script["title"], os.path.join(WORK, "title.png"))
    _logo_bug(os.path.join(WORK, "bug.png"))
    _end_card(script["title"], os.path.join(WORK, "end.png"))
    over, chapters, n, last_ch = [("title.png", 0.3, intro + 0.6), ("bug.png", intro + 1.0, total - 6.5),
                                  ("end.png", total - 6.2, total)], [("0:00", "Cold open")], 0, ""
    for i, p in enumerate(parts):
        ch = (p.get("chapter") or "").strip()
        if ch and ch.lower() != last_ch.lower():  # a NEW chapter starts (not every paragraph)
            last_ch = ch
            n += 1
            png = f"ch{n}.png"
            _lower_third(n, p["chapter"].strip(), os.path.join(WORK, png))
            over.append((png, starts[i] + 0.4, starts[i] + 5.0))
            sec = int(starts[i])
            chapters.append((f"{sec // 60}:{sec % 60:02d}", p["chapter"].strip()))
    gfx = 0  # teaching graphics, timed to the paragraph the narrator is reading
    for i, p in enumerate(parts):
        g = p.get("graphic")
        if not isinstance(g, dict) or not g.get("type"):
            continue
        a = starts[i] + 0.8
        b = (starts[i + 1] if i + 1 < len(starts) else total) - 0.4
        if b - a < 2.5:
            continue
        try:
            frames = _graphic_frames(g, a, b)
            for k, (png_img, ga, gb) in enumerate(frames):
                png = f"g{i:02d}_{k}.png"
                png_img.save(os.path.join(WORK, png))
                over.append((png, ga, gb, (k == 0, k == len(frames) - 1)))
            gfx += 1
        except Exception as e:
            log(f"  (graphic skipped: {str(e)[:100]})")
    log(f"  teaching graphics: {gfx}")
    busy = [(o[1], o[2]) for o in over if str(o[0]).startswith("ch")]
    cues = _sub_cues(parts, part_words, offsets, intro, total - 6.5) if cfg.get("doc_subtitles", True) else []
    log(f"  subtitles: {len(cues)} lines")
    mus = music.pick_music(total, seed, WORK)
    events = [(0.3, "impact", 0.22), (total - 6.2, "ding", 0.18)]
    for o in over:
        name = str(o[0])
        if name.startswith("ch"):
            events.append((o[1], "whoosh", 0.16))
        elif name.startswith("g"):
            events.append((o[1] + 0.1, "pop", 0.09))
    fx = None
    try:
        fx = _sound_design(parts, starts, total, events, os.path.join(WORK, "fx.wav"))
    except Exception as e:
        log(f"  (sound design skipped: {str(e)[:120]})")
    args = ["-i", film]
    for o in over:  # each picture overlay only exists for its own few seconds (keeps the render fast)
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{max(0.2, o[2] - o[1]):.2f}", "-i", os.path.join(WORK, o[0])]
    if cues:
        args += ["-f", "concat", "-safe", "0", "-i", _subtitle_track(cues, busy, total)]
    args += ["-i", wav, "-i", mus] + (["-i", fx] if fx else [])
    fc, cur = [], "[0:v]"
    for k, o in enumerate(over, 1):
        _, a, b = o[:3]
        dur = max(0.2, b - a)
        fin, fout = o[3] if len(o) > 3 else (True, True)  # build-up steps switch without fading in between
        fades = (["fade=t=in:st=0:d=0.5:alpha=1"] if fin else []) + \
                ([f"fade=t=out:st={max(0.0, dur - 0.5):.2f}:d=0.5:alpha=1"] if fout else [])
        fc.append(f"[{k}:v]" + ",".join(["format=rgba", *fades, f"setpts=PTS-STARTPTS+{a:.2f}/TB"]) + f"[o{k}]")
        fc.append(f"{cur}[o{k}]overlay=0:0:eof_action=pass[v{k}]")
        cur = f"[v{k}]"
    if cues:
        si_ = len(over) + 1
        fc.append(f"[{si_}:v]fps={FPS},format=rgba[subs]")
        fc.append(f"{cur}[subs]overlay=0:{H - SUB_H}:eof_action=pass[vs]")
        cur = "[vs]"
    vi, mi = len(over) + 1 + bool(cues), len(over) + 2 + bool(cues)
    # the narrator always comes first: louder, clearer (a little presence boost), and the music + nature
    # sounds automatically dip down whenever he speaks (broadcast "ducking")
    fc.append(f"[{vi}:a]adelay={int(intro * 1000)}|{int(intro * 1000)},highpass=f=70,"
              "equalizer=f=3200:t=q:w=1.2:g=3,loudnorm=I=-14:TP=-1.5:LRA=9,asplit=3[vo][vkey][vkey2]")
    fc.append(f"[{mi}:a]volume=0.09,afade=t=out:st={total - 3:.2f}:d=3[mu]")
    # music dips a lot under the voice; the nature sounds only dip a little, so you can still hear them
    fc.append("[mu][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=500[mud]")
    if fx:
        fc.append(f"[{mi + 1}:a]volume=1.0[fx]")
        fc.append("[fx][vkey2]sidechaincompress=threshold=0.03:ratio=2:attack=30:release=600[fxd]")
        fc.append("[vo][mud][fxd]amix=inputs=3:duration=longest:normalize=0,alimiter=limit=0.95[a]")
    else:
        fc.append("[vkey2]anullsink")
        fc.append("[vo][mud]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]")
    out = os.path.join(DATA, "documentary.mp4")
    _run([*args, "-filter_complex", ";".join(fc), "-map", cur, "-map", "[a]", "-t", f"{total:.2f}",
          *ENC, "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", out], timeout=3000)
    thumb = os.path.join(WORK, "thumb.jpg")
    # the thumbnail uses a REAL photo of the subject when we have one (they win clicks), else a strong still
    thumb_src = next((r["path"] for r in real if os.path.exists(r.get("path", ""))), None) or stills[min(2, len(stills) - 1)]
    try:
        _thumbnail(thumb_src, script.get("thumb") or script["title"], thumb)
    except Exception as e:
        log(f"  (thumbnail fallback: {str(e)[:100]})")
        _thumbnail(stills[min(2, len(stills) - 1)], script["title"], thumb)
    credits = sorted({r["credit"] for r in real})
    desc = (f"{script.get('description', '')}\n\nChapters:\n" + "\n".join(f"{a} {b}" for a, b in chapters) +
            "\n\nA Hidden Facts HQ documentary. New Shorts every day and a new documentary every Sunday - "
            "subscribe so you don't miss one." + ("\n\nPhotos: " + "; ".join(credits) if credits else "") +
            "\n\n#documentary #nature #science #facts")
    post = {"youtube": {"title": script["title"][:95], "description": desc[:4900],
                        "tags": (script.get("tags") or [])[:12] + ["documentary", "hidden facts hq"]},
            "question": None}
    log(f"  documentary ready: {total / 60:.1f} min, {len(chapters) - 1} chapters")
    return out, post, chapters, thumb, total


def preview_copy(path):
    """Small 480p copy that fits in Telegram (bots can send up to 50 MB)."""
    small = os.path.join(WORK, "preview.mp4")
    _run(["-i", path, "-vf", "scale=854:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
          "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", small])
    return small


def make_and_upload(st, chat, items, cfg, yt, privacy="public"):
    """Sunday job (and Run workflow > doc_preview): build the documentary from the week's best topic,
    upload it to YouTube as a normal (landscape) video with chapters + thumbnail, report to Telegram."""
    from . import telegram as tg
    seed_item = items[0] if items else None
    path, post, chapters, thumb, total = make(st, cfg, privacy, seed_item=seed_item)
    url = yt.upload(path, post, privacy, short=False)
    try:
        yt.set_thumbnail(yt.video_id(url), thumb)
    except Exception as e:
        log(f"  (documentary thumbnail skipped: {str(e)[:120]})")
    mins = int(total // 60)
    tag = "🔒 UNLISTED preview (only people with this link can see it)" if privacy == "unlisted" else "📺 New documentary posted"
    if chat:
        tg.send_message(chat, f"{tag}: {post['youtube']['title']} ({mins} min, {len(chapters) - 1} chapters)\n{url}")
    st.setdefault("history", []).append({"title": post["youtube"]["title"], "status": "weekly",
                                         "links": {"YouTube": url}, "theme": "documentary",
                                         "seed": (seed_item or {}).get("title"),
                                         "created": dt.datetime.now().isoformat(timespec="seconds")})
    os.remove(path)
    shutil.rmtree(WORK, ignore_errors=True)
    return url
