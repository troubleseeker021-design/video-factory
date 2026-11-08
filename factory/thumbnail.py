"""Agent - The Thumbnail Artist.
Designs a scroll-stopping cover for every Short: the best real photo/clip frame from the
video, a big glowing 3D object, 2-4 huge punchy words with a thick outline, a red arrow
and the series badge. The cover is also used as the video's first frame, so Instagram,
Facebook and YouTube all pick it up."""
import math
import os
import random
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import emoji as EMO
from .common import FONTS, ffmpeg_exe, log

W, H = 1080, 1920
YELLOW, WHITE, RED = (255, 214, 10), (255, 255, 255), (235, 30, 45)
STOP = {"the", "a", "an", "of", "that", "this", "is", "are", "was", "in", "on", "to", "and", "its", "it", "with"}


def _font(name, size, var=None):
    f = ImageFont.truetype(os.path.join(FONTS, name), size)
    if var:
        try:
            f.set_variation_by_name(var)
        except Exception:
            pass
    return f


def _glow(layer, radius, color, k=1.0):
    a = layer.getchannel("A").filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * k)))
    g = Image.new("RGBA", layer.size, color + (0,))
    g.putalpha(a)
    return g


def _clip_frame(path, at):
    out = os.path.join(tempfile.gettempdir(), f"thumb_{os.getpid()}.jpg")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1", out],
                   check=True, timeout=60)
    img = Image.open(out).convert("RGB")
    os.remove(out)
    return img


def _background(script, scenes, seed):
    """Best real picture from the video (hook scene first), else the animated theme background."""
    order = sorted(scenes, key=lambda sc: (sc.get("kind") != "hook", sc.get("media") is None))
    for sc in order:
        m = sc.get("media")
        if not m:
            continue
        try:
            if m["type"] == "clip":
                img = _clip_frame(m["path"], float(m.get("start", 0)) + 1.0)
            else:
                img = ImageOps.exif_transpose(Image.open(m["path"])).convert("RGB")
            if img.width < 300:
                continue
            return ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.4)), True
        except Exception as e:
            log(f"  thumbnail: could not use {m.get('path')}: {e}")
    from .visuals import ProceduralBG
    return ProceduralBG(script.get("theme", "science"), W, H, seed).frame(3.0), False


def thumb_words(script):
    """2-4 punchy words. Gemini writes them ("thumb"); otherwise taken from the title."""
    t = (script.get("thumb") or "").strip().upper()
    if t:
        return t
    words = script["title"].replace(":", " ").split()
    if words and words[0].lower() in ("the", "a", "an") and len(words) > 3:
        words = words[1:]
    if len(words) > 7:
        return " ".join(words[:7]).upper() + "..."
    text = " ".join(words).upper()
    return text if text.endswith(("?", "!")) else text + "!"


