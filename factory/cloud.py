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
        vid = make_videos.make_one(cfg)
        if not vid:
            tg.send_message(CHAT, "⚠️ A video failed to render. Check the GitHub Actions log.")
            continue
        d = os.path.join(REVIEW, vid)
        post = read_json(os.path.join(d, "post.json"), {})
        short = _short(vid)
        where = ", ".join(_platforms()) or "no platforms connected yet (you'll get the captions to post manually)"
        cap = (f"🎬 {post.get('title', vid)}\n\n{post.get('script', '')[:600]}\n\n"
               f"Approve to post to: {where}")
        buttons = [[{"text": "✅ Approve & post", "callback_data": f"ok:{short}"},
                    {"text": "❌ Reject", "callback_data": f"no:{short}"}]]
        msg = tg.send_video(CHAT, os.path.join(d, "video.mp4"), cap, buttons)
        st["pending"][short] = {"vid": vid, "post": post, "file_id": msg["video"]["file_id"],
                                "message_id": msg["message_id"], "created": dt.datetime.utcnow().isoformat()}
        write_json(STATE_PATH, st)
        shutil.rmtree(d, ignore_errors=True)  # the video now lives safely in your Telegram chat
        log(f"Sent to Telegram for approval: {post.get('title')}")


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


def poll():
    st = _state()
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
                    results = publish(item)
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
    log(f"Processed {len(updates)} Telegram update(s). Pending approvals: {len(st['pending'])}")


if __name__ == "__main__":
    {"make": make, "poll": poll}[sys.argv[1]]()
