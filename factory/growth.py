"""Growth team: learns from your numbers and makes extra content.

  - collect_stats(st)  once a day: views / likes / comments for recent videos on YouTube,
                       Instagram and Facebook (stored in data/cloud_state.json history)
  - insights(st)       a short "what works on this channel" note for the Writer
  - weekly_report(st)  Sunday evening: report in Telegram (best video, views, followers)
  - compilation(st)    Sunday afternoon: the week's best Shorts joined into one long
                       YouTube video with chapters (long videos count toward monetization)
Everything here is best-effort: if a step fails, the normal factory keeps running."""
import datetime as dt
import os
import re
import shutil
import subprocess

import requests

from . import telegram as tg
from .common import DATA, FONTS, ffmpeg_exe, log
from .diary import note

VER = os.environ.get("META_GRAPH_VERSION", "v24.0")
G = f"https://graph.facebook.com/{VER}"
WORK = os.path.join(DATA, "_weekly")


# ------------------------------------------------------------------ helpers
def _yt():
    from . import youtube_upload
    youtube_upload.prepare_token()
    return youtube_upload


def _meta(path, **params):
    tok = os.environ.get("META_PAGE_TOKEN", "").strip()
    if not tok:
        return None
    r = requests.get(f"{G}/{path}", params={"access_token": tok, **params}, timeout=30)
    data = r.json()
    if "error" in data:
        raise RuntimeError(str(data["error"].get("message", data["error"]))[:200])
    return data


def _ig_code(url):
    m = re.search(r"instagram\.com/(?:reel|reels|p)/([^/?#]+)", str(url))
    return m.group(1) if m else None


def _fb_id(url):
    m = re.search(r"facebook\.com/reel/(\d+)", str(url))
    return m.group(1) if m else None


def _created(item):
    try:
        return dt.datetime.fromisoformat(item.get("created", "")[:19])
    except ValueError:
        return None


def score(item):
    """One number per video: views count most, likes and comments help."""
    s = item.get("stats") or {}
    views = sum(p.get("views", 0) for p in s.values())
    eng = sum(p.get("likes", 0) + 3 * p.get("comments", 0) for p in s.values())
    return views + 25 * eng


def _posted(st, key="history"):
    return [h for h in st.get(key, []) if h.get("status") in ("posted", "auto-posted")]


# ------------------------------------------------------------------ stats
def collect_stats(st, key="history"):
    items = _posted(st, key)[-60:]
    if not items:
        return {}
    problems = {}
    now = dt.datetime.utcnow().isoformat(timespec="seconds")

    # YouTube
    try:
        yt = _yt()
        ids = {yt.video_id(h.get("links", {}).get("YouTube", "")): h for h in items}
        ids.pop(None, None)
        for vid, s in yt.stats(list(ids)).items():
            ids[vid].setdefault("stats", {})["YouTube"] = s
    except Exception as e:
        problems["YouTube"] = str(e)[:160]

    # Instagram (likes + comments always; plays only if the token allows insights)
    ig_user = os.environ.get("IG_USER_ID", "").strip()
    if ig_user:
        try:
            media = _meta(f"{ig_user}/media", fields="id,permalink,like_count,comments_count", limit=100) or {}
            by_code = {_ig_code(m.get("permalink")): m for m in media.get("data", [])}
            for h in items:
                m = by_code.get(_ig_code(h.get("links", {}).get("Instagram", "")))
                if not m:
                    continue
                s = {"likes": m.get("like_count", 0), "comments": m.get("comments_count", 0)}
                try:
                    ins = _meta(f"{m['id']}/insights", metric="views")
                    s["views"] = int(ins["data"][0]["values"][0]["value"])
                except Exception:
                    pass
                h.setdefault("stats", {})["Instagram"] = s
        except Exception as e:
            problems["Instagram"] = str(e)[:160]

    # Facebook
    if os.environ.get("META_PAGE_TOKEN", "").strip():
        for h in items:
            vid = _fb_id(h.get("links", {}).get("Facebook", ""))
            if not vid:
                continue
            try:
                d = _meta(vid, fields="likes.summary(true).limit(0),comments.summary(true).limit(0)")
                s = {"likes": d.get("likes", {}).get("summary", {}).get("total_count", 0),
                     "comments": d.get("comments", {}).get("summary", {}).get("total_count", 0)}
                try:
                    ins = _meta(f"{vid}/video_insights", metric="blue_reels_play_count")
                    s["views"] = int(ins["data"][0]["values"][0]["value"])
                except Exception:
                    pass
                h.setdefault("stats", {})["Facebook"] = s
            except Exception as e:
                problems["Facebook"] = str(e)[:160]
    for h in items:
        if h.get("stats"):
            h["stats_at"] = now
    st["stats_problems" if key == "history" else f"stats_problems_{key}"] = problems
    log(f"Stats updated for {sum(1 for h in items if h.get('stats'))} videos. Problems: {problems or 'none'}")
    note("manager", "Checked how our videos are doing - taking notes for the writer")
    return problems


