"""The 24/7 Cloud Manager (runs on GitHub Actions, free).

  python -m factory.cloud make   -> makes new videos and sends each one to your Telegram
                                    with [Approve & post] / [Reject] buttons
  python -m factory.cloud poll   -> reads your taps in Telegram; approved videos are
                                    uploaded to YouTube, Instagram and Facebook

Telegram commands you can send the bot: /make  /status  /help"""
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import traceback

import requests

from . import telegram as tg
from . import diary
from .diary import note
from .common import DATA, REVIEW, load_config, log, read_json, write_json
from . import hindi

STATE_PATH = os.path.join(DATA, "cloud_state.json")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def _state():
    return read_json(STATE_PATH, {"offset": 0, "pending": {}, "history": []})


def _short(vid):
    return hashlib.sha1(vid.encode()).hexdigest()[:10]


def _platforms():
    p = []
    if os.environ.get("YOUTUBE_TOKEN_JSON", "").strip():
        p.append("YouTube")
    if os.environ.get("IG_USER_ID", "").strip() and os.environ.get("META_PAGE_TOKEN", "").strip():
        p.append("Instagram")
    if os.environ.get("META_PAGE_ID", "").strip() and os.environ.get("META_PAGE_TOKEN", "").strip():
        p.append("Facebook")
    return p


# ------------------------------------------------------------------ make
def make():
    if not CHAT:
        sys.exit("TELEGRAM_CHAT_ID secret is missing - see CLOUD SETUP GUIDE.txt part 3.")
    from . import make_videos
    cfg = load_config()
    count = int(os.environ.get("VIDEOS_PER_RUN") or cfg.get("videos_per_run", 1))
    st = _state()
    try:
        from . import growth
        cfg["_insights"] = growth.insights(st)
    except Exception as e:
        log(f"(insights skipped: {e})")
    diary.status("working")
    note("manager", f"Team, time to make {count} new video{'s' if count > 1 else ''}.", kind="start")
    for _ in range(count):
        vid = None
        for attempt in range(2):  # one automatic second try with a different topic
            vid = make_videos.make_one(cfg)
            if vid:
                break
        if not vid:
            tg.send_message(CHAT, "⚠️ A video failed to render. Check the GitHub Actions log.")
            continue
        hid = None
        if hindi.enabled():
            hid = hindi.make(vid, cfg)
        _deliver(st, vid, cfg, "en")
        if hid:
            _deliver(st, hid, cfg, "hi")
    diary.status("idle")
    note("manager", "Great work everyone. Coffee break until the next video!", kind="idle")
    diary.flush()


def _deliver(st, vid, cfg, channel="en", preview_only=False):
    """Sends a finished video to Telegram, then posts it (autopilot) or waits for a tap.
    Videos whose facts the Writer is not sure about always wait for the owner's approval."""
    d = os.path.join(REVIEW, vid)
    post = read_json(os.path.join(d, "post.json"), {})
    short = _short(vid)
    check = bool(post.get("needs_check"))
    auto = cfg.get("auto_post") and not check and not preview_only
    with channel_env(channel):
        where = ", ".join(_platforms()) or "no platforms connected yet (you'll get the captions to post manually)"
    flag = "🇮🇳 HINDI CHANNEL\n" if channel == "hi" else ""
    warn = ""
    if check:
        doubts = "\n".join(f"• {x}" for x in post.get("doubts", [])[:4]) or "• the writer was not fully sure of every fact"
        warn = f"\n\n⚠️ FACT CHECK NEEDED before posting:\n{doubts}\nPlease check, then tap Approve or Reject."
    if preview_only:
        status = "👀 Preview only (not posted)."
    else:
        status = ("🤖 Autopilot: posting now to: " if auto else "Approve to post to: ") + where
    cap = f"{flag}🎬 {post.get('title', vid)}\n\n{post.get('script', '')[:500]}{warn}\n\n{status}"
    buttons = None if (auto or preview_only) else [[{"text": "✅ Approve & post", "callback_data": f"ok:{short}"},
                                                     {"text": "❌ Reject", "callback_data": f"no:{short}"}]]
    try:
        msg = tg.send_video(CHAT, os.path.join(d, "video.mp4"), cap[:1000], buttons)
    except Exception as e:
        log(f"Sending to Telegram failed: {e}")
        tg.send_message(CHAT, f"⚠️ '{post.get('title', vid)}' may have arrived without working buttons "
                              "(Telegram was slow). Ignore that copy - send /make for a new one.")
        shutil.rmtree(d, ignore_errors=True)
        return
    if not preview_only:
        st["pending"][short] = {"vid": vid, "post": post, "file_id": msg["video"]["file_id"], "channel": channel,
                                "message_id": msg["message_id"], "created": dt.datetime.utcnow().isoformat()}
        write_json(STATE_PATH, st)
    log(f"Sent to Telegram: {post.get('title')} [{channel}]")
    diary.status("working", post.get("title", ""))
    note("manager", "Sent the Hindi version to the boss on Telegram" if channel == "hi"
         else "Sent the finished video to the boss on Telegram")
    if check:
        note("manager", "Some facts need a human check - waiting for the boss's approval", kind="error")
    if auto:
        _auto_post(st, short, os.path.join(d, "video.mp4"))
    shutil.rmtree(d, ignore_errors=True)  # the video now lives safely in your Telegram chat


