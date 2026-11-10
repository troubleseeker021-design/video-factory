"""Agent 8 - The Researcher (images & footage).
Finds real visuals for each scene:
  - Pixabay stock video clips (free key: PIXABAY_API_KEY)
  - NASA Image & Video Library clips for space topics (public domain, no key)
  - Pexels stock videos/photos (free key, if you have one)
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

from .common import ASSETS, log, retry

UA = "FacelessVideoFactory/2.0 (personal hobby project; python urllib)"
CACHE = os.path.join(ASSETS, "footage", "_cache")
BAD_NAME = re.compile(r"map|diagram|logo|icon|flag|chart|graph|locator|range|distribution|coat|symbol|seal|"
                      r"signature|plot|scheme|table|svg|drawing|sketch|stamp|banknote|coin", re.I)


def _get_json(url, headers=None):
    return retry(lambda: _get_json_once(url, headers), tries=3, wait=3, what="image search")


def _get_json_once(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _download(url, ext, headers=None, max_mb=80):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.md5(url.encode()).hexdigest()[:16] + ext)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    size = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > max_mb * (1 << 20):
                f.close()
                os.remove(path + ".part")
                raise RuntimeError(f"file larger than {max_mb} MB, skipped")
            f.write(chunk)
    os.replace(path + ".part", path)
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


# ---------------- Pixabay (free stock videos) ----------------
def pixabay_video(query, key, used):
    q = urllib.parse.urlencode({"key": key, "q": query[:90], "per_page": 20, "safesearch": "true"})
    try:
        hits = _get_json(f"https://pixabay.com/api/videos/?{q}").get("hits", [])
    except Exception as e:
        log(f"  pixabay search failed for '{query}': {e}")
        return None
    random.shuffle(hits)
    for h in hits:
        if h.get("id") in used:
            continue
        vids = h.get("videos", {})
        for size in ("medium", "large", "small"):
            v = vids.get(size) or {}
            if v.get("url") and (v.get("height") or 0) >= 540:
                try:
                    path = _download(v["url"], ".mp4")
                except Exception as e:
                    log(f"  pixabay download failed: {e}")
                    break
                used.add(h.get("id"))
                portrait = (v.get("height") or 0) > (v.get("width") or 1)
                return {"type": "clip", "path": path, "credit": "Video: Pixabay",
                        "fit": "full" if portrait else "card"}
    return None


# ---------------- NASA Image & Video Library (public domain) ----------------
def nasa_video(query, used):
    q = urllib.parse.urlencode({"q": query[:90], "media_type": "video"})
    try:
        items = _get_json(f"https://images-api.nasa.gov/search?{q}").get("collection", {}).get("items", [])
    except Exception as e:
        log(f"  NASA search failed for '{query}': {e}")
        return None
    items = items[:12]
    random.shuffle(items)
    for it in items:
        href = it.get("href")
        nid = (it.get("data") or [{}])[0].get("nasa_id")
        if not href or nid in used:
            continue
        try:
            assets = _get_json(href)
        except Exception:
            continue
        mp4 = [a for a in assets if isinstance(a, str) and a.lower().endswith(".mp4")]
        pick = next((a for tag in ("~mobile", "~small", "~medium") for a in mp4 if tag in a), None)
        if not pick:
            continue
        try:
            path = _download(pick.replace("http://", "https://"), ".mp4")
        except Exception as e:
            log(f"  NASA download failed: {e}")
            continue
        used.add(nid)
        return {"type": "clip", "path": path, "credit": "Video: NASA", "fit": "card", "start": 6}
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
    # 3D-explainer look: an AI-generated 3D-render picture for every shot; real photos/footage
    # only fill the shots where the AI art failed
    if cfg.get("ai_visuals", True):
        try:
            import tempfile
            from . import aiart
            aiart.plan(script, scenes, cfg, seed, tempfile.mkdtemp(prefix="art_"))
        except Exception as e:
            log(f"  AI art skipped: {e}")
    targets = [k for k in range(n) if not scenes[k]["media"]]
    if not targets:
        log(f"  media: all {n} shots are AI 3D art")
        return scenes

    if own:
        for k in targets:
            scenes[k]["media"] = {"type": "clip", "path": rng.choice(own), "credit": ""}
        return scenes

    pics = []
    if cfg.get("use_wikipedia_images", True):
        for title in script.get("wiki", [])[:3]:
            pics += wiki_images(title, limit=8)
        seen, uniq = set(), []
        for p in pics:
            if p["url"] not in seen:
                seen.add(p["url"])
                uniq.append(p)
        pics = uniq

    pixabay_key = (os.environ.get("PIXABAY_API_KEY") or cfg.get("pixabay_api_key", "")).strip()
    space = script.get("theme") == "space"
    used = set()
    queries = [*script.get("wiki", []), *keywords]

    def clip_for(k):
        i = scenes[k]["i"]
        q = visuals[i] if i < len(visuals) and visuals[i] else keywords[k % len(keywords)]
        order = []
        if space:
            order.append(lambda: nasa_video(queries[k % len(queries)], used))
        if pixabay_key:
            order.append(lambda: pixabay_video(q, pixabay_key, used))
        if key:
            order.append(lambda: ({"type": "clip", "path": c, "credit": ""} if (c := pexels_video(q, key)) else None))
        for fn in order:
            try:
                m = fn()
            except Exception as e:
                log(f"  clip search failed: {e}")
                m = None
            if m:
                return m
        return None

    def photo_for():
        while pics:
            pic = pics.pop(0)
            try:
                return {"type": "photo", "path": _download(pic["url"], ".jpg"), "credit": pic["credit"]}
            except Exception as e:
                log(f"  image download failed: {e}")
        return None

    for n_t, k in enumerate(targets):
        # alternate moving clips and real photos; each falls back to the other
        first, second = (clip_for, photo_for) if n_t % 2 == 0 else (photo_for, clip_for)
        m = first(k) if first is clip_for else first()
        if not m:
            m = second(k) if second is clip_for else second()
        scenes[k]["media"] = m
    # not enough different visuals? reuse the ones we found (shown with a different move/crop)
    pool = [sc["media"] for sc in scenes if sc["media"]]
    if pool:
        for k, sc in enumerate(scenes):
            if not sc["media"]:
                sc["media"] = dict(pool[k % len(pool)])
    found = sum(1 for sc in scenes if sc["media"])
    log(f"  media: {found}/{n} scenes with real photos/footage, the rest use 3D graphics")
    return scenes