def followers():
    out = {}
    try:
        out["YouTube"] = _yt().channel_stats()["subscribers"]
    except Exception:
        pass
    try:
        ig = os.environ.get("IG_USER_ID", "").strip()
        if ig:
            out["Instagram"] = int(_meta(ig, fields="followers_count")["followers_count"])
    except Exception:
        pass
    try:
        pg = os.environ.get("META_PAGE_ID", "").strip()
        if pg:
            out["Facebook"] = int(_meta(pg, fields="followers_count")["followers_count"])
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ learning
def insights(st):
    """Text added to the Writer's instructions once enough videos have numbers."""
    rated = [h for h in _posted(st) if h.get("stats")]
    if len(rated) < 6:
        return ""
    rated.sort(key=score, reverse=True)
    top, low = rated[:4], rated[-3:]
    themes = {}
    for h in rated:
        if h.get("theme"):
            themes.setdefault(h["theme"], []).append(score(h))
    ranked = sorted(themes, key=lambda t: sum(themes[t]) / len(themes[t]), reverse=True)
    styles = {}
    for h in rated:
        if h.get("hook_style"):
            styles.setdefault(h["hook_style"], []).append(score(h))
    styles = {k: v for k, v in styles.items() if len(v) >= 2}
    best_styles = sorted(styles, key=lambda t: sum(styles[t]) / len(styles[t]), reverse=True)
    lines = ["\nWHAT WORKS ON THIS CHANNEL (from real view counts - use it, but keep topics fresh):"]
    lines.append("- Best performing videos: " + "; ".join(
        f"\"{h['title']}\"" + (f" (hook: {h['hook']})" if h.get("hook") else "") for h in top))
    lines.append("- Weakest videos (avoid similar angles): " + "; ".join(f"\"{h['title']}\"" for h in low))
    if len(ranked) >= 2:
        lines.append(f"- Strongest themes: {', '.join(ranked[:2])}. Weakest theme: {ranked[-1]}. "
                     "Pick a strong theme about 2 times out of 3.")
    if len(best_styles) >= 2:
        lines.append(f"- Hook styles that work best here: {', '.join(best_styles[:2])}. "
                     f"Weakest hook style: {best_styles[-1]}.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ weekly report
def weekly_report(st, chat, key="history", label=""):
    week_ago = dt.datetime.now() - dt.timedelta(days=7)
    items = [h for h in _posted(st, key) if (_created(h) or dt.datetime.min) >= week_ago]
    rated = sorted([h for h in items if h.get("stats")], key=score, reverse=True)
    lines = [f"{label}📊 Weekly report — {week_ago:%b %d} to {dt.datetime.now():%b %d}", "",
             f"🎬 Videos posted: {len(items)}"]
    totals = {}
    for h in items:
        for p, s in (h.get("stats") or {}).items():
            if "views" in s:
                totals[p] = totals.get(p, 0) + s["views"]
    for p, v in totals.items():
        lines.append(f"👀 {p} views on this week's videos: {v:,}")
    if rated:
        b = rated[0]
        bv = sum(s.get("views", 0) for s in b["stats"].values())
        lines.append(f"⭐ Best video: {b['title']} ({bv:,} views)")
        if len(rated) > 2:
            lines.append(f"📉 Weakest: {rated[-1]['title']}")
    series = {}
    for h in rated:
        if h.get("series"):
            name = h["series"].rsplit(" #", 1)[0]
            series.setdefault(name, []).append(score(h))
    if series:
        best = max(series, key=lambda k: sum(series[k]) / len(series[k]))
        lines.append(f"🏆 Best series: {best}")
    fkey = "followers" if key == "history" else f"followers_{key}"
    now_f, prev_f = followers(), st.get(fkey, {})
    if now_f:
        lines.append("")
        lines.append("👥 Followers: " + ", ".join(
            f"{p} {n:,}" + (f" ({n - prev_f[p]:+,})" if p in prev_f else "") for p, n in now_f.items()))
        st[fkey] = now_f
    if key == "history" and st.get("stats_problems", {}).get("YouTube"):
        lines += ["", "⚠️ YouTube numbers need the new sign-in: run YOUTUBE_SIGNIN.bat on your PC "
                      "and update the GitHub secret YT_REFRESH_TOKEN."]
    tip = insights(st) if key == "history" else ""
    if tip:
        lines += ["", "🧠 The writer is now leaning toward what works best for your audience."]
    tg.send_message(chat, "\n".join(lines))
    note("manager", "Sent the boss this week's report")


# ------------------------------------------------------------------ daily comments
QUESTIONS_FILE = os.path.join(DATA, "viewer_questions.json")


def comment_digest(st, chat):
    """Once a day: the newest viewer comments from YouTube and Instagram in one Telegram message,
    so you can reply personally. Questions are saved so the Writer can turn them into videos."""
    from .common import read_json, write_json
    week_ago = dt.datetime.now() - dt.timedelta(days=10)
    items = [h for h in _posted(st) if (_created(h) or dt.datetime.min) >= week_ago][-15:]
    seen = set(st.get("comments_seen", []))
    found = []  # (platform, title, author, text, link)
    try:
        yt = _yt()
        for h in items:
            url = h.get("links", {}).get("YouTube", "")
            vid = yt.video_id(url)
            if not vid:
                continue
            for c in yt.top_comments(vid, 10):
                if c["id"] not in seen and not c.get("mine"):
                    found.append(("YouTube", h.get("title", ""), c["author"], c["text"], url, c["id"]))
    except Exception as e:
        log(f"  comments (YouTube) skipped: {e}")
    ig_user = os.environ.get("IG_USER_ID", "").strip()
    if ig_user:
        try:
            media = _meta(f"{ig_user}/media", fields="id,permalink,comments_count", limit=25) or {}
            titles = {_ig_code(h.get("links", {}).get("Instagram", "")): h.get("title", "") for h in items}
            for m in media.get("data", []):
                code = _ig_code(m.get("permalink"))
                if code not in titles or not m.get("comments_count"):
                    continue
                cs = _meta(f"{m['id']}/comments", fields="id,text,username", limit=10) or {}
                for c in cs.get("data", []):
                    if c["id"] not in seen:
                        found.append(("Instagram", titles[code], c.get("username", "someone"),
                                      c.get("text", ""), m.get("permalink"), c["id"]))
        except Exception as e:
            log(f"  comments (Instagram) skipped: {e}")
    if not found:
        log("No new comments today.")
        return
    lines = ["💬 New comments today - reply personally, it helps the channel grow:", ""]
    for plat, title, who, text, link, _ in found[:20]:
        lines.append(f"• {plat} · {title[:40]}\n  {who}: {text[:200]}\n  {link}")
    if len(found) > 20:
        lines.append(f"...and {len(found) - 20} more.")
    tg.send_message(chat, "\n".join(lines)[:3900])
    qs = read_json(QUESTIONS_FILE, [])
    for f in found:
        if "?" in f[3] and len(f[3]) > 12:
            qs.append({"text": f[3][:300], "from": f[0], "video": f[1]})
    write_json(QUESTIONS_FILE, qs[-50:])
    st["comments_seen"] = (list(seen) + [f[5] for f in found])[-500:]
    note("manager", f"Sent the boss {len(found)} new viewer comments to reply to")


# ------------------------------------------------------------------ weekly compilation
def _card(text_top, text_big, path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1080, 1920), (8, 10, 24))
    d = ImageDraw.Draw(img)
    for y in range(1920):  # soft vertical glow
        k = max(0, 1 - abs(y - 900) / 900)
        d.line([(0, y), (1080, y)], fill=(int(8 + 40 * k), int(10 + 20 * k), int(24 + 70 * k)))
    try:
        big = ImageFont.truetype(os.path.join(FONTS, "Anton-Regular.ttf"), 150)
        small = ImageFont.truetype(os.path.join(FONTS, "Poppins-Bold.ttf"), 58)
    except Exception:
        big = small = ImageFont.load_default()
    d.text((540, 760), text_top, font=small, fill=(255, 214, 10), anchor="mm")
    words, lines, cur = text_big.split(), [], ""
    for w in words:
        if len(cur + " " + w) > 14 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    for i, ln in enumerate(lines[:4]):
        d.text((540, 920 + i * 170), ln.upper(), font=big, fill=(255, 255, 255), anchor="mm")
    img.save(path)


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
       "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