HI_ENV = {"YOUTUBE_TOKEN_JSON": "YOUTUBE_TOKEN_JSON_HI", "META_PAGE_ID": "HI_META_PAGE_ID",
          "META_PAGE_TOKEN": "HI_META_PAGE_TOKEN", "IG_USER_ID": "HI_IG_USER_ID"}


class channel_env:
    """Temporarily swaps the account secrets so the same code posts to the Hindi channel."""
    def __init__(self, channel):
        self.channel, self.saved = channel, {}

    def __enter__(self):
        if self.channel == "hi":
            for k, hk in HI_ENV.items():
                self.saved[k] = os.environ.get(k)
                os.environ[k] = os.environ.get(hk, "")
            _reset_yt()
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.saved:
            _reset_yt()


def _reset_yt():
    try:
        from . import youtube_upload
        youtube_upload._svc = None
        if os.path.exists(youtube_upload.TOKEN):  # never let one channel use the other's sign-in
            os.remove(youtube_upload.TOKEN)
    except Exception:
        pass


def _hist(st, item):
    key = "history_hi" if item.get("channel") == "hi" else "history"
    return st.setdefault(key, [])


# ------------------------------------------------------------------ publish
def _youtube(path, post, cover=None):
    from . import youtube_upload
    if not youtube_upload.prepare_token():
        return None
    privacy = os.environ.get("YOUTUBE_PRIVACY") or load_config().get("youtube_privacy", "public")
    url = youtube_upload.upload(path, post, privacy)
    if cover and os.path.exists(cover):
        try:
            youtube_upload.set_thumbnail(youtube_upload.video_id(url), cover)
        except Exception as e:
            log(f"  (YouTube thumbnail skipped: {str(e)[:120]})")
    q = post.get("question")
    if q:  # a question from the channel under the video gets people commenting
        try:
            youtube_upload.comment(youtube_upload.video_id(url), f"🤔 {q} Tell us below 👇")
        except Exception as e:
            log(f"  (YouTube comment skipped: {str(e)[:120]})")
    return url


def _record(item, status, results):
    """One history line per video - the growth team reads these to learn what works."""
    p = item["post"]
    return {"title": p.get("title"), "status": status, "links": results,
            "created": p.get("created") or dt.datetime.utcnow().isoformat(timespec="seconds"),
            "theme": p.get("theme", ""), "hook": p.get("hook", ""), "series": p.get("series", ""),
            "file_id": item.get("file_id"), "youtube": p.get("youtube")}


def publish(item, local_path=None):
    with channel_env(item.get("channel", "en")):
        return _publish(item, local_path)