def _wrap(text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        test = (cur + " " + w).strip()
        if font.getlength(test) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _text_block(text):
    """Huge outlined words, alternating white / yellow lines, auto-sized to fit."""
    for size in (230, 210, 190, 170, 150, 135, 120):
        f = _font("Anton-Regular.ttf", size)
        lines = _wrap(text, f, 960)
        if len(lines) <= 3:
            break
    lh = int(size * 1.08)
    blk = Image.new("RGBA", (W, lh * len(lines) + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(blk)
    for i, ln in enumerate(lines):
        col = YELLOW if i % 2 else WHITE
        if len(lines) == 1:
            col = YELLOW
        d.text((W / 2, 40 + i * lh), ln, font=f, fill=col + (255,), anchor="ma",
               stroke_width=max(8, size // 16), stroke_fill=(0, 0, 0, 255))
    return blk


def _arrow(d, p0, p1, p2, width=26):
    pts = []
    for k in range(40):
        t = k / 39
        pts.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]))
    ang = math.atan2(pts[-1][1] - pts[-4][1], pts[-1][0] - pts[-4][0])
    tip = (pts[-1][0] + math.cos(ang) * 40, pts[-1][1] + math.sin(ang) * 40)
    head = [tip, (pts[-1][0] + math.cos(ang + 2.3) * 60, pts[-1][1] + math.sin(ang + 2.3) * 60),
            (pts[-1][0] + math.cos(ang - 2.3) * 60, pts[-1][1] + math.sin(ang - 2.3) * 60)]
    for wdt, col in ((width + 14, (255, 255, 255, 255)), (width, RED + (255,))):
        d.line(pts, fill=col, width=wdt, joint="curve")
        d.polygon(head, fill=col)
        if col[0] == 255 and col[1] == 255:  # white outline for the head
            d.polygon([(x + math.cos(a) * 9, y + math.sin(a) * 9) for (x, y), a in
                       zip(head, (ang, ang + 2.3, ang - 2.3))], fill=col)


def make(script, scenes, cfg, seed, out_path=None):
    rng = random.Random(seed)
    bg, real = _background(script, scenes, seed)
    bg = ImageEnhance.Color(bg).enhance(1.35)
    bg = ImageEnhance.Contrast(bg).enhance(1.15)
    img = bg.convert("RGBA")

    # darken top & bottom so the words pop; soft vignette
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(H):
        a = 0
        if y < 420:
            a = 150 * (1 - y / 420)
        if y > 880:
            a = min(215, 215 * (y - 880) / 480)
        sd.line([(0, y), (W, y)], fill=int(a))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dark.putalpha(shade)
    img.alpha_composite(dark)
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-250, -150, W + 250, H + 150], fill=255)
    vig = ImageOps.invert(vig.filter(ImageFilter.GaussianBlur(160))).point(lambda v: int(v * .75))
    v = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    v.putalpha(vig)
    img.alpha_composite(v)

    # hero 3D object with a light burst behind it
    hook_sc = scenes[0] if scenes else {}
    e = hook_sc.get("emoji") or (EMO.THEME_POOL.get(script.get("theme", ""), ["🤯"]) or ["🤯"])[0]
    hero = EMO.image(e, 600)
    cx, cy = W // 2, 640
    burst = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(burst)
    for k in range(16):
        a = k * math.pi / 8 + rng.uniform(-.05, .05)
        bd.polygon([(cx, cy), (cx + 1400 * math.cos(a - .07), cy + 1400 * math.sin(a - .07)),
                    (cx + 1400 * math.cos(a + .07), cy + 1400 * math.sin(a + .07))], fill=(255, 230, 160, 40))
    bd.ellipse([cx - 300, cy - 300, cx + 300, cy + 300], fill=(255, 240, 200, 55))
    img.alpha_composite(burst.filter(ImageFilter.GaussianBlur(18)))
    if hero is not None:
        if hero.width < 560:
            hero = hero.resize((560, int(560 * hero.height / hero.width)), Image.LANCZOS)
        hs = hero.rotate(rng.choice([-9, -6, 6, 9]), Image.BICUBIC, expand=True)
        pad = Image.new("RGBA", (hs.width + 200, hs.height + 200), (0, 0, 0, 0))
        pad.alpha_composite(hs, (100, 100))
        sh = Image.new("RGBA", pad.size, (0, 0, 0, 0))
        sh.putalpha(pad.getchannel("A").filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * .6)))
        ox, oy = cx - pad.width // 2, cy - pad.height // 2
        img.alpha_composite(sh, (ox + 14, oy + 30))
        img.alpha_composite(_glow(pad, 30, (255, 240, 200), 1.2), (ox, oy))
        img.alpha_composite(pad, (ox, oy))

    # huge words
    blk = _text_block(thumb_words(script)).rotate(-3, Image.BICUBIC, expand=True)
    by = 960
    sh = Image.new("RGBA", blk.size, (0, 0, 0, 0))
    sh.putalpha(blk.getchannel("A").filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * .7)))
    img.alpha_composite(sh, ((W - blk.width) // 2 + 10, by + 16))
    img.alpha_composite(blk, ((W - blk.width) // 2, by))

    # red arrow from the words to the object
    d = ImageDraw.Draw(img)
    side = rng.choice([-1, 1])
    x0 = cx + side * 330
    _arrow(d, (x0, by + 10), (cx + side * 420, cy + 120), (cx + side * 230, cy + 150))

    # series badge on top
    series = script.get("series_tag", "")
    if series:
        f = _font("Montserrat-Variable.ttf", 46, "Black")
        tw = f.getlength(series.upper()) + 70
        d.rounded_rectangle([(W - tw) / 2, 150, (W + tw) / 2, 230], radius=40, fill=RED + (255,))
        d.text((W / 2, 191), series.upper(), font=f, fill=WHITE, anchor="mm")

    out = img.convert("RGB")
    if out_path:
        out.save(out_path, quality=90)
    return out
