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


def narrate(text, cfg, workdir, voices=None, rate=None, pitch=None):
    mp3 = os.path.join(workdir, "voice.mp3")
    wav = os.path.join(workdir, "voice.wav")
    # Curious, story-telling delivery: warm low male voice, slightly slower and deeper.
    voices = voices or [cfg.get("voice") or "en-US-AndrewMultilingualNeural", "en-US-AndrewNeural", "en-US-ChristopherNeural"]
    rate = rate or cfg.get("voice_speed", "+4%")
    pitch = pitch or cfg.get("voice_pitch", "-2Hz")
    words, last_err = None, None
    for v in dict.fromkeys(voices):
        try:
            words = retry(lambda: asyncio.run(_tts(text, v, rate, pitch, mp3)),
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


def _silences(wav):
    """(start, end) of every pause in the voice track (ffmpeg silencedetect)."""
    import re
    p = subprocess.run([ffmpeg_exe(), "-i", wav, "-af", "silencedetect=noise=-35dB:d=0.18", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def _place_lines(lines, wav, dur):
    """Word timings for a voice that gives none: each line ends on the pause nearest to where its share of the
    characters says it should end; its words are spread over its span by length."""
    sil = _silences(wav)
    start, end = 0.0, dur
    if sil and sil[0][0] <= 0.05 and sil[0][1]:
        start = sil[0][1]
    if sil and (sil[-1][1] is None or sil[-1][1] >= dur - 0.05):
        end = sil[-1][0]
    inner = [(s, e) for s, e in sil if e and s > start + 0.1 and e < end - 0.1]
    total = sum(len(x) for x in lines) or 1
    spans, cur, acc, used = [], start, 0, set()
    for i, line in enumerate(lines):
        acc += len(line)
        if i == len(lines) - 1:
            spans.append((cur, end))
            break
        guess = start + (end - start) * acc / total
        best = None
        for k, (s, e) in enumerate(inner):
            if k in used or s <= cur + 0.3 or abs((s + e) / 2 - guess) > 1.5:
                continue
            if best is None or abs((s + e) / 2 - guess) < abs(sum(inner[best]) / 2 - guess):
                best = k
        if best is not None:
            used.add(best)
            spans.append((cur, inner[best][0]))
            cur = inner[best][1]
        else:
            spans.append((cur, max(guess, cur + 0.3)))
            cur = max(guess, cur + 0.3)
    words = []
    for line, (s, e) in zip(lines, spans):
        words += _split_sentence(line, s, max(e, s + 0.3))
    return words


def gemini_narrate(lines, cfg, workdir, voice_name="Kore", lang="Hindi", tempo=None):
    """Hindi voice from Google Gemini text-to-speech (the owner's pick, Nov 27: voice "Kore").
    One call per video; Gemini returns no word timings, so lines are placed on the voice's pauses."""
    import base64
    import requests
    key = (cfg.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("no Gemini key")
    hdr = {"x-goog-api-key": key}
    models = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers=hdr, timeout=30)
        models = [m["name"].split("/")[-1] for m in r.json().get("models", []) if "tts" in m.get("name", "")]
    except Exception as e:
        log(f"  Gemini model list failed: {type(e).__name__}")
    models = sorted(models, key=lambda n: ("pro" in n, "preview" in n)) or ["gemini-2.5-flash-preview-tts"]
    prompt = (f"Read this {lang} script aloud like an excited, curious storyteller explaining an amazing fact to a "
              "friend, at a brisk, energetic pace, with only a very short pause between lines. Read every word exactly as written:\n"
              + "\n".join(lines))
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}}}
    pcm, last = None, ""
    for model in models[:3]:
        try:
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers=hdr, json=body, timeout=240)
            pcm = base64.b64decode(r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            if len(pcm) > 48000:  # at least ~1 second of audio
                break
            pcm = None
        except Exception as e:
            last = f"{model}: {type(e).__name__} {str(e)[:120].replace(key, '***')}"
    if not pcm:
        raise RuntimeError(f"Gemini voice failed ({last})")
    raw, wav = os.path.join(workdir, "voice.pcm"), os.path.join(workdir, "voice.wav")
    with open(raw, "wb") as f:
        f.write(pcm)
    tempo = float(tempo if tempo is not None else cfg.get("hindi_gemini_tempo", 1.0))
    # Gemini leaves long gaps between lines: cut every pause down to ~0.3 s, then speed up if asked
    flt = "silenceremove=start_periods=1:start_threshold=-40dB:stop_periods=-1:stop_duration=0.25:stop_threshold=-40dB:stop_silence=0.22"
    af = ["-af", flt + (f",atempo={tempo:.3f}" if abs(tempo - 1.0) > 0.01 else "")]
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", raw,
                    *af, "-ar", "44100", "-ac", "2", wav], check=True)
    os.remove(raw)
    dur = _wav_duration(wav)
    words = _place_lines(lines, wav, dur)
    log(f"  voice: Gemini '{voice_name}', {len(words)} words, {dur:.1f}s")
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