def _publish(item, local_path=None):
    from . import meta_upload
    path = os.path.join(REVIEW, "approved.mp4")
    if local_path and os.path.exists(local_path):
        shutil.copy(local_path, path)  # use the original file (no 20 MB Telegram download limit)
    else:
        tg.download(item["file_id"], path)
    post, results = item["post"], {}
    cover = os.path.join(os.path.dirname(local_path), "cover.jpg") if local_path else None
    jobs = [
        ("YouTube", lambda: _youtube(path, post, cover)),
        ("Facebook", lambda: meta_upload.facebook_reel(path, post["facebook"], os.environ["META_PAGE_ID"], os.environ["META_PAGE_TOKEN"], cover)
         if "Facebook" in _platforms() else None),
        ("Instagram", lambda: meta_upload.instagram_reel(
            path, post["instagram"], os.environ["IG_USER_ID"], os.environ["META_PAGE_TOKEN"],
            fallback_url=(lambda: meta_upload.facebook_video_source(results["Facebook"], os.environ["META_PAGE_TOKEN"]))
            if str(results.get("Facebook", "")).startswith("http") else None)
         if "Instagram" in _platforms() else None),
    ]
    note("publisher", f"Uploading \"{post.get('title', '')}\" to {', '.join(_platforms()) or 'no platforms'}...")
    for name, fn in jobs:
        try:
            url = fn()
            if url:
                results[name] = url
                note("publisher", f"Posted on {name}!", kind="done", platform=name, link=url)
        except Exception as e:
            log(f"{name} upload failed:\n{traceback.format_exc()}")
            results[name] = f"FAILED: {str(e)[:200]}"
            note("publisher", f"{name} said no this time: {str(e)[:80]}", kind="error", platform=name)
    os.remove(path)
    return results


def _dispatch_make():
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/factory.yml/dispatches",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                      json={"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"task": "make"}}, timeout=30)
    return r.status_code in (200, 204)


HELP = ("🤖 Video Factory commands\n"
        "/make - make a new video now (arrives in ~10 min)\n"
        "/status - videos waiting for approval\n"
        "/report - how your videos are doing (views, best video, followers)\n"
        "Tap ✅ under a video to post it, ❌ to throw it away.")


def _auto_post(st, short, local_path=None):
    """Autopilot: post right away without waiting for a tap (config.json "auto_post": true)."""
    item = st["pending"].pop(short)
    try:
        results = publish(item, local_path)
    except Exception as e:
        item["approved"], item["tries"] = True, 1
        st["pending"][short] = item
        tg.send_message(CHAT, f"⏳ Autopilot couldn't post '{item['post'].get('title')}' yet ({str(e)[:120]}). Retrying in 15 min.")
        write_json(STATE_PATH, st)
        return
    lines = [f"{k}: {v}" for k, v in results.items()] or ["No platforms connected yet."]
    tg.send_message(CHAT, f"🤖 Autopilot posted: {item['post'].get('title')}\n\n" + "\n".join(lines) +
                    "\n\n📱 TikTok caption 👇")
    tg.send_message(CHAT, item["post"].get("tiktok", ""))
    _hist(st, item).append(_record(item, "auto-posted", results))
    write_json(STATE_PATH, st)


def _retry_approved(st):
    """Approved videos whose posting failed earlier get another try (up to 6 tries, ~1.5 h)."""
    for short, item in list(st["pending"].items()):
        if not item.get("approved"):
            continue
        try:
            results = publish(item)
        except Exception as e:
            item["tries"] = item.get("tries", 0) + 1
            if item["tries"] >= 6:
                st["pending"].pop(short)
                tg.send_message(CHAT, f"❌ Gave up posting '{item['post'].get('title')}' after 6 tries: {str(e)[:150]}")
            write_json(STATE_PATH, st)
            continue
        st["pending"].pop(short)
        lines = [f"{k}: {v}" for k, v in results.items()] or ["No platforms connected yet."]
        tg.send_message(CHAT, f"🚀 (retry worked) {item['post'].get('title')}\n\n" + "\n".join(lines))
        _hist(st, item).append(_record(item, "posted", results))
        write_json(STATE_PATH, st)


