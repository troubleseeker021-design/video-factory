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

PROMPT = """You are the head writer of a nature & science documentary series in the style of Discovery Channel,
Animal Planet and BBC Earth, for the YouTube channel Hidden Facts HQ.

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
  "thumb": "2 to 4 word thumbnail text in CAPS",
  "wiki": ["2-4 exact English Wikipedia article titles for real photos"],
  "keywords": ["4 short stock-photo search phrases for beautiful real photos of this story (e.g. eucalyptus forest)"],
  "parts": [
    {{"chapter": "" for the cold open, otherwise a short chapter title,
      "text": "1 paragraph of narration, 2 to 4 sentences",
      "shot": "one cinematic landscape 16:9 shot that shows exactly this paragraph (subject, action, setting,
               light, camera), photorealistic documentary still, no text"}}
  ]}}
Use 22 to 30 parts in total (about 40 words each). The cold open is the first 2-3 parts with chapter "".
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
    img = ImageEnhance.Contrast(img).enhance(1.05)
    return ImageEnhance.Color(img).enhance(1.06)


def _ai_still(prompt, path, seed):
    from io import BytesIO
    from PIL import Image
    from . import aiart
    data = aiart._cloudflare(f"{prompt.strip().rstrip('.')}. {DOC_STYLE}", seed)
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


def _unsplash_landscape(queries, limit=12):
    """Beautiful real landscape photos from Unsplash (used when the free AI pictures are used up)."""
    import urllib.parse
    from . import media
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not key:
        return []
    hdr = {"Authorization": f"Client-ID {key}", "Accept-Version": "v1"}
    out = []
    for q in queries[:4]:
        try:
            qs = urllib.parse.urlencode({"query": q, "orientation": "landscape", "per_page": 4, "content_filter": "high"})
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
    from PIL import Image, ImageDraw, ImageEnhance
    img = ImageEnhance.Contrast(Image.open(still).convert("RGB").resize((1280, 720))).enhance(1.15)
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(700):
        d.line([(x, 0), (x, 720)], fill=(0, 0, 0, int(170 * (1 - x / 700))))
    f = _font("Anton-Regular.ttf", 120)
    words, lines, cur = text.upper().split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.getlength(t) > 620 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    for i, ln in enumerate(lines[:3]):
        fill = (255, 196, 0) if i == len(lines[:3]) - 1 else (255, 255, 255)
        d.text((50, 120 + i * 140), ln, font=f, fill=fill, stroke_width=6, stroke_fill=(0, 0, 0))
    img.save(path, quality=92)


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
    pieces, offsets, t, sr = [], [], 0.0, 44100
    for i, p in enumerate(parts):
        pdir = os.path.join(WORK, f"v{i:02d}")
        os.makedirs(pdir, exist_ok=True)
        pw, pdur, _ = voice.narrate(p["text"].strip(), cfg, pdir, voices=voices, rate=rate, pitch="-2Hz")
        with wave.open(pw, "rb") as w:
            sr = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        new_chapter = bool(nxt and (nxt.get("chapter") or "").strip()
                           and (nxt.get("chapter") or "").strip().lower() != (p.get("chapter") or "").strip().lower())
        gap = 2.0 if new_chapter else 1.0
        offsets.append(t)
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
    total = vdur + intro + 2.0
    durs = [max(3.0, b - a) for a, b in zip(starts, starts[1:] + [total])]

    # 3. pictures: real photos sprinkled between AI cinematic stills
    real = _real_photos(script.get("wiki") or [])
    stock, si = None, 0  # Unsplash photos, fetched only if the AI pictures run out
    stills = []
    ri = 0
    for i, p in enumerate(parts):
        path = os.path.join(WORK, f"s{i:02d}.jpg")
        use_real = real and ri < len(real) and i % 5 == 3
        if use_real:
            from PIL import Image
            _landscape(Image.open(real[ri]["path"])).save(path, quality=93)
            ri += 1
        elif not _ai_still(p.get("shot") or p["text"], path, seed + i):
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
            elif stills:
                shutil.copy(stills[-1], path)
            else:
                _fallback_still(p["text"], path, seed + i)
        stills.append(path)
    log(f"  pictures: {len(stills)} shots ({ri} real photos)")

    # 4. one clip per shot, joined with soft 1-second dissolves
    clips = []
    for i, (still, d) in enumerate(zip(stills, durs)):
        out = os.path.join(WORK, f"c{i:02d}.mp4")
        _shot_clip(still, d + 1.0, out, i)
        clips.append(out)
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
    over, chapters, n, last_ch = [("title.png", 0.3, intro + 0.6)], [("0:00", "Cold open")], 0, ""
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
    mus = music.pick_music(total, seed, WORK)
    args = ["-i", film]
    for png, _, _ in over:
        args += ["-loop", "1", "-i", os.path.join(WORK, png)]
    args += ["-i", wav, "-i", mus]
    fc, cur = [], "[0:v]"
    for k, (_, a, b) in enumerate(over, 1):
        fc.append(f"[{k}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.6:alpha=1,fade=t=out:st={b - 0.6:.2f}:d=0.6:alpha=1[o{k}]")
        fc.append(f"{cur}[o{k}]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[v{k}]")
        cur = f"[v{k}]"
    vi, mi = len(over) + 1, len(over) + 2
    fc.append(f"[{vi}:a]adelay={int(intro * 1000)}|{int(intro * 1000)}[vo]")
    fc.append(f"[{mi}:a]volume=0.15,afade=t=out:st={total - 3:.2f}:d=3[mu]")
    fc.append("[vo][mu]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]")
    out = os.path.join(DATA, "documentary.mp4")
    _run([*args, "-filter_complex", ";".join(fc), "-map", cur, "-map", "[a]", "-t", f"{total:.2f}",
          *ENC, "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", out], timeout=3000)
    thumb = os.path.join(WORK, "thumb.jpg")
    _thumbnail(stills[min(2, len(stills) - 1)], script.get("thumb") or script["title"], thumb)
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
                                         "created": dt.datetime.now().isoformat(timespec="seconds")})
    os.remove(path)
    shutil.rmtree(WORK, ignore_errors=True)
    return url
