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
        d = os.path.join(REVIEW, vid)
        post = read_json(os.path.join(d, "post.json"), {})
        short = _short(vid)
        where = ", ".join(_platforms()) or "no platforms connected yet (you'll get the captions to post manually)"
        cap = (f"🎬 {post.get('title', vid)}\n\n{post.get('script', '')[:600]}\n\n"
               + ("🤖 Autopilot: posting now to: " if cfg.get("auto_post") else "Approve to post to: ") + where)
        buttons = None if cfg.get("auto_post") else [[{"text": "✅ Approve & post", "callback_data": f"ok:{short}"},
                                                      {"text": "❌ Reject", "callback_data": f"no:{short}"}]]
        try:
            msg = tg.send_video(CHAT, os.path.join(d, "video.mp4"), cap, buttons)
        except Exception as e:
            log(f"Sending to Telegram failed: {e}")
            tg.send_message(CHAT, f"⚠️ '{post.get('title', vid)}' may have arrived without working buttons "
                                  "(Telegram was slow). Ignore that copy - send /make for a new one.")
            continue
        st["pending"][short] = {"vid": vid, "post": post, "file_id": msg["video"]["file_id"],
                                "message_id": msg["message_id"], "created": dt.datetime.utcnow().isoformat()}
        write_json(STATE_PATH, st)
        log(f"Sent to Telegram: {post.get('title')}")
        diary.status("working", post.get("title", ""))
        note("manager", "Sent the finished video to the boss on Telegram")
        if cfg.get("auto_post"):
            _auto_post(st, short, os.path.join(d, "video.mp4"))
        shutil.rmtree(d, ignore_errors=True)  # the video now lives safely in your Telegram chat
    diary.status("idle")
    note("manager", "Great work everyone. Coffee break until the next video!", kind="idle")
    diary.flush()


# ------------------------------------------------------------------ publish
def _youtube(path, post):
    tok = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
    if not tok:
        return None
    from . import youtube_upload
    # GitHub secrets pasted with a stray line break or space would break the JSON - clean them
    data = json.loads(tok, strict=False)
    data = {k: ("".join(v.split()) if isinstance(v, str) else v) for k, v in data.items()}
    with open(youtube_upload.TOKEN, "w") as f:
        json.dump(data, f)
    privacy = os.environ.get("YOUTUBE_PRIVACY") or load_config().get("youtube_privacy", "public")
    return youtube_upload.upload(path, post, privacy)


def publish(item, local_path=None):
    from . import meta_upload
    path = os.path.join(REVIEW, "approved.mp4")
    if local_path and os.path.exists(local_path):
        shutil.copy(local_path, path)  # use the original file (no 20 MB Telegram download limit)
    else:
        tg.download(item["file_id"], path)
    post, results = item["post"], {}
    jobs = [
        ("YouTube", lambda: _youtube(path, post)),
        ("Facebook", lambda: meta_upload.facebook_reel(path, post["facebook"], os.environ["META_PAGE_ID"], os.environ["META_PAGE_TOKEN"])
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
    st["history"].append({"title": item["post"].get("title"), "status": "auto-posted", "links": results})
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
        st["history"].append({"title": item["post"].get("title"), "status": "posted", "links": results})
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
                    st["history"].append({"title": item["post"].get("title"), "status": "rejected"})
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
                    st["history"].append({"title": item["post"].get("title"), "status": "posted", "links": results})
            st["history"] = st["history"][-300:]
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
        return
    if slot:
        st["last_slot"] = slot  # mark first, so a later tick never makes a duplicate
    write_json(STATE_PATH, st)
    log(f"Making a video now ({'slot ' + slot if slot else 'asked for with /make'}).")
    make()


if __name__ == "__main__":
    {"make": make, "poll": poll, "tick": tick}[sys.argv[1]]()