def poll(inline_make=False):
    st = _state()
    _retry_approved(st)
    updates = tg.get_updates(st.get("offset", 0))
    for u in updates:
        st["offset"] = u["update_id"] + 1
        try:
            if "message" in u:
                chat = str(u["message"]["chat"]["id"])
                text = (u["message"].get("text") or "").strip().lower()
                if not CHAT:
                    tg.send_message(chat, f"👋 Your chat ID is: {chat}\n\nAdd it to GitHub as the secret TELEGRAM_CHAT_ID "
                                          f"(see CLOUD SETUP GUIDE part 3).")
                    continue
                if chat != CHAT:
                    continue  # ignore strangers
                if text.startswith("/make"):
                    if inline_make:  # called from tick: make it in this same run
                        st["make_now"] = True
                        ok = True
                    else:
                        ok = _dispatch_make()
                    tg.send_message(CHAT, "🏭 Making a new video... it'll arrive here in about 10 minutes." if ok
                                    else "Couldn't start the factory - check the workflow permissions (guide part 2).")
                elif text.startswith("/report"):
                    st["report_now"] = True
                    tg.send_message(CHAT, "📊 Gathering your numbers... the report arrives in about 15 minutes.")
                elif text.startswith("/status"):
                    titles = [f"• {p['post'].get('title')}" for p in st["pending"].values()]
                    tg.send_message(CHAT, f"⏳ Waiting for approval: {len(titles)}\n" + "\n".join(titles[:20]))
                else:
                    tg.send_message(CHAT, HELP)
            elif "callback_query" in u:
                cb = u["callback_query"]
                chat = str(cb["message"]["chat"]["id"])
                if chat != CHAT:
                    continue
                action, _, short = cb.get("data", "").partition(":")
                item = st["pending"].pop(short, None)
                tg.clear_buttons(CHAT, cb["message"]["message_id"])
                if not item:
                    tg.answer_callback(cb["id"], "Already handled.")
                    continue
                if action == "no":
                    tg.answer_callback(cb["id"], "Rejected 🗑️")
                    tg.send_message(CHAT, f"🗑️ Rejected: {item['post'].get('title')}")
                    _hist(st, item).append({"title": item["post"].get("title"), "status": "rejected"})
                else:
                    tg.answer_callback(cb["id"], "Posting... 🚀")
                    try:
                        results = publish(item)
                    except Exception as e:
                        item["approved"] = True
                        item["tries"] = item.get("tries", 0) + 1
                        st["pending"][short] = item  # keep it so the next check retries
                        tg.send_message(CHAT, f"⏳ Couldn't post '{item['post'].get('title')}' yet ({str(e)[:120]}). "
                                              "I'll retry automatically in 15 minutes.")
                        log("publish failed:\n" + traceback.format_exc())
                        continue
                    lines = [f"{k}: {v}" for k, v in results.items()] or ["No platforms connected yet."]
                    manual = [p for p in ("TikTok", "YouTube", "Instagram", "Facebook")
                              if p == "TikTok" or p not in results or str(results[p]).startswith("FAILED")]
                    tg.send_message(CHAT, f"🚀 {item['post'].get('title')}\n\n" + "\n".join(lines) +
                                    f"\n\n📱 Post manually to: {', '.join(manual)}.\n"
                                    "Save the video above, then copy the caption for each app from the messages below 👇")
                    yt = item["post"].get("youtube", {})
                    captions = {"TikTok": item["post"].get("tiktok", ""),
                                "YouTube": f"{yt.get('title', '')}\n\n{yt.get('description', '')}",
                                "Instagram": item["post"].get("instagram", ""),
                                "Facebook": item["post"].get("facebook", "")}
                    for p in manual:
                        tg.send_message(CHAT, f"— {p} —\n{captions[p]}")
                    _hist(st, item).append(_record(item, "posted", results))
            st["history"] = st["history"][-300:]
            if "history_hi" in st:
                st["history_hi"] = st["history_hi"][-300:]
        finally:
            write_json(STATE_PATH, st)
    write_json(STATE_PATH, st)
    if updates:
        diary.flush()
    log(f"Processed {len(updates)} Telegram update(s). Pending approvals: {len(st['pending'])}")


# ------------------------------------------------------------------ tick
DEFAULT_TIMES = ["07:37", "11:37", "17:37"]  # Edmonton time; videos arrive ~20 min later


