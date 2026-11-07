"""Agent 7 - The Sound Designer.
Creates original sound effects (whoosh, pop, ding, impact, click, bell, ticks)
and places them on a timeline that matches the edit. 100% generated, no copyright."""
import wave

import numpy as np

SR = 44100
_rng = np.random.default_rng(7)


def _env(n, attack, decay_rate):
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) * decay_rate)


def _band(noise, lo, hi):
    spec = np.fft.rfft(noise)
    f = np.fft.rfftfreq(len(noise), 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(noise))


def whoosh(length=0.42, up=True):
    n = int(length * SR)
    noise = _rng.standard_normal(n)
    out = np.zeros(n)
    parts = 12
    for i in range(parts):
        a, b = i * n // parts, (i + 1) * n // parts
        k = i / (parts - 1)
        c = 400 + (3800 if up else 2200) * (k if up else 1 - k)
        seg = _band(noise[a:b], c * 0.5, c * 1.6)
        out[a:b] = seg
    t = np.linspace(0, 1, n)
    shape = np.sin(np.pi * t ** 0.7) ** 2
    out = out * shape
    return out / (np.abs(out).max() + 1e-9) * 0.55


def pop():
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    f = 900 * np.exp(-t * 30) + 220
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = np.sin(ph) * np.exp(-t * 38)
    s[:200] += _rng.standard_normal(200) * np.linspace(0.4, 0, 200)
    return s * 0.6


def ding():
    n = int(1.1 * SR)
    t = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * 1318.5 * m * t) * a * np.exp(-t * d)
            for m, a, d in ((1, 1, 4), (2.01, 0.45, 6), (3.02, 0.25, 9), (4.2, 0.12, 12)))
    return s * _env(n, 0.002, 0) * 0.35


def impact():
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    f = 30 + 90 * np.exp(-t * 12)
    boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)
    hit = _band(_rng.standard_normal(n), 80, 2500) * np.exp(-t * 18) * 0.5
    s = boom + hit
    return s / np.abs(s).max() * 0.8


def click():
    n = int(0.03 * SR)
    s = _band(_rng.standard_normal(n), 1500, 9000) * np.exp(-np.arange(n) / SR * 220)
    return s / np.abs(s).max() * 0.45


def bell():
    a = ding()
    n = int(0.16 * SR)
    t = np.arange(len(a)) / SR
    b = sum(np.sin(2 * np.pi * 1760 * m * t) * amp * np.exp(-t * d)
            for m, amp, d in ((1, 1, 4), (2.0, 0.4, 7), (3.0, 0.2, 10))) * 0.3
    out = np.zeros(len(a) + n)
    out[:len(a)] += a
    out[n:] += b
    return out


def tick():
    n = int(0.025 * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 300) * 0.22


def riser(length=1.0):
    n = int(length * SR)
    t = np.arange(n) / SR
    f = 200 + 900 * (t / length) ** 2
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR)
    nz = _band(_rng.standard_normal(n), 2000, 8000)
    nz /= np.abs(nz).max()
    s = (tone * 0.4 + nz * 0.4) * (t / length) ** 2
    return s * 0.5


BANK = {"whoosh": whoosh, "whoosh_down": lambda: whoosh(0.5, up=False), "pop": pop, "ding": ding,
        "impact": impact, "click": click, "bell": bell, "tick": tick, "riser": riser}


def build_track(events, duration, path):
    """events: list of (time_seconds, name, gain)."""
    n = int((duration + 1.5) * SR)
    track = np.zeros(n)
    cache = {}
    for t, name, gain in events:
        if name not in cache:
            cache[name] = BANK[name]()
        s = cache[name] * gain
        i = int(max(0, t) * SR)
        if i >= n:
            continue
        m = min(len(s), n - i)
        track[i:i + m] += s[:m]
    peak = np.abs(track).max()
    if peak > 0.95:
        track *= 0.95 / peak
    st = np.repeat(track[:, None], 2, axis=1)
    pcm = (st * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path
