"""The Animator: an AI-generated 3D-render style picture for every shot (the look of the viral
3D explainer reels), animated later by the renderer with camera moves.
Uses Cloudflare Workers AI (free daily allowance; GitHub secrets CF_ACCOUNT_ID + CF_API_TOKEN).
Gemini images are tried only if "ai_art_gemini" is true in config.json (needs a paid Gemini plan).
Returns None when nothing works, so the factory falls back to real photos/footage."""
import base64
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import log

STYLE = ("hyper-detailed 3D render in the style of a premium science-documentary animation, "
         "cinematic composition, soft studio lighting with rim light, shallow depth of field, rich colours, "
         "photorealistic materials, vertical 9:16 frame, the subject large and centred, clean background, "
         "absolutely no text, no letters, no labels, no watermark, no logo")
_GEMINI = None  # which Gemini image model works for this key (False = none)


def _gemini_models(key):
    global _GEMINI
    if _GEMINI is None:
        names = []
        try:
            with urllib.request.urlopen(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200",
                                        timeout=30) as r:
                for m in json.loads(r.read()).get("models", []):
                    n = m.get("name", "").split("/")[-1]
                    if "image" in n and "gemini" in n and "generateContent" in m.get("supportedGenerationMethods", []):
                        names.append(n)
        except Exception as e:
            log(f"  AI art: Gemini model list failed: {e}")
        names.sort(key=lambda n: ("preview" in n, "exp" in n, n))
        _GEMINI = names[:3] or False
        log(f"  AI art: Gemini image models: {_GEMINI or 'none'}")
    return _GEMINI or []


def _gemini(prompt, key):
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"responseModalities": ["IMAGE"],
                                            "imageConfig": {"aspectRatio": "9:16"}}}).encode()
    for model in _gemini_models(key):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
            for part in resp["candidates"][0]["content"]["parts"]:
                data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
                if data:
                    return base64.b64decode(data)
        except Exception as e:
            log(f"  AI art: Gemini ({model}) failed: {str(e)[:120]}")
    return None


CF_MODELS = [  # (model, extra settings) - best first; each falls back to the next
    ("@cf/leonardo/lucid-origin", {"width": 864, "height": 1536, "steps": 25}),
    ("@cf/black-forest-labs/flux-1-schnell", {"steps": 8}),
]


def _cloudflare(prompt, seed):
    acct, tok = os.environ.get("CF_ACCOUNT_ID", "").strip(), os.environ.get("CF_API_TOKEN", "").strip()
    if not acct or not tok:
        return None
    for model, extra in CF_MODELS:
        if model in _CF_BAD:
            continue
        body = json.dumps({"prompt": prompt[:2000], "seed": seed % 2_000_000_000, **extra}).encode()
        req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}", data=body,
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                ctype, raw = r.headers.get("Content-Type", ""), r.read()
            if ctype.startswith("image/"):
                return raw
            img = (json.loads(raw).get("result") or {}).get("image")
            if img:
                return base64.b64decode(img)
        except urllib.error.HTTPError as e:
            msg = e.read()[:200].decode("utf-8", "ignore")
            log(f"  AI art: Cloudflare {model.split('/')[-1]} failed: {e.code} {msg}")
            if e.code in (400, 404):  # model or settings not available - don't try it again this run
                _CF_BAD.add(model)
        except Exception as e:
            log(f"  AI art: Cloudflare {model.split('/')[-1]} failed: {str(e)[:120]}")
    return None


_CF_BAD = set()


def _pollinations(prompt, seed):
    q = urllib.parse.quote(prompt[:900])
    url = (f"https://image.pollinations.ai/prompt/{q}?width=1080&height=1920&nologo=true&enhance=false"
           f"&seed={seed}&model=flux")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "video-factory/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if len(data) > 30_000:
                return data
        except Exception as e:
            log(f"  AI art: Pollinations try {attempt + 1} failed: {str(e)[:120]}")
            time.sleep(8 * (attempt + 1))
    return None


def make(prompt, path, cfg, seed=None):
    """Creates one image file at `path`. Returns the path or None."""
    from PIL import Image
    full = f"{prompt.strip().rstrip('.')}. {STYLE}"
    key = cfg.get("gemini_api_key", "").strip()
    seed = seed if seed is not None else random.randint(1, 10 ** 6)
    data = _cloudflare(full, seed)
    if not data and key and cfg.get("ai_art_gemini", False):
        data = _gemini(full, key)
    if not data and cfg.get("ai_art_pollinations", False):
        data = _pollinations(full, seed)
    if not data:
        return None
    with open(path, "wb") as f:
        f.write(data)
    try:
        img = Image.open(path).convert("RGB")
        if min(img.size) < 400:
            return None
        if img.width / img.height > 0.7:  # square picture: keep the centre as a tall 9:16 frame, sharpened
            from PIL import ImageFilter
            w = int(img.height * 9 / 16)
            img = img.crop(((img.width - w) // 2, 0, (img.width + w) // 2, img.height))
            img = img.resize((1080, 1920), Image.LANCZOS).filter(ImageFilter.UnsharpMask(1.6, 70, 2))
        img.save(path, quality=93)
        return path
    except Exception:
        return None


def plan(script, scenes, cfg, seed, workdir):
    """Give every scene its own AI picture. Lines that are split into several shots get a different
    camera angle per shot. Returns how many scenes got art."""
    art = script.get("art") or []
    visuals = script.get("visuals") or []
    topic = script.get("title", "")
    angles = ["wide establishing shot", "extreme macro close-up", "cut-away cross-section view showing the inside",
              "dramatic low-angle shot", "top-down overhead view"]
    done, cache, fails = 0, {}, 0
    for k, sc in enumerate(scenes):
        if fails >= 3 and done == 0:  # the AI art services are down today - use photos/footage instead
            log("  AI art: not available right now, falling back to photos and footage")
            break
        i = sc["i"]
        base = (art[i] if i < len(art) and art[i] else "") or \
               (f"{visuals[i]}, about {topic}" if i < len(visuals) and visuals[i] else topic)
        part = sum(1 for s in scenes[:k] if s["i"] == i)
        prompt = base if part == 0 else f"{base}, {angles[(i + part) % len(angles)]}"
        if prompt in cache:
            sc["media"] = dict(cache[prompt])
            continue
        path = os.path.join(workdir, f"art_{k:02d}.jpg")
        if make(prompt, path, cfg, seed=seed + k):
            m = {"type": "photo", "path": path, "credit": "", "ai": True}
            sc["media"] = m
            cache[prompt] = m
            done += 1
        else:
            fails += 1
    log(f"  AI art: {done} new 3D-style pictures for {len(scenes)} shots")
    return done
