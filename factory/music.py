"""Agent 3 - The Composer.
Creates a brand-new, 100% original ambient music bed for every video
(no copyright claims, ever). If you drop your own .mp3/.wav files into
assets/music, it picks one of those instead."""
import glob
import os
import random
import wave

import numpy as np

from .common import ASSETS

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
# minor-key progressions (scale degrees as semitone offsets of chord roots)
PROGRESSIONS = [
    [0, 8, 3, 10],   # i  VI III VII  - epic
    [0, 5, 8, 7],    # i  iv VI  V    - mysterious
    [0, 10, 8, 10],  # i VII VI VII   - driving
    [0, 3, 10, 5],   # i III VII iv   - wonder
]


def _freq(semitone_from_a4):
    return 440.0 * 2 ** (semitone_from_a4 / 12.0)


def _chord(root_semi, rel):
    """Minor or major triad depending on degree (keeps it in natural minor)."""
    minor_degrees = {0, 5, 7}
    third = 3 if rel in minor_degrees else 4
    return [root_semi, root_semi + third, root_semi + 7, root_semi + 12]


def _pad(freqs, n, rng):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in freqs:
        for det in (-0.12, 0.12):
            ff = f * 2 ** (det / 12)
            ph = rng.uniform(0, 6.28)
            for k in range(1, 6):
                out += np.sin(2 * np.pi * ff * k * t + ph * k) / (k ** 1.6)
    return out / (len(freqs) * 2)


def generate(duration, seed, path):
    rng = np.random.default_rng(seed)
    root = NOTE[rng.choice(list(NOTE))] - 21  # around A2..G#3
    prog = PROGRESSIONS[rng.integers(len(PROGRESSIONS))]
    bpm = int(rng.integers(84, 104))
    beat = 60.0 / bpm
    chord_len = beat * 8
    total = int((duration + 2) * SR)
    mix = np.zeros(total)
    attack, release = int(1.0 * SR), int(1.6 * SR)

    i, start = 0, 0
    while start < total:
        rel = prog[i % len(prog)]
        notes = _chord(root + rel, rel)
        seg = int(chord_len * SR)
        n = min(seg + release, total - start)
        env = np.ones(n)
        a = min(attack, n)
        env[:a] = np.linspace(0, 1, a)
        if n > seg:
            env[seg:] = np.linspace(1, 0, n - seg)
        pad = _pad([_freq(x) for x in notes], n, rng) * env * 0.55
        bass = np.sin(2 * np.pi * _freq(root + rel - 12) * np.arange(n) / SR) * env * 0.35
        mix[start:start + n] += pad + bass

        # soft pluck arpeggio on 8th notes
        step = int(beat / 2 * SR)
        pattern = [0, 2, 1, 3, 2, 1, 3, 2]
        for j in range(16):
            p0 = start + j * step
            if p0 >= total or j * step >= seg:
                break
            m = min(int(0.9 * SR), total - p0)
            tt = np.arange(m) / SR
            f = _freq(notes[pattern[j % 8]] + 12)
            pl = np.sin(2 * np.pi * f * tt) * np.exp(-tt * 5) * 0.16
            mix[p0:p0 + m] += pl

        # gentle low pulse on each beat
        for j in range(8):
            p0 = start + int(j * beat * SR)
            if p0 >= total:
                break
            m = min(int(0.35 * SR), total - p0)
            tt = np.arange(m) / SR
            mix[p0:p0 + m] += np.sin(2 * np.pi * (55 + 40 * np.exp(-tt * 20)) * tt) * np.exp(-tt * 9) * 0.22
        start += seg
        i += 1

    # cheap stereo reverb
    left, right = mix.copy(), mix.copy()
    for d, g in ((0.031, 0.35), (0.053, 0.3), (0.089, 0.25), (0.137, 0.2), (0.211, 0.15)):
        k = int(d * SR)
        left[k:] += mix[:-k] * g
        k2 = int(d * 1.17 * SR)
        right[k2:] += mix[:-k2] * g
    st = np.stack([left, right], axis=1)
    fade = int(1.5 * SR)
    st[:fade] *= np.linspace(0, 1, fade)[:, None]
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    st = st / (np.abs(st).max() + 1e-9) * 0.85
    pcm = (st * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def pick_music(duration, seed, workdir):
    own = glob.glob(os.path.join(ASSETS, "music", "*.mp3")) + glob.glob(os.path.join(ASSETS, "music", "*.wav"))
    if own:
        return random.Random(seed).choice(own)
    return generate(duration, seed, os.path.join(workdir, "music.wav"))
