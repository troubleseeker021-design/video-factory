"""Agent 4 - The Visual Designer.
Scene backgrounds: animated themed backgrounds, real photos (Ken Burns +
blurred backdrop, framed card) and stock/own video clips."""
import math
import random
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .common import ffmpeg_exe

THEMES = {
    "space":   {"base": (6, 6, 22),   "blobs": [(90, 40, 180), (30, 60, 170), (170, 40, 140), (20, 120, 180)], "fx": "stars"},
    "ocean":   {"base": (2, 14, 28),  "blobs": [(0, 110, 150), (0, 70, 130), (20, 150, 160), (10, 50, 110)], "fx": "bubbles"},
    "nature":  {"base": (4, 18, 14),  "blobs": [(20, 140, 90), (60, 160, 60), (10, 110, 120), (120, 160, 40)], "fx": "dust"},
    "animals": {"base": (16, 12, 6),  "blobs": [(180, 110, 30), (140, 70, 20), (60, 130, 70), (200, 150, 60)], "fx": "dust"},
    "history": {"base": (18, 10, 4),  "blobs": [(170, 100, 40), (120, 60, 25), (200, 140, 70), (90, 50, 30)], "fx": "dust"},
    "body":    {"base": (20, 4, 12),  "blobs": [(200, 30, 80), (130, 20, 120), (230, 70, 90), (90, 30, 150)], "fx": "dust"},
    "science": {"base": (4, 8, 22),   "blobs": [(30, 90, 220), (120, 40, 200), (0, 170, 200), (60, 60, 180)], "fx": "dust"},
}


class ProceduralBG:
    """Animated gradient blobs + particles + light rays, rendered at 1/4 res and upscaled."""

    def __init__(self, theme, W, H, seed):
        th = THEMES.get(theme, THEMES["science"])
        self.th, self.W, self.H = th, W, H
        rng = random.Random(seed)
        self.lw, self.lh = W // 4, H // 4
        yy, xx = np.mgrid[0:self.lh, 0:self.lw].astype(np.float32)
        self.xx, self.yy = xx / self.lw, yy / self.lh
        vig = 1.0 - 0.55 * (((self.xx - 0.5) * 1.3) ** 2 + ((self.yy - 0.5) * 1.0) ** 2) * 2.2
        self.vig = np.clip(vig, 0.25, 1.0)[..., None]
        self.base = np.array(th["base"], np.float32)
        self.blobs = [{
            "c": np.array(c, np.float32) * rng.uniform(0.55, 0.9),
            "fx": rng.uniform(0.02, 0.07), "fy": rng.uniform(0.02, 0.06),
            "px": rng.uniform(0, 6.28), "py": rng.uniform(0, 6.28), "r": rng.uniform(0.18, 0.34),
        } for c in th["blobs"] * 2]
        n = 80 if th["fx"] == "stars" else 40
        self.parts = [{"x": rng.uniform(0, W), "y": rng.uniform(0, H),
                       "s": rng.uniform(1.5, 4.5) if th["fx"] == "stars" else rng.uniform(3, 11),
                       "v": rng.uniform(8, 40), "tw": rng.uniform(0, 6.28)} for _ in range(n)]
        # angle of the rotating light rays
        self.ray_ang = np.arctan2(self.yy - 0.32, (self.xx - 0.5) * 0.56)
        self.ray_r = np.sqrt(((self.xx - 0.5) * 0.56) ** 2 + (self.yy - 0.32) ** 2)

    def frame(self, t, hue_shift=0.0):
        img = np.broadcast_to(self.base, (self.lh, self.lw, 3)).copy()
        for b in self.blobs:
            cx = 0.5 + 0.42 * math.sin(t * b["fx"] * 6.28 + b["px"] + hue_shift)
            cy = 0.5 + 0.45 * math.sin(t * b["fy"] * 6.28 + b["py"])
            d2 = (self.xx - cx) ** 2 + ((self.yy - cy) * 1.78) ** 2
            img += b["c"][None, None, :] * np.exp(-d2 / ((b["r"] * 1.5) ** 2))[..., None] * 0.6
        rays = (np.cos(self.ray_ang * 9 + t * 0.35) * 0.5 + 0.5) ** 6 * np.exp(-self.ray_r * 2.2)
        img += rays[..., None] * 38
        img = np.clip(img * self.vig, 0, 255).astype(np.uint8)
        frame = Image.fromarray(img).resize((self.W, self.H), Image.BILINEAR)
        d = ImageDraw.Draw(frame, "RGBA")
        fx, H = self.th["fx"], self.H
        for p in self.parts:
            if fx == "stars":
                y, x = (p["y"] + t * p["v"] * 0.3) % H, p["x"]
                a = int(140 + 115 * math.sin(t * 3 + p["tw"]))
            elif fx == "bubbles":
                y, x, a = (p["y"] - t * p["v"] * 2.2) % H, p["x"] + 12 * math.sin(t * 1.5 + p["tw"]), 70
            else:
                y, x = (p["y"] - t * p["v"]) % H, p["x"] + 20 * math.sin(t * 0.7 + p["tw"])
                a = int(45 + 35 * math.sin(t * 2 + p["tw"]))
            s = p["s"]
            if fx == "bubbles":
                d.ellipse([x - s, y - s, x + s, y + s], outline=(200, 240, 255, a), width=2)
            else:
                d.ellipse([x - s, y - s, x + s, y + s], fill=(255, 255, 255, max(0, a)))
        return frame