def _run(args):
    p = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"ffmpeg failed: {p.stderr[-300:]}")


def _duration(path):
    p = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", p.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _card_clip(png, secs, out):
    _run(["-loop", "1", "-i", png, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
          "-t", str(secs), "-vf", "scale=1080:1920,format=yuv420p", *ENC, out])


def _stamp(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build_compilation(items, out_path, download=tg.download):
    """items = best first. Makes a countdown video (#N ... #1). Returns chapter list."""
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    n = len(items)
    parts, chapters, t = [], [], 0.0
    _card("THIS WEEK ON HIDDEN FACTS HQ", f"{n} facts you've never heard", os.path.join(WORK, "intro.png"))
    _card_clip(os.path.join(WORK, "intro.png"), 3, os.path.join(WORK, "p00.mp4"))
    parts.append("p00.mp4")
    chapters.append(("0:00", "Intro"))
    t += 3
    for i, h in enumerate(reversed(items)):  # countdown: weakest first, best last
        rank = n - i
        raw = os.path.join(WORK, f"raw{i}.mp4")
        download(h["file_id"], raw)
        card = os.path.join(WORK, f"c{i}.png")
        _card(f"#{rank}", h["title"], card)
        _card_clip(card, 1.6, os.path.join(WORK, f"p{i:02d}a.mp4"))
        _run(["-i", raw, "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1", *ENC, os.path.join(WORK, f"p{i:02d}b.mp4")])
        chapters.append((_stamp(t), f"#{rank} {h['title']}"))
        t += 1.6 + _duration(os.path.join(WORK, f"p{i:02d}b.mp4"))
        parts += [f"p{i:02d}a.mp4", f"p{i:02d}b.mp4"]
        os.remove(raw)
    with open(os.path.join(WORK, "list.txt"), "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    _run(["-f", "concat", "-safe", "0", "-i", os.path.join(WORK, "list.txt"), "-c", "copy",
          "-movflags", "+faststart", out_path])
    return chapters, t


