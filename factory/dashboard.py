"""The Control Room - a private review page on your own PC (http://localhost:8765).
Watch each new video, then Approve or Reject. Nothing is ever posted without your click."""
import html
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .common import APPROVED, POSTED, REJECTED, REVIEW, ROOT, load_config, log, read_json, write_json

PORT = 8765
STATE = {"making": False, "msg": ""}
FOLDERS = {"review": REVIEW, "approved": APPROVED, "posted": POSTED}
LINKS = {
    "tiktok": ("TikTok", "https://www.tiktok.com/tiktokstudio/upload"),
    "instagram": ("Instagram + Facebook", "https://business.facebook.com/latest/reels_composer"),
    "youtube": ("YouTube", "https://studio.youtube.com"),
}


def _items(folder):
    base = FOLDERS[folder]
    out = []
    for name in sorted(os.listdir(base), reverse=True):
        d = os.path.join(base, name)
        if os.path.isfile(os.path.join(d, "video.mp4")):
            out.append((name, read_json(os.path.join(d, "post.json"), {})))
    return out


def _move(vid, src, dst):
    shutil.move(os.path.join(FOLDERS.get(src, src), vid), os.path.join(dst, vid))


def _approve(vid):
    _move(vid, "review", APPROVED)
    cfg = load_config()
    d = os.path.join(APPROVED, vid)
    post = read_json(os.path.join(d, "post.json"), {})
    post["status"] = "approved"
    post.setdefault("posted", {})
    if cfg.get("youtube_upload_enabled"):
        try:
            from .youtube_upload import upload
            post["posted"]["youtube"] = upload(os.path.join(d, "video.mp4"), post, cfg.get("youtube_privacy", "private"))
            STATE["msg"] = "Uploaded to YouTube."
        except Exception as e:
            log("YouTube upload failed:\n" + traceback.format_exc())
            STATE["msg"] = f"YouTube upload failed: {e}"
    write_json(os.path.join(d, "post.json"), post)


def _mark(vid, platform):
    d = os.path.join(APPROVED, vid)
    post = read_json(os.path.join(d, "post.json"), {})
    post.setdefault("posted", {})[platform] = True
    write_json(os.path.join(d, "post.json"), post)
    if all(k in post["posted"] for k in ("tiktok", "instagram", "youtube")):
        _move(vid, APPROVED, POSTED)


def _make():
    STATE["making"] = True
    try:
        subprocess.run([sys.executable, "-m", "factory.make_videos"], cwd=ROOT)
    finally:
        STATE["making"] = False
        STATE["msg"] = "New videos are ready below."


def _open_folder(path):
    if os.name == "nt":
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.run(["open", path])
    else:
        subprocess.run(["xdg-open", path])


CSS = """
:root{--bg:#0f1115;--card:#181b22;--line:#2a2f3a;--text:#eef1f6;--mute:#9aa3b2;--acc:#ffd60a;--ok:#2ecc71;--bad:#ff5c5c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:#0f1115ee;backdrop-filter:blur(6px);border-bottom:1px solid var(--line);padding:14px 24px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;z-index:5}
h1{font-size:18px;margin:0 12px 0 0}nav a{color:var(--mute);text-decoration:none;margin-right:14px;font-weight:600}nav a.on{color:var(--acc)}
.grow{flex:1}button,.btn{border:0;border-radius:10px;padding:9px 14px;font-weight:700;cursor:pointer;font-size:14px;text-decoration:none;display:inline-block}
.p{background:var(--acc);color:#111}.ok{background:var(--ok);color:#06210f}.bad{background:#2a1416;color:var(--bad);border:1px solid #5a2a2e}.g{background:#232834;color:var(--text)}
main{padding:24px;display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;display:flex;flex-direction:column;gap:10px}
video{width:100%;aspect-ratio:9/16;border-radius:12px;background:#000}
h3{margin:0;font-size:16px}.mute{color:var(--mute);font-size:13px}.row{display:flex;gap:8px;flex-wrap:wrap}
details{background:#11141a;border-radius:10px;padding:8px 10px}summary{cursor:pointer;font-weight:600}
textarea{width:100%;background:#0b0d11;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px;font:13px system-ui;resize:vertical}
.msg{background:#1d2a1d;border:1px solid #2f5a2f;padding:8px 12px;border-radius:10px}.empty{color:var(--mute);padding:40px}
.done{opacity:.55;text-decoration:line-through}
"""


