"""Agent 8 - The Researcher (images & footage).
Finds real visuals for each scene:
  - Pexels stock videos/photos (free key, no credit needed)
  - Wikipedia / Wikimedia Commons photos (no key needed; only public-domain,
    CC0 and CC BY images are used, and the credit is shown on screen)
  - your own clips in assets/footage
Anything that fails is skipped and the scene falls back to 3D graphics."""
import glob
import hashlib
import json
import os
import random
import re
import urllib.parse
import urllib.request

from .common import ASSETS, log

UA = "FacelessVideoFactory/2.0 (personal hobby project; python urllib)"
CACHE = os.path.join(ASSETS, "footage", "_cache")
BAD_NAME = re.compile(r"map|diagram|logo|icon|flag|chart|graph|locator|range|distribution|coat|symbol|seal|"
                      r"signature|plot|scheme|table|svg|drawing|sketch|stamp|banknote|coin", re.I)


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _download(url, ext, headers=None):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest()[:16] + ext)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=90) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def _clean(html_text):
    return re.sub(r"<[^>]+>", "", html_text or "").strip()


# ---------------- Wikipedia / Commons ----------------
def wiki_images(title, limit=4):
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": "2", "redirects": "1",
        "titles": title, "generator": "images", "gimlimit": "40",
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlheight": "1920",
    })
    try:
        data = _get_json(f"https://en.wikipedia.org/w/api.php?{q}")
    except Exception as e:
        log(f"  wikipedia lookup failed for '{title}': {e}")
        return []
    out = []
    for page in data.get("query", {}).get("pages", []):
        info = (page.get("imageinfo") or [{}])[0]
        name = page.get("title", "")
        if info.get("mime") != "image/jpeg" or BAD_NAME.search(name):
            continue
        if info.get("width", 0) < 700 or info.get("height", 0) < 500:
            continue
        meta = info.get("extmetadata", {})
        lic = _clean(meta.get("LicenseShortName", {}).get("value", ""))
        if meta.get("NonFree", {}).get("value") == "true":
            continue
        lic_l = lic.lower()
        ok = ("public domain" in lic_l or lic_l.startswith("pd") or "cc0" in lic_l or
              (lic_l.startswith("cc by") and "sa" not in lic_l.replace("cc by", "")))
        if not ok:
            continue
        artist = _clean(meta.get("Artist", {}).get("value", ""))[:40] or "Wikimedia Commons"
        url = info.get("thumburl") or info.get("url")
        out.append({"url": url, "credit": f"Photo: {artist} · {lic} · Wikimedia Commons", "w": info["width"], "h": info["height"]})
    out.sort(key=lambda x: -(x["w"] * x["h"]))
    return out[:limit]


# ---------------- Pexels ----------------
def pexels_video(query, key):
    q = urllib.parse.urlencode({"query": query, "orientation": "portrait", "per_page": 8, "size": "medium"})
    try:
        vids = _get_json(f"https://api.pexels.com/videos/search?{q}", {"Authorization": key}).get("videos", [])
    except Exception as e:
        log(f"  pexels video search failed for '{query}': {e}")
        return None
    random.shuffle(vids)
    for v in vids:
        files = [f for f in v.get("video_files", []) if (f.get("height") or 0) >= 1280 and (f.get("width") or 0) < f["height"]]
        if files:
            best = min(files, key=lambda f: abs(f["height"] - 1920))
            try:
                return _download(best["link"], ".mp4")
            except Exception as e:
                log(f"  pexels download failed: {e}")
    return None


def pexels_photo(query, key):
    q = urllib.parse.urlencode({"query": query, "orientation": "portrait", "per_page": 10})
    try:
        photos = _get_json(f"https://api.pexels.com/v1/search?{q}", {"Authorization": key}).get("photos", [])
    except Exception as e:
        log(f"  pexels photo search failed for '{query}': {e}")
        return None
    if not photos:
        return None
    p = random.choice(photos[:6])
    try:
        return _download(p["src"]["large2x"], ".jpg")
    except Exception:
        return None


# ---------------- per-scene plan ----------------
def plan(script, scenes, cfg, seed):
    """Attach real visuals to scenes (sets scene["media"]). Hook gets the strongest image."""
    rng = random.Random(seed)
    n = len(scenes)
    for sc in scenes:
        sc["media"] = None
    own = glob.glob(os.path.join(ASSETS, "footage", "*.mp4")) + glob.glob(os.path.join(ASSETS, "footage", "*.mov"))
    key = cfg.get("pexels_api_key", "").strip()
    visuals = script.get("visuals") or []
    keywords = script.get("keywords") or [script.get("theme", "nature")]
    # 2 of every 3 scenes get real visuals, the rest show 3D graphics (variety = retention)
    targets = [k for k in range(n) if k % 3 != 2]

    if own:
        for k in targets:
            scenes[k]["media"] = {"type": "clip", "path": rng.choice(own), "credit": ""}
        return scenes

    pics = []
    if cfg.get("use_wikipedia_images", True):
        for title in script.get("wiki", [])[:3]:
            pics += wiki_images(title, limit=5)
        seen, uniq = set(), []
        for p in pics:
            if p["url"] not in seen:
                seen.add(p["url"])
                uniq.append(p)
        pics = uniq

    for k in targets:
        sc = scenes[k]
        if pics and (not key or k == 0 or k % 2 == 0):
            pic = pics.pop(0)
            try:
                sc["media"] = {"type": "photo", "path": _download(pic["url"], ".jpg"), "credit": pic["credit"]}
                continue
            except Exception as e:
                log(f"  image download failed: {e}")
        if key:
            i = sc["i"]
            q = visuals[i] if i < len(visuals) and visuals[i] else keywords[k % len(keywords)]
            clip = pexels_video(q, key)
            if clip:
                sc["media"] = {"type": "clip", "path": clip, "credit": ""}
                continue
            ph = pexels_photo(q, key)
            if ph:
                sc["media"] = {"type": "photo", "path": ph, "credit": ""}
    found = sum(1 for sc in scenes if sc["media"])
    log(f"  media: {found}/{n} scenes with real photos/footage, the rest use 3D graphics")
    return scenes