def compilation(st, chat, privacy="public"):
    week_ago = dt.datetime.now() - dt.timedelta(days=8)
    items = [h for h in _posted(st) if h.get("file_id") and (_created(h) or dt.datetime.min) >= week_ago]
    if len(items) < 4:
        log(f"Weekly compilation skipped: only {len(items)} videos with saved copies this week.")
        return None
    items.sort(key=lambda h: (score(h), h.get("created", "")), reverse=True)
    items = items[:10]
    note("editor", f"Putting together this week's best-of: {len(items)} videos in one long video...")
    out = os.path.join(DATA, "weekly.mp4")
    chapters, total = build_compilation(items, out)
    n = len(items)
    title = f"{n} Mind-Blowing Facts You've Never Heard | Hidden Facts HQ Weekly"
    desc = (f"The {n} most mind-blowing facts from this week on Hidden Facts HQ, counted down to #1.\n\n"
            "Chapters:\n" + "\n".join(f"{a} {b}" for a, b in chapters) +
            "\n\nNew facts every day - subscribe so you don't miss one!\n\n#facts #science #didyouknow")
    post = {"youtube": {"title": title, "description": desc,
                        "tags": ["facts", "did you know", "science facts", "mind blowing facts", "compilation"]}}
    url = _yt().upload(out, post, privacy, short=False)
    os.remove(out)
    shutil.rmtree(WORK, ignore_errors=True)
    note("publisher", "Posted this week's long video on YouTube!", link=url)
    tg.send_message(chat, f"📺 Weekly long video posted ({int(total // 60)} min, {n} facts):\n{url}")
    st.setdefault("history", []).append({"title": title, "status": "weekly", "links": {"YouTube": url},
                                         "created": dt.datetime.now().isoformat(timespec="seconds")})
    return url
