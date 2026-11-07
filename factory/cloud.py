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
        shutil.rmtree(d, ignore_errors=True)  # the video now lives safely in your Telegram chat
        log(f"Sent to Telegram: {post.get('title')}")
        if cfg.get("auto_post"):
            _auto_post(st, short)


# ------------------------------------------------------------------ publish
def _youtube(path, post):
    tok = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
    if not tok:
        return None
    from . import youtube_upload
    with open(youtube_upload.TOKEN, "w") as f:
        f.write(tok)
    privacy = os.environ.get("YOUTUBE_PRIVACY") or load_config().get("youtube_privacy", "public")
    return youtube_upload.upload(path, post, privacy)


def publish(item):
    from . import meta_upload
    path = os.path.join(REVIEW, "approved.mp4")
    tg.download(item["file_id"], path)
    post, results = item["post"], {}
    jobs = [
        ("YouTube", lambda: _youtube(path, post)),
        ("Instagram", lambda: meta_upload.instagram_reel(path, post["instagram"], os.environ["IG_USER_ID"], os.environ["META_PAGE_TOKEN"])
         if "Instagram" in _platforms() else None),
        ("Facebook", lambda: meta_upload.facebook_reel(path, post["facebook"], os.environ["META_PAGE_ID"], os.environ["META_PAGE_TOKEN"])
         if "Facebook" in _platforms() else None),
    ]
    for name, fn in jobs:
        try:
            url = fn()
            if url:
                results[name] = url
        except Exception as e:
            log(f"{name} upload failed:\n{traceback.format_exc()}")
            results[name] = f"FAILED: {str(e)[:200]}"
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


def _auto_post(st, short):
    """Autopilot: post right away without waiting for a tap (config.json "auto_post": true)."""
    item = st["pending"].pop(short)
    try:
        tg.clear_buttons(CHAT, item["message_id"])
        results = publish(item)
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


def poll():
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