def _page(tab):
    items = _items(tab)
    cfg = load_config()
    cards = []
    for vid, post in items:
        q = urllib.parse.quote(vid)
        posted = post.get("posted", {})
        caps = ""
        for key, label in (("tiktok", "TikTok caption"), ("instagram", "Instagram caption"), ("facebook", "Facebook caption")):
            if post.get(key):
                caps += f"<details><summary>{label}</summary><textarea rows=4 onclick='this.select()'>{html.escape(post[key])}</textarea>" \
                        f"<button class='g' onclick=\"navigator.clipboard.writeText(this.previousElementSibling.value);this.textContent='Copied!'\">Copy</button></details>"
        yt = post.get("youtube", {})
        caps += f"<details><summary>YouTube title + description</summary><textarea rows=5>{html.escape(yt.get('title', ''))}\n\n{html.escape(yt.get('description', ''))}</textarea></details>"
        if tab == "review":
            actions = f"<form method=post action='/approve?id={q}'><button class=ok>✓ Approve</button></form>" \
                      f"<form method=post action='/reject?id={q}'><button class=bad>✕ Reject</button></form>"
        elif tab == "approved":
            acts = []
            for key, (label, url) in LINKS.items():
                if posted.get(key):
                    val = posted[key]
                    link = f" <a href='{html.escape(val)}' target=_blank style='color:var(--acc)'>view</a>" if isinstance(val, str) else ""
                    acts.append(f"<span class='mute done'>{label} posted</span>{link}")
                else:
                    acts.append(f"<a class='btn g' href='{url}' target=_blank>Open {label}</a>"
                                f"<form method=post action='/mark?id={q}&p={key}'><button class=g>Mark {label} posted</button></form>")
            actions = f"<form method=post action='/open?id={q}'><button class=p>📂 Show video file</button></form>" + "".join(acts)
        else:
            actions = ""
        cards.append(f"""<div class=card><video controls preload=metadata poster='/media/{tab}/{q}/cover.jpg' src='/media/{tab}/{q}/video.mp4'></video>
<h3>{html.escape(post.get('title', vid))}</h3><div class=mute>{html.escape(post.get('created', ''))} · script: {html.escape(str(post.get('source', '')))}</div>
<div class=row>{actions}</div>{caps}</div>""")
    counts = {k: len(_items(k)) for k in FOLDERS}
    nav = "".join(f"<a class='{'on' if k == tab else ''}' href='/?tab={k}'>{k.title()} ({counts[k]})</a>" for k in FOLDERS)
    making = "<span class=mute>⏳ Making videos… refresh in a few minutes</span>" if STATE["making"] else \
             "<form method=post action='/make'><button class=p>＋ Make new videos now</button></form>"
    msg = f"<div class=msg>{html.escape(STATE['msg'])}</div>" if STATE["msg"] else ""
    STATE["msg"] = ""
    yt = "YouTube auto-upload: ON" if cfg.get("youtube_upload_enabled") else "YouTube auto-upload: off (see START HERE)"
    empty = {"review": "No videos waiting. Click 'Make new videos now'.", "approved": "Approved videos waiting to be posted show up here.",
             "posted": "Fully posted videos are archived here."}[tab]
    body = "".join(cards) or f"<div class=empty>{empty}</div>"
    refresh = "<meta http-equiv=refresh content=20>" if STATE["making"] else ""
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>{refresh}
<title>Video Factory</title><style>{CSS}</style></head><body><header><h1>🎬 Faceless Video Factory</h1><nav>{nav}</nav>
<span class=grow></span><span class=mute>{yt}</span>{making}</header>{('<div style="padding:16px 24px 0">' + msg + '</div>') if msg else ''}<main>{body}</main></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/media/"):
            parts = [urllib.parse.unquote(p) for p in u.path.split("/")[2:]]
            if len(parts) != 3 or parts[0] not in FOLDERS or ".." in parts[1] or parts[2] not in ("video.mp4", "cover.jpg"):
                return self.send_error(404)
            return self._file(os.path.join(FOLDERS[parts[0]], parts[1], parts[2]))
        tab = urllib.parse.parse_qs(u.query).get("tab", ["review"])[0]
        tab = tab if tab in FOLDERS else "review"
        data = _page(tab).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path):
        if not os.path.isfile(path):
            return self.send_error(404)
        size = os.path.getsize(path)
        ctype = "video/mp4" if path.endswith(".mp4") else "image/jpeg"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = int(b) if b else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError):
                pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        vid = q.get("id", [""])[0]
        if vid and (".." in vid or "/" in vid or "\\" in vid):
            return self.send_error(400)
        try:
            if u.path == "/approve":
                _approve(vid)
                return self._redirect("/?tab=approved" if not STATE["msg"].startswith("YouTube upload failed") else "/?tab=approved")
            if u.path == "/reject":
                _move(vid, "review", REJECTED)
                return self._redirect("/?tab=review")
            if u.path == "/mark":
                _mark(vid, q.get("p", [""])[0])
                return self._redirect("/?tab=approved")
            if u.path == "/open":
                _open_folder(os.path.join(APPROVED, vid))
                return self._redirect("/?tab=approved")
            if u.path == "/make" and not STATE["making"]:
                threading.Thread(target=_make, daemon=True).start()
                return self._redirect("/?tab=review")
        except Exception as e:
            STATE["msg"] = f"Error: {e}"
            log(traceback.format_exc())
        self._redirect("/")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://localhost:{PORT}"
    print(f"\n  Review page running at {url}\n  Keep this window open while reviewing. Close it when done.\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