def _rounded_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=r, fill=255)
    return m


class PhotoBG:
    """Full-screen Ken Burns if the photo is tall, otherwise a framed photo card
    over a blurred, darkened copy of itself (the classic Shorts look)."""

    def __init__(self, path, W, H, seed, credit=""):
        self.W, self.H, self.credit = W, H, credit
        rng = random.Random(seed)
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        self.zoom_in = rng.random() < 0.7
        self.tall = img.height / img.width >= 1.45
        if self.tall:
            self.full = ImageOps.fit(img, (int(W * 1.15), int(H * 1.15)), Image.LANCZOS)
        else:
            back = ImageOps.fit(img, (W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(6))
            back = back.resize((W, H), Image.BILINEAR)
            self.back = Image.blend(back, Image.new("RGB", (W, H), (0, 0, 0)), 0.45)
            cw = 940
            ch = min(int(cw * img.height / img.width), 1000)
            card = ImageOps.fit(img, (int(cw * 1.12), int(ch * 1.12)), Image.LANCZOS)
            self.card_src, self.cw, self.ch = card, cw, ch
            self.mask = _rounded_mask((cw, ch), 34)
            sh = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle([40, 50, cw + 40, ch + 50], radius=40, fill=(0, 0, 0, 170))
            self.shadow = sh.filter(ImageFilter.GaussianBlur(22))

    def frame(self, t, dur):
        k = min(1.0, t / max(dur, 0.1))
        z = 1.0 + 0.12 * (k if self.zoom_in else 1 - k)
        if self.tall:
            fw, fh = self.full.size
            cw, ch = int(fw / z * 0.87), int(fh / z * 0.87)
            x0, y0 = (fw - cw) // 2, int((fh - ch) * (0.35 + 0.3 * k))
            return self.full.crop((x0, y0, x0 + cw, y0 + ch)).resize((self.W, self.H), Image.BILINEAR)
        f = self.back.copy().convert("RGBA")
        sw, sh = self.card_src.size
        cw, ch = int(sw / z), int(sh / z)
        x0, y0 = (sw - cw) // 2, (sh - ch) // 2
        card = self.card_src.crop((x0, y0, x0 + cw, y0 + ch)).resize((self.cw, self.ch), Image.BILINEAR)
        top = 640 - self.ch // 2
        top = max(260, top)
        x = (self.W - self.cw) // 2
        f.alpha_composite(self.shadow, (x - 40, top - 40))
        f.paste(card, (x, top), self.mask)
        ImageDraw.Draw(f).rounded_rectangle([x, top, x + self.cw - 1, top + self.ch - 1], radius=34,
                                            outline=(255, 255, 255, 230), width=5)
        self.card_box = (x, top, x + self.cw, top + self.ch)
        return f.convert("RGB")


class ClipBG:
    """Reads a video clip (looped) as 1080x1920 frames, sequentially.
    fit="full": fills the screen. fit="card": landscape clip shown as a framed card over a
    blurred copy of itself (keeps wide footage sharp instead of cropping and enlarging it)."""

    def __init__(self, path, W, H, dur, fps, fit="full", start=0):
        self.W, self.H = W, H
        self.size = W * H * 3
        if fit == "card":
            cw = 1000
            vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"boxblur=24:2,eq=brightness=-0.18[bg];[b]scale={cw}:-2,eq=saturation=1.1[fg];"
                  f"[bg][fg]overlay=(W-w)/2:640-h/2,fps={fps}")
            ch = int(cw * 9 / 16)
            self.card_box = (int((W - cw) / 2), 640 - ch // 2, int((W + cw) / 2), 640 + ch // 2)
        else:
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},eq=brightness=-0.05:saturation=1.12"
        ss = ["-ss", str(start)] if start else []
        self.proc = subprocess.Popen([ffmpeg_exe(), "-loglevel", "error", "-stream_loop", "-1", *ss, "-i", path,
                                      "-t", f"{dur + 0.5:.2f}", "-filter_complex" if fit == "card" else "-vf", vf,
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.last = Image.new("RGB", (W, H), (0, 0, 0))

    def frame(self, t, dur):
        buf = self.proc.stdout.read(self.size)
        if len(buf) == self.size:
            self.last = Image.frombuffer("RGB", (self.W, self.H), buf, "raw", "RGB", 0, 1)
        return self.last

    def close(self):
        try:
            self.proc.stdout.close()
            self.proc.kill()
        except Exception:
            pass