def _due_slot(cfg, st, now=None):
    """Return the scheduled slot (e.g. '2026-11-07 07:37') that still needs a video, or None.
    A missed slot is caught up to 3 hours late; each slot is made only once."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg.get("timezone", "America/Edmonton"))
    now = now or dt.datetime.now(tz)
    due = None
    for day in (now.date() - dt.timedelta(days=1), now.date()):
        for hm in cfg.get("make_times", DEFAULT_TIMES):
            h, m = map(int, hm.split(":"))
            t = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if t <= now <= t + dt.timedelta(hours=3):
                due = t.strftime("%Y-%m-%d %H:%M")
    if due and due != st.get("last_slot"):
        return due
    return None


def tick():
    """Run every 15 minutes (by GitHub's timer and/or the backup timer):
    reads Telegram, then makes a video if one of today's slots is due."""
    poll(inline_make=True)
    cfg = load_config()
    st = _state()
    slot = _due_slot(cfg, st)
    asked = st.pop("make_now", False)
    if not slot and not asked:
        write_json(STATE_PATH, st)
        log("No video due right now.")
        _growth_jobs(cfg)
        return
    if slot:
        st["last_slot"] = slot  # mark first, so a later tick never makes a duplicate
    write_json(STATE_PATH, st)
    log(f"Making a video now ({'slot ' + slot if slot else 'asked for with /make'}).")
    make()


def clear_pending():
    """Removes videos stuck in 'waiting for approval' (Run workflow > task: clear)."""
    st = _state()
    old = st.get("pending", {})
    titles = [p.get("post", {}).get("title", p.get("vid", "?")) for p in old.values()]
    for p in old.values():
        st.setdefault("history", []).append({"title": p.get("post", {}).get("title"), "status": "cleared"})
    st["pending"] = {}
    write_json(STATE_PATH, st)
    log(f"Cleared {len(titles)} old pending video(s).")
    if CHAT:
        tg.send_message(CHAT, f"🧹 Cleared {len(titles)} old video(s) from the approval list:\n" + "\n".join(f"• {t}" for t in titles[:20]))


def repost(st=None):
    """Re-uploads recent videos whose YouTube upload failed (runs by itself every 6 hours when needed,
    or Run workflow > task: repost)."""
    from . import youtube_upload
    st = st or _state()
    done = 0
    for h in st.get("history", [])[-30:]:
        links = h.get("links") or {}
        if not str(links.get("YouTube", "")).startswith("FAILED") or not h.get("file_id"):
            continue
        title = h.get("title", "Mind-blowing fact")
        series = h.get("series", "")
        yt = h.get("youtube") or {
            "title": (f"{title} | {series}" if series and len(title) + len(series) < 88 else title)[:90] + " #shorts",
            "description": f"{h.get('hook') or title}\n\n👇 Did you already know this? Tell us below!\n\n#facts #didyouknow #science #shorts",
            "tags": ["facts", "did you know", "shorts", h.get("theme") or "science"]}
        path = os.path.join(REVIEW, "repost.mp4")
        try:
            tg.download(h["file_id"], path)
            url = _youtube(path, {"youtube": yt, "question": "Did you already know this?"})
        except Exception as e:
            log(f"Repost of '{title}' failed: {e}")
            continue
        finally:
            if os.path.exists(path):
                os.remove(path)
        if url:
            links["YouTube"] = url
            done += 1
            note("publisher", f"Re-posted '{title}' on YouTube!", kind="done", platform="YouTube", link=url)
            tg.send_message(CHAT, f"🔁 Re-posted on YouTube: {title}\n{url}")
            write_json(STATE_PATH, st)
    log(f"Repost finished: {done} video(s) uploaded to YouTube.")
    write_json(STATE_PATH, st)
    diary.flush()


def _in_hi(fn):
    with channel_env("hi"):
        return fn()


def hindi_test():
    """Makes one English + Hindi video and sends both to Telegram as previews (nothing is posted)."""
    from . import make_videos
    cfg = load_config()
    st = _state()
    note("manager", "Making a Hindi test video for the boss.", kind="start")
    from . import writer
    used_before = read_json(writer.USED, [])
    try:
        vid = make_videos.make_one(cfg)
    finally:  # a preview must not use up a script from the queue
        write_json(writer.USED, used_before)
    if not vid:
        tg.send_message(CHAT, "⚠️ The test video failed to render.")
        return
    hid = hindi.make(vid, cfg)
    if not hid:
        tg.send_message(CHAT, "⚠️ The Hindi version failed - check the GitHub Actions log.")
    _deliver(st, vid, cfg, "en", preview_only=True)
    if hid:
        _deliver(st, hid, cfg, "hi", preview_only=True)
    diary.status("idle")
    diary.flush()


HI_SAMPLE = ("सोचो, ये जगह बिल्कुल नकली लगती है, लेकिन ये सौ प्रतिशत असली है। और असली राज़ क्या है, "
             "ज़्यादातर लोगों ने इसके बारे में कभी सुना ही नहीं।")
VOICE_SAMPLES = [  # (label, voice, text, rate)
    ("🇮🇳 HINDI · Madhur, shorter pauses, a little faster (new setting)", "hi-IN-MadhurNeural", HI_SAMPLE, "+6%"),
    ("🇮🇳 HINDI · Madhur, shorter pauses, faster", "hi-IN-MadhurNeural", HI_SAMPLE, "+10%"),
]


def voices():
    """Sends short voice samples to Telegram so the owner can pick the voice by ear."""
    import asyncio
    import tempfile
    from .voice import _tts
    cfg = load_config()
    tg.send_message(CHAT, "🎙️ Voice audition! Listen and tell Claude which number sounds best.")
    for n, (label, v, text, rate) in enumerate(VOICE_SAMPLES, 1):
        mp3 = os.path.join(tempfile.gettempdir(), f"sample_{n}.mp3")
        try:
            asyncio.run(_tts(text, v, rate, "+0Hz", mp3))
            tg.send_audio(CHAT, mp3, f"#{n} {label}", title=f"{n} {v}")
        except Exception as e:
            log(f"sample {v} failed: {e}")


def _growth_now(job):
    """Manual run from GitHub (Run workflow > task: stats / weekly / report)."""
    from . import growth
    st = _state()
    privacy = os.environ.get("YOUTUBE_PRIVACY") or load_config().get("youtube_privacy", "public")
    growth.collect_stats(st)
    if job == "weekly":
        growth.compilation(st, CHAT, privacy)
    elif job == "report":
        growth.weekly_report(st, CHAT)
    write_json(STATE_PATH, st)
    diary.flush()


def _growth_jobs(cfg):
    """Daily stats, Sunday long video and Sunday report - run in quiet ticks only."""
    from zoneinfo import ZoneInfo
    from . import growth
    now = dt.datetime.now(ZoneInfo(cfg.get("timezone", "America/Edmonton")))
    today, week = now.strftime("%Y-%m-%d"), now.strftime("%G-W%V")
    st = _state()
    jobs = []
    if st.get("stats_day") != today and now.hour >= 5:
        jobs.append(("stats_day", today, lambda: growth.collect_stats(st)))
        if hindi.enabled():
            jobs.append(("stats_day_hi", today, lambda: _in_hi(lambda: growth.collect_stats(st, key="history_hi"))))
    block = f"{today}-{now.hour // 6}"
    if st.get("repost_block") != block and any(str((h.get("links") or {}).get("YouTube", "")).startswith("FAILED")
                                               and h.get("file_id") for h in st.get("history", [])[-30:]):
        jobs.append(("repost_block", block, lambda: repost(st)))  # e.g. YouTube's daily upload limit
    if st.get("comments_day") != today and now.hour >= 17:
        jobs.append(("comments_day", today, lambda: growth.comment_digest(st, CHAT)))
    if now.weekday() == 6 and now.hour >= 14 and st.get("compile_week") != week and cfg.get("weekly_video", True):
        privacy = os.environ.get("YOUTUBE_PRIVACY") or cfg.get("youtube_privacy", "public")
        jobs.append(("compile_week", week, lambda: growth.compilation(st, CHAT, privacy)))
    if st.get("report_now"):
        st.pop("report_now")
        if st.get("stats_day") == today:  # numbers not fresh yet today? get them first
            jobs.append(("stats_at_report", today, lambda: growth.collect_stats(st)))
        sunday_evening = now.weekday() == 6 and now.hour >= 19
        jobs.append(("report_week" if sunday_evening else "last_report", week if sunday_evening else today,
                     lambda: growth.weekly_report(st, CHAT)))
    elif now.weekday() == 6 and now.hour >= 19 and st.get("report_week") != week:
        jobs.append(("report_week", week, lambda: growth.weekly_report(st, CHAT)))
        if hindi.enabled():
            jobs.append(("report_week_hi", week, lambda: _in_hi(lambda: growth.weekly_report(
                st, CHAT, key="history_hi", label="🇮🇳 HINDI CHANNEL · "))))
    for key, value, fn in jobs:
        st[key] = value  # mark first, so a failing job is not retried every 15 minutes
        write_json(STATE_PATH, st)
        try:
            fn()
        except Exception:
            log(f"{key} job failed:\n{traceback.format_exc()}")
        write_json(STATE_PATH, st)
    if jobs:
        diary.flush()


if __name__ == "__main__":
    {"make": make, "poll": poll, "tick": tick, "repost": repost, "clear": clear_pending, "hindi_test": hindi_test, "voices": voices,
     "comments": lambda: __import__("factory.growth", fromlist=["x"]).comment_digest(_state(), CHAT), "stats": lambda: _growth_now("stats"),
     "weekly": lambda: _growth_now("weekly"), "report": lambda: _growth_now("report")}[sys.argv[1]]()
