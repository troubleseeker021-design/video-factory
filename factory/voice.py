"""Agent 2 - The Narrator.
Turns the script into a natural AI voice-over (free Microsoft Edge neural voices)
and returns exact timings for every word so captions sync perfectly."""
import asyncio
import os
import subprocess
import wave

from .common import ffmpeg_exe, log, retry


def _split_sentence(text, start, end):
    """Spread a sentence's words across its time span, weighted by length."""
    words = text.split()
    if not words:
        return []
    weights = [len(w) + 2 for w in words]
    total = sum(weights)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append({"word": w, "start": t, "end": t + d})
        t += d
    return out


async def _tts(text, voice, rate, pitch, mp3_path):
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # older edge-tts
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    words, sentences = [], []
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                s = chunk["offset"] / 1e7
                e = s + chunk["duration"] / 1e7
                item = {"word": chunk["text"], "start": s, "end": e}
                (words if chunk["type"] == "WordBoundary" else sentences).append(item)
    if not words:
        for s in sentences:
            words += _split_sentence(s["word"], s["start"], s["end"])
    return words


def _wav_duration(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def narrate(text, cfg, workdir):
    mp3 = os.path.join(workdir, "voice.mp3")
    wav = os.path.join(workdir, "voice.wav")
    # Curious, story-telling delivery: warm low male voice, slightly slower and deeper.
    voices = [cfg.get("voice") or "en-US-AndrewMultilingualNeural", "en-US-AndrewNeural", "en-US-ChristopherNeural"]
    words, last_err = None, None
    for v in dict.fromkeys(voices):
        try:
            words = retry(lambda: asyncio.run(_tts(text, v, cfg.get("voice_speed", "+4%"), cfg.get("voice_pitch", "-2Hz"), mp3)),
                          tries=3, wait=10, what=f"voice '{v}'")
            if os.path.getsize(mp3) > 1000:
                break
        except Exception as e:
            last_err = e
            log(f"  voice '{v}' failed: {e}")
    if not words:
        raise RuntimeError(f"All voices failed: {last_err}")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", mp3, "-ar", "44100", "-ac", "2", wav], check=True)
    dur = _wav_duration(wav)
    log(f"  voice: {len(words)} words, {dur:.1f}s")
    return wav, dur, words


def estimate_timings(text, wps=2.7):
    """No-voice mode: estimate readable word timings (used for 'Text + music' videos and tests)."""
    words, t = [], 0.6
    for w in text.split():
        d = max(0.22, (len(w) + 2) / (wps * 6.5))
        if w[-1] in ".!?":
            d += 0.35
        words.append({"word": w, "start": t, "end": t + d})
        t += d
    return words, t + 1.2
