"""The 3D Department.
Turns any flat photo into a moving 3D shot ("2.5D parallax", the 3D Ken Burns look
used by big documentary Shorts channels). A small free AI model (MiDaS, MIT license)
estimates how far away every pixel is, then the virtual camera dollies and orbits so
near objects move more than the background."""
import math
import os
import urllib.request

import numpy as np
from PIL import Image

from .common import ASSETS, log

MODEL = os.path.join(ASSETS, "models", "midas_small.onnx")
MODEL_URL = "https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx"
_net = None
_failed = False


def _load():
    global _net, _failed
    if _net is not None or _failed:
        return _net
    try:
        import cv2
        if not os.path.exists(MODEL):
            os.makedirs(os.path.dirname(MODEL), exist_ok=True)
            log("  downloading the free 3D depth model (one time, 65 MB)...")
            urllib.request.urlretrieve(MODEL_URL, MODEL + ".part")
            os.replace(MODEL + ".part", MODEL)
        _net = cv2.dnn.readNet(MODEL)
    except Exception as e:
        log(f"  3D depth model unavailable ({e}); photos will use 2D motion")
        _failed = True
    return _net


def depth_map(rgb):
    """rgb: HxWx3 uint8. Returns float32 HxW in 0..1 (1 = closest)."""
    import cv2
    net = _load()
    if net is None:
        return None
    bgr = rgb[..., ::-1]
    blob = cv2.dnn.blobFromImage(bgr, 1 / 255.0, (256, 256), (123.675, 116.28, 103.53), swapRB=True, crop=False)
    net.setInput(blob)
    d = net.forward()[0]
    d = cv2.resize(d, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    d = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
    d = cv2.GaussianBlur(d, (0, 0), sigmaX=max(3, rgb.shape[1] / 160))  # soften edges -> fewer tears
    return d.astype(np.float32)


class Parallax3D:
    """Full-screen 3D camera move over a photo."""

    MOVES = ["dolly_in", "orbit_left", "orbit_right", "crane_up", "dolly_out"]

    def __init__(self, img, W, H, rng):
        import cv2
        self.cv2 = cv2
        self.W, self.H = W, H
        margin = 1.18
        tw, th = int(W * margin), int(H * margin)
        s = max(tw / img.width, th / img.height)
        big = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
        x0, y0 = (big.width - tw) // 2, (big.height - th) // 2
        self.src = np.asarray(big.crop((x0, y0, x0 + tw, y0 + th)).convert("RGB"))
        d = depth_map(self.src)
        self.ok = d is not None
        self.depth = d if d is not None else np.full(self.src.shape[:2], 0.5, np.float32)
        self.move = rng.choice(self.MOVES)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.ox = (tw - W) / 2
        self.oy = (th - H) / 2
        self.xx, self.yy = xx, yy
        self.cx, self.cy = W / 2, H / 2
        # depth sampled on the output grid (approximation good for small moves)
        self.dgrid = cv2.resize(self.depth[int(self.oy):int(self.oy) + H, int(self.ox):int(self.ox) + W], (W, H))

    def frame(self, t, dur):
        k = min(1.0, t / max(dur, 0.1))
        e = 0.5 - 0.5 * math.cos(math.pi * k)  # smooth in/out
        m = self.move
        shift_x = shift_y = 0.0
        zoom_near = zoom_far = 0.0
        if m == "dolly_in":
            zoom_near, zoom_far = 0.16 * e, 0.04 * e
        elif m == "dolly_out":
            zoom_near, zoom_far = 0.16 * (1 - e), 0.04 * (1 - e)
        elif m == "orbit_left":
            shift_x, zoom_near = -1, 0.05
        elif m == "orbit_right":
            shift_x, zoom_near = 1, 0.05
        elif m == "crane_up":
            shift_y, zoom_near = -1, 0.05
        amp = 70.0
        d = self.dgrid
        par = (e - 0.5) * 2  # -1..1
        z = 1 + zoom_far + (zoom_near - zoom_far) * d
        mx = self.cx + (self.xx - self.cx) / z - shift_x * amp * par * (d - 0.35) + self.ox
        my = self.cy + (self.yy - self.cy) / z - shift_y * amp * par * (d - 0.35) + self.oy
        out = self.cv2.remap(self.src, mx.astype(np.float32), my.astype(np.float32),
                             interpolation=self.cv2.INTER_LINEAR, borderMode=self.cv2.BORDER_REFLECT)
        return Image.fromarray(out)
